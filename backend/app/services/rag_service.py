# RAG (Retrieval-Augmented Generation) Service
# Refactored v3.0 — Parent-Child Chunking + Cross-Encoder Reranker Pipeline
#
# Architecture (Refactored):
#   Offline: ParentChildChunker → Qdrant (child vectors w/ parent_id metadata) + ParentStore (parent content)
#   Online: Query → Qdrant dense search (child) → Resolve parent via ParentStore → Cross-Encoder Rerank → LLM

import os
import re
import uuid
import time
from typing import List, Tuple, Optional, Dict, Any

from langchain_community.document_loaders import (
    PyPDFLoader,
    Docx2txtLoader,
    CSVLoader,
    TextLoader,
    UnstructuredMarkdownLoader,
    UnstructuredFileLoader,
    UnstructuredPowerPointLoader,
    UnstructuredExcelLoader,
)
from langchain_core.documents import Document
from langchain_qdrant import Qdrant
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

from app.core.config import get_settings

from app.rag.ingestion.chunking.parent_child_chunker import ParentChildChunker
from app.rag.ingestion.storage.parent_store import get_parent_store, ParentStore
from app.rag.libs.reranker.reranker_factory import create_reranker

settings = get_settings()

# =========================================================================
# Embedding Model Constants
# =========================================================================

DEFAULT_LOCAL_EMBEDDING = "qwen3-embedding:4b"
DEFAULT_OPENAI_EMBEDDING = "text-embedding-3-small"

BGE_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
BGE_DOC_INSTRUCTION = ""

MAX_QDRANT_BATCH = 50

# =========================================================================
# Document Loader Registry
# =========================================================================

LOADER_MAP = {
    ".pdf":  "pdf_ocr",
    ".docx": Docx2txtLoader,
    ".doc":  Docx2txtLoader,
    ".csv":  CSVLoader,
    ".txt":  TextLoader,
    ".md":   UnstructuredMarkdownLoader,
    ".ppt":  UnstructuredPowerPointLoader,
    ".pptx": UnstructuredPowerPointLoader,
    ".xlsx": UnstructuredExcelLoader,
    ".xls":  UnstructuredExcelLoader,
    ".html": UnstructuredFileLoader,
    ".htm":  UnstructuredFileLoader,
    ".xml":  UnstructuredFileLoader,
    ".json": UnstructuredFileLoader,
    ".epub": UnstructuredFileLoader,
}

SUPPORTED_EXTENSIONS = set(LOADER_MAP.keys())


def _get_loader_for_extension(file_path: str, ext: str):
    loader_cls = LOADER_MAP.get(ext.lower(), UnstructuredFileLoader)
    return loader_cls


def _load_with_encoding_detection(file_path: str, loader_cls) -> List[Document]:
    if loader_cls == "pdf_ocr":
        try:
            from app.rag.ingestion.loader import PDFOCREnhancedLoader
            loader = PDFOCREnhancedLoader(file_path=file_path, ocr_threshold=0.30)
            return loader.load()
        except Exception as e:
            from langchain_community.document_loaders import PyPDFLoader
            pypdf_loader = PyPDFLoader(file_path)
            return pypdf_loader.load()

    loader_kwargs = {}

    if loader_cls is CSVLoader:
        import chardet
        with open(file_path, "rb") as f:
            raw = f.read()
        detected = chardet.detect(raw)
        encoding = detected.get("encoding", "utf-8") or "utf-8"
        loader_kwargs["encoding"] = encoding

    if loader_cls is TextLoader:
        loader_kwargs["encoding"] = "utf-8"

    if loader_cls is UnstructuredFileLoader:
        loader_kwargs["autodetect_encoding"] = True

    loader = loader_cls(file_path, **loader_kwargs)
    return loader.load()


class RAGService:
    """
    Refactored RAG service using Parent-Child Chunking + Cross-Encoder Reranker.
    """

    def __init__(self):
        self._qdrant_instances: Dict[str, Qdrant] = {}
        self._embedding_cache: Dict[str, object] = {}
        self._qdrant_client: Optional[QdrantClient] = None
        self._parent_chunker = self._build_chunker_for_type("manual")
        self._parent_store: ParentStore = get_parent_store(
            persist_dir="data/db/parent_store"
        )

    def _get_qdrant_client(self) -> QdrantClient:
        if self._qdrant_client is None:
            self._qdrant_client = QdrantClient(
                url=settings.QDRANT_URL,
                timeout=60,
                prefer_grpc=True,
            )
        return self._qdrant_client

    def _get_vector_store(self, collection_name: str, emb_fn):
        client = self._get_qdrant_client()
        return Qdrant(
            client=client,
            collection_name=collection_name,
            embeddings=emb_fn,
            distance_strategy="COSINE",
        )

    def _ensure_collection_exists(self, collection_name: str, vector_size: int):
        """Ensure a Qdrant collection exists, recreating it if needed."""
        client = self._get_qdrant_client()
        try:
            client.get_collection(collection_name)
        except Exception:
            client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )

    # =====================================================================
    # v2.0: Adaptive chunking — select parameters based on document type
    # =====================================================================

    @staticmethod
    def _detect_doc_type(filename: str, text_preview: str = "") -> str:
        name_lower = filename.lower()
        manual_keywords = [
            "说明书", "manual", "guide", "指南", "使用说明",
            "安装", "用户手册", "操作手册", "维护手册",
        ]
        tech_keywords = [
            "技术", "spec", "specification", "参考", "reference",
            "白皮书", "whitepaper", "api", "sdk", "开发",
        ]

        for kw in manual_keywords:
            if kw in name_lower:
                return "manual"
        for kw in tech_keywords:
            if kw in name_lower:
                return "tech_doc"

        if text_preview:
            text_lower = text_preview.lower()
            for kw in manual_keywords:
                if kw in text_lower:
                    return "manual"
            for kw in tech_keywords:
                if kw in text_lower:
                    return "tech_doc"

        return "generic"

    @staticmethod
    def _build_chunker_for_type(doc_type: str) -> ParentChildChunker:
        configs = {
            "manual":    {"parent_chunk_size": 600,   "child_chunk_size": 200},
            "tech_doc":  {"parent_chunk_size": 900,   "child_chunk_size": 256},
            "generic":   {"parent_chunk_size": 750,   "child_chunk_size": 200},
        }
        cfg = configs.get(doc_type, configs["generic"])
        overlap_ratio = 0.15
        return ParentChildChunker(
            parent_chunk_size=cfg["parent_chunk_size"],
            parent_overlap=int(cfg["parent_chunk_size"] * overlap_ratio),
            child_chunk_size=cfg["child_chunk_size"],
            child_overlap=int(cfg["child_chunk_size"] * overlap_ratio),
            min_chunk_size=50,
        )

    def _embed_in_batches(self, emb_fn, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = emb_fn.embed_documents(batch)
            all_embeddings.extend(batch_embeddings)
        return all_embeddings

    @staticmethod
    def _check_document_quality(
        filename: str, total_chars: int, num_pages: int,
        num_parents: int, num_children: int, loader_name: str
    ) -> List[str]:
        warnings = []
        pages = max(num_pages, 1)

        expected_min_children = max(20, pages * 2)
        if num_children < expected_min_children:
            warnings.append(
                f"Low chunk count: {num_children} children for {pages} pages "
                f"(expected >{expected_min_children}). Document may be image-heavy."
            )

        chars_per_page = total_chars / pages
        if chars_per_page < 50:
            warnings.append(
                f"Low text density: {chars_per_page:.0f} chars/page "
                f"(expected >50). Document may be scanned/image-only."
            )

        if num_parents > 0:
            ratio = num_children / num_parents
            if ratio < 1.5:
                warnings.append(
                    f"Unusual parent-child ratio: {ratio:.1f} children per parent "
                    f"(expected >1.5). Chunking may be suboptimal."
                )

        return warnings

    # =====================================================================
    # Embedding Function
    # =====================================================================

    def get_embedding_function(
        self,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        from langchain_community.embeddings import HuggingFaceBgeEmbeddings
        from langchain_community.embeddings import OpenAIEmbeddings
        from langchain_community.embeddings import OllamaEmbeddings

        model_name = model_name or DEFAULT_LOCAL_EMBEDDING
        cache_key = f"{model_name}__{hash(api_key or 'no_key')}__{provider or 'none'}"

        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]

        ollama_api_host = os.getenv(
            "OLLAMA_HOST",
            os.getenv("OLLAMA_BASE_URL", settings.OLLAMA_HOST),
        )
        for suffix in ("/v1", "/"):
            if ollama_api_host.rstrip("/").endswith(suffix):
                ollama_api_host = ollama_api_host.rstrip("/")
                if suffix in ("/v1",):
                    ollama_api_host = ollama_api_host[:-len(suffix)]
                if suffix in ("/",):
                    ollama_api_host = ollama_api_host[:-len(suffix)]
                break

        provider_lower = (provider or "").lower()

        is_ollama_model = (
            not provider_lower
            and any(kw in model_name.lower() for kw in ["qwen", "bge", "nomic", "mxbai"])
        )
        if provider_lower == "ollama" or is_ollama_model:
            embedding = OllamaEmbeddings(
                model=model_name,
                base_url=ollama_api_host,
            )
        elif provider_lower == "openai" or model_name.startswith("text-embedding-"):
            if not api_key:
                api_key = os.getenv("OPENAI_API_KEY", settings.OPENAI_API_KEY or "")
            if not api_key:
                raise ValueError(
                    "OpenAI API key is required for OpenAI embedding models. "
                    "Set OPENAI_API_KEY in environment variables or .env file."
                )
            embedding = OpenAIEmbeddings(
                model=model_name,
                openai_api_key=api_key,
            )
        else:
            embedding = HuggingFaceBgeEmbeddings(
                model_name=model_name,
                model_kwargs={"device": getattr(settings, "EMBEDDING_DEVICE", "cpu")},
                encode_kwargs={"normalize_embeddings": True},
                query_instruction=BGE_QUERY_INSTRUCTION,
            )

        return embedding

    def _get_vector_dimension(self, emb_fn) -> int:
        """Dynamically get the vector dimension from the embedding function."""
        test_vec = emb_fn.embed_query("test")
        return len(test_vec)

    # =====================================================================
    # Document Loading
    # =====================================================================

    def _load_document_safe(self, file_path: str, ext: str) -> Tuple[str, List[Document]]:
        ext_lower = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        preferred_cls = _get_loader_for_extension(file_path, ext_lower)
        loader_name = (getattr(preferred_cls, "__name__", None)
                       or preferred_cls.__class__.__name__)

        try:
            docs = _load_with_encoding_detection(file_path, preferred_cls)
            return loader_name, docs
        except Exception:
            try:
                fallback_cls = UnstructuredFileLoader
                docs = _load_with_encoding_detection(file_path, fallback_cls)
                return "UnstructuredFileLoader (fallback)", docs
            except Exception:
                raise

    def load_document(self, file_path: str, file_type: str) -> List[Document]:
        _, docs = self._load_document_safe(file_path, file_type)
        return docs

    # =====================================================================
    # Processing Pipeline
    # =====================================================================

    def process_and_index(
        self,
        file_path: str,
        file_type: str,
        collection_name: str,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        embedding_model: Optional[str] = None,
        embedding_api_key: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> int:
        import logging
        logger = logging.getLogger(__name__)

        loader_name, raw_docs = self._load_document_safe(file_path, file_type)

        if not raw_docs:
            raise ValueError(f"No content extracted from file (loader={loader_name})")

        simple_filename = file_path.replace('\\', '/').split('/')[-1]
        text_preview = raw_docs[0].page_content[:500] if raw_docs[0].page_content else ""
        doc_type = self._detect_doc_type(simple_filename, text_preview)
        chunker = self._build_chunker_for_type(doc_type)

        logger.info(
            f"[RAG] Detected doc type '{doc_type}' for {simple_filename}. "
            f"Parent={chunker.parent_chunk_size*2} chars, Child={chunker.child_chunk_size*2} chars"
        )

        all_parents: List[Dict[str, Any]] = []
        all_children: List[Dict[str, Any]] = []
        total_chars = 0

        for doc_idx, doc in enumerate(raw_docs):
            text = doc.page_content
            if not text:
                continue
            total_chars += len(text)
            doc_meta = {
                **(doc.metadata or {}),
                "source": file_path,
                "file_type": file_type,
                "loader": loader_name,
                "doc_index": doc_idx,
                "filename": simple_filename,
                "pipeline_version": "3.0",
            }

            result = chunker.split_text(text, metadata=doc_meta)

            for parent in result["parents"]:
                parent["metadata"]["doc_index"] = doc_idx
                parent["metadata"]["filename"] = simple_filename
                parent["metadata"]["pipeline_version"] = "3.0"
                all_parents.append(parent)

            for child in result["children"]:
                child["metadata"]["doc_index"] = doc_idx
                child["metadata"]["filename"] = simple_filename
                child["metadata"]["pipeline_version"] = "3.0"
                all_children.append(child)

        if not all_children:
            raise ValueError("Document processed but no child chunks were created")
        if not all_parents:
            raise ValueError("Document processed but no parent chunks were created")

        parent_items: Dict[str, str] = {}
        for p in all_parents:
            parent_items[p["parent_id"]] = p["content"]
        self._parent_store.put_batch(collection_name, parent_items)

        child_texts = [c["content"] for c in all_children]
        child_metadatas = [c["metadata"] for c in all_children]

        try:
            emb_fn = self.get_embedding_function(
                model_name=embedding_model,
                api_key=embedding_api_key,
                provider=embedding_provider,
            )
            embeddings = self._embed_in_batches(emb_fn, child_texts, batch_size=32)

            # Dynamic vector dimension
            vector_size = self._get_vector_dimension(emb_fn)
            self._ensure_collection_exists(collection_name, vector_size)

            client = self._get_qdrant_client()

            # Batch upsert with MAX_QDRANT_BATCH protection
            child_ids = [str(uuid.uuid4()) for _ in child_texts]
            points = []
            for i in range(len(child_texts)):
                points.append(PointStruct(
                    id=child_ids[i],
                    vector=embeddings[i],
                    payload={
                        "text": child_texts[i],
                        **child_metadatas[i],
                    }
                ))

            for batch_start in range(0, len(points), MAX_QDRANT_BATCH):
                batch = points[batch_start:batch_start + MAX_QDRANT_BATCH]
                client.upsert(
                    collection_name=collection_name,
                    points=batch,
                    wait=True,
                )

            chunk_count = len(child_ids)
        except Exception as qdrant_err:
            raise RuntimeError(
                f"Qdrant insert failed for collection '{collection_name}': {qdrant_err}"
            ) from qdrant_err

        self._parent_store.flush(collection_name)

        num_pages = len(raw_docs)
        if file_type == "pdf":
            try:
                import fitz
                pdf = fitz.open(file_path)
                num_pages = len(pdf)
                pdf.close()
            except Exception:
                pass

        quality_warnings = self._check_document_quality(
            filename=simple_filename,
            total_chars=total_chars,
            num_pages=num_pages,
            num_parents=len(all_parents),
            num_children=len(all_children),
            loader_name=loader_name,
        )
        for warning in quality_warnings:
            logger.warning(f"[RAG Quality] {simple_filename}: {warning}")

        return chunk_count

    # =====================================================================
    # Search — Parent-Child Dense Retrieval
    # =====================================================================

    def search_child_chunks(
        self,
        query: str,
        collection_name: str,
        top_k: int = 15,
        embedding_model: Optional[str] = None,
        embedding_api_key: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            emb_fn = self.get_embedding_function(
                model_name=embedding_model,
                api_key=embedding_api_key,
                provider=embedding_provider,
            )
            query_vec = emb_fn.embed_query(query)
        except Exception:
            return []

        try:
            client = self._get_qdrant_client()
            search_result = client.search(
                collection_name=collection_name,
                query_vector=query_vec,
                limit=top_k,
                with_payload=True,
            )
        except Exception:
            return []

        formatted = []
        seen_ids = set()
        for scored_point in search_result:
            cid = str(scored_point.id)
            if cid in seen_ids:
                continue
            seen_ids.add(cid)

            payload = scored_point.payload or {}
            text = payload.pop("text", "") if isinstance(payload, dict) else ""
            meta = dict(payload) if isinstance(payload, dict) else {}

            similarity = float(scored_point.score)
            parent_id = meta.get("parent_id", "")

            formatted.append({
                "chunk_id": cid,
                "parent_id": parent_id,
                "text": text,
                "score": similarity,
                "metadata": dict(meta),
            })

        return formatted

    def resolve_parents(
        self,
        collection_name: str,
        child_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not child_results:
            return []

        parent_ids = set()
        child_by_parent: Dict[str, List[Dict[str, Any]]] = {}
        for child in child_results:
            pid = child.get("parent_id", "")
            if not pid:
                continue
            parent_ids.add(pid)
            if pid not in child_by_parent:
                child_by_parent[pid] = []
            child_by_parent[pid].append(child)

        if not parent_ids:
            return []

        parent_map = self._parent_store.get_batch(collection_name, list(parent_ids))

        parents = []
        for pid, content in parent_map.items():
            children = child_by_parent.get(pid, [])
            max_score = max((c.get("score", 0) for c in children), default=0.0)
            parents.append({
                "parent_id": pid,
                "content": content,
                "max_child_score": max_score,
                "child_count": len(children),
                "child_scores": [c.get("score", 0) for c in children],
            })

        parents.sort(key=lambda x: x["max_child_score"], reverse=True)

        return parents

    def search_with_parent_child(
        self,
        query: str,
        collection_name: str,
        top_k_children: int = 15,
        embedding_model: Optional[str] = None,
        embedding_api_key: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        children = self.search_child_chunks(
            query=query,
            collection_name=collection_name,
            top_k=top_k_children,
            embedding_model=embedding_model,
            embedding_api_key=embedding_api_key,
            embedding_provider=embedding_provider,
        )

        if not children:
            return []

        parents = self.resolve_parents(collection_name, children)

        return parents

    # =====================================================================
    # Collection Management
    # =====================================================================

    def delete_collection(self, collection_name: str) -> bool:
        try:
            keys_to_delete = [k for k in self._qdrant_instances if k.startswith(collection_name)]
            for k in keys_to_delete:
                del self._qdrant_instances[k]

            client = self._get_qdrant_client()
            try:
                client.delete_collection(collection_name)
            except Exception:
                pass

            self._parent_store.delete_collection(collection_name)

            return True
        except Exception:
            return False

    def get_collection_count(self, collection_name: str) -> int:
        try:
            client = self._get_qdrant_client()
            collection_info = client.get_collection(collection_name)
            return collection_info.points_count
        except Exception:
            return 0

    def list_collections(self) -> List[str]:
        try:
            client = self._get_qdrant_client()
            collections = client.get_collections()
            return [c.name for c in collections.collections]
        except Exception:
            return []

    def clear_cache(self):
        self._embedding_cache.clear()
        self._qdrant_instances.clear()


rag_service = RAGService()
# Celery Tasks for Document Processing
# Offloads heavy embedding/indexing work from the FastAPI request cycle

import asyncio
import traceback
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from app.celery_app import celery_app
from app.services.rag_service import rag_service
from app.core.database import engine, get_db
from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument, KBStatus


# Create a sync session factory for Celery worker (sync context)
from sqlalchemy import create_engine as sync_create_engine
from app.core.config import get_settings

_settings = get_settings()
_sync_engine = sync_create_engine(_settings.DATABASE_URL_SYNC)
_SyncSessionLocal = sessionmaker(bind=_sync_engine)


def _update_doc_progress(doc_id: str, progress: int, message: str):
    """Update document processing progress without changing status."""
    session = _SyncSessionLocal()
    try:
        doc = session.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
        if doc:
            doc.progress = progress
            doc.progress_message = message
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"[DOC CELERY] Progress update failed for doc {doc_id}: {e}", flush=True)
    finally:
        session.close()


def _update_doc_status(doc_id: str, kb_id: str, status: str, chunk_count: int = 0, error_message: str = None,
                       progress: int = 0, progress_message: str = None):
    """
    Update the document status in the database after processing.
    This runs in the Celery worker's sync context.

    NOTE: We do NOT update kb.document_count here because:
      - The upload/delete API endpoints already set it correctly via flush()
      - The Celery worker runs in a separate process/session and may query
        BEFORE the async API session commits, leading to incorrect count=0
      - Document count only changes when documents are added or deleted,
        not when their processing status changes
    """
    import traceback as _tb
    session = _SyncSessionLocal()
    try:
        doc = session.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
        if doc:
            doc.status = status
            doc.chunk_count = chunk_count
            doc.progress = progress
            doc.progress_message = progress_message
            if error_message:
                doc.error_message = error_message
            else:
                doc.error_message = None

        session.commit()
    except Exception as e:
        session.rollback()
        print(f"[DOC CELERY] DB update failed for doc {doc_id}: {e}\n{_tb.format_exc()}", flush=True)
        raise
    finally:
        session.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, autoretry_for=(Exception,))
def process_document_task(
    self,
    file_path: str,
    file_type: str,
    collection_name: str,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    embedding_model: str = None,
    embedding_api_key: str = None,
    embedding_provider: str = None,
    doc_id: str = None,
    kb_id: str = None,
) -> int:
    """
    Async Celery task to process a document: load, chunk, embed, and index into Qdrant.

    This replaces the old synchronous call in knowledge.py upload endpoint.
    The Celery worker handles the heavy lifting (model download, embedding generation)
    without blocking the FastAPI request cycle.

    After processing, the document status in the database is updated to READY or ERROR.

    Args:
        file_path: Path to the uploaded document.
        file_type: Document extension (pdf, docx, csv, md, txt).
        collection_name: Qdrant collection name.
        chunk_size: Size of text chunks.
        chunk_overlap: Overlap between chunks.
        embedding_model: Embedding model name (None = default local BGE).
        embedding_api_key: API key for cloud embedding services.
        embedding_provider: Provider type ('ollama', 'openai', or None for local).
        doc_id: Document record ID to update status for.
        kb_id: Knowledge base ID to update document count for.

    Returns:
        Number of chunks indexed.
    """
    print(f"[DOC TASK] Starting processing: file={file_path} collection={collection_name} "
          f"model={embedding_model} provider={embedding_provider}", flush=True)

    try:
        # Stage 1: 正在解析文档
        if doc_id:
            _update_doc_progress(doc_id, progress=10, message="正在解析文档")
        chunk_count = rag_service.process_and_index(
            file_path=file_path,
            file_type=file_type,
            collection_name=collection_name,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            embedding_model=embedding_model,
            embedding_api_key=embedding_api_key,
            embedding_provider=embedding_provider,
        )

        # v3.0: Parent-Child chunking stores parents in ParentStore during process_and_index.
        # No separate BM25 build needed anymore — precision ranking is handled by Cross-Encoder.
        print(f"[DOC TASK] SUCCESS: {chunk_count} child chunks indexed for {file_path} "
              f"(Parent-Child Chunking v3.0)", flush=True)

        # Update document status to READY
        if doc_id and kb_id:
            _update_doc_status(
                doc_id=doc_id,
                kb_id=kb_id,
                status=KBStatus.READY.value,
                chunk_count=chunk_count,
                progress=100,
                progress_message="处理完成",
            )
        return chunk_count

    except Exception as exc:
        tb = traceback.format_exc()
        print(f"[DOC TASK] FAILED: file={file_path}\n{tb}", flush=True)

        # Update document status to ERROR
        if doc_id and kb_id:
            try:
                _update_doc_status(
                    doc_id=doc_id,
                    kb_id=kb_id,
                    status=KBStatus.ERROR.value,
                    chunk_count=0,
                    error_message=str(exc),
                )
            except Exception as db_err:
                print(f"[DOC TASK] DB update failed after task error: {db_err}", flush=True)

        # Retry the task
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=2)
def delete_collection_task(self, collection_name: str) -> bool:
    """
    Async task to delete a Qdrant collection.
    """
    try:
        return rag_service.delete_collection(collection_name)
    except Exception as exc:
        self.retry(exc=exc)

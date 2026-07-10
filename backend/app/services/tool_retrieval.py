"""
工具语义召回模块（基于 Qdrant + bge-m3）
==========================================
复用项目现有 Qdrant 客户端与 Ollama bge-m3 嵌入能力，构建独立的工具语义索引。

核心能力：
  1. 集合初始化（startup 时自动调用）
  2. 批量工具入库，支持幂等跳过（不重复写已存在的向量）
  3. 单条 upsert（MCP 动态注册/注销时同步更新）
  4. 语义召回（根据用户 Query 召回 Top-K 语义相关工具）
  5. 小工具量短路（工具数 ≤ TopK 时跳过向量检索）
  6. 置信度检索（返回工具名+相似度得分，用于兜底阈值判断）
  7. 否定意图检测（用户拒图表述时跳过兜底）

无状态设计：向量统一存储于 Qdrant，兼容多实例与 Celery 异步任务。
"""

import json
import logging
import os
import threading
from typing import Dict, List, Optional, Tuple

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# ── 常量 ────────────────────────────────────────────────────────────────

TOOL_INDEX_COLLECTION = "tool_semantic_index"
EMBEDDING_DIM = 512

# ── 预计算工具 Embedding（启动时从 JSON 文件加载，零 Ollama 调用） ──
# 用 backend/generate_embeddings.py 离线生成，结果存入 backend/data/embeddings.json
# 容器内路径：/app/data/embeddings.json（Docker build context = backend/ 目录）
_EMBEDDINGS_PATH = '/app/data/embeddings.json'

PRECOMPUTED_EMBEDDINGS: Dict[str, List[float]] = {}
try:
    with open(_EMBEDDINGS_PATH, 'r') as f:
        PRECOMPUTED_EMBEDDINGS = json.load(f)
    logger.info(f"[ToolRetrieval] Loaded {len(PRECOMPUTED_EMBEDDINGS)} precomputed tool embeddings")
except Exception as e:
    logger.warning(f"[ToolRetrieval] Failed to load precomputed embeddings from {_EMBEDDINGS_PATH}: {e}")

_NEGATION_TRIGGERS = frozenset([
    "不用图片", "不要图片", "不用搜图", "不要搜图",
    "不要找图", "不用找图", "文字描述", "用文字",
    "不用插图", "不要插图", "不用配图", "不要配图",
    "no image", "no picture", "text only", "don't search",
    "不用图", "无图",
])

TOOL_INDEX_TEMPLATES: List[dict] = [
    {
        "name": "bing_image_search",
        "triggers": [
            "发一张图片", "找一张图片", "搜一张图片", "给我看图片",
            "来一张", "看看长什么样", "长什么样子", "图片", "照片",
            "壁纸", "表情包", "插图", "配图", "封面图", "高清图",
            "猫的图片", "风景图", "头像", "背景图", "素材图",
            "给我看看", "图", "image", "photo", "picture",
            "搜索图片", "show me a picture", "show me an image",
            "发图", "看图", "贴图", "晒图",
            # v22.0: 扩展 trigger 覆盖所有"找图"场景
            "来一张猫的图片", "来一张狗的图片", "来一张风景图片",
            "找一张图", "搜图", "找图", "上图", "发个图",
            "给我来一张", "帮我找一张", "帮我搜一下图片",
            "搜索猫的图片", "搜索狗的图片", "搜索风景图片",
            "我想要一张", "发给我", "看看图片", "图片搜索",
            "图片查找", "图片检索", "图片资源", "图片素材",
            "给我图片", "给我看", "发图片", "找图片",
            "猫图片", "狗图片", "风景图片", "美女图片",
            "搞笑图片", "可爱图片", "卡通图片",
        ],
        "description": "必应图片搜索工具。当用户需要查看/获取/搜索任何类型的图片时，必须使用此工具，禁止用文字描述替代真实图片。尤其适用于'来一张XXX的图片'、'找一张XXX的图'、'给我看XXX'等请求获取真实图片的场景。",
    },
    {
        "name": "tavily_web_search",
        "triggers": [
            "搜索", "查一下", "查询", "搜一搜", "最新",
            "新闻", "天气", "实时", "今天", "现在",
            "search", "news", "current", "latest", "weather",
            "查找", "找一下",
        ],
        "description": "联网搜索工具，获取实时最新信息（新闻、价格、版本、天气等）。当用户需要联网查询实时数据时使用。",
    },
    {
        "name": "web_fetch_content",
        "triggers": [
            "抓取网页", "读取网页", "获取网页内容", "爬取",
            "打开链接", "查看网页", "网页内容", "文章正文",
            "fetch url", "read webpage", "scrape",
        ],
        "description": "网页全文本深度抓取工具，提取指定URL的正文核心可读文本。适用于需要获取完整文章、文档内容的场景。",
    },
    {
        "name": "python_executor",
        "triggers": [
            "执行代码", "运行Python", "算一下", "计算",
            "数据分析", "数据处理", "脚本", "代码",
            "run code", "execute python", "calculate",
        ],
        "description": "安全Python代码沙盒执行工具，在受控环境中执行代码并返回结果。适用于复杂数学计算、数据处理、字符串变换等场景。",
    },
    {
        "name": "document_reader",
        "triggers": [
            "读取文档", "打开文件", "查看文件", "读文件",
            "PDF", "Word", "文档内容", "文件内容",
            "read document", "open file", "read pdf",
        ],
        "description": "多格式本地文档读取工具，支持PDF/DOCX/CSV/TXT四种格式。当用户需要查看工作区中的文件内容时使用。",
    },
    {
        "name": "http_client_request",
        "triggers": [
            "发送请求", "调用API", "请求接口", "测试接口",
            "HTTP请求", "POST请求", "GET请求", "API调用",
            "api call", "http request", "test endpoint",
        ],
        "description": "万能HTTP客户端请求工具，支持GET/POST/PUT/DELETE等方法。适用于测试外部API、发送Webhook、与非标准第三方服务交互。",
    },
    {
        "name": "markdown_to_html",
        "triggers": [
            "转HTML", "Markdown转HTML", "格式转换",
            "转换为HTML", "md转html", "渲染",
            "markdown to html", "convert to html",
        ],
        "description": "Markdown转HTML转换工具，将Markdown文本渲染为整洁的HTML片段。适用于幻灯片制作、文章发布、富文本展示等场景。",
    },
    {
        "name": "image_metadata_extractor",
        "triggers": [
            "图片信息", "图片元数据", "查看图片", "图片尺寸",
            "图片格式", "图片详情", "照片信息",
            "image info", "image metadata", "picture details",
        ],
        "description": "图片元数据提取工具，获取本地图片的基本信息（尺寸、格式、色彩模式、文件大小）。适用于前端排版前的图片信息预检。",
    },
    {
        "name": "qr_code_generator",
        "triggers": [
            "生成二维码", "二维码", "QR码", "二维码图片",
            "生成QR", "qrcode", "qr code",
        ],
        "description": "二维码生成工具，将任意文本或URL生成二维码PNG图片并保存到工作区。适用于快速分享链接、WiFi配置、联系方式等场景。",
    },
    {
        "name": "get_current_time",
        "triggers": [
            "现在几点", "当前时间", "现在时间", "几点了",
            "时间", "日期", "今天几号", "current time",
            "现在日期", "今天日期", "date", "time",
        ],
        "description": "获取当前日期和时间工具，支持时区偏移（默认东八区）。适用于查询当前时间、日期的场景。",
    },
]


class ToolRetrievalService:
    """工具语义召回服务（单例，线程安全）。"""

    def __init__(self):
        self._client: Optional[QdrantClient] = None
        self._lock = threading.Lock()
        self._initialized = False
        self._tool_count: int = 0
        self._ollama_base_url = settings.OLLAMA_HOST.rstrip("/")
        self._embedding_model = "bge-m3"

    # ── 初始化 ──────────────────────────────────────────────────────────

    def _get_client(self) -> QdrantClient:
        if self._client is None:
            self._client = QdrantClient(url=settings.QDRANT_URL, timeout=30)
        return self._client

    def _get_ollama_embedding(self, text: str) -> List[float]:
        resp = httpx.post(
            f"{self._ollama_base_url}/api/embed",
            json={"model": self._embedding_model, "input": text},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings", [])
        if not embeddings:
            raise ValueError("Ollama returned empty embeddings")
        return embeddings[0]

    def ensure_collection(self):
        client = self._get_client()
        try:
            client.get_collection(TOOL_INDEX_COLLECTION)
        except Exception:
            logger.info(f"[ToolRetrieval] Creating collection '{TOOL_INDEX_COLLECTION}' (dim={EMBEDDING_DIM})")
            client.create_collection(
                collection_name=TOOL_INDEX_COLLECTION,
                vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
            )

    def _point_exists(self, client, tool_name: str) -> bool:
        try:
            scroll_result = client.scroll(
                collection_name=TOOL_INDEX_COLLECTION,
                scroll_filter=Filter(
                    must=[FieldCondition(key="tool_name", match=MatchValue(value=tool_name))]
                ),
                limit=1,
            )
            return len(scroll_result[0]) > 0
        except Exception:
            return False

    def sync_all_tools(self):
        """幂等批量入库：用预计算 embedding 写入 Qdrant，不调用 Ollama，毫秒级完成。"""
        client = self._get_client()
        self.ensure_collection()

        with self._lock:
            points = []
            for idx, tmpl in enumerate(TOOL_INDEX_TEMPLATES):
                if self._point_exists(client, tmpl["name"]):
                    logger.info(f"[ToolRetrieval] Idempotent skip: '{tmpl['name']}' already exists")
                    continue

                trigger_text = " ".join(tmpl["triggers"])
                index_text = f"{tmpl['name']} {trigger_text} {tmpl['description']}"
                # ── 从预计算 embedding 字典中获取向量，不调用 Ollama ──
                vector = PRECOMPUTED_EMBEDDINGS.get(tmpl["name"])
                if not vector:
                    logger.warning(f"[ToolRetrieval] No precomputed embedding for '{tmpl['name']}', falling back to Ollama")
                    try:
                        vector = self._get_ollama_embedding(index_text)
                    except Exception as e:
                        logger.warning(f"[ToolRetrieval] Embedding failed for '{tmpl['name']}': {e}")
                        continue

                payload = {
                    "tool_name": tmpl["name"],
                    "triggers": tmpl["triggers"],
                    "description": tmpl["description"],
                    "index_text": index_text,
                }
                points.append(PointStruct(id=idx + 1, vector=vector, payload=payload))

            if points:
                client.upsert(collection_name=TOOL_INDEX_COLLECTION, points=points)
                logger.info(f"[ToolRetrieval] Synced {len(points)} new tool index entries (zero Ollama calls)")

            try:
                count_result = client.count(collection_name=TOOL_INDEX_COLLECTION)
                self._tool_count = count_result.count or 0
            except Exception:
                self._tool_count = len(TOOL_INDEX_TEMPLATES)

        self._initialized = True
        logger.info(f"[ToolRetrieval] Initialized with {self._tool_count} tools")

    def upsert_tool(self, tool_name: str, triggers: List[str], description: str):
        """单条工具 upsert（MCP 动态注册/注销时调用）。"""
        client = self._get_client()
        with self._lock:
            try:
                scroll_result = client.scroll(
                    collection_name=TOOL_INDEX_COLLECTION,
                    scroll_filter=Filter(
                        must=[FieldCondition(key="tool_name", match=MatchValue(value=tool_name))]
                    ),
                    limit=100,
                )
                for point in scroll_result[0]:
                    client.delete(collection_name=TOOL_INDEX_COLLECTION, points_selector=[point.id])
            except Exception:
                pass

            trigger_text = " ".join(triggers)
            index_text = f"{tool_name} {trigger_text} {description}"
            try:
                vector = self._get_ollama_embedding(index_text)
            except Exception as e:
                logger.warning(f"[ToolRetrieval] Embedding failed for '{tool_name}': {e}")
                return

            payload = {
                "tool_name": tool_name,
                "triggers": triggers,
                "description": description,
                "index_text": index_text,
            }
            try:
                count_result = client.count(collection_name=TOOL_INDEX_COLLECTION)
                new_id = (count_result.count or 0) + 1
            except Exception:
                new_id = 1

            client.upsert(collection_name=TOOL_INDEX_COLLECTION, points=[PointStruct(id=new_id, vector=vector, payload=payload)])
            self._tool_count = max(self._tool_count, new_id)
            logger.info(f"[ToolRetrieval] Upserted tool: {tool_name}")

    # ── 核心召回 ────────────────────────────────────────────────────────

    def retrieve(self, query: str, top_k: Optional[int] = None, threshold: Optional[float] = None) -> List[str]:
        """语义召回工具名称列表。小工具量时短路跳过向量检索。"""
        top_k = top_k or settings.TOOL_RETRIEVAL_TOP_K
        threshold = threshold or settings.TOOL_RETRIEVAL_THRESHOLD

        if self._initialized and self._tool_count <= top_k:
            logger.info(f"[ToolRetrieval] Shortcut: tool_count={self._tool_count} <= top_k={top_k}")
            try:
                client = self._get_client()
                scroll_result = client.scroll(collection_name=TOOL_INDEX_COLLECTION, limit=self._tool_count + 10)
                names = [p.payload.get("tool_name", "") for p in scroll_result[0] if p.payload]
                logger.info(f"[ToolRetrieval] Shortcut returned {len(names)} tools")
                return names
            except Exception as e:
                logger.warning(f"[ToolRetrieval] Shortcut scroll failed: {e}")

        if not query or not query.strip() or not self._initialized:
            return []

        client = self._get_client()
        try:
            query_vector = self._get_ollama_embedding(query)
            search_result = client.search(
                collection_name=TOOL_INDEX_COLLECTION,
                query_vector=query_vector,
                limit=top_k,
                score_threshold=threshold,
            )
            if not search_result:
                logger.info(f"[ToolRetrieval] No tools retrieved for query='{query[:50]}'")
                return []

            tool_names = [point.payload.get("tool_name", "") for point in search_result if point.payload]
            scores = [round(s.score, 4) for s in search_result]
            logger.info(f"[ToolRetrieval] Retrieved {len(tool_names)} tools: {list(zip(tool_names, scores))}")
            return tool_names
        except Exception as e:
            logger.error(f"[ToolRetrieval] Retrieval failed: {e}", exc_info=True)
            return []

    def _lazy_init(self):
        """如果首次初始化失败，在首次使用时自动重试初始化。"""
        if not self._initialized:
            try:
                self.ensure_collection()
                self.sync_all_tools()
                logger.info("[ToolRetrieval] Lazy init succeeded on first use")
            except Exception as e:
                logger.warning(f"[ToolRetrieval] Lazy init failed (will retry): {e}")

    # ── 本地 sentence-transformers 嵌入模型（懒加载，进程内推理） ──
    _LOCAL_EMBEDDER: Optional["SentenceTransformer"] = None
    _LOCAL_EMBEDDER_LOCK = threading.Lock()

    @classmethod
    def _get_local_embedder(cls):
        """懒加载并缓存本地 bge-small-zh-v1.5 模型。首次调用时加载，后续复用。
        从容器内本地绝对路径加载，不走 HuggingFace 缓存，零网络依赖。"""
        if cls._LOCAL_EMBEDDER is None:
            with cls._LOCAL_EMBEDDER_LOCK:
                if cls._LOCAL_EMBEDDER is None:
                    from sentence_transformers import SentenceTransformer
                    # 直接加载容器内本地绝对路径，和 reranker 挂载风格一致，零网络依赖
                    model_path = "/app/models_cache/bge-small-zh-v1.5"
                    logger.info(f"[ToolRetrieval] Loading local embedding model from {model_path}")
                    cls._LOCAL_EMBEDDER = SentenceTransformer(
                        model_path,
                        device="cpu",
                    )
                    logger.info("[ToolRetrieval] Local embedding model loaded successfully")
        return cls._LOCAL_EMBEDDER

    def _get_ollama_embedding(self, text: str) -> List[float]:
        """
        用本地 sentence-transformers 计算 embedding，归一化后返回 512 维向量。
        方法签名与返回值格式完全兼容原 Ollama 版本，上层调用无感知。
        """
        model = self._get_local_embedder()
        embedding = model.encode(text, normalize_embeddings=True)
        return embedding.tolist()

    def retrieve_with_scores(self, query: str, top_k: Optional[int] = None,
                             threshold: Optional[float] = None) -> List[Tuple[str, float]]:
        """语义召回，返回 (工具名, 相似度得分) 列表。"""
        top_k = top_k or settings.TOOL_RETRIEVAL_TOP_K
        threshold = threshold or settings.TOOL_RETRIEVAL_THRESHOLD

        if not query or not query.strip():
            return []

        # P3: 启动时初始化失败则首次调用时自动重试
        self._lazy_init()
        if not self._initialized:
            return []

        client = self._get_client()
        try:
            query_vector = self._get_ollama_embedding(query)
            # P0: 不传 score_threshold，避免 qdrant-client 1.11+ 阈值=0.0 时过滤所有结果
            search_kwargs = {
                "collection_name": TOOL_INDEX_COLLECTION,
                "query_vector": query_vector,
                "limit": top_k,
            }
            search_result = client.search(**search_kwargs)
            if not search_result:
                if self._tool_count <= top_k:
                    logger.info(
                        f"[ToolRetrieval][Shortcut] tool_count={self._tool_count} <= top_k={top_k}, "
                        f"search returned empty for query='{query[:50]}'"
                    )
                return []

            result = [(point.payload.get("tool_name", ""), round(s.score, 4))
                      for point, s in zip(search_result, search_result) if point.payload]

            if self._tool_count <= top_k:
                logger.info(
                    f"[ToolRetrieval][Shortcut] tool_count={self._tool_count} <= top_k={top_k}, "
                    f"result={result}"
                )
            else:
                logger.info(f"[ToolRetrieval] Retrieved w/ scores: {result}")
            return result
        except Exception as e:
            logger.error(f"[ToolRetrieval] Retrieval with scores failed: {e}", exc_info=True)
            return []

    # ── 兜底辅助 ────────────────────────────────────────────────────────

    def is_tool_relevant_with_confidence(self, query: str, target_tool_name: str,
                                         min_score: Optional[float] = None) -> Tuple[bool, float]:
        if min_score is None:
            min_score = settings.TOOL_FALLBACK_IMAGE_THRESHOLD

        scored = self.retrieve_with_scores(query, top_k=1, threshold=0.0)
        if not scored:
            return False, 0.0

        top_name, top_score = scored[0]
        if top_name == target_tool_name and top_score >= min_score:
            return True, top_score
        return False, top_score

    @staticmethod
    def has_negation_intent(query: str) -> bool:
        if not query:
            return False
        q_lower = query.lower()
        return any(trigger in q_lower for trigger in _NEGATION_TRIGGERS)


# ── 全局单例 ────────────────────────────────────────────────────────────

_tool_retrieval_service: Optional[ToolRetrievalService] = None
_init_lock = threading.Lock()

# ── 模块级自动预热（放 class 定义之后、模块加载时执行，`--reload` 场景也生效） ──
try:
    _warm_t0 = __import__('time').time()
    _ = ToolRetrievalService._get_local_embedder()
    logger.info(f"[ToolRetrieval] Module-level embedder pre-warmed in {__import__('time').time() - _warm_t0:.2f}s")
except Exception:
    pass


def get_tool_retrieval_service() -> ToolRetrievalService:
    global _tool_retrieval_service
    if _tool_retrieval_service is None:
        with _init_lock:
            if _tool_retrieval_service is None:
                _tool_retrieval_service = ToolRetrievalService()
    return _tool_retrieval_service


def init_tool_retrieval():
    service = get_tool_retrieval_service()
    service.ensure_collection()
    try:
        service.sync_all_tools()
        logger.info("[ToolRetrieval] Initialization complete")
    except Exception as e:
        logger.warning(f"[ToolRetrieval] Sync skipped (will retry on first use): {e}")
        # 不设置 _initialized = True，让 _lazy_init() 在首次使用时重试

    # ── P0: 启动时预加载 sentence-transformers 模型，避免首次用户请求时 7.5s 阻塞 ──
    try:
        import time as _time
        _t0 = _time.time()
        _ = service._get_local_embedder()
        _elapsed = _time.time() - _t0
        logger.info(f"[ToolRetrieval] Embedding model pre-warmed in {_elapsed:.2f}s")
    except Exception as e:
        logger.warning(f"[ToolRetrieval] Embedding model pre-warm failed (will lazy-load): {e}")

"""
# Long/Short-term Memory Service (v1.1)
# Industry-standard two-layer memory architecture:
#   - Short-term: Sliding window over conversation context
#   - Long-term: Vector DB (Qdrant) + Embedding (qwen3-embedding:4b)
#
# Architecture follows LangChain/OpenAI standards:
#   https://python.langchain.com/docs/modules/model_io/chat/message_types
#   https://python.langchain.com/docs/modules/model_io/chat/function_calling
#
# v1.1 fixes:
#   - All Qdrant sync ops wrapped with asyncio.to_thread / run_in_executor
#   - Added apply_short_term_window_dicts() for dict-based message lists
#   - extract_and_store_memories now accepts optional LLM for lightweight extraction
#   - Added async wrappers _async_similarity_search, _async_add_texts
"""

import json
import hashlib
import asyncio
import logging
import time
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
from collections import defaultdict

from langchain_community.embeddings import OllamaEmbeddings
from langchain_qdrant import Qdrant
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from cachetools import TTLCache

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ── 语义去重阈值 ───────────────────────────────────────────────────────────
# 两条记忆的 cosine similarity 超过此阈值则视为重复，跳过存储
DEDUP_SIMILARITY_THRESHOLD = 0.92

# ── 记忆注入 Token 预算 ──────────────────────────────────────────────────
MAX_MEMORY_COUNT = 3           # 最多注入 3 条记忆
MAX_MEMORY_LENGTH = 300        # 每条记忆最多 300 字符
MAX_MEMORY_TOTAL = 1000        # 总注入长度不超过 1000 字符

# qwen3-embedding:4b 的向量维度
EMBEDDING_DIM = 2560

# 集合名称前缀
LTM_COLLECTION_PREFIX = "user_memory"


@dataclass
class MemoryEntry:
    """A single memory entry with freshness tracking."""
    id: str
    user_id: str
    content: str
    summary: str = ""  # Extractive summary of the memory
    memory_type: str = "fact"  # "fact", "preference", "habit", "summary"
    created_at: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    last_accessed: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    access_count: int = 0
    freshness_score: float = 1.0  # 0.0 to 1.0, decays over time


def _ensure_memory_collection(client: QdrantClient, collection_name: str):
    """Ensure a Qdrant collection exists for memory storage."""
    try:
        client.get_collection(collection_name)
    except Exception:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )


class MemoryService:
    """
    Two-layer memory system:
    
    1. Short-term Memory (STM):
       - Sliding window of recent conversation turns
       - Must preserve the latest tool call chain (AIMessage + ToolMessage pairs)
       - Configurable window size (default: 10 turns)
       - Supports both LangChain message objects and raw dict lists
    
    2. Long-term Memory (LTM):
       - Qdrant vector DB with qwen3-embedding:4b embeddings
       - Stores user facts, preferences, and conversation summaries
       - Retrieval: top-k semantically similar memories with freshness re-ranking
       - v1.1: All Qdrant operations are async (run_in_executor)
    """

    DEFAULT_TOP_K = 5
    SHORT_TERM_WINDOW = 10  # 保留最近 10 轮对话

    def __init__(self):
        self._embedding_model = None
        # v7.0 fix: 使用 TTLCache 限制 Qdrant 客户端数量，1h 未使用自动释放
        self._qdrant_clients: TTLCache = TTLCache(maxsize=100, ttl=3600)

    def _get_qdrant_client(self) -> QdrantClient:
        """Get or create the shared QdrantClient instance."""
        cache_key = "default"
        if cache_key not in self._qdrant_clients:
            self._qdrant_clients[cache_key] = QdrantClient(
                url=settings.QDRANT_URL,
                timeout=60,
                prefer_grpc=True,
            )
        return self._qdrant_clients[cache_key]

    def _get_embedding(self) -> OllamaEmbeddings:
        """Get or create the Ollama embedding instance using qwen3-embedding:4b."""
        if self._embedding_model is None:
            ollama_base_url = settings.OLLAMA_HOST
            self._embedding_model = OllamaEmbeddings(
                model="qwen3-embedding:4b",
                base_url=ollama_base_url,
            )
            logger.info(f"[Memory] Initialized embedding model: qwen3-embedding:4b @ {ollama_base_url}")
        return self._embedding_model

    def _get_embedding_dim(self) -> int:
        """Get the embedding dimension for the current model."""
        return EMBEDDING_DIM

    def _get_qdrant_collection(self, user_id: str) -> Qdrant:
        """Get or create a Qdrant collection for the user's long-term memory."""
        client = self._get_qdrant_client()
        collection_name = f"{LTM_COLLECTION_PREFIX}_{user_id}"
        _ensure_memory_collection(client, collection_name)
        return Qdrant(
            client=client,
            collection_name=collection_name,
            embeddings=self._get_embedding(),
            distance_strategy="COSINE",
        )

    # ----------------------------------------------------------------
    # v1.1: Qdrant 异步包装器
    # ----------------------------------------------------------------

    async def _async_similarity_search(
        self, collection: Qdrant, query: str, k: int
    ) -> list:
        """
        异步执行 Qdrant similarity_search_with_relevance_scores。
        通过 run_in_executor 将同步操作移出事件循环。
        """
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None,
                lambda: collection.similarity_search_with_relevance_scores(query, k)
            )
        except Exception as e:
            logger.error(f"[Memory] Qdrant similarity search failed: {e}")
            return []

    async def _async_add_texts(
        self, collection: Qdrant, texts: list[str], metadatas: list[dict], ids: list[str]
    ) -> list[str]:
        """
        异步执行 Qdrant add_texts。
        通过 run_in_executor 将同步操作移出事件循环。
        """
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None,
                lambda: collection.add_texts(texts=texts, metadatas=metadatas, ids=ids)
            )
        except Exception as e:
            logger.error(f"[Memory] Qdrant add_texts failed: {e}")
            raise

    async def _async_delete_by_metadata(
        self, collection: Qdrant, filter_dict: dict
    ) -> None:
        """
        异步执行 Qdrant delete 操作，按元数据过滤。
        """
        loop = asyncio.get_event_loop()
        try:
            # Qdrant 不支持直接 delete by metadata via langchain wrapper,
            # use low-level client API
            client = self._get_qdrant_client()
            collection_name = collection.collection_name

            scroll_result = await loop.run_in_executor(
                None,
                lambda: client.scroll(
                    collection_name=collection_name,
                    scroll_filter={
                        "must": [
                            {"key": k, "match": {"value": v}}
                            for k, v in filter_dict.items()
                        ]
                    },
                    limit=100,
                    with_payload=False,
                )
            )
            points_to_delete = [p.id for p in scroll_result[0]]
            if points_to_delete:
                await loop.run_in_executor(
                    None,
                    lambda: client.delete(
                        collection_name=collection_name,
                        points_selector=points_to_delete,
                    )
                )
            logger.info(f"[Memory] Qdrant delete with filter {filter_dict} completed ({len(points_to_delete)} points)")
        except Exception as e:
            logger.error(f"[Memory] Qdrant delete failed with filter {filter_dict}: {e}")
            # 删除失败不抛出异常，只记录日志（非关键路径）

    # ----------------------------------------------------------------
    # Short-term Memory (滑动窗口)
    # ----------------------------------------------------------------

    @staticmethod
    def apply_short_term_window(
        messages: list,
        window_size: int = None,
    ) -> list:
        """
        Apply sliding window to LangChain conversation messages.
        
        Rules (industry standard):
        1. Always keep the SystemMessage (first message)
        2. Keep ALL complete tool call chains (AIMessage(tool_calls) + ToolMessage)
        3. Keep the last `window_size` turns of regular messages
        
        Args:
            messages: Full list of LangChain messages
            window_size: Number of turns to keep (default: SHORT_TERM_WINDOW)
            
        Returns:
            Truncated message list
        """
        if window_size is None:
            window_size = MemoryService.SHORT_TERM_WINDOW

        if len(messages) <= window_size + 1:  # +1 for system message
            return messages

        # Always keep system message
        system_msgs = [m for m in messages if hasattr(m, 'type') and m.type == 'system']
        non_system = [m for m in messages if not (hasattr(m, 'type') and m.type == 'system')]

        if not non_system:
            return messages

        # Find the last tool call chain to protect
        protected_indices = set()
        for i in range(len(non_system) - 1, -1, -1):
            msg = non_system[i]
            if hasattr(msg, 'type') and msg.type == 'ai':
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    # Protect this AIMessage and all following ToolMessages
                    j = i
                    while j < len(non_system):
                        protected_indices.add(j)
                        if hasattr(non_system[j], 'type') and non_system[j].type == 'tool':
                            j += 1
                        else:
                            break
                    break  # Only protect the most recent tool call chain

        # Keep the last window_size turns, plus protected messages
        if len(non_system) <= window_size:
            return system_msgs + non_system

        # Strategy: keep the last `window_size` messages, ensuring protected ones are included
        tail = non_system[-window_size:]

        # If not all protected messages are in tail, extend to include them
        tail_indices = set(range(len(non_system) - window_size, len(non_system)))
        missing_protected = protected_indices - tail_indices
        if missing_protected:
            # Expand window to include the earliest protected message
            earliest_protected = min(missing_protected)
            new_start = min(earliest_protected, len(non_system) - window_size)
            tail = non_system[new_start:]
            # If still too long, truncate from the front (protected messages are already inside)
            if len(tail) > window_size + len(protected_indices):
                tail = tail[-(window_size + len(protected_indices)):]

        logger.debug(f"[Memory] Short-term window: {len(system_msgs)} system + {len(tail)} messages "
                      f"(truncated from {len(non_system)})")
        return system_msgs + tail

    @staticmethod
    def apply_short_term_window_dicts(
        messages: list[dict],
        window_size: int = None,
    ) -> list[dict]:
        """
        Apply sliding window to dict-format conversation messages.
        
        Works with the dict format returned by _load_conversation_messages().
        Same protection rules as apply_short_term_window() but operates on
        dict messages without requiring LangChain imports.
        
        Args:
            messages: List of message dicts (keys: role, content, tool_calls, tool_call_id)
            window_size: Number of turns to keep (default: SHORT_TERM_WINDOW)
            
        Returns:
            Truncated message dict list
        """
        if window_size is None:
            window_size = MemoryService.SHORT_TERM_WINDOW

        if len(messages) <= window_size + 1:  # +1 for system message
            return messages

        # Always keep system message
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if not non_system:
            return messages

        # Find the last tool call chain to protect
        protected_indices = set()
        for i in range(len(non_system) - 1, -1, -1):
            msg = non_system[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                # Protect this assistant message and all following tool messages
                j = i
                while j < len(non_system):
                    protected_indices.add(j)
                    if non_system[j].get("role") == "tool":
                        j += 1
                    else:
                        break
                break  # Only protect the most recent tool call chain

        # Keep the last window_size turns, plus protected messages
        if len(non_system) <= window_size:
            return system_msgs + non_system

        # Strategy: keep the last `window_size` messages, ensuring protected ones are included
        tail = non_system[-window_size:]

        # If not all protected messages are in tail, extend to include them
        tail_indices = set(range(len(non_system) - window_size, len(non_system)))
        missing_protected = protected_indices - tail_indices
        if missing_protected:
            # Expand window to include the earliest protected message
            earliest_protected = min(missing_protected)
            new_start = min(earliest_protected, len(non_system) - window_size)
            tail = non_system[new_start:]
            # If still too long, truncate from the front (protected messages are already inside)
            if len(tail) > window_size + len(protected_indices):
                tail = tail[-(window_size + len(protected_indices)):]

        logger.debug(f"[Memory] Short-term window dicts: {len(system_msgs)} system + {len(tail)} messages "
                      f"(truncated from {len(non_system)})")
        return system_msgs + tail

    # ----------------------------------------------------------------
    # Long-term Memory (向量数据库 + Embedding) — v1.1 全部异步
    # ----------------------------------------------------------------

    async def store_memory(
        self,
        user_id: str,
        content: str,
        summary: str = "",
        memory_type: str = "fact",
        message_id: str = "",  # v1.2: 消息 ID，用于级联删除
    ) -> str:
        """
        Store a memory entry in long-term memory.
        
        Uses Qdrant + qwen3-embedding:4b for vector storage.
        v7.0: Added semantic dedup — if existing memory with similarity > 0.92
        is found, the new memory is skipped to prevent duplicate storage.
        v1.1: Qdrant operations are async (run_in_executor).
        v1.2: Added message_id parameter stored in metadata for cascade deletion.
        
        Args:
            user_id: User identifier
            content: Memory content text
            summary: Optional summary (defaults to first 200 chars of content)
            memory_type: Type of memory ("fact", "preference", "habit", "summary")
            message_id: Source message ID for cascade deletion support
        
        Returns:
            Memory ID if stored, empty string if skipped or failed
        """
        try:
            # ── v7.0: Semantic dedup check ──
            collection = self._get_qdrant_collection(user_id)
            # v1.1: 异步 Qdrant 查询
            existing = await self._async_similarity_search(collection, content, k=3)
            
            if existing:
                for _, score in existing:
                    if score >= DEDUP_SIMILARITY_THRESHOLD:
                        logger.info(
                            f"[Memory] Skipped duplicate memory for user {user_id}: "
                            f"similarity={score:.4f} >= threshold={DEDUP_SIMILARITY_THRESHOLD}"
                        )
                        return ""  # Skip duplicate
            
            memory_id = hashlib.md5(f"{user_id}_{content}_{datetime.now().timestamp()}".encode()).hexdigest()[:16]
            entry = MemoryEntry(
                id=memory_id,
                user_id=user_id,
                content=content,
                summary=summary or content[:200],
                memory_type=memory_type,
            )
            
            # v1.2: 注入 message_id 到元数据，用于删除时按消息 ID 级联清理
            # 如果 message_id 为空则跳过注入（兼容旧数据）
            file_message_id = message_id or ""
            
            metadata = {
                "user_id": user_id,
                "memory_id": memory_id,
                "memory_type": memory_type,
                "summary": entry.summary,
                "created_at": str(entry.created_at),
                "freshness_score": str(entry.freshness_score),
            }
            # 仅在提供 message_id 时注入，不污染旧数据
            if file_message_id:
                metadata["message_id"] = file_message_id
            
            # v1.1: 异步 Qdrant 写入
            await self._async_add_texts(
                collection,
                texts=[content],
                metadatas=[metadata],
                ids=[memory_id],
            )
            
            logger.info(f"[Memory] Stored {memory_type} memory for user {user_id}: {summary[:50]}...")
            return memory_id
        except Exception as e:
            logger.error(f"[Memory] Failed to store memory: {e}")
            return ""

    async def retrieve_memories(
        self,
        user_id: str,
        query: str,
        top_k: int = None,
        include_summaries: bool = True,
    ) -> list[dict]:
        """
        Retrieve top-k memories relevant to the query.
        
        Uses semantic search with freshness-based re-ranking:
        score = (1 - alpha) * similarity + alpha * freshness
        
        v1.1: Qdrant similarity search is async (run_in_executor).
        
        Args:
            user_id: User identifier
            query: Search query
            top_k: Number of results (default: DEFAULT_TOP_K)
            include_summaries: Whether to include summaries in results
            
        Returns:
            List of memory dicts with content, summary, type, score
        """
        if top_k is None:
            top_k = self.DEFAULT_TOP_K

        try:
            collection = self._get_qdrant_collection(user_id)
            # v1.1: 异步 Qdrant 查询
            results = await self._async_similarity_search(
                collection, query, k=top_k * 2
            )
            
            if not results:
                return []

            now = datetime.now(timezone.utc).timestamp()
            scored_memories = []
            
            for doc, similarity_score in results:
                metadata = doc.metadata or {}
                created_at_str = metadata.get("created_at", "0")
                try:
                    created_at = float(created_at_str)
                except (ValueError, TypeError):
                    created_at = 0
                
                # Freshness score: decays exponentially over 30 days
                age_days = (now - created_at) / 86400
                freshness = max(0.0, 1.0 - (age_days / 30.0))
                
                # Combined score: 70% semantic similarity + 30% freshness
                alpha = 0.3
                combined_score = (1 - alpha) * similarity_score + alpha * freshness
                
                scored_memories.append({
                    "content": doc.page_content,
                    "summary": metadata.get("summary", doc.page_content[:200]),
                    "memory_type": metadata.get("memory_type", "fact"),
                    "memory_id": metadata.get("memory_id", ""),
                    "similarity_score": round(similarity_score, 4),
                    "freshness": round(freshness, 4),
                    "combined_score": round(combined_score, 4),
                })
            
            # Sort by combined score and take top_k
            scored_memories.sort(key=lambda x: x["combined_score"], reverse=True)
            top_memories = scored_memories[:top_k]
            
            logger.info(f"[Memory] Retrieved {len(top_memories)} memories for user {user_id} (query: {query[:50]}...)")
            return top_memories
            
        except Exception as e:
            logger.error(f"[Memory] Failed to retrieve memories: {e}")
            return []

    def format_memories_for_context(self, memories: list[dict]) -> str:
        """
        Format retrieved memories into a context string for system prompt injection.
        
        v7.0 fix: 限制注入记忆条数和长度，防止 context overflow。
        最多 3 条、每条 300 字符、总长度 1000 字符。
        """
        if not memories:
            return ""
        
        parts = []
        total_len = 0
        # 按 combined_score 降序取前 MAX_MEMORY_COUNT 条
        sorted_memories = sorted(memories, key=lambda x: x.get("combined_score", 0), reverse=True)
        for mem in sorted_memories[:MAX_MEMORY_COUNT]:
            summary = mem.get("summary", "")[:MAX_MEMORY_LENGTH]
            if total_len + len(summary) > MAX_MEMORY_TOTAL:
                break
            parts.append(f"{len(parts)+1}. [{mem.get('memory_type', 'fact')}] {summary}")
            total_len += len(summary)
        
        if not parts:
            return ""
        
        return (
            "\n\n## 相关记忆（来自历史对话）\n"
            + "\n".join(parts)
            + "\n\n请参考以上信息回答用户问题。如果记忆中的信息与当前问题相关，请直接利用。"
        )

    async def extract_and_store_memories(
        self,
        user_id: str,
        conversation_history: list[dict],
        query: str,
        response: str,
        llm=None,  # v1.1: 可选的 LLM 实例用于轻量级记忆提取
        message_id: str = "",  # 消息 ID，用于删除时级联清理 Qdrant 中的对应记忆
    ) -> None:
        """
        After each conversation turn, extract important information and store as long-term memory.
        
        v1.1: 
          - 支持 LLM 轻量级提取：当传入 llm 参数时，用 LLM 将对话总结为一句事实
          - LLM 提取失败时回退到简单截取逻辑
          - 控制内部调用 Token 消耗（max_tokens=100, temperature=0.1）
        
        v1.2:
          - 新增 message_id 参数，存入 Qdrant 元数据
          - 用于消息删除时级联清理对应的 Qdrant 记忆
        """
        try:
            # ── v1.1: LLM 轻量级提取 ──
            if llm is not None and query.strip() and len(query) > 20:
                try:
                    summary_prompt = (
                        f"将以下对话总结为一句简洁的事实陈述（不超过50字）：\n"
                        f"用户：{query[:200]}\n"
                        f"助手：{response[:300]}\n"
                        f"事实："
                    )
                    summary_response = await llm.ainvoke(
                        [HumanMessage(content=summary_prompt)],
                        temperature=0.1,
                        max_tokens=100,
                    )
                    extracted = summary_response.content if hasattr(summary_response, 'content') else ""
                    if extracted and len(extracted.strip()) > 10:
                        await self.store_memory(
                            user_id=user_id,
                            content=extracted.strip()[:300],
                            summary=extracted.strip()[:100],
                            memory_type="fact",
                            message_id=message_id,  # v1.2: 注入消息 ID
                        )
                        logger.info(f"[Memory] LLM-extracted fact: {extracted.strip()[:80]}...")
                        return  # LLM 提取成功，跳过 fallback
                except Exception as llm_err:
                    logger.warning(f"[Memory] LLM extraction failed (non-blocking), using fallback: {llm_err}")

            # ── Fallback: 简单截取（与旧逻辑兼容） ──
            # Store the user's query as context
            if query.strip() and len(query) > 20:
                memory_text = f"用户询问: {query[:200]}"
                await self.store_memory(
                    user_id=user_id,
                    content=memory_text,
                    summary=f"用户问题: {query[:100]}",
                    memory_type="fact",
                    message_id=message_id,  # v1.2: 注入消息 ID
                )
            
            # Store key response points
            if response.strip() and len(response) > 50:
                summary = response[:200].replace('\n', ' ').strip()
                await self.store_memory(
                    user_id=user_id,
                    content=response[:500],
                    summary=f"对话摘要: {summary[:100]}",
                    memory_type="summary",
                    message_id=message_id,  # v1.2: 注入消息 ID
                )
        except Exception as e:
            logger.warning(f"[Memory] Failed to extract memories: {e}")

    # ----------------------------------------------------------------
    # 内置工具接口（供 Agent 调用）
    # ----------------------------------------------------------------

    # ----------------------------------------------------------------
    # v1.2: 级联删除 — 按消息 ID 清理 Qdrant 中的对应记忆
    # ----------------------------------------------------------------

    async def delete_memories_by_message_id(self, user_id: str, message_id: str) -> None:
        """
        根据消息 ID 删除 Qdrant 中对应的长期记忆。
        
        这是非关键路径操作——删除失败不会影响主流程，日志记录即可。
        
        Args:
            user_id: 用户 ID，用于获取对应的 Qdrant collection
            message_id: 消息 ID，用于匹配元数据中的 message_id 字段
        """
        if not user_id or not message_id:
            logger.warning(f"[Memory] delete_memories_by_message_id skipped: user_id={user_id}, message_id={message_id}")
            return
        
        try:
            collection = self._get_qdrant_collection(user_id)
            await self._async_delete_by_metadata(collection, {"message_id": message_id})
            logger.info(f"[Memory] Cascade delete triggered for message {message_id} (user {user_id})")
        except Exception as e:
            # 删除 Qdrant 记忆是非关键操作，失败仅记录日志
            logger.warning(f"[Memory] Failed to cascade delete memories for message {message_id}: {e}")

    # ----------------------------------------------------------------
    # 内置工具接口（供 Agent 调用）
    # ----------------------------------------------------------------

    def get_memory_tool_definition(self) -> dict:
        """
        返回 retrieve_memories 工具定义，供 Agent 主动调用。
        符合 OpenAI/LangChain 工具调用规范。
        """
        return {
            "type": "function",
            "function": {
                "name": "retrieve_memories",
                "description": "从长期记忆中检索与当前问题相关的历史信息。当你需要回忆用户的偏好、习惯或之前讨论过的重要事实时调用此工具。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索查询，描述你想查找的记忆内容"
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "返回结果数量（默认5条）",
                            "default": 5
                        }
                    },
                    "required": ["query"]
                }
            }
        }

    # ----------------------------------------------------------------
    # Task 3: 滑动窗口批处理 — 累积 N 轮后异步批量存储
    # ----------------------------------------------------------------
    BATCH_WINDOW_SIZE: int = 5
    BATCH_IDLE_TIMEOUT: float = 60.0
    _batch_buffers: dict = {}

    async def enqueue_extraction(
        self,
        user_id: str,
        conversation_history: list[dict],
        query: str,
        response: str,
        llm=None,
        message_id: str = "",
    ) -> None:
        """
        滑动窗口批处理入口：将一次对话回合加入用户缓冲区。
        当累积到 BATCH_WINDOW_SIZE 轮时，才触发实际落库。
        """
        if user_id not in self._batch_buffers:
            self._batch_buffers[user_id] = {
                "turns": [],
                "last_activity": time.time(),
            }
        buf = self._batch_buffers[user_id]
        buf["turns"].append({
            "conversation_history": conversation_history,
            "query": query,
            "response": response,
            "llm": llm,
            "message_id": message_id,
        })
        buf["last_activity"] = time.time()
        turn_count = len(buf["turns"])
        if turn_count >= self.BATCH_WINDOW_SIZE:
            logger.info(f"[Memory] Batch threshold ({turn_count}/{self.BATCH_WINDOW_SIZE}), flushing user {user_id}")
            await self._flush_user_buffer(user_id)
        else:
            logger.debug(f"[Memory] Buffered {turn_count}/{self.BATCH_WINDOW_SIZE} for {user_id}")

    async def _flush_user_buffer(self, user_id: str) -> None:
        """将用户缓冲区的所有对话回合批量执行 extract_and_store_memories。"""
        buf = self._batch_buffers.pop(user_id, None)
        if not buf or not buf["turns"]:
            return
        turns = buf["turns"]
        logger.info(f"[Memory] Batch flushing {len(turns)} turns for user {user_id}")
        for turn in turns:
            try:
                await self.extract_and_store_memories(
                    user_id=user_id,
                    conversation_history=turn["conversation_history"],
                    query=turn["query"],
                    response=turn["response"],
                    llm=turn["llm"],
                    message_id=turn["message_id"],
                )
            except Exception as e:
                logger.warning(f"[Memory] Batch turn failed (non-blocking): {e}")
        logger.info(f"[Memory] Batch flush done: {len(turns)} turns for {user_id}")

    async def flush_stale_buffers(self, max_age: float = None) -> int:
        """强制刷新所有超过空闲超时的用户缓冲区。"""
        if max_age is None:
            max_age = self.BATCH_IDLE_TIMEOUT
        now = time.time()
        stale_users = [
            uid for uid, buf in list(self._batch_buffers.items())
            if now - buf["last_activity"] >= max_age
        ]
        for uid in stale_users:
            logger.info(f"[Memory] Idle flush for {uid} (>{max_age}s)")
            await self._flush_user_buffer(uid)
        return len(stale_users)


# Singleton
memory_service = MemoryService()
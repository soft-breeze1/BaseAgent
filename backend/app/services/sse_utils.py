# SSE Utilities — OpenAI-compatible Server-Sent Events
# v8.0: 生产级 SSE 实现
#
# 格式标准: https://platform.openai.com/docs/api-reference/chat/streaming
#
# 支持事件类型:
#   - data: [DONE]           → 流结束标记
#   - data: {"choices":[...]} → OpenAI 兼容块
#   - :keepalive             → 心跳注释行（Nginx 防断开）
#   - event: error           → 错误事件
#
# 特性:
#   - 内置 15s 心跳，防止 Nginx/gateway 超时断开
#   - 自动 asyncio.create_task 心跳协程
#   - 严格 OpenAI chat.completion.chunk 格式
#   - 超时控制（timeout 参数）

import asyncio
import json
import logging
import time
import uuid
from typing import AsyncIterator, Optional, Callable, Awaitable

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------
# OpenAI 兼容 SSE 数据块格式
# ----------------------------------------------------------------

def _make_choices_delta(
    token: str,
    index: int = 0,
    finish_reason: Optional[str] = None,
    role: Optional[str] = None,
) -> list[dict]:
    """
    构造 OpenAI 格式的 choices delta。
    
    Args:
        token: 当前 token 文本
        index: choice 索引
        finish_reason: 结束原因 ("stop", "length", etc.)
        role: 角色 (仅在首个块中为 "assistant")
    
    Returns:
        choices 列表
    """
    delta: dict = {}
    if role:
        delta["role"] = role
    if token:
        delta["content"] = token
    
    choice: dict = {
        "index": index,
        "delta": delta,
    }
    if finish_reason:
        choice["finish_reason"] = finish_reason
    
    return [choice]


def make_chunk(
    token: str,
    conversation_id: str,
    index: int = 0,
    finish_reason: Optional[str] = None,
    role: Optional[str] = None,
) -> str:
    """
    构造 OpenAI 兼容的 SSE data 行。
    
    Args:
        token: 当前 token 文本
        conversation_id: 对话 ID（映射为 OpenAI id）
        index: choice 索引
        finish_reason: 结束原因
        role: 角色
    
    Returns:
        完整的 SSE 行 (含 "data: " 前缀和 "\n\n" 后缀)
    """
    chunk = {
        "id": f"chatcmpl-{conversation_id[:8]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "baseagent",
        "choices": _make_choices_delta(
            token=token,
            index=index,
            finish_reason=finish_reason,
            role=role,
        ),
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


# ----------------------------------------------------------------
# 心跳行（SSE 注释，Nginx 不断开）
# ----------------------------------------------------------------

def make_heartbeat() -> str:
    """生成心跳 SSE 注释行。"""
    return f": heartbeat {int(time.time())}\n\n"


# ----------------------------------------------------------------
# 错误事件（OpenAI 风格）
# ----------------------------------------------------------------

def make_error_chunk(
    code: str = "server_error",
    message: str = "Internal server error",
) -> str:
    """
    构造 OpenAI 风格的错误 SSE 事件。
    
    Returns:
        包含 {error} 的 SSE data 行
    """
    error_data = {
        "error": {
            "message": message,
            "type": code,
            "param": None,
            "code": code,
        }
    }
    return f"event: error\ndata: {json.dumps(error_data, ensure_ascii=False)}\n\n"


def make_done() -> str:
    """生成 OpenAI 流结束标记。"""
    return "data: [DONE]\n\n"


# ----------------------------------------------------------------
# 步骤/元数据等非 OpenAI 标准事件（BaseAgent 扩展）
# ----------------------------------------------------------------

def make_step_event(content: str) -> str:
    """生成步骤事件（BaseAgent 扩展，前端可用于显示思考过程）。"""
    payload = {
        "type": "step",
        "content": content,
    }
    return f"event: step\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def make_meta_event(route: str, sources: list) -> str:
    """生成元数据事件（BaseAgent 扩展，前端可用于显示来源）。"""
    payload = {
        "type": "meta",
        "route": route,
        "sources": sources,
    }
    return f"event: meta\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ----------------------------------------------------------------
# 心跳协程管理器
# ----------------------------------------------------------------

class HeartbeatManager:
    """
    管理 SSE 连接的心跳包。
    
    用法:
        async with HeartbeatManager(write_callback, interval=15):
            # 你的流式循环
            ...
        # 退出上下文时自动停止心跳
    """
    
    def __init__(self, write: Callable[[str], Awaitable[None]], interval: float = 15.0):
        """
        Args:
            write: 异步回调，接收一个 SSE 字符串并写入响应
            interval: 心跳间隔（秒）
        """
        self._write = write
        self._interval = interval
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
    
    async def _heartbeat_loop(self):
        """持续发送心跳直到收到停止信号。"""
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(self._interval)
                if not self._stop_event.is_set():
                    try:
                        await self._write(make_heartbeat())
                    except Exception:
                        # 连接可能已关闭，静默停止
                        break
        except asyncio.CancelledError:
            pass
    
    async def __aenter__(self):
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.debug(f"[SSE] Heartbeat started (interval={self._interval}s)")
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        logger.debug("[SSE] Heartbeat stopped")


# ----------------------------------------------------------------
# 高级 SSE 写入器（带缓冲、错误处理）
# ----------------------------------------------------------------

class SSEWriter:
    """
    高级 SSE 写入器，封装 async callable write 函数。
    
    提供:
      - send_chunk(token) → 发送 OpenAI 兼容 token
      - send_done()       → 发送 [DONE] 标记
      - send_error()      → 发送错误事件
      - send_step()       → 发送步骤事件
      - send_meta()       → 发送元数据事件
    """
    
    def __init__(self, write: Callable[[str], Awaitable[None]], conversation_id: str):
        """
        Args:
            write: 原始 write 回调
            conversation_id: 对话 ID
        """
        self._write = write
        self._conversation_id = conversation_id
        self._has_sent_first_token = False
    
    async def send_chunk(self, token: str, finish_reason: Optional[str] = None):
        """
        发送一个 token 块。
        
        第一个 token 会自动附带 role: "assistant"。
        """
        role = "assistant" if not self._has_sent_first_token else None
        self._has_sent_first_token = True
        await self._write(make_chunk(
            token=token,
            conversation_id=self._conversation_id,
            role=role,
            finish_reason=finish_reason,
        ))
    
    async def send_done(self):
        """发送流结束标记。"""
        await self._write(make_done())
    
    async def send_error(self, code: str = "server_error", message: str = "Internal server error"):
        """发送错误事件。"""
        await self._write(make_error_chunk(code=code, message=message))
    
    async def send_step(self, content: str):
        """发送步骤事件。"""
        await self._write(make_step_event(content))
    
    async def send_meta(self, route: str, sources: list):
        """发送元数据事件。"""
        await self._write(make_meta_event(route=route, sources=sources))
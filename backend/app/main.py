# BaseAgent FastAPI Application Entry Point
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.api import api_router
from app.core.config import get_settings
from app.core.database import init_db, engine
from app.core.redis import init_redis, close_redis
from app.core.mcp.process_manager import process_manager
from app.core.mcp.executor import MCPHttpExecutor

import logging
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # Startup
    await init_db()
    await init_redis()

    # v7.0: MCP 子进程通过 ProcessManager 按需启动（不再全局预加载）
    # 用户在 UI 中创建 MCP Server 记录后，通过 /mcp/servers/stdio 端点手动启动

    # SKILL.md 按需加载：Progressive Disclosure 通过 Tool Calling 机制
    # 在 ReAct 循环中由 ExecutionInterceptor 拦截 load_skill_* 调用时实时读取

    # v10.0: 注册系统级基础工具（文件系统 + 终端执行）
    try:
        from app.tools.system_tools import register_all as register_system_tools
        count = register_system_tools()
        if count > 0:
            print(f"[BaseAgent] 系统基础工具已注册: {count} 个")
    except Exception as e:
        print(f"[BaseAgent] 系统基础工具注册跳过 ({e})")

    # v10.5: 注册实用工具包（网页抓取、Python沙盒、文档读取、HTTP请求等）
    try:
        from app.tools.utility_tools import register_all as register_utility_tools
        count = register_utility_tools()
        if count > 0:
            print(f"[BaseAgent] 实用工具包已注册: {count} 个")
    except Exception as e:
        print(f"[BaseAgent] 实用工具包注册跳过 ({e})")

    # v18.0: 初始化工具语义召回索引（基于 Qdrant + bge-m3）
    try:
        from app.services.tool_retrieval import init_tool_retrieval
        init_tool_retrieval()
        print("[BaseAgent] 工具语义召回索引已初始化")
    except Exception as e:
        print(f"[BaseAgent] 工具语义召回索引初始化跳过 ({e})")

    print(f"[BaseAgent] {settings.APP_NAME} v{settings.APP_VERSION} started")
    yield
    # Shutdown
    # ★ v7.0: 使用 ProcessManager 优雅关闭所有 MCP 子进程
    try:
        await process_manager.stop_all_processes()
        print("[BaseAgent] MCP: All stdio servers shut down")
    except Exception as e:
        print(f"[BaseAgent] MCP: Shutdown error (non-blocking): {e}")

    await close_redis()
    await engine.dispose()
    print("[BaseAgent] Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="全栈大模型知识库与智能体平台",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# v8.0: SSE 专用响应头中间件 — 将所有 /chat/stream 响应标记为 no-cache
@app.middleware("http")
async def sse_headers_middleware(request: Request, call_next):
    """
    为 SSE 端点添加/强化必要响应头。
    
    包括:
      - text/event-stream 的 Content-Type（已在 StreamingResponse 中设置）
      - 确保 Nginx/Kong 等网关不缓冲 SSE 流
    """
    response = await call_next(request)
    
    # 为 SSE 端点强化缓存和缓冲控制
    if request.url.path.endswith("/chat/stream"):
        response.headers["Cache-Control"] = "no-cache, no-store, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        # 对于不支持 StreamingResponse 头覆盖的代理，二次确保
        if "X-Accel-Buffering" not in response.headers:
            response.headers["X-Accel-Buffering"] = "no"
    
    return response

# 先挂载静态文件目录，再注册API路由（StaticFiles需在路由之前）
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# Include API router
app.include_router(api_router)


@app.get("/")
async def root():
    return {"name": settings.APP_NAME, "version": settings.APP_VERSION, "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok"}
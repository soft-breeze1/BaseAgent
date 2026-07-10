# MCP Extension Endpoints — v3.0 (DB-backed, MCP 1.0 Protocol)
import json
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.core.database import get_db
from app.models.user import User
from app.models.mcp_server import MCPServer
from app.schemas.mcp import (
    MCPServerCreate,
    MCPServerUpdate,
    MCPServerOut,
    MCPServerTestRequest,
    MCPServerTestResult,
)
from app.services.auth_deps import get_current_user
from app.core.mcp.discovery import (
    discover_mcp_tools,
    clear_session_cache,
    clear_all_caches,
    clear_user_caches,
)
from app.core.mcp.executor import MCPHttpExecutor, execute_mcp_tool
from app.core.mcp.process_manager import process_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["MCP 扩展"])


# ==========================================================================
# DB-Backed CRUD Endpoints
# ==========================================================================


@router.get("/servers", response_model=list[MCPServerOut])
async def list_mcp_servers(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    列出当前用户所有已配置的 MCP Server。
    包含 HTTP 和 Stdio 两种模式。
    """
    result = await db.execute(
        select(MCPServer)
        .where(MCPServer.user_id == current_user.id)
        .order_by(desc(MCPServer.updated_at))
    )
    servers = result.scalars().all()

    # 合并运行时状态
    output = []
    for s in servers:
        server_dict = _server_to_dict(s)
        # 尝试从进程管理器获取实时状态
        if s.type == "stdio":
            proc_info = process_manager.get_process_info(s.name)
            if proc_info:
                server_dict["status"] = proc_info["status"]
                server_dict["tool_count_display"] = proc_info["tool_count"]
        output.append(MCPServerOut(**server_dict))

    return output


@router.post("/servers", response_model=MCPServerOut, status_code=201)
async def create_mcp_server(
    req: MCPServerCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    添加一个新的 MCP Server 配置并尝试连接。
    连接成功后状态设为 "connected"，失败则设为 "error"。
    """
    # 检查是否已存在同名 server
    existing = await db.execute(
        select(MCPServer).where(
            MCPServer.user_id == current_user.id,
            MCPServer.name == req.name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"MCP Server '{req.name}' already exists")

    # 创建数据库记录
    server_id = str(uuid.uuid4())
    config_json = json.dumps(req.config, ensure_ascii=False)

    server = MCPServer(
        id=server_id,
        name=req.name,
        type=req.type,
        config=config_json,
        status="connecting",  # 初始状态
        user_id=current_user.id,
    )
    db.add(server)

    # 测试连接
    test_result = await _test_connection(req.type, req.config)

    if test_result.success:
        server.status = "connected"
    else:
        server.status = "error"

    await db.commit()
    await db.refresh(server)

    # v7.0 fix: 清除该用户的 MCP 缓存，确保新 Server 的工具立即可见
    clear_user_caches(str(current_user.id))

    return MCPServerOut(**_server_to_dict(server))


@router.get("/servers/{server_id}", response_model=MCPServerOut)
async def get_mcp_server(
    server_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取单个 MCP Server 的详细信息。"""
    server = await _get_server_or_404(server_id, current_user.id, db)
    server_dict = _server_to_dict(server)

    # 合并运行时状态
    if server.type == "stdio":
        proc_info = process_manager.get_process_info(server.name)
        if proc_info:
            server_dict["status"] = proc_info["status"]

    return MCPServerOut(**server_dict)


@router.put("/servers/{server_id}", response_model=MCPServerOut)
async def update_mcp_server(
    server_id: str,
    req: MCPServerUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    更新 MCP Server 配置。
    如果更新了 config，会自动重新测试连接。
    """
    server = await _get_server_or_404(server_id, current_user.id, db)

    if req.name is not None:
        server.name = req.name
    if req.type is not None:
        server.type = req.type
    if req.config is not None:
        server.config = json.dumps(req.config, ensure_ascii=False)
        # 配置变更后重新测试连接
        server_type = req.type or server.type
        test_result = await _test_connection(server_type, req.config)
        server.status = "connected" if test_result.success else "error"
    if req.status is not None:
        server.status = req.status

    await db.commit()
    await db.refresh(server)

    # v7.0 fix: 配置变更后清除缓存
    clear_user_caches(str(current_user.id))

    return MCPServerOut(**_server_to_dict(server))


@router.delete("/servers/{server_id}")
async def delete_mcp_server(
    server_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    删除 MCP Server 配置。
    如果该 Server 有运行中的 Stdio 子进程，会先终止进程。
    """
    server = await _get_server_or_404(server_id, current_user.id, db)
    server_name = server.name

    # 如果存在运行中的 Stdio 进程，先停止
    if server.type == "stdio" and process_manager.is_running(server_name):
        await process_manager.stop_process(server_name)

    await db.delete(server)
    await db.commit()

    # v7.0 fix: 删除后清除缓存
    clear_user_caches(str(current_user.id))

    return {"message": f"MCP Server '{server_name}' deleted"}


# ==========================================================================
# Connection Testing
# ==========================================================================


@router.post("/servers/test", response_model=MCPServerTestResult)
async def test_mcp_connection(
    req: MCPServerTestRequest,
    current_user: User = Depends(get_current_user),
):
    """
    测试 MCP Server 连接。
    不保存配置，仅用于验证连接是否正常。

    支持两种模式：
      - stdio: 测试本地子进程启动和工具发现
      - http: 测试远端 HTTP 服务的工具发现
    """
    return await _test_connection(req.type, req.config)


# ==========================================================================
# Stdio Process Management (保留现有接口，改为代理到 ProcessManager)
# ==========================================================================


@router.post("/servers/stdio", response_model=MCPServerOut)
async def connect_stdio_mcp_server(
    server_id: str = Query(..., description="数据库中的 MCP Server ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    启动指定 MCP Server 的 Stdio 子进程。
    先检查数据库配置，然后通过 ProcessManager 启动进程。
    """
    server = await _get_server_or_404(server_id, current_user.id, db)
    if server.type != "stdio":
        raise HTTPException(status_code=400, detail="Server is not a Stdio type")

    config = json.loads(server.config) if server.config else {}

    info = await process_manager.start_process(
        server_name=server.name,
        command=config.get("command", ""),
        args=config.get("args", []),
        env=config.get("env", {}),
    )

    server.status = info.status.value
    await db.commit()
    await db.refresh(server)

    return MCPServerOut(**_server_to_dict(server))


@router.delete("/servers/stdio/{server_name}")
async def disconnect_stdio_mcp_server(
    server_name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    停止 Stdio 子进程并更新数据库状态。
    """
    # 停止进程
    success = await process_manager.stop_process(server_name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Process '{server_name}' not found")

    # 更新数据库状态
    result = await db.execute(
        select(MCPServer).where(
            MCPServer.user_id == current_user.id,
            MCPServer.name == server_name,
        )
    )
    server = result.scalar_one_or_none()
    if server:
        server.status = "disconnected"
        await db.commit()

    return {"message": f"MCP stdio server '{server_name}' disconnected"}


@router.post("/load-config")
async def load_mcp_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    从 JSON 配置文件加载 MCP Server 配置到数据库。
    兼容 Cline 的 cline_mcp_settings.json 格式。
    """
    import os
    config_path = os.getenv("MCP_CONFIG_PATH", "/app/data/mcp_servers.json")

    if not os.path.exists(config_path):
        # 回退到 data 目录下的 mcp_servers.json
        config_path = "/app/data/mcp_servers.json"
        if not os.path.exists(config_path):
            raise HTTPException(status_code=404, detail=f"MCP config file not found")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        raise HTTPException(status_code=400, detail=f"Failed to load config: {e}")

    servers_raw = raw.get("mcpServers", raw)
    if not isinstance(servers_raw, dict):
        raise HTTPException(status_code=400, detail="Invalid config format")

    added = 0
    for server_name, server_cfg in servers_raw.items():
        if not isinstance(server_cfg, dict):
            continue
        if server_cfg.get("disabled", False):
            continue

        # 检查是否已存在
        existing = await db.execute(
            select(MCPServer).where(
                MCPServer.user_id == current_user.id,
                MCPServer.name == server_name,
            )
        )
        if existing.scalar_one_or_none():
            continue

        config = {
            "command": server_cfg.get("command", ""),
            "args": server_cfg.get("args", []),
            "env": server_cfg.get("env", {}),
        }
        if not config["command"]:
            continue

        server = MCPServer(
            id=str(uuid.uuid4()),
            name=server_name,
            type="stdio",
            config=json.dumps(config, ensure_ascii=False),
            status="connected",
            user_id=current_user.id,
        )
        db.add(server)
        added += 1

    if added > 0:
        await db.commit()

    return {"message": f"Loaded config: {added} servers added to database"}


# ==========================================================================
# Cache Management
# ==========================================================================


@router.post("/cache/clear")
async def clear_mcp_cache(
    session_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    """
    清除 MCP 工具发现缓存。
    如果指定 session_id，只清除该对话的缓存。
    否则清除所有缓存。
    """
    if session_id:
        clear_session_cache(session_id)
        return {"message": f"Cache cleared for session '{session_id}'"}
    else:
        clear_all_caches()
        return {"message": "All MCP caches cleared"}


# ==========================================================================
# Internal Helpers
# ==========================================================================


async def _get_server_or_404(
    server_id: str,
    user_id: str,
    db: AsyncSession,
) -> MCPServer:
    """获取 MCP Server，不存在则返回 404。"""
    result = await db.execute(
        select(MCPServer).where(
            MCPServer.id == server_id,
            MCPServer.user_id == user_id,
        )
    )
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP Server not found")
    return server


async def _test_connection(
    server_type: str,
    config: dict,
) -> MCPServerTestResult:
    """
    测试 MCP Server 连接并发现工具。

    Args:
        server_type: "http" 或 "stdio"
        config: 配置字典

    Returns:
        测试结果
    """
    tools = []

    try:
        if server_type == "http":
            url = config.get("url", "")
            if not url:
                return MCPServerTestResult(
                    success=False,
                    message="HTTP mode requires 'url' in config",
                )
            executor = MCPHttpExecutor(base_url=url)
            tools = await executor.list_tools()

        elif server_type == "stdio":
            command = config.get("command", "")
            if not command:
                return MCPServerTestResult(
                    success=False,
                    message="Stdio mode requires 'command' in config",
                )
            info = await process_manager.start_process(
                server_name="__test__",
                command=command,
                args=config.get("args", []),
                env=config.get("env", {}),
            )
            if info.status == "running":
                executor = process_manager.get_executor("__test__")
                if executor:
                    tools = await executor.list_tools()
                # 停止测试进程
                await process_manager.stop_process("__test__")
            else:
                return MCPServerTestResult(
                    success=False,
                    message=f"Failed to start: {info.last_error or 'unknown error'}",
                )
        else:
            return MCPServerTestResult(
                success=False,
                message=f"Unknown server type: {server_type}",
            )

        if tools:
            return MCPServerTestResult(
                success=True,
                message=f"Connection successful, discovered {len(tools)} tools",
                tool_count=len(tools),
                tools=tools,
            )
        else:
            return MCPServerTestResult(
                success=True,
                message="Connection successful, but no tools discovered",
                tool_count=0,
            )

    except Exception as e:
        logger.error(f"[MCP Test] Connection test failed: {e}")
        return MCPServerTestResult(
            success=False,
            message=f"Connection failed: {str(e)}",
        )


def _server_to_dict(server: MCPServer) -> dict:
    """将 MCPServer ORM 对象转为字典。"""
    config = None
    if server.config:
        try:
            config = json.loads(server.config)
        except (json.JSONDecodeError, TypeError):
            config = server.config

    return {
        "id": server.id,
        "name": server.name,
        "type": server.type,
        "config": config,
        "status": server.status,
        "user_id": server.user_id,
        "created_at": server.created_at,
        "updated_at": server.updated_at,
    }
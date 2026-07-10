"""
MCP 工具发现模块
================
每次新建对话时执行一次，发现用户所有已连接且状态正常的 MCP Server 的工具。

核心功能：
  1. 从数据库查询当前用户所有已连接且状态为"正常"的 MCP Server
  2. 对每个 MCP Server，按照 MCP 1.0 协议调用 list_tools 方法获取工具定义
  3. 将 MCP 工具定义转换为与现有静态工具完全相同的格式
  4. 将转换后的 MCP 工具列表与静态工具列表合并，形成完整的可用工具列表

v7.0 变更：
  - 使用 cachetools.TTLCache 替代无限增长的 dict
  - 对话级别缓存：maxsize=1000, ttl=300s
  - 路由缓存：maxsize=1000, ttl=300s
  - 服务器工具缓存：maxsize=100, ttl=300s
"""
import asyncio
import json
import logging
import time
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from cachetools import TTLCache

from app.models.mcp_server import MCPServer
from app.core.mcp.protocol import mcp_tools_to_unified_format
from app.core.mcp.executor import MCPHttpExecutor, cache_tool_definitions
from app.core.mcp.process_manager import process_manager, ProcessStatus

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────

# v7.0: 使用 TTLCache 替代无限增长的 dict
# 对话级别缓存：session_id -> list[dict]
_discovery_cache: TTLCache = TTLCache(maxsize=1000, ttl=300)

# 会话级别路由缓存：tool_name -> {server_name, raw_tool_name, executor_type}
_tool_route_cache: TTLCache = TTLCache(maxsize=1000, ttl=300)

# 服务器级别工具缓存：server_name -> list[tool_dict]
_server_tool_cache: TTLCache = TTLCache(maxsize=100, ttl=300)

# 单服务器发现超时（秒）
SERVER_DISCOVERY_TIMEOUT = 10.0

# 服务器工具缓存 TTL（秒）
SERVER_TOOL_CACHE_TTL = 300  # 5 分钟


async def discover_mcp_tools(
    user_id: str,
    db: AsyncSession,
    session_id: Optional[str] = None,
    force_refresh: bool = False,
) -> list[dict]:
    """
    发现指定用户的所有可用 MCP 工具。

    执行流程：
      1. 如果提供了 session_id，优先从缓存返回
      2. 从数据库查询用户已连接且状态正常的 MCP Server
      3. 对 Stdio 模式 Server：从 ProcessManager 获取工具定义
      4. 对 HTTP 模式 Server：通过 HTTP 调用 tools/list 发现工具
      5. 所有工具的发现过程都有超时控制（默认 10 秒/Server）
      6. 单个 Server 失败不影响其他 Server
      7. 将所有工具转换为统一格式（OpenAI tool schema）
      8. 缓存结果（按 session_id）

    Args:
        user_id: 用户 ID（用于权限隔离）
        db: 数据库会话
        session_id: 当前对话 ID（用于缓存）
        force_refresh: 是否强制刷新缓存

    Returns:
        OpenAI-compatible tool schema 列表，
        可直接与静态工具列表合并后传给 LLM。
    """
    start_time = time.time()

    # 缓存检查
    if session_id and session_id in _discovery_cache and not force_refresh:
        cached = _discovery_cache[session_id]
        logger.info(
            f"[MCP Discovery] Returning cached tools for session '{session_id}': "
            f"{len(cached)} tools (hit rate 100%)"
        )
        return cached

    # 从数据库查询用户的 MCP Server
    servers = await _get_user_mcp_servers(user_id, db)
    if not servers:
        logger.info(
            f"[MCP Discovery] No MCP servers found for user '{user_id}' "
            f"(took {time.time() - start_time:.2f}s)"
        )
        if session_id:
            _discovery_cache[session_id] = []
        return []

    all_mcp_tools: list[dict] = []
    route_info: dict[str, dict] = {}
    success_count = 0
    fail_count = 0

    logger.info(
        f"[MCP Discovery] Starting discovery for user '{user_id}': "
        f"{len(servers)} server(s) to check"
    )

    for server in servers:
        server_name = server.name
        server_type = server.type

        try:
            # 解析 config
            config = _parse_config(server.config)

            # 增量更新检查：如果 Server 的工具已缓存，直接使用缓存
            if not force_refresh and server_name in _server_tool_cache:
                cached_tools = _server_tool_cache[server_name]
                logger.info(
                    f"[MCP Discovery] Using cached tools for server '{server_name}' "
                    f"({len(cached_tools)} tools)"
                )
                if cached_tools:
                    unified = mcp_tools_to_unified_format(cached_tools, server_name)
                    all_mcp_tools.extend(unified)
                    _update_route_info(route_info, cached_tools, server_name, server_type)
                success_count += 1
                continue

            # 带超时的工具发现
            if server_type == "stdio":
                try:
                    tools = await asyncio.wait_for(
                        _discover_stdio_tools(server_name=server_name, config=config),
                        timeout=SERVER_DISCOVERY_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[MCP Discovery] Stdio server '{server_name}' discovery timed out "
                        f"(>{SERVER_DISCOVERY_TIMEOUT}s), skipping"
                    )
                    fail_count += 1
                    continue

            elif server_type == "http":
                try:
                    tools = await asyncio.wait_for(
                        _discover_http_tools(server_name=server_name, config=config),
                        timeout=SERVER_DISCOVERY_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[MCP Discovery] HTTP server '{server_name}' discovery timed out "
                        f"(>{SERVER_DISCOVERY_TIMEOUT}s), skipping"
                    )
                    fail_count += 1
                    continue
            else:
                logger.warning(
                    f"[MCP Discovery] Unknown server type '{server_type}' "
                    f"for '{server_name}', skipping"
                )
                continue

            # 缓存该 Server 的工具定义（用于增量更新）
            _server_tool_cache[server_name] = tools

            if tools:
                # 缓存工具定义到 executor 层的缓存（用于参数验证）
                cache_tool_definitions(server_name, tools)

                # 转换为统一格式
                unified = mcp_tools_to_unified_format(tools, server_name)
                all_mcp_tools.extend(unified)

                # 更新路由缓存
                _update_route_info(route_info, tools, server_name, server_type)

                logger.info(
                    f"[MCP Discovery] Server '{server_name}' ({server_type}): "
                    f"discovered {len(tools)} tools, "
                    f"converted to {len(unified)} unified tool(s)"
                )
                success_count += 1
            else:
                logger.warning(
                    f"[MCP Discovery] Server '{server_name}' ({server_type}): "
                    f"no tools discovered"
                )
                fail_count += 1

        except Exception as e:
            logger.error(
                f"[MCP Discovery] Failed to discover tools from "
                f"server '{server_name}': {e}", exc_info=True
            )
            fail_count += 1
            continue

    # 更新全局路由缓存
    _tool_route_cache.update(route_info)

    # 缓存结果
    if session_id:
        _discovery_cache[session_id] = all_mcp_tools

    elapsed = time.time() - start_time
    logger.info(
        f"[MCP Discovery] Completed for user '{user_id}': "
        f"{len(all_mcp_tools)} tools from {success_count + fail_count} servers "
        f"({success_count} success, {fail_count} failed, took {elapsed:.2f}s)"
    )

    return all_mcp_tools


def _update_route_info(
    route_info: dict,
    tools: list[dict],
    server_name: str,
    server_type: str,
):
    """
    更新路由信息缓存。

    Args:
        route_info: 路由信息字典（会被修改）
        tools: 工具定义列表
        server_name: 服务器名称
        server_type: 服务器类型（http/stdio）
    """
    for t in tools:
        raw_name = t.get("name", "")
        if raw_name:
            unique_name = f"mcp_{server_name}_{raw_name}"
            route_info[unique_name] = {
                "server_name": server_name,
                "raw_tool_name": raw_name,
                "executor_type": server_type,
            }


async def _get_user_mcp_servers(
    user_id: str,
    db: AsyncSession,
) -> list:
    """
    从数据库查询用户已连接且状态正常的 MCP Server。

    Args:
        user_id: 用户 ID
        db: 数据库会话

    Returns:
        MCPServer ORM 对象列表
    """
    try:
        start_time = time.time()
        result = await db.execute(
            select(MCPServer).where(
                MCPServer.user_id == user_id,
                MCPServer.status == "connected",
            )
        )
        servers = result.scalars().all()
        logger.debug(
            f"[MCP Discovery] Queried {len(servers)} servers for user '{user_id}' "
            f"(took {time.time() - start_time:.3f}s)"
        )
        return servers
    except Exception as e:
        logger.error(f"[MCP Discovery] Database query failed for user '{user_id}': {e}")
        return []


async def _discover_stdio_tools(
    server_name: str,
    config: dict,
) -> list[dict]:
    """
    通过 Stdio 模式发现 MCP 工具。

    如果子进程尚未启动，则先启动进程再发现工具。
    如果子进程已运行，直接从已建立的 session 获取。
    
    警告：在全 Docker 部署下，stdio 模式会直接在主 Agent 容器内拉起子进程，
    导致镜像臃肿且破坏容器隔离性。建议将 MCP Server 作为独立的 Sidecar 容器
    通过 HTTP/SSE 模式接入。设置环境变量 MCP_STDIO_ENABLED=false 可禁用。

    Args:
        server_name: 服务器名称
        config: 配置字典（包含 command, args, env 等）

    Returns:
        工具定义列表
    """
    discover_start = time.time()

    # ── Docker 环境 stdio 降级警告 ──
    import os
    _mcp_stdio_enabled = os.getenv("MCP_STDIO_ENABLED", "true").lower() in ("true", "1", "yes")
    if not _mcp_stdio_enabled:
        logger.warning(
            f"[MCP Discovery] Skipping stdio server '{server_name}' — "
            f"MCP_STDIO_ENABLED is false. Use HTTP/SSE Sidecar mode in Docker."
        )
        return []

    # 检查进程管理器是否已有运行中的会话
    if process_manager.is_running(server_name):
        logger.info(
            f"[MCP Discovery] Reusing existing process for '{server_name}'"
        )
        tools = await process_manager.discover_tools(server_name)
        elapsed = time.time() - discover_start
        logger.info(
            f"[MCP Discovery] Stdio '{server_name}' tools discovered from existing session "
            f"({len(tools)} tools, took {elapsed:.2f}s)"
        )
        return tools

    # 启动新进程
    command = config.get("command", "")
    if not command:
        logger.warning(
            f"[MCP Discovery] Stdio server '{server_name}' has no command configured"
        )
        return []

    args = config.get("args", [])
    env = config.get("env", {})

    info = await process_manager.start_process(
        server_name=server_name,
        command=command,
        args=args,
        env=env,
    )

    if info.status == ProcessStatus.RUNNING:
        tools = await process_manager.discover_tools(server_name)
        elapsed = time.time() - discover_start
        logger.info(
            f"[MCP Discovery] Stdio '{server_name}' started and discovered "
            f"({len(tools)} tools, took {elapsed:.2f}s)"
        )
        return tools
    else:
        logger.error(
            f"[MCP Discovery] Failed to start Stdio server '{server_name}': "
            f"{info.last_error}"
        )
        return []


async def _discover_http_tools(
    server_name: str,
    config: dict,
) -> list[dict]:
    """
    通过 HTTP 模式发现 MCP 工具。

    Args:
        server_name: 服务器名称
        config: 配置字典（包含 url）

    Returns:
        工具定义列表
    """
    url = config.get("url", "")
    if not url:
        logger.warning(
            f"[MCP Discovery] HTTP server '{server_name}' has no URL configured"
        )
        return []

    executor = MCPHttpExecutor(base_url=url)
    executor._server_name = server_name
    return await executor.list_tools()


# ── 工具路由 ──────────────────────────────────────────────────────────────


def get_tool_route(tool_name: str) -> Optional[dict]:
    """
    根据统一工具名获取路由信息。

    Args:
        tool_name: 统一工具名（格式：mcp_{server}_{tool}）

    Returns:
        {
            "server_name": str,
            "raw_tool_name": str,
            "executor_type": str  # "http" 或 "stdio"
        }
        如果未找到，返回 None。
    """
    return _tool_route_cache.get(tool_name)


def is_mcp_tool(tool_name: str) -> bool:
    """
    判断一个工具名是否是 MCP 工具。

    Args:
        tool_name: 工具名

    Returns:
        如果是 MCP 工具（以 mcp_ 开头）返回 True，否则返回 False
    """
    return tool_name.startswith("mcp_")


# ── 缓存管理 ──────────────────────────────────────────────────────────────


def clear_session_cache(session_id: str):
    """
    清除指定对话的 MCP 工具发现缓存。

    在对话结束时调用，释放内存。

    Args:
        session_id: 对话 ID
    """
    _discovery_cache.pop(session_id, None)
    logger.info(f"[MCP Discovery] Cleared discovery cache for session '{session_id}'")


def clear_all_caches():
    """清除所有 MCP 工具发现缓存（用于测试或重置）。"""
    _discovery_cache.clear()
    _tool_route_cache.clear()
    _server_tool_cache.clear()
    logger.info("[MCP Discovery] All caches cleared (discovery, route, server)")


def clear_user_caches(user_id: str):
    """
    清除指定用户的所有缓存。
    当用户添加/删除/修改 MCP Server 时自动调用。

    Args:
        user_id: 用户 ID
    """
    removed_sessions = []
    for session_id, tools in list(_discovery_cache.items()):
        if user_id in session_id:
            _discovery_cache.pop(session_id, None)
            removed_sessions.append(session_id)

    removed_servers = []
    for server_name in list(_server_tool_cache.keys()):
        _server_tool_cache.pop(server_name, None)
        removed_servers.append(server_name)

    removed_routes = []
    for tool_name in list(_tool_route_cache.keys()):
        route = _tool_route_cache.get(tool_name)
        if route and route.get("server_name") in removed_servers:
            _tool_route_cache.pop(tool_name, None)
            removed_routes.append(tool_name)

    from app.core.mcp.executor import clear_tool_def_cache
    for server_name in removed_servers:
        clear_tool_def_cache(server_name)

    logger.info(
        f"[MCP Discovery] Cleared caches for user '{user_id}': "
        f"{len(removed_sessions)} session(s), "
        f"{len(removed_servers)} server(s), "
        f"{len(removed_routes)} route(s)"
    )


# ── 辅助函数 ──────────────────────────────────────────────────────────────


def _parse_config(config_str: Optional[str]) -> dict:
    """
    解析数据库中的 config JSON 字符串。

    Args:
        config_str: JSON 字符串或 None

    Returns:
        配置字典
    """
    if not config_str:
        return {}
    try:
        return json.loads(config_str)
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"[MCP Discovery] Failed to parse config JSON: {config_str[:100]}")
        return {}
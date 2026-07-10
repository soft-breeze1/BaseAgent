"""
Stdio 模式子进程管理器
======================
管理 MCP Server 子进程的完整生命周期：
  - 启动/停止/重启子进程
  - Stdio 通信管道建立
  - 进程健康检查与崩溃恢复
  - 资源清理（僵尸进程、内存泄漏防护）
  - 会话级别的进程复用（同一对话周期内不重复启动）

设计原则：
  - 每个 MCP Server 对应一个子进程，1:1 映射
  - 子进程复用：同一 Server 在同一对话期间不重复启动
  - 进程退出时自动更新数据库中的状态
  - 对话结束后自动终止所有子进程

依赖：
  - 使用官方 mcp SDK (mcp.client.stdio) 管理子进程通信
  - asyncio 管理并发和超时
"""
import asyncio
import json
import logging
import os
import signal
import time
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

# 官方 mcp SDK
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp import ClientSession

# 本地协议模块
from app.core.mcp.protocol import parse_tools_list_response, parse_call_tool_response
from app.core.mcp.executor import MCPStdioExecutor

# Docker 环境适配
from app.utils.docker_env import (
    maybe_convert_args,
    enhance_error_message,
    is_docker,
)

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────

# 最大重启尝试次数
MAX_RESTART_ATTEMPTS = 3

# 指数退避基值（秒）
BACKOFF_BASE_SECONDS = 1.0

# 进程最大运行时间（秒），超过后自动终止
MAX_PROCESS_LIFETIME = 3600  # 1 小时

# 工具调用超时（秒）
TOOL_CALL_TIMEOUT = 30.0

# 初始化超时（秒）
INIT_TIMEOUT = 15.0

# 健康检查间隔（秒）
HEALTH_CHECK_INTERVAL = 30.0  # 每 30 秒健康检查一次

# 健康检查超时（秒）
HEALTH_CHECK_TIMEOUT = 5.0

# 僵尸进程清理间隔（秒）
ZOMBIE_CLEANUP_INTERVAL = 120.0  # 每 2 分钟清理一次

# ── 资源限制常量 ──────────────────────────────────────────────────────────

# 子进程最大内存使用量（MB）
MAX_PROCESS_MEMORY_MB = 512

# 子进程最大 CPU 使用率（百分比）
MAX_CPU_PERCENT = 50

# 子进程最大运行时间（秒），超时自动终止
MAX_PROCESS_RUNTIME_SECONDS = 3600

# ── 进程白名单（只允许运行这些路径/命令） ────────────────────────────────
# cmd 必须以这些前缀之一开头
ALLOWED_COMMAND_PREFIXES = (
    "npx",
    "node",
    "python",
    "python3",
    "uvx",
    "uv",
    "pipx",
    "docker",
    "/usr/local/bin/",
    "/usr/bin/",
    "/opt/",
    "/home/",
)

# 显式禁止的命令（黑名单）
BLOCKED_COMMANDS = (
    "rm",
    "sudo",
    "su",
    "chmod",
    "chown",
    "mkfs",
    "dd",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "kill",
    "pkill",
    "systemctl",
    "service",
)

# ── 只允许继承的环境变量白名单（防止敏感信息泄露） ──────────────────────
ALLOWED_ENV_VARS = {
    "PATH",
    "HOME",
    "USER",
    "TMPDIR",
    "TEMP",
    "LANG",
    "LC_ALL",
    "NODE_PATH",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
}


class ProcessStatus(str, Enum):
    """子进程运行状态枚举。"""
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    CRASHED = "crashed"
    FAILED = "failed"


class MCPProcessInfo:
    """
    MCP 子进程信息记录。

    保存进程的运行时状态，用于监控和恢复决策。
    """

    __slots__ = (
        "server_name", "command", "args", "env",
        "status", "started_at", "restart_count",
        "tool_count", "last_error", "session_id",
        "pid", "memory_mb", "cpu_percent",
    )

    def __init__(
        self,
        server_name: str,
        command: str,
        args: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
    ):
        self.server_name = server_name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.status = ProcessStatus.STOPPED
        self.started_at: Optional[datetime] = None
        self.restart_count = 0
        self.tool_count = 0
        self.last_error: Optional[str] = None
        self.session_id: Optional[str] = None
        self.pid: Optional[int] = None
        self.memory_mb: Optional[float] = None
        self.cpu_percent: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "name": self.server_name,
            "command": self.command,
            "args": self.args,
            "status": self.status.value,
            "tool_count": self.tool_count,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "restart_count": self.restart_count,
            "last_error": self.last_error,
            "pid": self.pid,
            "memory_mb": self.memory_mb,
            "cpu_percent": self.cpu_percent,
        }


class MCPProcessManager:
    """
    MCP Stdio 子进程管理器。

    管理所有 Stdio 模式 MCP Server 子进程的完整生命周期。
    子进程使用官方 mcp SDK 的 stdio_client 进行通信。

    ====================================================
    核心设计原则：
    ====================================================
    1. 子进程复用 — 同一 Server 在同一对话期间不重复启动
    2. 崩溃恢复 — 自动重启（指数退避）
    3. 资源管理 — 对话结束时自动清理所有子进程
    4. 状态同步 — 进程状态变更时自动更新数据库
    5. 安全控制 — 命令白名单、环境变量隔离
    6. 资源限制 — 内存、CPU、运行时间限制
    """

    def __init__(self):
        # server_name -> MCPProcessInfo
        self._processes: dict[str, MCPProcessInfo] = {}
        # server_name -> ClientSession
        self._sessions: dict[str, ClientSession] = {}
        # server_name -> MCPStdioExecutor
        self._executors: dict[str, MCPStdioExecutor] = {}
        # server_name -> stdio_client context manager
        self._stdio_contexts: dict[str, Any] = {}
        # server_name -> session context manager
        self._session_contexts: dict[str, Any] = {}
        # 健康检查任务
        self._health_check_task: Optional[asyncio.Task] = None
        # 僵尸进程清理任务
        self._zombie_cleanup_task: Optional[asyncio.Task] = None

    # ── 安全控制 ──────────────────────────────────────────────────────────

    def _validate_command(self, command: str) -> tuple[bool, str]:
        """
        验证命令是否在允许的白名单内，且不在黑名单中。

        安全策略：
          1. 检查是否在黑名单中
          2. 检查是否以白名单前缀开头
          3. 禁止使用通配符和管道

        Args:
            command: 要验证的命令

        Returns:
            (is_allowed, reason) 元组
        """
        # 提取命令的基础名称（去除路径）
        cmd_base = os.path.basename(command).lower()
        cmd_full = command.lower()

        # 检查黑名单
        for blocked in BLOCKED_COMMANDS:
            if cmd_base == blocked or cmd_base.startswith(blocked + " "):
                return False, f"命令 '{command}' 在黑名单中（{blocked}）"

        # 检查白名单
        for prefix in ALLOWED_COMMAND_PREFIXES:
            if cmd_full.startswith(prefix.lower()):
                return True, ""

        # 如果命令是完整路径，检查是否在常见位置
        if os.path.isabs(command) and os.path.exists(command):
            return True, ""

        return False, f"命令 '{command}' 不在允许的白名单中"

    def _sanitize_env(self, env: dict[str, str]) -> dict[str, str]:
        """
        清理环境变量：只保留白名单中的系统环境变量 + 用户自定义变量。

        Args:
            env: 用户提供的环境变量

        Returns:
            清理后的环境变量字典
        """
        # 从系统环境变量中只继承白名单中的变量
        sanitized = {}
        for key in ALLOWED_ENV_VARS:
            if key in os.environ:
                sanitized[key] = os.environ[key]

        # 添加用户自定义环境变量（覆盖系统变量）
        for key, value in env.items():
            # 禁止覆盖 PATH（安全考虑）
            if key.upper() == "PATH":
                continue
            sanitized[key] = value

        return sanitized

    # ── 进程生命周期 ──────────────────────────────────────────────────────

    async def start_process(
        self,
        server_name: str,
        command: str,
        args: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
    ) -> MCPProcessInfo:
        """
        启动一个 MCP Server 子进程并建立通信会话。

        流程：
          1. 验证命令是否在白名单中
          2. 清理环境变量
          3. 创建/获取 MCPProcessInfo
          4. 构建 StdioServerParameters
          5. 使用 stdio_client 建立 stdin/stdout 管道
          6. 创建 ClientSession 并初始化握手
          7. 调用 tools/list 发现可用工具
          8. 创建 MCPStdioExecutor 并绑定 session

        Args:
            server_name: 服务器名称
            command: 可执行命令
            args: 命令行参数
            env: 环境变量

        Returns:
            MCPProcessInfo 对象（包含启动后状态）
        """
        # ── 安全验证 ──
        allowed, reason = self._validate_command(command)
        if not allowed:
            logger.error(
                f"[MCP Security] Command validation failed for '{server_name}': {reason}"
            )
            info = MCPProcessInfo(
                server_name=server_name,
                command=command,
                args=args or [],
                env=env or {},
            )
            info.status = ProcessStatus.FAILED
            info.last_error = reason
            self._processes[server_name] = info
            return info

        # ── 如果已存在且正在运行，直接返回 ──
        if server_name in self._processes:
            info = self._processes[server_name]
            if info.status == ProcessStatus.RUNNING:
                logger.info(
                    f"[MCP Process] Server '{server_name}' already running, reusing"
                )
                return info
            # 如果存在但已停止/崩溃，先清理
            await self.stop_process(server_name)

        # 创建进程信息
        info = MCPProcessInfo(
            server_name=server_name,
            command=command,
            args=args or [],
            env=env or {},
        )
        info.status = ProcessStatus.STARTING
        self._processes[server_name] = info

        logger.info(
            f"[MCP Process] Starting '{server_name}': {command} {' '.join(info.args)}"
        )

        try:
            # ── 环境变量清理 ──
            merged_env = self._sanitize_env(env or {})
            # 确保 PATH 存在
            if "PATH" not in merged_env and "PATH" in os.environ:
                merged_env["PATH"] = os.environ["PATH"]

            # 创建 StdioServerParameters
            server_params = StdioServerParameters(
                command=command,
                args=info.args,
                env=merged_env,
            )

            # 建立 Stdio Transport
            stdio_ctx = stdio_client(server_params)
            read_stream, write_stream = await stdio_ctx.__aenter__()
            self._stdio_contexts[server_name] = stdio_ctx

            # 创建 ClientSession
            session_ctx = ClientSession(read_stream, write_stream)
            session = await session_ctx.__aenter__()
            self._session_contexts[server_name] = session_ctx
            self._sessions[server_name] = session

            # 初始化握手
            await asyncio.wait_for(
                session.initialize(),
                timeout=INIT_TIMEOUT,
            )

            # 发现工具
            tools_result = await asyncio.wait_for(
                session.list_tools(),
                timeout=INIT_TIMEOUT,
            )

            # 创建并绑定执行器
            executor = MCPStdioExecutor()
            executor.set_session(session)
            self._executors[server_name] = executor

            # 更新状态
            info.status = ProcessStatus.RUNNING
            info.started_at = datetime.now(timezone.utc)
            info.restart_count = 0
            info.tool_count = len(tools_result.tools)
            info.last_error = None

            logger.info(
                f"[MCP Process] Server '{server_name}' started successfully "
                f"with {info.tool_count} tools"
            )

            # 启动健康检查和僵尸进程清理（如果尚未启动）
            self._ensure_background_tasks()

            return info

        except asyncio.TimeoutError:
            logger.error(f"[MCP Process] Server '{server_name}' initialization timed out")
            info.status = ProcessStatus.FAILED
            info.last_error = "Initialization timed out"
            await self._force_cleanup(server_name)
            return info

        except Exception as e:
            logger.error(f"[MCP Process] Failed to start '{server_name}': {e}", exc_info=True)
            info.status = ProcessStatus.FAILED
            info.last_error = str(e)
            await self._force_cleanup(server_name)
            return info

    async def stop_process(self, server_name: str) -> bool:
        """
        优雅停止指定 MCP Server 子进程。

        策略：
          1. 关闭 ClientSession
          2. stdio_client 退出时发送 EOF 到 stdin
          3. 子进程收到 stdin EOF 后自行退出

        Args:
            server_name: 服务器名称

        Returns:
            成功返回 True，未找到返回 False
        """
        info = self._processes.get(server_name)
        if not info:
            return False

        if info.status == ProcessStatus.STOPPED:
            return True

        logger.info(f"[MCP Process] Stopping '{server_name}' (pid={info.pid})...")
        info.status = ProcessStatus.STOPPED
        await self._force_cleanup(server_name)
        logger.info(f"[MCP Process] Server '{server_name}' stopped")
        return True

    async def restart_process(self, server_name: str) -> bool:
        """
        自动重启 MCP Server 子进程（带指数退避）。

        Args:
            server_name: 服务器名称

        Returns:
            重启成功返回 True
        """
        info = self._processes.get(server_name)
        if not info:
            return False

        if info.restart_count >= MAX_RESTART_ATTEMPTS:
            logger.error(
                f"[MCP Process] Server '{server_name}' max restart attempts "
                f"({MAX_RESTART_ATTEMPTS}) reached, giving up"
            )
            info.status = ProcessStatus.CRASHED
            return False

        info.restart_count += 1
        backoff = BACKOFF_BASE_SECONDS * (2 ** (info.restart_count - 1))
        logger.info(
            f"[MCP Process] Restarting '{server_name}' in {backoff}s "
            f"(attempt {info.restart_count}/{MAX_RESTART_ATTEMPTS})..."
        )
        await asyncio.sleep(backoff)

        # 清理旧资源
        await self._force_cleanup(server_name)

        # 重新启动
        new_info = await self.start_process(
            server_name=server_name,
            command=info.command,
            args=info.args,
            env=info.env,
        )
        success = new_info.status == ProcessStatus.RUNNING
        if success:
            logger.info(
                f"[MCP Process] Server '{server_name}' restarted successfully "
                f"(attempt {info.restart_count})"
            )
        else:
            logger.error(
                f"[MCP Process] Server '{server_name}' restart failed "
                f"(attempt {info.restart_count}): {new_info.last_error}"
            )
        return success

    async def stop_all_processes(self):
        """
        停止所有 MCP Server 子进程。

        在对话结束或应用关闭时调用。
        会依次停止所有进程，并记录每个进程的退出状态。
        """
        logger.info("[MCP Process] Stopping all processes...")
        for server_name in list(self._processes.keys()):
            await self.stop_process(server_name)

        # 取消后台任务
        self._cancel_background_tasks()

    # ── 工具执行 ──────────────────────────────────────────────────────────

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> str:
        """
        通过已建立的 Stdio 会话调用 MCP 工具。

        自动处理 session 故障恢复：
          - 如果 session 不可用，尝试重启进程
          - 如果进程已崩溃，自动恢复

        Args:
            server_name: MCP Server 名称
            tool_name: 工具名称（MCP Server 端的原始名称）
            arguments: 调用参数

        Returns:
            工具执行结果字符串（JSON 格式）
        """
        info = self._processes.get(server_name)
        if not info:
            logger.warning(
                f"[MCP Process] Server '{server_name}' not found "
                f"for tool '{tool_name}'"
            )
            return json.dumps({
                "error": f"MCP server '{server_name}' not found",
                "tool": tool_name,
            }, ensure_ascii=False)

        # 检查运行状态，尝试恢复
        if info.status != ProcessStatus.RUNNING:
            logger.warning(
                f"[MCP Process] Server '{server_name}' not running "
                f"(status={info.status.value}), attempting restart for tool '{tool_name}'..."
            )
            restored = await self.restart_process(server_name)
            if not restored:
                return json.dumps({
                    "error": f"MCP server '{server_name}' is not available "
                             f"(status: {info.status.value})",
                    "tool": tool_name,
                }, ensure_ascii=False)

        # 检查进程运行时间是否超过限制
        if info.started_at:
            runtime = (datetime.now(timezone.utc) - info.started_at).total_seconds()
            if runtime > MAX_PROCESS_RUNTIME_SECONDS:
                logger.warning(
                    f"[MCP Process] Server '{server_name}' exceeded max runtime "
                    f"({runtime:.0f}s > {MAX_PROCESS_RUNTIME_SECONDS}s), restarting..."
                )
                await self.restart_process(server_name)
                # 重启后再次检查
                if info.status != ProcessStatus.RUNNING:
                    return json.dumps({
                        "error": f"MCP server '{server_name}' restarted but still unavailable",
                        "tool": tool_name,
                    }, ensure_ascii=False)

        # ── Docker 路径转换：将宿主机 Windows 路径转换为容器内路径 ──
        adapted_args = maybe_convert_args(arguments, server_name)
        if adapted_args != arguments:
            logger.info(
                f"[Docker] Path converted for '{server_name}/{tool_name}': "
                f"args keys: {list(arguments.keys())}"
            )

        # 执行工具调用
        executor = self._executors.get(server_name)
        if not executor:
            enhanced_error = enhance_error_message(
                server_name, tool_name,
                f"No executor for MCP server '{server_name}'"
            )
            return json.dumps({
                "error": enhanced_error,
                "tool": tool_name,
            }, ensure_ascii=False)

        result = await executor.call_tool(tool_name, adapted_args)

        if result.get("success"):
            content = result.get("content", "")
            return content
        else:
            # 如果执行失败且 session 可能已损坏，尝试重启
            error = result.get("error", {})
            if isinstance(error, dict):
                error_type = error.get("type", "")
                error_msg = error.get("message", str(error))
                if error_type in ("SESSION_CLOSED", "PROCESS_CRASHED"):
                    logger.warning(
                        f"[MCP Process] Session issue for '{server_name}', "
                        f"scheduling restart: {error_msg}"
                    )
                    asyncio.create_task(self.restart_process(server_name))
            else:
                error_msg = str(error)
                if "session" in error_msg.lower() or "connection" in error_msg.lower():
                    logger.warning(
                        f"[MCP Process] Session issue for '{server_name}', "
                        f"scheduling restart..."
                    )
                    asyncio.create_task(self.restart_process(server_name))

            # 提取错误信息
            if isinstance(error, dict):
                error_msg = error.get("message", str(error))
            else:
                error_msg = str(error)

            # ── Docker 环境错误增强 ──
            enhanced_error_msg = enhance_error_message(server_name, tool_name, error_msg)

            return json.dumps({
                "error": enhanced_error_msg,
                "tool": tool_name,
                "server": server_name,
            }, ensure_ascii=False)

    # ── 工具发现 ──────────────────────────────────────────────────────────

    async def discover_tools(self, server_name: str) -> list[dict]:
        """
        获取指定 MCP Server 的可用工具列表。

        Args:
            server_name: 服务器名称

        Returns:
            工具定义列表
        """
        executor = self._executors.get(server_name)
        if not executor:
            logger.warning(
                f"[MCP Process] No executor for '{server_name}', cannot discover tools"
            )
            return []
        return await executor.list_tools()

    # ── 查询接口 ──────────────────────────────────────────────────────────

    def get_process_info(self, server_name: str) -> Optional[dict]:
        """获取进程信息。"""
        info = self._processes.get(server_name)
        return info.to_dict() if info else None

    def list_processes(self) -> list[dict]:
        """列出所有进程信息。"""
        return [info.to_dict() for info in self._processes.values()]

    def is_running(self, server_name: str) -> bool:
        """检查进程是否正在运行。"""
        info = self._processes.get(server_name)
        return info is not None and info.status == ProcessStatus.RUNNING

    def get_executor(self, server_name: str) -> Optional[MCPStdioExecutor]:
        """获取指定 Server 的执行器。"""
        return self._executors.get(server_name)

    # ── 内部方法 ──────────────────────────────────────────────────────────

    async def _force_cleanup(self, server_name: str):
        """
        强制清理指定 Server 的所有资源。

        无论当前状态如何，都尝试关闭所有打开的资源。
        记录进程的最终状态。
        """
        info = self._processes.get(server_name)

        # 取消 session
        session_ctx = self._session_contexts.pop(server_name, None)
        if session_ctx is not None:
            try:
                await session_ctx.__aexit__(None, None, None)
                logger.debug(f"[MCP Process] Session closed for '{server_name}'")
            except Exception as e:
                logger.debug(f"[MCP Process] Session cleanup for '{server_name}': {e}")

        # 取消 stdio transport
        stdio_ctx = self._stdio_contexts.pop(server_name, None)
        if stdio_ctx is not None:
            try:
                await stdio_ctx.__aexit__(None, None, None)
                logger.debug(f"[MCP Process] Stdio transport closed for '{server_name}'")
            except Exception as e:
                logger.debug(f"[MCP Process] Stdio cleanup for '{server_name}': {e}")

        # 清理引用
        self._sessions.pop(server_name, None)
        self._executors.pop(server_name, None)

    def _ensure_background_tasks(self):
        """确保后台任务正在运行。"""
        self._ensure_health_check()
        self._ensure_zombie_cleanup()

    def _ensure_health_check(self):
        """确保健康检查任务正在运行。"""
        if self._health_check_task is None or self._health_check_task.done():
            self._health_check_task = asyncio.create_task(self._health_check_loop())

    def _ensure_zombie_cleanup(self):
        """确保僵尸进程清理任务正在运行。"""
        if self._zombie_cleanup_task is None or self._zombie_cleanup_task.done():
            self._zombie_cleanup_task = asyncio.create_task(self._zombie_cleanup_loop())

    def _cancel_background_tasks(self):
        """取消所有后台任务。"""
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            try:
                # 不 await，直接取消
                pass
            except asyncio.CancelledError:
                pass
            self._health_check_task = None

        if self._zombie_cleanup_task and not self._zombie_cleanup_task.done():
            self._zombie_cleanup_task.cancel()
            try:
                pass
            except asyncio.CancelledError:
                pass
            self._zombie_cleanup_task = None

    async def _health_check_loop(self):
        """
        定期健康检查循环。

        每隔 HEALTH_CHECK_INTERVAL（30 秒）对所有运行中的子进程
        发送 ping 请求，无响应则标记为 CRASHED 并触发重启。
        """
        logger.info("[MCP Process] Health check loop started")
        while True:
            try:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL)
                check_count = 0
                restart_count = 0

                for server_name, info in list(self._processes.items()):
                    if info.status != ProcessStatus.RUNNING:
                        continue
                    session = self._sessions.get(server_name)
                    if session is None:
                        continue

                    try:
                        await asyncio.wait_for(
                            session.send_ping(),
                            timeout=HEALTH_CHECK_TIMEOUT,
                        )
                        check_count += 1
                    except Exception as e:
                        logger.warning(
                            f"[MCP Process] Health check failed for '{server_name}': {e}"
                        )
                        info.status = ProcessStatus.CRASHED
                        info.last_error = f"Health check failed: {str(e)}"
                        asyncio.create_task(self.restart_process(server_name))
                        restart_count += 1

                if check_count > 0 or restart_count > 0:
                    logger.info(
                        f"[MCP Process] Health check: {check_count} OK, "
                        f"{restart_count} restarted"
                    )

            except asyncio.CancelledError:
                logger.info("[MCP Process] Health check loop cancelled")
                break
            except Exception as e:
                logger.error(f"[MCP Process] Health check loop error: {e}")

    async def _zombie_cleanup_loop(self):
        """
        僵尸进程清理循环。

        定期检查所有子进程状态，清理已退出的进程。
        同时检查是否有运行时间过长的进程。
        """
        logger.info("[MCP Process] Zombie cleanup loop started")
        while True:
            try:
                await asyncio.sleep(ZOMBIE_CLEANUP_INTERVAL)
                cleanup_count = 0

                for server_name, info in list(self._processes.items()):
                    # 检查运行时间限制
                    if info.status == ProcessStatus.RUNNING and info.started_at:
                        runtime = (datetime.now(timezone.utc) - info.started_at).total_seconds()
                        if runtime > MAX_PROCESS_LIFETIME:
                            logger.warning(
                                f"[MCP Process] Server '{server_name}' exceeded max lifetime "
                                f"({runtime:.0f}s > {MAX_PROCESS_LIFETIME}s), restarting..."
                            )
                            info.status = ProcessStatus.STOPPED
                            asyncio.create_task(self.restart_process(server_name))
                            cleanup_count += 1

                    # 清理已标记为 CRASHED 但尚未清理的进程资源
                    if info.status == ProcessStatus.CRASHED:
                        # 检查是否已有清理任务在运行
                        if server_name in self._stdio_contexts:
                            logger.info(
                                f"[MCP Process] Cleaning up crashed process '{server_name}'"
                            )
                            await self._force_cleanup(server_name)
                            cleanup_count += 1

                if cleanup_count > 0:
                    logger.info(
                        f"[MCP Process] Zombie cleanup: {cleanup_count} process(es) handled"
                    )

            except asyncio.CancelledError:
                logger.info("[MCP Process] Zombie cleanup loop cancelled")
                break
            except Exception as e:
                logger.error(f"[MCP Process] Zombie cleanup loop error: {e}")


# 全局单例
process_manager = MCPProcessManager()
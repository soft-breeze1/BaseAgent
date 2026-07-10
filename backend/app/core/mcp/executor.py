"""
MCP 工具执行器 — HTTP 和 Stdio 两种传输模式的工具调用实现
=============================================================
严格遵循 MCP 1.0 协议规范，提供统一的工具调用接口。

两种模式：
  1. HTTP 模式：通过 POST 请求调用远端 MCP Server 的 tools/call 端点
  2. Stdio 模式：通过子进程 stdin/stdout 调用本地 MCP Server

两种模式返回统一格式：
  {
    "success": bool,
    "content": str,       # 工具执行结果文本
    "error": Optional[str | dict] # 错误信息
  }
"""
import asyncio
import json
import logging
import time
from typing import Optional

import httpx
from cachetools import TTLCache

from app.core.mcp.protocol import (
    build_tools_call_request,
    build_tools_list_request,
    parse_call_tool_response,
    parse_tools_list_response,
    mcp_tools_to_unified_format,
    DEFAULT_HTTP_TIMEOUT,
)
from app.core.mcp.errors import (
    MCPErrorType,
    make_mcp_error,
    make_mcp_success,
    format_error_for_agent,
)

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────

# HTTP 调用超时（默认 30 秒）
DEFAULT_HTTP_TOOL_TIMEOUT = 30.0

# HTTP 重试配置
HTTP_MAX_RETRIES = 2          # 最大重试次数（仅对幂等工具）
HTTP_RETRY_DELAY = 1.0        # 重试间隔（秒）

# IDEMPOTENT_TOOLS: 幂等工具列表（可以安全重试）
# 以 mcp_ 开头的工具由 discovery 模块处理，此处只定义原始工具名
IDEMPOTENT_TOOL_PATTERNS = ("list", "get", "read", "search", "find", "show")

# Stdio 调用超时（默认 60 秒）
STDIO_TOOL_TIMEOUT = 60.0

# Stdio 进程挂起检测阈值（秒）
STDIO_HANG_THRESHOLD = 30.0


# ── 工具定义缓存 ──────────────────────────────────────────────────────────

# v7.0: 使用 TTLCache 替代无限增长的 dict + 时间戳管理
# server_name -> { tool_name -> tool_definition_dict }
_tool_def_cache: TTLCache = TTLCache(maxsize=100, ttl=300)

# 缓存 TTL（秒）
TOOL_DEF_CACHE_TTL = 300  # 5 分钟


# ==========================================================================
# 参数验证
# ==========================================================================


def _validate_parameter_type(value, expected_type: str, param_name: str) -> Optional[str]:
    """
    验证单个参数的类型。

    Args:
        value: 参数值
        expected_type: 期望的 JSON Schema 类型
        param_name: 参数名称

    Returns:
        如果类型正确返回 None，否则返回错误描述
    """
    if value is None:
        return f"参数 '{param_name}' 不能为空"

    type_validators = {
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "array": lambda v: isinstance(v, list),
        "object": lambda v: isinstance(v, dict),
    }

    validator = type_validators.get(expected_type, lambda v: True)
    if not validator(value):
        actual_type = type(value).__name__
        return f"参数 '{param_name}' 需要类型 '{expected_type}'，实际类型 '{actual_type}'"
    return None


def _validate_parameter_value(value, param_def: dict, param_name: str) -> Optional[str]:
    """
    验证单个参数的值（枚举、长度、范围等）。

    Args:
        value: 参数值
        param_def: 参数的 JSON Schema 定义
        param_name: 参数名称

    Returns:
        如果验证通过返回 None，否则返回错误描述
    """
    # 枚举值验证
    enum_values = param_def.get("enum")
    if enum_values is not None and value not in enum_values:
        return (
            f"参数 '{param_name}' 的值 '{value}' 不在允许的范围内。"
            f"允许的值：{', '.join(str(v) for v in enum_values)}"
        )

    # 字符串长度验证
    if isinstance(value, str):
        min_length = param_def.get("minLength")
        max_length = param_def.get("maxLength")
        if min_length is not None and len(value) < min_length:
            return f"参数 '{param_name}' 长度不能少于 {min_length} 个字符（当前 {len(value)}）"
        if max_length is not None and len(value) > max_length:
            return f"参数 '{param_name}' 长度不能超过 {max_length} 个字符（当前 {len(value)}）"

    # 数字范围验证
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = param_def.get("minimum")
        maximum = param_def.get("maximum")
        if minimum is not None and value < minimum:
            return f"参数 '{param_name}' 不能小于 {minimum}（当前 {value}）"
        if maximum is not None and value > maximum:
            return f"参数 '{param_name}' 不能大于 {maximum}（当前 {value}）"

    return None


def validate_tool_arguments(
    tool_name: str,
    arguments: dict,
    input_schema: dict,
    server_name: str,
) -> Optional[dict]:
    """
    根据工具定义验证调用参数。

    在调用任何 MCP 工具之前调用此函数。
    验证失败时不发送请求给 MCP Server。

    Args:
        tool_name: 原始工具名称
        arguments: 调用参数
        input_schema: 工具的 inputSchema 定义
        server_name: 所属 MCP Server 名称

    Returns:
        验证通过返回 None，否则返回统一错误格式的 dict
    """
    if not input_schema or not isinstance(input_schema, dict):
        return None

    properties = input_schema.get("properties", {})
    required_params = input_schema.get("required", [])

    # 1. 检查必填参数
    for param_name in required_params:
        if param_name not in arguments or arguments[param_name] is None:
            return make_mcp_error(
                MCPErrorType.INVALID_PARAMS,
                f"缺少必填参数 '{param_name}'",
                details={
                    "tool": tool_name,
                    "server": server_name,
                    "missing_param": param_name,
                    "required_params": required_params,
                    "provided_params": list(arguments.keys()),
                },
            )

    # 2. 检查提供的参数类型和值
    for param_name, param_value in arguments.items():
        param_def = properties.get(param_name, {})

        # 类型验证
        expected_type = param_def.get("type")
        if expected_type:
            type_error = _validate_parameter_type(param_value, expected_type, param_name)
            if type_error:
                return make_mcp_error(
                    MCPErrorType.INVALID_PARAMS,
                    type_error,
                    details={
                        "tool": tool_name,
                        "server": server_name,
                        "param": param_name,
                        "expected_type": expected_type,
                        "actual_value": str(param_value)[:200],
                    },
                )

        # 值验证（枚举、长度、范围）
        value_error = _validate_parameter_value(param_value, param_def, param_name)
        if value_error:
            return make_mcp_error(
                MCPErrorType.INVALID_PARAMS,
                value_error,
                details={
                    "tool": tool_name,
                    "server": server_name,
                    "param": param_name,
                    "param_def": param_def,
                },
            )

    # 3. 检查是否有未知参数（可选，记录警告而不阻止）
    for param_name in arguments:
        if param_name not in properties and param_name not in required_params:
            logger.warning(
                f"[MCP Param] Tool '{server_name}/{tool_name}' received "
                f"unexpected parameter '{param_name}'"
            )

    return None  # 验证通过


# ── 工具定义缓存管理 ──────────────────────────────────────────────────────


def cache_tool_definitions(server_name: str, tools: list[dict]):
    """
    缓存某个 MCP Server 的工具定义。

    Args:
        server_name: 服务器名称
        tools: 工具定义列表
    """
    def_map = {}
    for tool in tools:
        name = tool.get("name", "")
        if name:
            def_map[name] = tool

    _tool_def_cache[server_name] = def_map

    logger.info(
        f"[MCP Cache] Cached {len(def_map)} tool definitions "
        f"for server '{server_name}'"
    )


def get_cached_tool_definition(server_name: str, tool_name: str) -> Optional[dict]:
    """
    获取缓存的工具定义。

    TTLCache 自动处理过期，无需手动检查时间戳。

    Args:
        server_name: 服务器名称
        tool_name: 工具名称

    Returns:
        工具定义字典，如果未缓存或不复存在返回 None
    """
    def_map = _tool_def_cache.get(server_name)
    if not def_map:
        return None

    return def_map.get(tool_name)


def clear_tool_def_cache(server_name: Optional[str] = None):
    """
    清除工具定义缓存。

    Args:
        server_name: 可选，指定服务器名称。为 None 时清除所有缓存。
    """
    if server_name:
        _tool_def_cache.pop(server_name, None)
        logger.info(f"[MCP Cache] Cleared tool def cache for '{server_name}'")
    else:
        _tool_def_cache.clear()
        logger.info("[MCP Cache] Cleared all tool definition caches")


# ==========================================================================
# HTTP 模式执行器
# ==========================================================================


class MCPHttpExecutor:
    """
    MCP HTTP 模式工具执行器。

    通过 HTTP POST 请求调用远端 MCP Server 的 tools/list 和 tools/call 端点。
    严格按照 MCP 1.0 标准的 JSON-RPC 2.0 格式进行通信。

    MCP 1.0 HTTP 端点约定：
      - POST /mcp/v1/tools/list  -> 发现工具
      - POST /mcp/v1/tools/call  -> 调用工具

    兼容性：自动尝试 /mcp/ 和 /mcp/v1/ 两个路径前缀。
    """

    def __init__(self, base_url: str, timeout: float = DEFAULT_HTTP_TIMEOUT):
        """
        Args:
            base_url: MCP Server 的基础 URL（如 http://localhost:8001）
            timeout: 请求超时（秒）
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._server_name = ""  # 由外部设置

    def _get_endpoints(self) -> list[str]:
        """
        获取可能的端点路径列表（向后兼容）。

        MCP 1.0 标准端点：
          - /mcp/v1/tools/list
          - /mcp/v1/tools/call

        遗留端点（兼容旧版）：
          - /mcp/tools/list
          - /mcp/tools/call
        """
        return [
            f"{self.base_url}/mcp/v1",
            f"{self.base_url}/mcp",
        ]

    async def list_tools(self) -> list[dict]:
        """
        调用 MCP Server 的 tools/list 方法，发现可用工具。

        超时控制：默认 10 秒。
        单个 HTTP 端点尝试失败后自动尝试下一个。

        Returns:
            工具定义列表，每个元素包含 name, description, inputSchema。
            连接失败或解析错误时返回空列表。
        """
        request_body = build_tools_list_request()
        start_time = time.time()

        for prefix in self._get_endpoints():
            url = f"{prefix}/tools/list"
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                    resp = await client.post(url, json=request_body)
                    if resp.status_code == 200:
                        tools = parse_tools_list_response(resp.json())
                        elapsed = time.time() - start_time
                        if tools:
                            logger.info(
                                f"[MCP HTTP] Discovered {len(tools)} tools from {url} "
                                f"(took {elapsed:.2f}s)"
                            )
                            return tools
                        logger.warning(f"[MCP HTTP] No tools found at {url} (took {elapsed:.2f}s)")
                    else:
                        logger.debug(
                            f"[MCP HTTP] {url} returned status {resp.status_code}"
                        )
            except httpx.TimeoutException:
                logger.warning(f"[MCP HTTP] Timeout connecting to {url} (after 10s)")
            except httpx.ConnectError:
                logger.debug(f"[MCP HTTP] Cannot connect to {url}")
            except Exception as e:
                logger.warning(f"[MCP HTTP] Error calling {url}: {e}")

        elapsed = time.time() - start_time
        logger.error(
            f"[MCP HTTP] Failed to discover tools from {self.base_url} "
            f"(tried {len(self._get_endpoints())} endpoints, took {elapsed:.2f}s)"
        )
        return []

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """
        调用 MCP Server 的 tools/call 方法执行工具。

        支持自动重试（对幂等工具最多重试 HTTP_MAX_RETRIES 次）。

        Args:
            tool_name: 工具名称（MCP Server 端的原始名称）
            arguments: 调用参数字典

        Returns:
            统一返回格式：
            {
                "success": bool,
                "content": str,
                "error": Optional[str | dict]
            }
        """
        # 参数验证
        input_schema = get_cached_tool_definition(self._server_name, tool_name)
        if input_schema:
            validation_error = validate_tool_arguments(
                tool_name=tool_name,
                arguments=arguments,
                input_schema=input_schema,
                server_name=self._server_name,
            )
            if validation_error:
                return validation_error

        # 判断是否为幂等操作（可安全重试）
        is_idempotent = any(tool_name.startswith(p) for p in IDEMPOTENT_TOOL_PATTERNS)
        max_attempts = HTTP_MAX_RETRIES + 1 if is_idempotent else 1

        request_body = build_tools_call_request(tool_name, arguments)
        last_exception = None
        start_time = time.time()

        for attempt in range(max_attempts):
            if attempt > 0:
                logger.info(
                    f"[MCP HTTP] Retry attempt {attempt + 1}/{max_attempts} "
                    f"for tool '{tool_name}' (after {HTTP_RETRY_DELAY}s delay)"
                )
                await asyncio.sleep(HTTP_RETRY_DELAY)

            for prefix in self._get_endpoints():
                url = f"{prefix}/tools/call"
                try:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(self.timeout)
                    ) as client:
                        resp = await client.post(url, json=request_body)
                        elapsed = time.time() - start_time

                        if resp.status_code == 200:
                            result = parse_call_tool_response(resp.json())
                            if result.get("success"):
                                logger.info(
                                    f"[MCP HTTP] Tool '{tool_name}' executed successfully "
                                    f"(took {elapsed:.2f}s, attempt {attempt + 1})"
                                )
                                return result
                            else:
                                error_msg = result.get("error", "Unknown error")
                                logger.warning(
                                    f"[MCP HTTP] Tool '{tool_name}' returned error "
                                    f"(took {elapsed:.2f}s, attempt {attempt + 1}): "
                                    f"{error_msg}"
                                )
                                # 如果是固定错误（非临时性），不再重试
                                if isinstance(error_msg, dict):
                                    error_type = error_msg.get("type", "")
                                    if error_type == MCPErrorType.INVALID_PARAMS.value:
                                        return result
                                    if error_type == MCPErrorType.TOOL_NOT_FOUND.value:
                                        return result
                                return result
                        else:
                            logger.warning(
                                f"[MCP HTTP] {url} returned status {resp.status_code} "
                                f"for tool '{tool_name}' (attempt {attempt + 1})"
                            )
                except httpx.TimeoutException:
                    elapsed = time.time() - start_time
                    last_exception = TimeoutError(
                        f"HTTP timeout after {self.timeout}s calling '{tool_name}'"
                    )
                    logger.warning(
                        f"[MCP HTTP] Timeout calling tool '{tool_name}' at {url} "
                        f"(elapsed {elapsed:.2f}s, attempt {attempt + 1})"
                    )
                except httpx.ConnectError as e:
                    last_exception = ConnectionError(
                        f"Cannot connect to {url}: {e}"
                    )
                    logger.warning(
                        f"[MCP HTTP] Cannot connect to {url} "
                        f"(attempt {attempt + 1}): {e}"
                    )
                    break  # 连接失败，切换到下一个端点前缀需要内层循环返回
                except Exception as e:
                    last_exception = e
                    logger.warning(
                        f"[MCP HTTP] Error calling tool '{tool_name}' at {url} "
                        f"(attempt {attempt + 1}): {e}"
                    )

            # 如果连接成功但返回错误且不可重试，直接返回
            if not is_idempotent and attempt == 0:
                break

        elapsed = time.time() - start_time
        logger.error(
            f"[MCP HTTP] Failed to call tool '{tool_name}' after {max_attempts} attempt(s) "
            f"(took {elapsed:.2f}s)"
        )

        # 根据最后的异常类型返回对应的错误
        if isinstance(last_exception, TimeoutError):
            return make_mcp_error(
                MCPErrorType.TIMEOUT,
                f"HTTP timeout after {self.timeout}s calling '{tool_name}'",
                details={"tool": tool_name, "attempts": max_attempts, "elapsed": round(elapsed, 2)},
            )
        elif isinstance(last_exception, ConnectionError):
            return make_mcp_error(
                MCPErrorType.CONNECTION_FAILED,
                f"Cannot connect to MCP server at {self.base_url}",
                details={"tool": tool_name, "url": self.base_url, "elapsed": round(elapsed, 2)},
            )
        else:
            return make_mcp_error(
                MCPErrorType.SERVER_ERROR,
                f"Failed to call tool '{tool_name}' after {max_attempts} attempt(s)",
                details={"tool": tool_name, "url": self.base_url, "elapsed": round(elapsed, 2)},
            )


# ==========================================================================
# Stdio 模式执行器
# ==========================================================================


class MCPStdioExecutor:
    """
    MCP Stdio 模式工具执行器。

    通过子进程的标准输入输出与 MCP Server 通信。
    子进程由 ProcessManager 管理，本执行器只负责发送请求和接收响应。

    注意：StdioExecutor 不管理子进程生命周期，
    子进程管理由 ProcessManager 负责。
    调用前需要确保子进程已启动且通信管道已建立。
    """

    def __init__(self):
        self._read_stream = None
        self._write_stream = None
        self._session = None
        self._server_name = ""

    def set_session(self, session) -> None:
        """
        设置已建立的 MCP SDK ClientSession。

        Args:
            session: mcp SDK 的 ClientSession 实例
        """
        self._session = session

    async def list_tools(self) -> list[dict]:
        """
        通过 Stdio Transport 获取 MCP Server 的工具列表。

        Returns:
            工具定义列表
        """
        if self._session is None:
            logger.error("[MCP Stdio] No session available for list_tools")
            return []

        try:
            start_time = time.time()
            result = await asyncio.wait_for(
                self._session.list_tools(),
                timeout=15.0,
            )
            # 解析 mcp SDK 返回的 ListToolsResult
            tools = []
            for tool in result.tools:
                tools.append({
                    "name": tool.name,
                    "description": getattr(tool, "description", ""),
                    "inputSchema": getattr(tool, "inputSchema", {
                        "type": "object",
                        "properties": {},
                    }),
                })
            elapsed = time.time() - start_time
            logger.info(
                f"[MCP Stdio] Discovered {len(tools)} tools "
                f"(took {elapsed:.2f}s)"
            )
            return tools
        except asyncio.TimeoutError:
            logger.error(f"[MCP Stdio] list_tools timed out after 15s")
            return []
        except Exception as e:
            logger.error(f"[MCP Stdio] list_tools failed: {e}", exc_info=True)
            return []

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """
        通过 Stdio Transport 调用 MCP 工具。

        超时控制：默认 STDIO_TOOL_TIMEOUT（60 秒）。
        超时后会发出进程挂起警告。

        Args:
            tool_name: 工具名称
            arguments: 调用参数

        Returns:
            统一返回格式：
            {
                "success": bool,
                "content": str,
                "error": Optional[str | dict]
            }
        """
        if self._session is None:
            logger.error(f"[MCP Stdio] No session available for tool '{tool_name}'")
            return make_mcp_error(
                MCPErrorType.SESSION_CLOSED,
                "MCP session is not available",
                details={"tool": tool_name, "server": self._server_name},
            )

        # 参数验证
        input_schema = get_cached_tool_definition(self._server_name, tool_name)
        if input_schema:
            validation_error = validate_tool_arguments(
                tool_name=tool_name,
                arguments=arguments,
                input_schema=input_schema,
                server_name=self._server_name,
            )
            if validation_error:
                return validation_error

        start_time = time.time()
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(tool_name, arguments),
                timeout=STDIO_TOOL_TIMEOUT,
            )

            # 解析返回结果
            content_parts = []
            for item in result.content:
                if hasattr(item, "text"):
                    content_parts.append(item.text)
                elif hasattr(item, "data"):
                    content_parts.append(str(item.data))
                else:
                    content_parts.append(str(item))

            full_content = "\n".join(content_parts)
            is_error = getattr(result, "isError", False)
            elapsed = time.time() - start_time

            if is_error:
                logger.warning(
                    f"[MCP Stdio] Tool '{tool_name}' returned error "
                    f"(took {elapsed:.2f}s): {full_content[:300]}"
                )
                return make_mcp_error(
                    MCPErrorType.SERVER_ERROR,
                    full_content[:500] or "Tool returned error",
                    details={"tool": tool_name, "server": self._server_name, "elapsed": round(elapsed, 2)},
                )

            logger.info(
                f"[MCP Stdio] Tool '{tool_name}' executed successfully "
                f"({len(full_content)} chars, took {elapsed:.2f}s)"
            )
            return make_mcp_success(full_content)

        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            logger.error(
                f"[MCP Stdio] Tool '{tool_name}' timed out after {STDIO_TOOL_TIMEOUT}s"
            )
            # 检查是否挂起（超过 hang 阈值）
            if elapsed >= STDIO_HANG_THRESHOLD:
                logger.warning(
                    f"[MCP Stdio] Process may be hanging for '{self._server_name}' "
                    f"(elapsed {elapsed:.2f}s)"
                )
            return make_mcp_error(
                MCPErrorType.TIMEOUT,
                f"Tool call '{tool_name}' timed out after {STDIO_TOOL_TIMEOUT}s",
                details={"tool": tool_name, "server": self._server_name, "elapsed": round(elapsed, 2)},
            )
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(
                f"[MCP Stdio] Tool '{tool_name}' failed (took {elapsed:.2f}s): {e}",
                exc_info=True,
            )
            return make_mcp_error(
                MCPErrorType.UNKNOWN,
                f"Tool execution error: {str(e)}",
                details={"tool": tool_name, "server": self._server_name, "elapsed": round(elapsed, 2)},
            )


# ==========================================================================
# 工具调用统一分发器
# ==========================================================================


async def execute_mcp_tool(
    executor_type: str,
    executor,
    tool_name: str,
    arguments: dict,
) -> str:
    """
    统一的 MCP 工具调用入口。

    根据 executor_type 选择调用方式，并将结果转换为字符串格式
    （兼容 SmartRouter 的现有工具返回格式）。

    Args:
        executor_type: "http" 或 "stdio"
        executor: MCPHttpExecutor 或 MCPStdioExecutor 实例
        tool_name: 工具名称（MCP Server 端的原始名称）
        arguments: 调用参数

    Returns:
        工具执行结果字符串（JSON 格式）
    """
    start_time = time.time()

    try:
        if executor_type == "http":
            result = await executor.call_tool(tool_name, arguments)
        elif executor_type == "stdio":
            result = await executor.call_tool(tool_name, arguments)
        else:
            result = make_mcp_error(
                MCPErrorType.UNKNOWN,
                f"Unknown executor type: {executor_type}",
            )

        elapsed = time.time() - start_time

        if result.get("success"):
            content = result.get("content", "")
            logger.info(
                f"[MCP Executor] Tool '{tool_name}' succeeded "
                f"(executor={executor_type}, took {elapsed:.2f}s)"
            )
            return content
        else:
            error = result.get("error", {})
            if isinstance(error, dict):
                error_msg = error.get("message", str(error))
                error_type = error.get("type", "UNKNOWN")
            else:
                error_msg = str(error)
                error_type = "UNKNOWN"

            # 格式化为 Agent 可理解的错误信息
            agent_message = format_error_for_agent(
                error_type=next(
                    (e for e in MCPErrorType if e.value == error_type),
                    MCPErrorType.UNKNOWN,
                ),
                message=error_msg,
                tool_name=tool_name,
            )

            logger.warning(
                f"[MCP Executor] Tool '{tool_name}' failed "
                f"(executor={executor_type}, took {elapsed:.2f}s): "
                f"[{error_type}] {error_msg}"
            )

            return json.dumps({
                "error": agent_message,
                "tool": tool_name,
                "mcp_error_type": error_type,
                "mcp_retryable": isinstance(error, dict) and error.get("retryable", False),
            }, ensure_ascii=False)

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(
            f"[MCP Executor] Unexpected error calling '{tool_name}' "
            f"(took {elapsed:.2f}s): {e}",
            exc_info=True,
        )
        return json.dumps({
            "error": f"MCP execution error: {str(e)}",
            "tool": tool_name,
        }, ensure_ascii=False)
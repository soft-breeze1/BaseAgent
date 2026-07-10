"""
MCP 统一错误处理模块
====================
定义 MCP 错误类型枚举和统一的错误格式转换器。
所有 MCP 错误都转换为与静态工具完全一致的返回格式。

错误格式：
  {
      "success": False,
      "content": "",
      "error": {
          "type": "MCPErrorType",
          "message": "用户友好的错误信息",
          "retryable": True/False
      }
  }
"""
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class MCPErrorType(str, Enum):
    """MCP 错误类型枚举。"""
    CONNECTION_FAILED = "CONNECTION_FAILED"      # 连接失败（HTTP 连接拒绝/Stdio 进程启动失败）
    TIMEOUT = "TIMEOUT"                          # 超时（请求超时/初始化超时）
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"            # 工具不存在
    INVALID_PARAMS = "INVALID_PARAMS"            # 参数验证失败
    SERVER_ERROR = "SERVER_ERROR"                # MCP Server 内部错误
    SESSION_CLOSED = "SESSION_CLOSED"            # Session 已关闭
    PROTOCOL_ERROR = "PROTOCOL_ERROR"            # 协议解析错误（JSON-RPC 格式错误）
    PROCESS_CRASHED = "PROCESS_CRASHED"          # 子进程崩溃
    RATE_LIMITED = "RATE_LIMITED"               # 频率限制
    UNKNOWN = "UNKNOWN"                          # 未知错误


# 定义哪些错误类型可以重试
RETRYABLE_ERRORS = {
    MCPErrorType.CONNECTION_FAILED,
    MCPErrorType.TIMEOUT,
    MCPErrorType.SESSION_CLOSED,
    MCPErrorType.PROCESS_CRASHED,
}

# 定义哪些错误类型不可重试
NON_RETRYABLE_ERRORS = {
    MCPErrorType.TOOL_NOT_FOUND,
    MCPErrorType.INVALID_PARAMS,
    MCPErrorType.PROTOCOL_ERROR,
}


def is_retryable(error_type: MCPErrorType) -> bool:
    """
    判断给定的错误类型是否可重试。

    Args:
        error_type: MCP 错误类型

    Returns:
        可重试返回 True，不可重试返回 False
    """
    return error_type in RETRYABLE_ERRORS


def make_mcp_error(
    error_type: MCPErrorType,
    message: str,
    details: Optional[dict] = None,
) -> dict:
    """
    创建一个统一格式的 MCP 错误响应。

    Args:
        error_type: 错误类型枚举
        message: 用户友好的错误描述
        details: 可选的额外详细信息

    Returns:
        统一错误格式字典：
        {
            "success": False,
            "content": "",
            "error": {
                "type": "MCPErrorType",
                "message": "描述",
                "retryable": True/False,
                "details": { ... }  # 可选
            }
        }
    """
    retryable = is_retryable(error_type)

    error_dict = {
        "type": error_type.value,
        "message": message,
        "retryable": retryable,
    }
    if details:
        error_dict["details"] = details

    return {
        "success": False,
        "content": "",
        "error": error_dict,
    }


def make_mcp_success(content: str) -> dict:
    """
    创建一个统一格式的 MCP 成功响应。

    Args:
        content: 工具执行结果文本

    Returns:
        统一成功格式字典
    """
    return {
        "success": True,
        "content": content,
        "error": None,
    }


def extract_error_message(result: dict) -> str:
    """
    从 MCP 统一响应中提取用户友好的错误信息。

    兼容新旧两种错误格式：
      - 新格式：result["error"]["message"]
      - 旧格式：result["error"]（字符串）

    Args:
        result: MCP 执行器的返回结果

    Returns:
        错误信息字符串
    """
    error = result.get("error")
    if not error:
        return ""

    if isinstance(error, dict):
        return error.get("message", str(error))

    if isinstance(error, str):
        return error

    return str(error)


def format_error_for_agent(error_type: MCPErrorType, message: str, tool_name: str) -> str:
    """
    将 MCP 错误格式化为 Agent 可理解的自然语言描述。

    Agent 根据此错误信息决定下一步操作（重试或跳过）。

    Args:
        error_type: 错误类型
        message: 原始错误信息
        tool_name: 工具全名（含 mcp_ 前缀）

    Returns:
        格式化的错误描述字符串（可直接作为 ToolMessage 返回给 Agent）
    """
    type_descriptions = {
        MCPErrorType.CONNECTION_FAILED: "MCP 服务器连接失败",
        MCPErrorType.TIMEOUT: "MCP 工具调用超时",
        MCPErrorType.TOOL_NOT_FOUND: "MCP 工具不存在",
        MCPErrorType.INVALID_PARAMS: "MCP 工具参数无效",
        MCPErrorType.SERVER_ERROR: "MCP 服务器返回错误",
        MCPErrorType.SESSION_CLOSED: "MCP 连接会话已关闭",
        MCPErrorType.PROTOCOL_ERROR: "MCP 协议通信错误",
        MCPErrorType.PROCESS_CRASHED: "MCP 本地进程已崩溃",
        MCPErrorType.RATE_LIMITED: "MCP 工具调用频率过高",
        MCPErrorType.UNKNOWN: "MCP 工具调用未知错误",
    }

    type_desc = type_descriptions.get(error_type, "MCP 工具调用错误")
    retry_hint = "你可以稍后重试此操作。" if is_retryable(error_type) else "请检查配置或参数后重试。"

    return (
        f"[MCP 工具 '{tool_name}'] {type_desc}：{message}\n"
        f"[建议] {retry_hint}"
    )
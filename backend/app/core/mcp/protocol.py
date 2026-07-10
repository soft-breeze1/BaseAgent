"""
MCP 1.0 协议层 — 序列化/反序列化实现
=====================================
严格遵循 MCP 1.0 官方规范（JSON-RPC 2.0 over HTTP/Stdio）。

协议规范要点：
  - 所有请求使用 JSON-RPC 2.0 格式
  - 方法命名空间：tools/initialize, tools/list, tools/call
  - 响应中包含 result.content 数组，每个元素有 type 和 text 字段
  - 错误时返回 isError 标记

依赖：
  - httpx (HTTP 客户端)
  - json (标准库)
  - uuid (生成请求 ID)
"""
import json
import uuid
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────

MCP_PROTOCOL_VERSION = "2025-03-26"  # MCP 1.0 规范版本号

# 默认超时
DEFAULT_HTTP_TIMEOUT = 30.0

# ── MCP Protocol Data Models ──────────────────────────────────────────────


class MCPToolSchema:
    """
    MCP 工具定义的标准结构。
    用于将 MCP Server 返回的工具定义转换为统一的内部格式。
    """
    __slots__ = ("name", "description", "input_schema", "server_name")

    def __init__(
        self,
        name: str,
        description: str = "",
        input_schema: Optional[dict] = None,
        server_name: str = "",
    ):
        self.name = name
        self.description = description or ""
        self.input_schema = input_schema or {"type": "object", "properties": {}}
        self.server_name = server_name

    def to_openai_tool(self) -> dict:
        """
        转换为 OpenAI API bind_tools 格式。
        与现有静态工具格式完全一致，可无缝合并。
        """
        return {
            "type": "function",
            "function": {
                "name": f"mcp_{self.server_name}_{self.name}",
                "description": f"[{self.server_name}] {self.description}" if self.server_name else self.description,
                "parameters": self.input_schema,
            },
        }

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "server_name": self.server_name,
        }

    @classmethod
    def from_dict(cls, data: dict, server_name: str = "") -> "MCPToolSchema":
        """从字典反序列化。"""
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            input_schema=data.get("inputSchema", data.get("input_schema", {})),
            server_name=server_name or data.get("server_name", ""),
        )


# ── JSON-RPC 2.0 消息构建 ───────────────────────────────────────────────


def build_jsonrpc_request(method: str, params: Optional[dict] = None) -> dict:
    """
    构建 MCP 1.0 标准的 JSON-RPC 2.0 请求。

    Args:
        method: 方法名，如 "tools/list"、"tools/call"
        params: 方法参数

    Returns:
        JSON-RPC 2.0 请求字典
    """
    return {
        "jsonrpc": "2.0",
        "id": f"req-{uuid.uuid4().hex[:12]}",
        "method": method,
        "params": params or {},
    }


def build_tools_call_request(tool_name: str, arguments: dict) -> dict:
    """
    构建 MCP 1.0 tools/call 请求。

    Args:
        tool_name: 工具名称（MCP Server 端的原始名称）
        arguments: 调用参数

    Returns:
        符合 MCP 1.0 规范的 tools/call 请求字典
    """
    return build_jsonrpc_request(
        method="tools/call",
        params={
            "name": tool_name,
            "arguments": arguments,
        },
    )


def build_tools_list_request() -> dict:
    """
    构建 MCP 1.0 tools/list 请求。

    Returns:
        符合 MCP 1.0 规范的 tools/list 请求字典
    """
    return build_jsonrpc_request(method="tools/list")


# ── 响应解析 ──────────────────────────────────────────────────────────────


def parse_tools_list_response(response_data: dict) -> list[dict]:
    """
    解析 MCP 1.0 tools/list 响应，提取工具定义列表。

    MCP 1.0 标准响应格式：
    {
        "jsonrpc": "2.0",
        "id": "req-xxx",
        "result": {
            "tools": [
                {
                    "name": "tool_name",
                    "description": "Tool description",
                    "inputSchema": { ... }
                }
            ]
        }
    }

    Args:
        response_data: MCP Server 返回的 JSON-RPC 响应

    Returns:
        工具定义字典列表，每个 dict 包含 name, description, inputSchema 字段。
        如果响应格式错误或包含错误，返回空列表。
    """
    try:
        if isinstance(response_data, str):
            response_data = json.loads(response_data)

        # 检查 JSON-RPC 错误
        if "error" in response_data:
            error_data = response_data["error"]
            logger.warning(
                f"[MCP Protocol] tools/list returned error: "
                f"code={error_data.get('code')}, message={error_data.get('message')}"
            )
            return []

        result = response_data.get("result", {})
        if not result:
            logger.warning("[MCP Protocol] tools/list response missing 'result'")
            return []

        tools = result.get("tools", [])
        if not isinstance(tools, list):
            logger.warning("[MCP Protocol] tools/list 'tools' is not a list")
            return []

        # 确保每个工具定义都包含必需字段
        validated_tools = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name", "")
            if not name:
                continue
            validated_tools.append({
                "name": name,
                "description": tool.get("description", ""),
                "inputSchema": tool.get("inputSchema", tool.get("input_schema", {
                    "type": "object",
                    "properties": {},
                })),
            })

        return validated_tools

    except json.JSONDecodeError as e:
        logger.error(f"[MCP Protocol] Failed to parse tools/list response as JSON: {e}")
        return []
    except Exception as e:
        logger.error(f"[MCP Protocol] Unexpected error parsing tools/list response: {e}")
        return []


def parse_call_tool_response(response_data: Any) -> dict:
    """
    解析 MCP 1.0 tools/call 响应，提取工具执行结果。

    MCP 1.0 标准成功响应格式：
    {
        "jsonrpc": "2.0",
        "id": "req-xxx",
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": "工具执行结果文本"
                }
            ],
            "isError": false
        }
    }

    MCP 1.0 标准失败响应格式（isError=True）：
    {
        "jsonrpc": "2.0",
        "id": "req-xxx",
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": "错误信息"
                }
            ],
            "isError": true
        }
    }

    Args:
        response_data: MCP Server 返回的 JSON-RPC 响应（dict 或 str）

    Returns:
        统一的工具返回字典，格式：
        {
            "success": bool,
            "content": str,       # 执行结果文本
            "error": Optional[str] # 错误信息，成功时为 None
        }
    """
    try:
        if isinstance(response_data, str):
            response_data = json.loads(response_data)

        # 检查 JSON-RPC 层错误
        if isinstance(response_data, dict) and "error" in response_data:
            error_data = response_data["error"]
            error_msg = error_data.get("message", "Unknown MCP error")
            error_code = error_data.get("code", -1)
            logger.warning(
                f"[MCP Protocol] tools/call JSON-RPC error: "
                f"code={error_code}, message={error_msg}"
            )
            return {
                "success": False,
                "content": "",
                "error": f"MCP error (code {error_code}): {error_msg}",
            }

        # 获取 result 对象
        result = response_data.get("result", response_data) if isinstance(response_data, dict) else {}

        if not result:
            return {
                "success": False,
                "content": "",
                "error": "MCP response missing 'result' field",
            }

        # 提取 content 数组中的文本
        content_parts = []
        content_list = result.get("content", [])
        if isinstance(content_list, list):
            for item in content_list:
                if isinstance(item, dict):
                    item_type = item.get("type", "text")
                    if item_type == "text":
                        text = item.get("text", "")
                        if text:
                            content_parts.append(text)
                    elif item_type == "resource":
                        # resource 类型：提取 resource 的文本
                        resource = item.get("resource", {})
                        if isinstance(resource, dict):
                            content_parts.append(resource.get("text", str(resource)))
                    else:
                        # 其他类型：尝试转字符串
                        content_parts.append(str(item))
                else:
                    content_parts.append(str(item))

        full_content = "\n".join(content_parts) if content_parts else ""

        # 检查 isError 标记
        is_error = result.get("isError", False)

        if is_error:
            return {
                "success": False,
                "content": full_content,
                "error": full_content or "MCP tool returned isError=True",
            }

        return {
            "success": True,
            "content": full_content,
            "error": None,
        }

    except json.JSONDecodeError as e:
        logger.error(f"[MCP Protocol] Failed to parse tools/call response as JSON: {e}")
        return {
            "success": False,
            "content": "",
            "error": f"Failed to parse MCP response: {str(e)}",
        }
    except Exception as e:
        logger.error(f"[MCP Protocol] Unexpected error parsing tools/call response: {e}")
        return {
            "success": False,
            "content": "",
            "error": f"MCP response parse error: {str(e)}",
        }


# ── 工具定义转换 ──────────────────────────────────────────────────────────


# ── 文件操作工具路径约束关键词 ──────────────────────────────────────────
# 当 MCP 工具名称或参数名包含以下关键词时，自动注入绝对路径约束
FILE_PATH_TOOL_KEYWORDS = ("read_file", "write_file", "edit_file", "list_directory",
                           "create_directory", "delete_file", "move_file", "copy_file",
                           "file_search", "grep_search", "get_file_info", "search_files")


def _inject_path_constraints(tool_name: str, description: str, parameters: dict) -> tuple[str, dict]:
    """
    向文件操作工具的 path 参数注入严格约束描述。
    不修改参数结构（type/enum/required 等），仅增强 description。

    Args:
        tool_name: 工具原始名称（不含 mcp_ 前缀）
        description: 原始描述文本
        parameters: parameters schema dict（会被原地修改）

    Returns:
        (enhanced_description, enhanced_parameters)
    """
    # 仅对文件操作类工具生效
    is_file_tool = any(kw in tool_name.lower() for kw in FILE_PATH_TOOL_KEYWORDS)
    if not is_file_tool:
        return description, parameters

    path_constraint = (
        "CRITICAL: The 'path' MUST be an absolute path. "
        "If the user only provides a relative filename (e.g., 'hello.txt'), you MUST "
        "scan the conversation history to find the correct parent directory from "
        "previous tool calls, then construct the full absolute path. "
        "NEVER guess default directories like '/app/' or './'. "
        "NEVER pass a bare filename without its full directory."
    )

    # 为 description 追加路径约束
    enhanced_desc = description
    if path_constraint not in description:
        enhanced_desc = f"{description}\n\n{path_constraint}" if description else path_constraint

    # 遍历所有参数，对名字包含 "path" 的参数增强 description
    props = parameters.get("properties", {})
    for prop_name, prop_def in props.items():
        if "path" in prop_name.lower() and isinstance(prop_def, dict):
            existing_desc = prop_def.get("description", "")
            if "CRITICAL" not in existing_desc:
                prop_desc_constraint = (
                    "CRITICAL: MUST be an absolute path. "
                    "Do NOT pass relative paths or bare filenames. "
                    "Resolve the full path from conversation history before calling."
                )
                if existing_desc:
                    prop_def["description"] = f"{existing_desc}. {prop_desc_constraint}"
                else:
                    prop_def["description"] = prop_desc_constraint

    return enhanced_desc, parameters


def mcp_tools_to_unified_format(
    tools: list[dict],
    server_name: str,
) -> list[dict]:
    """
    将 MCP 1.0 工具定义列表转换为与现有静态工具兼容的 OpenAI JSON Schema 格式。

    转换逻辑：
      MCP 工具定义 -> OpenAI function calling 格式

    v1.1: 对文件操作工具自动注入 path 绝对路径约束到 description。

    此格式可直接用于 llm.bind_tools() 或添加到现有工具列表中。

    Args:
        tools: MCP tools/list 返回的工具定义列表
        server_name: 所属 MCP Server 名称

    Returns:
        OpenAI-compatible tool schema list，每个元素结构：
        {
            "type": "function",
            "function": {
                "name": "mcp_{server}_{tool}",
                "description": "[{server}] {description}",
                "parameters": { ... }
            }
        }
    """
    result = []
    for tool in tools:
        name = tool.get("name", "unknown")
        description = tool.get("description", "")
        input_schema = tool.get("inputSchema", tool.get("input_schema", {}))

        # 生成全局唯一名称，避免与内置工具命名冲突
        unique_name = f"mcp_{server_name}_{name}"

        # 标准化 parameters schema
        parameters = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        if isinstance(input_schema, dict):
            props = input_schema.get("properties", {})
            required = input_schema.get("required", [])
            # 确保每个 property 有 type 字段
            for prop_name, prop_def in props.items():
                if isinstance(prop_def, dict) and "type" not in prop_def:
                    prop_def["type"] = "string"
                parameters["properties"][prop_name] = prop_def
            parameters["required"] = required if isinstance(required, list) else []

        # v1.1: 对文件操作工具注入 path 绝对路径约束
        description, parameters = _inject_path_constraints(name, description, parameters)

        result.append({
            "type": "function",
            "function": {
                "name": unique_name,
                "description": f"[{server_name}] {description}",
                "parameters": parameters,
            },
        })

    return result


def convert_tool_name_to_mcp_full_name(tool_name: str) -> tuple:
    """
    将统一工具名解析为 (server_name, raw_tool_name)。

    统一工具名格式：mcp_{server}_{tool_name}
    例如：mcp_github_create_issue -> ("github", "create_issue")

    Args:
        tool_name: 统一工具名

    Returns:
        (server_name, raw_tool_name) 元组。
        如果格式不匹配，返回 (None, None)。
    """
    if not tool_name.startswith("mcp_"):
        return None, None

    # 格式：mcp_{server}_{tool_name}
    # 注意 tool_name 本身可能包含下划线，所以从第二个下划线之后都是 tool_name
    parts = tool_name.split("_", 2)
    if len(parts) < 3:
        return None, None

    return parts[1], parts[2]
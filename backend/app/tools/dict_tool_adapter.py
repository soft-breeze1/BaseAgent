"""
Dict Schema → LangChain StructuredTool 适配器
==============================================
将 OpenAI function calling 格式的 dict 工具（Skill、MCP）
转换为 LangChain StructuredTool 实例，确保 bind_tools() 兼容。

核心函数：
  wrap_dict_to_tool(tool_dict) → StructuredTool

执行逻辑：
  StructuredTool 的 _run 为占位函数（返回空值），因为实际执行
  dispatch 在 SmartRouter._execute_single_tool() 中通过 tool_name
  路由到 ExecutionInterceptor / MCP executor / ToolManager。
  此适配器只解决 bind_tools() 的格式兼容问题。
"""

import json
import logging
from typing import Any, Optional, Type

from pydantic import BaseModel, Field, create_model
from langchain_core.tools import StructuredTool

logger = logging.getLogger(__name__)


def wrap_dict_to_tool(tool_dict: dict) -> Optional[StructuredTool]:
    """
    将 OpenAI function calling 格式的 dict 工具包装为 LangChain StructuredTool。

    Args:
        tool_dict: dict 格式的工具定义，格式如下：
            {
                "type": "function",
                "function": {
                    "name": "...",
                    "description": "...",
                    "parameters": {"type": "object", "properties": {...}, "required": [...]}
                }
            }
            或简写格式：{"name": "...", "description": "...", "parameters": {...}}

    Returns:
        StructuredTool 实例，若转换失败则返回 None
    """
    # ── 解析输入格式 ──
    func_info = tool_dict.get("function", tool_dict)
    name = func_info.get("name", "")
    description = func_info.get("description", "")
    parameters = func_info.get("parameters", {})

    if not name:
        logger.warning(f"[DictToolAdapter] 工具缺少 name 字段: {tool_dict}")
        return None

    # ── 动态构建 Pydantic args_schema ──
    props = parameters.get("properties", {}) if isinstance(parameters, dict) else {}
    required_params = set(parameters.get("required", []) if isinstance(parameters, dict) else [])

    if not props:
        # 无参分支：使用预定义的空模型
        class _EmptyArgs(BaseModel):
            """No parameters required."""
            pass
        args_schema: Type[BaseModel] = _EmptyArgs
    else:
        # 有参分支：动态创建 Pydantic 模型（仅支持基本类型映射）
        field_definitions = {}
        for prop_name, prop_def in props.items():
            js_type = prop_def.get("type", "string") if isinstance(prop_def, dict) else "string"
            prop_desc = prop_def.get("description", "") if isinstance(prop_def, dict) else ""
            is_required = prop_name in required_params

            # JSON Schema type → Python type 映射
            type_map = {
                "string": str,
                "integer": int,
                "number": float,
                "boolean": bool,
                "array": list,
                "object": dict,
            }
            py_type = type_map.get(js_type, str)

            # 对于非必填字段，使用 Optional
            if is_required:
                field_definitions[prop_name] = (py_type, Field(..., description=prop_desc))
            else:
                field_definitions[prop_name] = (Optional[py_type], Field(None, description=prop_desc))

        args_schema = create_model(
            f"{name}_args",
            **field_definitions,
        )

    # ── 创建 StructuredTool（使用占位 _run 函数） ──
    # 实际执行路由在 _execute_single_tool 中通过 tool_name 分发
    async def _dummy_placeholder(**kwargs: Any) -> str:
        """
        占位执行函数。实际执行由 SmartRouter._execute_single_tool 完成。
        此函数不应被直接调用。
        """
        logger.warning(
            f"[DictToolAdapter] Dummy _run called for '{name}' with args={kwargs}. "
            "This should not happen — actual execution is handled by _execute_single_tool."
        )
        return json.dumps({"status": "error", "message": "internal dispatcher bypass"})

    tool = StructuredTool(
        name=name,
        description=description,
        args_schema=args_schema,
        func=_dummy_placeholder,
        coroutine=_dummy_placeholder,
        handle_tool_error=False,
    )

    logger.debug(f"[DictToolAdapter] Wrapped '{name}' as StructuredTool (args_schema={args_schema.__name__})")
    return tool


def normalize_tools_list(tools: list) -> list:
    """
    统一清洗工具列表：将所有 dict 格式的工具转换为 StructuredTool 实例。

    在传给 bind_tools() 之前调用此函数。

    Args:
        tools: 混合列表，包含 BaseTool 实例和 dict 格式的工具定义

    Returns:
        全部为 LangChain 工具实例的列表
    """
    normalized = []
    for t in tools:
        if isinstance(t, dict):
            wrapped = wrap_dict_to_tool(t)
            if wrapped:
                normalized.append(wrapped)
            else:
                logger.warning(f"[DictToolAdapter] 跳过无法适配的 dict 工具: {t.get('function', t).get('name', 'unknown')}")
        else:
            # 已经是 LangChain 工具实例，直接保留
            normalized.append(t)
    return normalized
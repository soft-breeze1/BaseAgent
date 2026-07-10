"""
模块 B：DynamicSchemaBuilder — Schema 自动组装器
==================================================
职责：将 YAML 字典转换为 OpenAI/DeepSeek 标准的 JSON Schema Tool 格式。

v12.0 重构：
  - 工具名从 load_skill_ 前缀改为直接使用技能名（如 csdn_blog_writer）
  - description 改为动作导向描述，说明工具能产出什么
  - 参数使用原始 YAML frontmatter 定义的 parameters，不再抹平
  - 此模块纯作格式转换，不涉及任何文件系统或 LLM 调用
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 标准前缀（v12.0: 保留用于向后兼容，但不再用于工具名）
TOOL_NAME_PREFIX = "load_skill_"


def build_tool_schema(yaml_meta: dict) -> Optional[dict]:
    """
    将 YAML 元数据字典转换为 OpenAI/DeepSeek 标准的 JSON Schema Tool 格式。

    v12.0 重构：
      - 工具名使用技能文件夹名（中划线转下划线），去掉 load_skill_ 前缀
      - description 改为动作导向："撰写高质量 CSDN 技术博客的完整工具..."
      - 参数使用原始 YAML frontmatter 定义的 parameters，保留 required

    Args:
        yaml_meta: SkillManager.get_active_skills_metadata() 返回的单个技能元数据 dict。
            必须包含: folder_name, name, description, parameters 等字段。

    Returns:
        OpenAI function calling 格式的 Tool Schema dict：
        {
            "type": "function",
            "function": {
                "name": "csdn_blog_writer",
                "description": "撰写高质量 CSDN 技术博客的完整工具...",
                "parameters": { "type": "object", "properties": {...}, "required": [...] }
            }
        }
        转换失败时返回 None。

    Raises:
        ValueError: 当传入的 yaml_meta 缺少必要字段时抛出明确错误。
    """
    # ── 输入校验 ──
    if not isinstance(yaml_meta, dict):
        logger.error(f"build_tool_schema 接收到的 yaml_meta 不是 dict 类型: {type(yaml_meta)}")
        raise ValueError(f"yaml_meta 必须是 dict 类型，收到 {type(yaml_meta)}")

    folder_name = yaml_meta.get("folder_name", "")
    name = yaml_meta.get("name", "")
    display_name = yaml_meta.get("display_name", name)
    description = yaml_meta.get("description", "")

    if not folder_name:
        logger.error(f"build_tool_schema 缺少 folder_name 字段: {yaml_meta}")
        raise ValueError("yaml_meta 必须包含 folder_name 字段")
    if not description:
        logger.warning(f"技能 [{folder_name}] 缺少 description，生成的 Tool Schema 可能无效")

    # ── 标准化工具名 ──
    # v12.0: 直接使用文件夹名作为工具名（中划线转下划线），去掉 load_skill_ 前缀
    normalized_folder = folder_name.replace("-", "_")
    tool_name = normalized_folder

    # ── 动作导向描述（v12.0） ──
    # 描述这个工具能做什么、产出什么，而不是"加载指南"
    injected_description = (
        f"[{display_name}] {description}. "
        f"Call this tool directly when the user's request matches this capability. "
        f"This tool will handle the entire workflow internally."
    )

    # ── 参数 Schema 处理（v12.0: 保留原始参数，不抹平） ──
    parameters = yaml_meta.get("parameters", None)
    if parameters is None:
        # 没有定义参数时，使用默认的 topic 参数
        parameters = {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": f"The topic or request to be processed by {display_name}"
                }
            },
            "required": ["topic"],
        }
        logger.debug(f"技能 [{folder_name}] 无 parameters 字段，已自动补齐 topic 参数")
    elif not isinstance(parameters, dict):
        logger.warning(f"技能 [{folder_name}] 的 parameters 不是 dict 类型 ({type(parameters)})，已重置")
        parameters = {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": f"The topic or request to be processed by {display_name}"
                }
            },
            "required": ["topic"],
        }
    else:
        if "type" not in parameters:
            parameters["type"] = "object"
        if "properties" not in parameters:
            parameters["properties"] = {}
        if "required" not in parameters:
            parameters["required"] = []

    # ── 组装 OpenAI/DeepSeek 标准 Tool Schema ──
    tool_schema = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": injected_description,
            "parameters": parameters,
        },
    }

    logger.debug(f"Tool Schema 构建完成: {tool_name}")
    return tool_schema


def is_skill_context_tool(tool_name: str) -> bool:
    """
    判断工具名是否为技能工具。
    v12.0: 技能工具不再有 load_skill_ 前缀，通过检查是否在技能元数据中判断。
    保留此函数用于向后兼容，但实际判断逻辑改为在 execution_interceptor 中查表。

    Args:
        tool_name: 工具调用的名称

    Returns:
        如果是技能工具返回 True，否则 False
    """
    # v12.0: 不再通过前缀判断，改为在 execution_interceptor 中通过技能元数据查表
    return False


def extract_skill_folder_name(tool_name: str) -> Optional[str]:
    """
    从工具名中提取技能文件夹名。
    v12.0: 工具名直接等于文件夹名（中划线转下划线），所以直接返回 tool_name 转回中划线。
    "csdn_blog_writer" → "csdn-blog-writer"

    Args:
        tool_name: 完整工具名

    Returns:
        技能文件夹名（原始文件夹名，含中划线），如果不是技能工具则返回 None
    """
    if not tool_name:
        return None
    # 将下划线转回中划线（因为文件夹名可能包含中划线）
    return tool_name.replace("_", "-")
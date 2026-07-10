"""
模块 C：ToolInjector — PLANNER 拦截与注入
============================================
职责：在每次向大模型（PLANNER）发起对话请求前，实施偷天换日。

核心流程：
  1. 调用 SkillManager 获取当前物理挂载的技能列表
  2. 经过 Schema Builder 将每个技能包装为 Tool Schema
  3. 将其与系统内置工具 append 到同一个列表中
  4. 确保大模型在当前上下文中看到这些"虚拟工具"

此模块只做"注入"动作，不干涉文件系统或 Schema 生成逻辑。
"""

import logging
from typing import List, Optional

from app.progressive_disclosure.skill_manager import SkillManager
from app.progressive_disclosure.schema_builder import (
    build_tool_schema,
    TOOL_NAME_PREFIX,
)

logger = logging.getLogger(__name__)


class ToolInjector:
    """
    PLANNER 工具注入器。
    
    在 SmartRouter 组装 tools 列表时，调用 inject_skill_tools() 将
    当前物理挂载的所有技能包装为 Tool Schema 并注入到 tools 列表中。
    
    Usage:
        injector = ToolInjector(skill_manager_instance)
        all_tools = injector.inject_skill_tools(existing_tools_list)
    """

    def __init__(self, skill_manager: SkillManager):
        """
        Args:
            skill_manager: SkillManager 实例，用于获取技能元数据。
        """
        self._skill_manager = skill_manager

    def inject_skill_tools(self, existing_tools: list) -> list:
        """
        将当前挂载的技能作为 Tool Schema 注入到已有工具列表中。
        
        这是 PLANNER 阶段的核心注入方法：
          1. 调用 SkillManager.get_active_skills_metadata() 扫描磁盘
          2. 对每个有效技能调用 build_tool_schema() 转换为 OpenAI Schema
          3. 将生成的 Schema 追加到 existing_tools 列表
        
        Args:
            existing_tools: 当前已有的工具列表（内置工具 + MCP 工具）。
                可以是 LangChain BaseTool 实例或 dict Schema 的混合列表。
        
        Returns:
            注入后的完整工具列表（在原有列表上追加，不修改原列表的引用）。
            如果没有技能，返回原列表的副本。
        """
        # 复制原列表，避免副作用
        all_tools = list(existing_tools)

        # Step 1: 获取当前物理挂载的所有技能元数据
        skills_metadata = self._skill_manager.get_active_skills_metadata()
        if not skills_metadata:
            logger.debug("没有发现任何技能，跳过工具注入")
            return all_tools

        # Step 2 & 3: 遍历每个技能，构建 Tool Schema 并注入
        injected_count = 0
        for skill_meta in skills_metadata:
            try:
                tool_schema = build_tool_schema(skill_meta)
                if tool_schema:
                    # 将 OpenAI 格式的 Tool Schema 追加到 tools 列表
                    all_tools.append(tool_schema)
                    tool_name = tool_schema.get("function", {}).get("name", "unknown")
                    logger.info(f"技能工具已注入 PLANNER: {tool_name}")
                    injected_count += 1
                else:
                    folder_name = skill_meta.get("folder_name", "unknown")
                    logger.warning(f"技能 [{folder_name}] 的 Tool Schema 构建失败，跳过注入")
            except ValueError as e:
                folder_name = skill_meta.get("folder_name", "unknown")
                logger.warning(f"技能 [{folder_name}] 注入失败（参数校验错误）: {e}")
            except Exception as e:
                folder_name = skill_meta.get("folder_name", "unknown")
                logger.error(f"技能 [{folder_name}] 注入异常: {e}", exc_info=True)

        if injected_count > 0:
            logger.info(f"PLANNER 工具注入完成: 共注入 {injected_count} 个技能工具")
        else:
            logger.debug("没有注入任何技能工具")

        return all_tools

    def get_injected_tool_names(self) -> List[str]:
        """
        返回当前所有已注入的技能工具名称列表。
        
        用于调试和日志记录。
        
        Returns:
            ["load_skill_context_skill1", "load_skill_context_skill2", ...]
        """
        skills_metadata = self._skill_manager.get_active_skills_metadata()
        return [
            f"{TOOL_NAME_PREFIX}{meta['folder_name']}"
            for meta in skills_metadata
            if meta.get("folder_name")
        ]

    def get_skill_count(self) -> int:
        """返回当前发现的技能数量。"""
        return len(self._skill_manager.get_active_skills_metadata())
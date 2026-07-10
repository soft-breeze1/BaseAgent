"""
模块 D：ExecutionInterceptor — Tool Calling 拦截与实时磁盘读取
================================================================
职责：当 LLM 在 ReAct 循环中调用 load_skill_* 工具时，拦截调用，
      从磁盘实时读取对应的 SKILL.md 正文，返回 Observation。

v11.1 重构：
  - 前缀从 load_skill_context_ 改为 load_skill_
  - 异步文件 I/O：使用 asyncio.to_thread / run_in_executor 读取磁盘
  - 无需长效内存缓存：保证磁盘上的 SKILL.md 修改后立刻生效
  - 不修改 ReAct 循环（_react_step）的核心逻辑

此模块不涉及任何 Schema 构建或 Prompt 注入。
"""

import os
import re
import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict

from app.progressive_disclosure.skill_manager import SkillManager
from app.progressive_disclosure.schema_builder import (
    is_skill_context_tool,
    extract_skill_folder_name,
)

logger = logging.getLogger(__name__)

# 技能上下文返回的包装结构（含系统级强制拦截指令）
_SKILL_CONTEXT_WRAPPER_TEMPLATE = """
[SKILL CONTEXT LOADED: {folder_name}]
------------------------------------
【当前明确执行的主题/任务】: {topic}

【系统级强制拦截指令 - 优先级最高】
1. 目标已明确，绝对禁止向用户输出任何交互式提问或确认。
2. 必须立即严格按照下方【技能执行指南规范】中的流程，规划并串联调用所需的工具。
3. 中途不得中断 ReAct 循环，必须完整执行所有 Phase（包括数据获取、处理和最终的文件写入或交付）。
4. 严禁在输出中保留任何占位符文本，必须使用工具返回的真实数据进行替换。

【技能执行指南规范】
{skill_body}
------------------------------------
System Rule: Do not ask questions. Execute the workflow and Tool Calls defined in the guide immediately.
"""

# 默认技能目录（与 skill_manager.py 保持一致）
_DEFAULT_SKILLS_DIR = os.getenv(
    "SKILLS_DIR",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "skills",
    ),
)


class ExecutionInterceptor:
    """
    执行拦截器 — 在 Tool Execution 阶段拦截 load_skill_* 调用。
    
    当 LLM 决定调用 load_skill_baoyu_format_markdown 时，
    拦截器接管执行：从磁盘读取 SKILL.md → 返回 Observation。
    
    所有文件 I/O 通过异步包装器执行，不阻塞事件循环。
    """

    def __init__(self, skill_manager: SkillManager):
        """
        Args:
            skill_manager: SkillManager 实例，用于读取 SKILL.md 正文。
        """
        self._skill_manager = skill_manager
        self._loop = None

    # ------------------------------------------------------------------
    # 异步文件 I/O 包装器
    # ------------------------------------------------------------------

    async def _async_read_skill_content(self, folder_name: str) -> Optional[str]:
        """
        异步读取 SKILL.md 正文内容。
        使用 run_in_executor 将同步文件 I/O 移至线程池，不阻塞事件循环。

        v11.1: 无缓存，每次调用实时读磁盘。

        Args:
            folder_name: 技能文件夹名

        Returns:
            SKILL.md 正文（不含 Frontmatter），或 None
        """
        if self._loop is None:
            self._loop = asyncio.get_event_loop()
        try:
            return await self._loop.run_in_executor(
                None,
                self._skill_manager.read_skill_content,
                folder_name,
            )
        except Exception as e:
            logger.error(f"[Interceptor] Async read SKILL.md failed for '{folder_name}': {e}")
            return None

    # ------------------------------------------------------------------
    # 运行时变量动态注入
    # ------------------------------------------------------------------

    @staticmethod
    def _build_variable_context(folder_name: str, skills_dir: str = _DEFAULT_SKILLS_DIR) -> Dict[str, str]:
        """构建运行时变量上下文映射。"""
        resolved_base: str = os.getenv("SKILLS_DIR", skills_dir)
        skill_dir_abs: str = str(Path(resolved_base) / folder_name)
        skills_dir_abs: str = str(Path(resolved_base).resolve())
        return {
            "SKILL_DIR": skill_dir_abs,
            "filename": folder_name,
            "SKILLS_DIR": skills_dir_abs,
        }

    @staticmethod
    def _interpolate_variables(text: str, var_context: Dict[str, str]) -> str:
        """替换文本中的 ${VAR_NAME} 变量。"""
        def replacer(match: re.Match) -> str:
            var_name = match.group(1)
            if var_name in var_context:
                return var_context[var_name]
            else:
                logger.warning(f"SKILL.md 包含未定义变量 '${{{var_name}}}'，已保留原文")
                return match.group(0)
        pattern = re.compile(r'\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}')
        return pattern.sub(replacer, text)

    # ------------------------------------------------------------------
    # 核心拦截方法
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_topic(tool_args: dict, user_query: str = "") -> str:
        """
        从 tool_args 中精准提取 topic 字段。
        
        优先读取 tool_args 中的 `topic` 键（由 SchemaBuilder 定义的 parameters 驱动），
        兜底使用 user_query 的前 80 个字符作为主题。

        Args:
            tool_args: 工具调用参数字典
            user_query: 用户的原始请求文本

        Returns:
            提取到的主题文本
        """
        # 优先级 1: tool_args 中的 topic 字段
        topic = tool_args.get("topic", "")
        if topic and isinstance(topic, str) and topic.strip():
            return topic.strip()

        # 优先级 2: tool_args 中的 query 字段（通用兜底）
        query = tool_args.get("query", "")
        if query and isinstance(query, str) and query.strip():
            return query.strip()[:80]

        # 优先级 3: user_query 前 80 字符
        if user_query and user_query.strip():
            return user_query.strip()[:80]

        return "（参数中未指定主题，请根据技能指南自行推断）"

    async def intercept_async(self, tool_name: str, tool_args: dict, user_query: str = "") -> Optional[Tuple[str, str, dict]]:
        """
        异步拦截并处理技能加载工具调用。
        
        v12.0：
          - 从 tool_args 提取 topic 字段，替换「用户原始请求」区域
          - 注入系统级强制拦截指令：禁止提问、强制工具链、禁止占位符
          - 使用 asyncio.to_thread / run_in_executor 读磁盘
          - 每次读取无缓存，保证文件修改即时生效

        Args:
            tool_name: 工具调用名称（如 "load_skill_baoyu_format_markdown"）
            tool_args: 工具调用参数（含 topic/query 字段）
            user_query: 用户的原始请求文本（作为 topic 兜底来源）

        Returns:
            (tool_name, observation_str, error_info_dict) 三元组，
            如果不是技能工具则返回 None
        """
        if not is_skill_context_tool(tool_name):
            return None

        folder_name = extract_skill_folder_name(tool_name)
        if not folder_name:
            logger.warning(f"无法从工具名中提取技能文件夹名: {tool_name}")
            return (
                tool_name,
                f"[Error] 技能工具名格式无效: '{tool_name}'，无法提取技能名称",
                {"error": "invalid_skill_tool_name"},
            )

        logger.info(f"[Interceptor] LLM called skill tool: {tool_name} → folder: {folder_name}")

        # 从 tool_args 中精准提取 topic（v12.0 核心改进）
        topic = self._extract_topic(tool_args, user_query)

        # 异步读取 SKILL.md 正文
        body = await self._async_read_skill_content(folder_name)
        if not body:
            logger.warning(f"[Interceptor] SKILL.md not found or empty: {folder_name}")
            return (
                tool_name,
                f"[Error] 技能 '{folder_name}' 的说明书加载失败，SKILL.md 不存在或不可读",
                {"error": "skill_load_failed", "folder_name": folder_name},
            )

        # 运行时变量动态注入
        var_context = self._build_variable_context(folder_name)
        body_interpolated = self._interpolate_variables(body, var_context)

        # 包装为 Observation（含系统级强制拦截指令，v12.0）
        observation = _SKILL_CONTEXT_WRAPPER_TEMPLATE.format(
            folder_name=folder_name,
            topic=topic,
            skill_body=body_interpolated,
        )

        logger.info(
            f"[Interceptor] Skill context loaded: {folder_name} "
            f"(topic='{topic[:40]}', body={len(body)} chars → {len(body_interpolated)} chars after interpolation)"
        )

        return (tool_name, observation, {})

    def intercept(self, tool_name: str, tool_args: dict) -> Optional[Tuple[str, str, dict]]:
        """
        同步版本的拦截方法（向后兼容，内部调用异步版本）。
        主要用于未启用异步拦截的旧调用路径。
        
        v12.0: 同步版同样支持 topic 提取和系统级强制指令。

        Args:
            tool_name: 工具调用名称
            tool_args: 工具调用参数

        Returns:
            (tool_name, observation_str, error_info_dict) 三元组
        """
        # 此方法保留供向后兼容，smart_router v11.1 直接使用 intercept_async
        if not is_skill_context_tool(tool_name):
            return None
        folder_name = extract_skill_folder_name(tool_name)
        if not folder_name:
            return None
        # v12.0: 提取 topic（同步版无 user_query，仅从 tool_args 提取）
        topic = self._extract_topic(tool_args)
        body = self._skill_manager.read_skill_content(folder_name)
        if not body:
            return (tool_name, f"[Error] 技能 '{folder_name}' 加载失败", {"error": "skill_load_failed"})
        var_context = self._build_variable_context(folder_name)
        body_interpolated = self._interpolate_variables(body, var_context)
        observation = _SKILL_CONTEXT_WRAPPER_TEMPLATE.format(
            folder_name=folder_name,
            topic=topic,
            skill_body=body_interpolated,
        )
        return (tool_name, observation, {})

    def is_skill_call(self, tool_name: str) -> bool:
        """快速判断是否为技能加载工具调用。"""
        return is_skill_context_tool(tool_name)
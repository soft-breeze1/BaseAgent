"""
渐进式信息披露 (Progressive Disclosure) 动态工具注入机制
========================================================
四个严格解耦的模块：

模块 A - SkillManager:      文件系统读写，作为 Single Source of Truth
模块 B - DynamicSchemaBuilder: YAML → OpenAI JSON Schema Tool 格式转换
模块 C - ToolInjector:      PLANNER 阶段将技能工具注入 tools 列表
模块 D - ExecutionInterceptor: EXECUTOR 阶段拦截 load_skill_context_* 调用

数据流：
  SkillManager.scan() → get_active_skills_metadata()
    → SchemaBuilder.build_tool_schema() → ToolInjector.inject()
      → PLANNER 看到虚拟工具 → 选中调用 → EXECUTOR
        → ExecutionInterceptor.intercept() → SkillManager.read_skill_content()
          → 返回 Markdown 正文作为 Observation → 状态机继续
"""

from app.progressive_disclosure.skill_manager import SkillManager
from app.progressive_disclosure.schema_builder import (
    build_tool_schema,
    is_skill_context_tool,
    extract_skill_folder_name,
    TOOL_NAME_PREFIX,
)
from app.progressive_disclosure.tool_injector import ToolInjector
from app.progressive_disclosure.execution_interceptor import ExecutionInterceptor


# 统一工厂函数：创建全套 Progressive Disclosure 组件
def create_progressive_disclosure_system(skills_dir: str = None) -> dict:
    """
    工厂函数：创建完整的渐进式信息披露系统。
    
    返回四个模块实例的 dict，供 SmartRouter 集成使用。
    
    Args:
        skills_dir: 技能目录路径，为 None 时使用默认路径。
    
    Returns:
        {
            "skill_manager": SkillManager,
            "tool_injector": ToolInjector,
            "execution_interceptor": ExecutionInterceptor,
            "schema_builder": build_tool_schema function,
        }
    """
    skill_manager = SkillManager(skills_dir=skills_dir)
    tool_injector = ToolInjector(skill_manager=skill_manager)
    execution_interceptor = ExecutionInterceptor(skill_manager=skill_manager)

    return {
        "skill_manager": skill_manager,
        "tool_injector": tool_injector,
        "execution_interceptor": execution_interceptor,
        "schema_builder": build_tool_schema,
    }


# 导出的公共 API
__all__ = [
    # 模块 A
    "SkillManager",
    # 模块 B
    "build_tool_schema",
    "is_skill_context_tool",
    "extract_skill_folder_name",
    "TOOL_NAME_PREFIX",
    # 模块 C
    "ToolInjector",
    # 模块 D
    "ExecutionInterceptor",
    # 模块 E
    "SkillRunner",
    # 工厂函数
    "create_progressive_disclosure_system",
]

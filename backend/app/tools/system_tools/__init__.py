"""
系统级基础工具包 (System Base Tools)
=====================================
为 Agent 提供操作文件系统和终端的物理执行能力。

模块划分：
  模块 A - FileSystem Tools: write_local_file, read_local_file
  模块 B - Terminal Tools: execute_bash_command

这些工具通过 tool_manager 注册后，Agent 在 PLANNER 阶段即可看到并调用。
"""

from app.tools.system_tools.system_tools import (
    write_local_file,
    read_local_file,
    execute_bash_command,
    SYSTEM_TOOLS,
    register_all,
)

__all__ = [
    "write_local_file",
    "read_local_file",
    "execute_bash_command",
    "SYSTEM_TOOLS",
    "register_all",
]
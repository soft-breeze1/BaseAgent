"""
模块 A + B：系统级基础工具（FileSystem + Terminal）
=====================================================
高内聚低耦合：这些工具封装在独立模块中，通过 tool_manager 注册。

模块 A - FileSystem Tools:
  - write_local_file(filepath, content) -> str (仅返回状态元数据，不返回内容)
  - read_local_file(filepath) -> str (强制 8000 字符截断)

模块 B - Terminal Tools:
  - execute_bash_command(command, work_dir) -> str

模块 C - 全局注册: system_tools.register_all() 注册到 ToolManager
模块 D - System Prompt: 在 smart_router.py 中注入执行守则
"""

import os
import sys
import json
import logging
import subprocess
import traceback
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# 默认超时时间（秒）
_DEFAULT_CMD_TIMEOUT = 120

# 读取文件最大字符数（上下文保护）
_READ_FILE_MAX_CHARS = 8000

# ------------------------------------------------------------------
# 模块 A：文件系统工具
# ------------------------------------------------------------------


@tool
def write_local_file(filepath: str, content: str) -> str:
    """
    将大模型生成的代码或文本写入到指定的本地文件路径。如果目录不存在，请自动创建。

    Args:
        filepath: 目标文件路径（绝对路径或相对路径，如 /app/output/article.md）
        content: The RAW, COMPLETE generated text to be written to the file. CRITICAL: This MUST BE the full-length technical article. You MUST NOT put the structured delivery report, summary, or conversational reply in this argument. Pass the exact markdown article content here.

    Returns:
        执行状态 JSON（含路径、字节数），不含文件内容
    """
    try:
        path = Path(filepath)
        # 自动创建父目录
        path.parent.mkdir(parents=True, exist_ok=True)

        # 写入文件
        bytes_written = path.write_text(content, encoding="utf-8")

        abs_path = str(path.absolute())
        logger.info(f"[SystemTool] 文件写入成功: {abs_path} ({bytes_written} bytes)")

        # v12.0: 仅返回状态元数据，不返回文件内容
        result = {
            "status": "success",
            "file": abs_path,
            "bytes_written": bytes_written,
            "action": "write",
        }
        return json.dumps(result, ensure_ascii=False)
    except PermissionError as e:
        logger.error(f"[SystemTool] 文件写入权限被拒绝: {filepath}: {e}")
        return json.dumps({
            "status": "error",
            "error": "permission_denied",
            "file": filepath,
            "message": str(e),
        }, ensure_ascii=False)
    except OSError as e:
        logger.error(f"[SystemTool] 文件写入 I/O 错误: {filepath}: {e}")
        return json.dumps({
            "status": "error",
            "error": "io_error",
            "file": filepath,
            "message": str(e),
        }, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[SystemTool] 文件写入异常: {filepath}: {e}")
        return json.dumps({
            "status": "error",
            "error": "unknown",
            "file": filepath,
            "message": str(e),
        }, ensure_ascii=False)


@tool
def read_local_file(filepath: str) -> str:
    """
    读取本地指定路径的文件内容，常用于检查脚本输出或二次确认文件状态。

    Args:
        filepath: 要读取的文件路径

    Returns:
        文件内容字符串（强制 8000 字符截断），或错误信息
    """
    try:
        path = Path(filepath)

        if not path.exists():
            return (
                f"[Error] File not found at {str(path.absolute())}. "
                "DO NOT retry or guess the path. "
                "Stop execution immediately and ask the user to provide the correct path."
            )

        if not path.is_file():
            return (
                f"[Error] Path is not a file: {str(path.absolute())}. "
                "DO NOT retry or guess the path. "
                "Stop execution immediately and ask the user to provide a valid file path."
            )

        # v12.0: 上下文保护 — 强制 8000 字符截断（之前是 10MB）
        file_size = path.stat().st_size

        content = path.read_text(encoding="utf-8")
        abs_path = str(path.absolute())
        original_length = len(content)
        logger.info(f"[SystemTool] 文件读取成功: {abs_path} ({original_length} chars)")

        # v12.0: 强制截断
        truncated = original_length > _READ_FILE_MAX_CHARS
        if truncated:
            content = content[:_READ_FILE_MAX_CHARS]

        # 返回文件头信息 + 截断后的内容
        result = (
            f"📄 文件内容（{abs_path}）:\n"
            f"   原始大小: {original_length} 字符 / {file_size} 字节\n"
        )
        if truncated:
            result += f"   ⚠️ [Truncated] 仅显示前 {_READ_FILE_MAX_CHARS} 字符（原始 {original_length} 字符）\n"
        result += (
            f"{'=' * 50}\n"
            f"{content}"
        )
        return result
    except UnicodeDecodeError:
        # 尝试二进制读取（对于非文本文件）
        try:
            path = Path(filepath)
            binary = path.read_bytes()
            return (
                f"⚠️ 文件包含二进制数据，无法以文本方式完整显示。\n"
                f"   路径: {str(path.absolute())}\n"
                f"   大小: {len(binary)} 字节\n"
                f"   前512字节 Hex: {binary[:512].hex()[:200]}..."
            )
        except Exception as e2:
            return f"❌ 读取失败（二进制回退也失败）: {e2}"
    except PermissionError as e:
        return f"❌ 读取失败：权限被拒绝\n   路径: {filepath}\n   错误: {e}"
    except Exception as e:
        logger.error(f"[SystemTool] 文件读取异常: {filepath}: {e}")
        return f"❌ 读取失败：未知错误\n   路径: {filepath}\n   错误: {e}"


# ------------------------------------------------------------------
# 模块 B：终端执行工具
# ------------------------------------------------------------------


@tool
def execute_bash_command(command: str, work_dir: str = ".") -> str:
    """
    在指定的本地工作目录中执行 Bash/Shell 命令（如 python 脚本、npm 命令等）。
    必须等待命令执行完毕并返回终端的 stdout 和 stderr 日志。

    Args:
        command: 要执行的 Shell 命令
        work_dir: 工作目录（相对或绝对路径，默认为当前目录）

    Returns:
        命令执行的完整输出（合并 stdout + stderr），或错误信息
    """
    # 安全限制：阻止明显的危险命令
    dangerous_commands = ["rm -rf /", "rm -rf /*", "mkfs.", ":(){ :|:& };:", "dd if=/dev/zero"]
    for dangerous in dangerous_commands:
        if dangerous in command:
            logger.warning(f"[SystemTool] 危险命令被拦截: {command[:100]}")
            return (
                f"❌ 命令执行被安全策略拦截：检测到危险命令 '{dangerous}'。\n"
                f"   此操作已被系统自动阻止。"
            )

    try:
        # 解析工作目录
        work_path = Path(work_dir).expanduser().resolve()
        if not work_path.exists():
            return f"❌ 执行失败：工作目录不存在\n   路径: {work_path}"
        if not work_path.is_dir():
            return f"❌ 执行失败：路径不是目录\n   路径: {work_path}"

        logger.info(f"[SystemTool] 执行命令: {command[:200]} (work_dir: {work_path})")

        # 使用 subprocess.run 执行命令
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(work_path),
            capture_output=True,
            text=True,
            timeout=_DEFAULT_CMD_TIMEOUT,
            executable="/bin/bash" if sys.platform != "win32" else None,
        )

        # 合并 stdout 和 stderr
        output_parts = []
        if result.stdout:
            output_parts.append(f"[stdout]\n{result.stdout}")
        if result.stderr:
            output_parts.append(f"[stderr]\n{result.stderr}")

        combined = "\n\n".join(output_parts) if output_parts else "(无输出)"

        # 截断过长输出（100KB 上限）
        max_output = 100 * 1024
        if len(combined) > max_output:
            combined = combined[:max_output] + (
                f"\n\n[System: 输出过长，已截断至 {max_output / 1024:.0f} KB。"
                f"原始长度: {len(combined) / 1024:.0f} KB]"
            )

        # 构建返回信息
        status = "成功" if result.returncode == 0 else f"失败 (exit code: {result.returncode})"
        return (
            f"🔧 命令执行{status}\n"
            f"   命令: {command[:200]}{'...' if len(command) > 200 else ''}\n"
            f"   目录: {work_path}\n"
            f"   退出码: {result.returncode}\n"
            f"   耗时: {_DEFAULT_CMD_TIMEOUT}s 超时限制\n"
            f"{'=' * 50}\n"
            f"{combined}"
        )

    except subprocess.TimeoutExpired:
        logger.warning(f"[SystemTool] 命令执行超时 ({_DEFAULT_CMD_TIMEOUT}s): {command[:100]}")
        return (
            f"❌ 命令执行超时\n"
            f"   命令: {command[:200]}\n"
            f"   目录: {work_path}\n"
            f"   超时限制: {_DEFAULT_CMD_TIMEOUT}s\n\n"
            f"命令未能在 {_DEFAULT_CMD_TIMEOUT} 秒内完成，已被自动终止。"
        )
    except PermissionError as e:
        return f"❌ 执行失败：权限被拒绝\n   命令: {command[:100]}\n   目录: {work_path}\n   错误: {e}"
    except FileNotFoundError as e:
        return f"❌ 执行失败：命令不存在\n   命令: {command[:100]}\n   错误: {e}"
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[SystemTool] 命令执行异常: {command[:100]}: {e}\n{tb}")
        return (
            f"❌ 命令执行异常\n"
            f"   命令: {command[:200]}\n"
            f"   错误: {e}\n"
            f"   详情: {tb[:500]}"
        )


# ------------------------------------------------------------------
# 模块 B2：人机交互 Mock 工具
# ------------------------------------------------------------------


@tool
def ask_user_question(question: str, options: Optional[list] = None) -> str:
    """
    [Human-in-the-loop Mock] 向用户提出一个问题并等待选择。
    SKILL.md 中要求在关键决策点向用户确认（如标题选择、格式化方式等）。
    
    当前阶段采用 Auto-Mock 策略：自动模拟用户批准默认选项，返回允许继续的信号。
    TODO: 未来接入 LangGraph interrupt / yield 机制后，将替换为真正的
          暂停-等待-恢复 流程，并支持 websocket/polling 回调。
    
    Args:
        question: 要向用户提出的问题文本
        options: 可选的选项列表，用户可以从中选择（如 ["Option A", "Option B"]）
    
    Returns:
        Auto-Mock 响应字符串，模拟用户已确认并授权继续执行
    """
    logger.info(
        f"[AskUserQuestion Mock] 模拟用户确认 - 问题: {question[:200]}"
        + (f" | 选项: {options}" if options else "")
    )
    
    result = (
        "System Auto-Reply: User approved default execution. "
        "Proceed to the next step."
    )
    
    logger.info(f"[AskUserQuestion Mock] 返回: {result}")
    return result


# ------------------------------------------------------------------
# 模块 C：全局工具注册
# ------------------------------------------------------------------

# 所有系统工具的列表（供 tool_manager 注册使用）
SYSTEM_TOOLS = [
    write_local_file,
    read_local_file,
    execute_bash_command,
    ask_user_question,
]


def register_all() -> int:
    """
    将所有系统工具注册到全局 ToolManager。

    在应用启动时调用（main.py 的 lifespan startup 阶段）。
    已注册的工具不会重复注册。

    Returns:
        成功注册的工具数量
    """
    from app.services.tool_manager import tool_manager, ToolDescriptor

    count = 0
    for tool_fn in SYSTEM_TOOLS:
        name = tool_fn.name
        # 检查是否已注册
        existing = tool_manager.get_tool(name)
        if existing:
            logger.debug(f"[SystemTools] 工具已存在，跳过: {name}")
            continue

        desc = ToolDescriptor(
            name=name,
            display_name=name,
            description=tool_fn.description,
            tool_type="builtin",
            tool_instance=tool_fn,
            is_enabled=True,
        )
        tool_manager.register_tool(desc)
        count += 1
        logger.info(f"[SystemTools] 已注册系统工具: {name}")

    if count > 0:
        logger.info(f"[SystemTools] 注册完成: 新增 {count} 个系统工具")
    return count
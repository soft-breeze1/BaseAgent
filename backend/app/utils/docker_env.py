"""
Docker 环境检测与路径适配工具 (v7.0)
=====================================
为 BaseAgent 提供 Docker 环境下的 MCP 文件系统路径自动转换能力。

核心功能：
  1. 环境检测 — 判断当前是否运行在 Docker 容器中
  2. 路径转换 — 将宿主机 Windows 路径（C:\\xxx）自动转换为容器内路径（/app/workspace/xxx）
  3. 错误增强 — 文件系统"路径不存在"错误时提示 Docker 上下文
  4. 提示注入 — 生成 Docker 环境说明供系统提示词使用

设计原则：
  - 最小改动：不修改 MCP 协议核心逻辑
  - 无感适配：非 Docker 环境下所有功能不受影响
  - 清晰提示：所有错误提示明确引导用户正确操作
"""
import logging
import os
import re
import json
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Docker 环境检测 ─────────────────────────────────────────────────────────

# Docker 容器内标记文件
_DOCKER_ENV_FILE = "/.dockerenv"

# Docker 容器默认工作目录
DEFAULT_DOCKER_WORKSPACE = "/app/workspace"

# 宿主机 Windows 驱动器号正则
_WINDOWS_DRIVE_RE = re.compile(r'^[A-Za-z]:[\\/]')

# 文件系统工具名称列表（需要路径转换的工具）
FILESYSTEM_TOOL_NAMES = {
    "read_file",
    "read_multiple_files",
    "write_file",
    "edit_file",
    "create_directory",
    "list_directory",
    "directory_tree",
    "move_file",
    "search_files",
    "get_file_info",
    "list_allowed_directories",
}


def _is_docker() -> bool:
    """
    检测当前进程是否运行在 Docker 容器中。

    检测策略（按优先级）：
      1. /.dockerenv 文件存在（最可靠的 Docker 标记）
      2. /proc/1/cgroup 包含 /docker/ 或 /.containerenv（Linux 特有）
      3. 环境变量 DOCKER_CONTAINER=true（手动设置）

    Returns:
        True 表示在 Docker 容器中运行，False 表示在宿主机运行
    """
    if os.path.exists(_DOCKER_ENV_FILE):
        return True

    try:
        if os.path.exists("/proc/1/cgroup"):
            with open("/proc/1/cgroup", "r") as f:
                content = f.read()
                if "/docker/" in content or "/lxc/" in content or ".containerenv" in content:
                    return True
    except (IOError, OSError):
        pass

    if os.environ.get("DOCKER_CONTAIN", "").lower() in ("true", "1", "yes"):
        return True

    return False


def is_docker() -> bool:
    """
    公开接口：检测当前是否运行在 Docker 容器中。

    使用缓存减少重复检测开销。

    Returns:
        True 表示在 Docker 容器中
    """
    if not hasattr(is_docker, "_cached"):
        is_docker._cached = _is_docker()
    return is_docker._cached


# ── 路径转换 ────────────────────────────────────────────────────────────────


def is_windows_path(path: str) -> bool:
    """
    判断一个路径是否为 Windows 风格路径。

    Args:
        path: 路径字符串

    Returns:
        True 表示是 Windows 风格路径（如 C:\\xxx 或 D:/xxx）
    """
    if not path:
        return False
    return bool(_WINDOWS_DRIVE_RE.match(path.replace("\\", "/")))


def convert_windows_path(path: str) -> str:
    """
    将宿主机 Windows 路径转换为容器内路径。

    转换规则：
      1. C:\\xxx -> /app/workspace/xxx
      2. D:\\xxx -> /app/workspace/xxx
      3. 已符合容器路径格式的不作转换
      4. 相对路径不作转换

    Args:
        path: 原始路径（可能是 Windows 格式或容器格式）

    Returns:
        转换后的容器内路径
    """
    if not path or not is_windows_path(path):
        return path

    normalized = path.replace("\\", "/")

    if len(normalized) >= 2 and normalized[1] == ":":
        rest = normalized[2:]
        rest = rest.lstrip("/")
        converted = f"{DEFAULT_DOCKER_WORKSPACE}/{rest}"
        logger.info(f"[Docker Path] Converted '{path}' -> '{converted}'")
        return converted

    return path


def maybe_convert_args(
    arguments: dict,
    server_name: str,
) -> dict:
    """
    在 Docker 环境下，自动转换工具参数中的 Windows 路径。

    仅在以下条件同时满足时执行转换：
      1. 当前运行在 Docker 容器中
      2. 参数中包含路径字段且值为 Windows 路径

    Supports:
      - 字符串路径字段：path, paths, directory, source, destination, origin, target, file
      - 路径列表字段：paths 为字符串数组

    Args:
        arguments: 工具参数字典
        server_name: MCP Server 名称（用于日志）

    Returns:
        转换后的参数字典（非 Docker 环境或无需转换时返回原对象）
    """
    if not is_docker():
        return arguments

    has_windows_path = any(
        _is_path_field(k) and isinstance(v, str) and is_windows_path(v)
        for k, v in arguments.items()
    )
    has_windows_path_list = any(
        _is_path_field(k) and isinstance(v, list) and any(
            isinstance(item, str) and is_windows_path(item) for item in v
        )
        for k, v in arguments.items()
    )

    if not has_windows_path and not has_windows_path_list:
        return arguments

    converted = {}
    for key, value in arguments.items():
        if _is_path_field(key) and isinstance(value, str):
            converted[key] = convert_windows_path(value)
        elif _is_path_field(key) and isinstance(value, list):
            converted[key] = [
                convert_windows_path(item) if isinstance(item, str) and is_windows_path(item)
                else item
                for item in value
            ]
        else:
            converted[key] = value

    if converted != arguments:
        logger.info(
            f"[Docker Path] Converted args for server '{server_name}': "
            f"{json.dumps(arguments, ensure_ascii=False)[:300]} -> "
            f"{json.dumps(converted, ensure_ascii=False)[:300]}"
        )

    return converted


def _is_path_field(field_name: str) -> bool:
    """判断字段名是否可能是路径字段。"""
    path_keywords = {"path", "paths", "directory", "source", "destination", "origin", "target", "file"}
    field_lower = field_name.lower()
    return field_lower in path_keywords or field_lower.endswith("_path") or field_lower.endswith("_dir")


# ── 错误处理增强 ────────────────────────────────────────────────────────────


def enhance_error_message(
    server_name: str,
    tool_name: str,
    error_message: str,
) -> str:
    """
    增强文件系统工具的错误信息，加入 Docker 环境提示。

    当 MCP 工具返回"路径不存在"等错误时，自动添加 Docker 上下文说明。

    Args:
        server_name: MCP Server 名称
        tool_name: 工具名称
        error_message: 原始错误信息

    Returns:
        增强后的错误信息（非 Docker 环境或非文件系统错误时返回原信息）
    """
    if not is_docker():
        return error_message

    is_filesystem_error = (
        "filesystem" in server_name.lower()
        or any(fs_tool in tool_name for fs_tool in FILESYSTEM_TOOL_NAMES)
    )
    if not is_filesystem_error:
        return error_message

    path_error_keywords = [
        "not found", "不存在", "no such", "cannot find",
        "does not exist", "没有那个", "not a directory",
        "不是一个目录", "permission denied", "权限不足",
        "EACCES", "ENOENT", "EISDIR", "ENOTDIR",
        "readdir", "readfile",
    ]

    should_enhance = any(
        kw.lower() in error_message.lower()
        for kw in path_error_keywords
    )

    if not should_enhance:
        return error_message

    docker_hint = (
        "\n\n⚠️ Docker 环境提示：当前系统运行在 Docker 容器中。"
        f"\n   • 容器内只能访问工作目录 {DEFAULT_DOCKER_WORKSPACE}/ 下的文件。"
        f"\n   • 宿主机路径（如 C:\\xxx）在容器内不可用。"
        f"\n   • 请将路径修改为容器内路径，如 {DEFAULT_DOCKER_WORKSPACE}/your-file。"
        f"\n   • 如需访问宿主机文件，请在 docker-compose.yml 中配置 volumes 映射。"
    )

    enhanced = f"{error_message}{docker_hint}"
    logger.info(f"[Docker Error] Enhanced error for '{server_name}/{tool_name}'")
    return enhanced


# ── 系统提示词注入 ─────────────────────────────────────────────────────────


_DOCKER_SYSTEM_PROMPT_SUFFIX = """
## Docker Environment
This agent is running inside a Docker container.

### File Path Rules
1. All file operations must use container-mapped paths under `/app/workspace/` or `/app/data/uploads/`.
2. If a tool returns "path not found", first verify the path uses the correct container prefix.
3. Windows/macOS local paths (e.g., `C:\\Users\\...` or `/Users/...`) are not accessible inside the container unless explicitly mapped in `docker-compose.yml`. Convert them to `/app/workspace/<basename>` if a volume mapping exists.
"""


def get_docker_prompt_suffix() -> str:
    """
    获取 Docker 环境描述文本，用于注入到系统提示词中。

    非 Docker 环境返回空字符串。

    Returns:
        Docker 环境描述文本（空字符串表示非 Docker 环境）
    """
    if not is_docker():
        return ""
    return _DOCKER_SYSTEM_PROMPT_SUFFIX
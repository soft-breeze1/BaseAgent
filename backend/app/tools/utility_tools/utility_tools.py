"""
Utility Tools 工具包（7 个工业级实用工具）
===========================================
为 Agent 提供网页抓取、Python 沙盒、文档读取、HTTP 请求、
Markdown→HTML 转换、图片元数据以及二维码生成能力。

模块划分:
  工具 1 - web_fetch_content:    网页全文本深度抓取
  工具 2 - python_executor:      安全 Python 代码沙盒执行
  工具 3 - document_reader:      多格式本地文档读取 (PDF/DOCX/CSV/TXT)
  工具 4 - http_client_request:  万能 HTTP 客户端请求
  工具 5 - markdown_to_html:     Markdown → HTML 转换
  工具 6 - image_metadata_extractor: 图片元数据提取
  工具 7 - qr_code_generator:    二维码生成器
  工具 8 - image_search:    图片搜索（百度图片 AJAX 接口，无需 API Key）

模块 E - 全局注册: utility_tools.register_all() 注册到 ToolManager
"""

import io
import os
import sys
import csv
import json
import logging
import traceback
import subprocess
import threading
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# 允许用户代码访问的安全内置模块
_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bin": bin, "bool": bool,
    "chr": chr, "complex": complex, "dict": dict, "divmod": divmod,
    "enumerate": enumerate, "filter": filter, "float": float,
    "format": format, "frozenset": frozenset, "globals": globals,
    "hash": hash, "hex": hex, "int": int, "isinstance": isinstance,
    "issubclass": issubclass, "iter": iter, "len": len, "list": list,
    "locals": locals, "map": map, "max": max, "min": min, "next": next,
    "object": object, "oct": oct, "ord": ord, "pow": pow, "print": print,
    "range": range, "repr": repr, "reversed": reversed, "round": round,
    "set": set, "slice": slice, "sorted": sorted, "str": str,
    "sum": sum, "tuple": tuple, "type": type, "zip": zip,
    "__import__": __import__,
}

# 禁止进入沙盒的危险内置
_DANGEROUS_BUILTINS = {
    "eval", "exec", "compile", "open", "execfile", "input",
    "__import__",  # 限制 import 访问
}


# ===================================================================
# 工具 1: 网页全文本深度抓取
# ===================================================================

@tool
def web_fetch_content(url: str) -> str:
    """
    对指定 URL 执行全文本深度抓取，提取网页正文区的核心可读文本内容。
    适用于需要获取完整文章、文档或页面细节的场景（Tavily 仅返回摘要，
    此工具可补充抓取完整正文）。

    Args:
        url: 要抓取的完整网页 URL（必须以 http:// 或 https:// 开头）

    Returns:
        网页正文的纯文本内容（最长 8000 字符），或详细的错误描述
    """
    import httpx

    try:
        logger.info(f"[UtilityTool] web_fetch_content: {url[:200]}")
        resp = httpx.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            follow_redirects=True,
            timeout=30.0,
        )
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            return (
                f"⚠️ 响应不是 HTML 页面（Content-Type: {content_type}）。\n"
                f"如需直接获取原始数据，请使用 http_client_request 工具。"
            )

        html_text = resp.text
        logger.info(f"[UtilityTool] web_fetch_content: 已下载 HTML ({len(html_text)} bytes)")

        # 使用 BeautifulSoup 提取正文
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return (
                "❌ BeautifulSoup 库未安装，无法解析 HTML。\n"
                f"原始 HTML 大小: {len(html_text)} bytes\n"
                "请在 Docker 中安装: pip install beautifulsoup4"
            )

        soup = BeautifulSoup(html_text, "html.parser")

        # 移除干扰标签
        for tag_name in ("script", "style", "nav", "header", "footer",
                         "aside", "noscript", "iframe", "svg", "form",
                         "button", "select", "input", "textarea"):
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # 移除隐藏元素
        for tag in soup.find_all(True):
            if tag.attrs is None:
                continue
            if tag.has_attr("style"):
                style = tag["style"].lower()
                if "display:none" in style or "visibility:hidden" in style:
                    tag.decompose()
                    continue
            if tag.has_attr("hidden"):
                tag.decompose()
                continue
            if tag.has_attr("aria-hidden") and tag["aria-hidden"] == "true":
                tag.decompose()
                continue

        # 提取标题
        title = ""
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)

        # 提取正文
        body = soup.find("body")
        if body:
            text = body.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)

        # 清理空行
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        clean_text = "\n".join(lines)

        # 构建输出
        output_parts = []
        if title:
            output_parts.append(f"# {title}")
        output_parts.append(f"来源: {url}")
        output_parts.append("---")
        output_parts.append(clean_text)

        result = "\n\n".join(output_parts)

        # 限制长度
        max_chars = 8000
        if len(result) > max_chars:
            result = result[:max_chars] + (
                f"\n\n[System: 内容过长，已截断至 {max_chars} 字符。"
                f"原始长度: {len(result)} 字符]"
            )

        return result

    except httpx.TimeoutException:
        return (
            f"❌ 网页抓取超时（30 秒）\n"
            f"   URL: {url}\n"
            f"   该页面响应较慢，请稍后重试或检查 URL 是否正确。"
        )
    except httpx.HTTPStatusError as e:
        return (
            f"❌ 网页返回错误状态码\n"
            f"   URL: {url}\n"
            f"   状态码: {e.response.status_code}\n"
            f"   原因: {e.response.reason_phrase}"
        )
    except httpx.RequestError as e:
        return (
            f"❌ 网络请求失败\n"
            f"   URL: {url}\n"
            f"   错误: {e}"
        )
    except Exception as e:
        logger.error(f"[UtilityTool] web_fetch_content 异常: {e}", exc_info=True)
        return f"❌ 网页抓取异常: {str(e)}"


# ===================================================================
# 工具 2: 安全 Python 代码沙盒执行器
# ===================================================================

@tool
def python_executor(code: str) -> str:
    """
    在受控的沙盒环境中执行 Python 代码，捕获 stdout 输出并返回。
    适用于复杂数学计算、数据处理、字符串变换等 LLM 不擅长的逻辑操作。
    代码将在 3 秒超时后自动终止以防范死循环。

    Args:
        code: 要执行的 Python 代码字符串（支持多行）

    Returns:
        代码执行的标准输出（stdout），或错误堆栈信息
    """
    import ast

    # ── 静态代码安全检查 ──
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return (
            f"❌ Python 语法错误\n"
            f"   行号: {e.lineno}\n"
            f"   错误: {e.msg}\n"
            f"   详情: {e.text}"
        )

    # 禁止 import 危险模块
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("os", "subprocess", "shutil", "sys",
                                  "socket", "ctypes", "multiprocessing",
                                  "threading", "signal"):
                    return (
                        f"❌ 安全策略禁止导入模块: '{alias.name}'\n"
                        f"   沙盒环境禁止使用系统/网络/进程模块。\n"
                        f"   允许的模块: math, json, re, collections, "
                        f"datetime, random, statistics, itertools, functools 等纯 Python 库。"
                    )
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            for alias in node.names:
                full_name = f"{module_name}.{alias.name}" if module_name else alias.name
                dangerous_keywords = ("os", "subprocess", "shutil", "sys",
                                      "socket", "ctypes", "multiprocessing")
                if any(dk in full_name for dk in dangerous_keywords):
                    return (
                        f"❌ 安全策略禁止从 '{module_name}' 导入 '{alias.name}'\n"
                        f"   沙盒环境禁止使用系统/网络/进程模块。"
                    )

    # ── 执行代码并捕获 stdout ──
    stdout_capture = io.StringIO()
    old_stdout = sys.stdout
    result_container = {"output": "", "error": ""}
    exception_container = [None]

    def run_code():
        try:
            sys.stdout = stdout_capture
            local_vars = {"__builtins__": _SAFE_BUILTINS}
            exec(code, {"__builtins__": _SAFE_BUILTINS}, local_vars)
        except Exception as e:
            exception_container[0] = e
        finally:
            sys.stdout = old_stdout

    thread = threading.Thread(target=run_code, daemon=True)
    thread.start()
    thread.join(timeout=3.0)

    if thread.is_alive():
        # 线程仍然存活 = 超时
        return (
            f"❌ 代码执行超时（3 秒限制）\n"
            f"   代码:\n{code[:500]}{'...' if len(code) > 500 else ''}\n\n"
            f"   提示: 代码未能在 3 秒内完成，可能包含死循环或耗时过长操作。"
        )

    if exception_container[0] is not None:
        exc = exception_container[0]
        tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
        # 过滤掉 exec 内部框架，只保留用户代码的 traceback
        filtered_tb = []
        for line in tb_lines:
            if 'exec(code' not in line and '<string>' in line:
                filtered_tb.append(line)
            elif 'exec(code' in line:
                continue
            elif 'Traceback' in line:
                filtered_tb.append(line)
            elif 'Exception' in line or 'Error' in line:
                filtered_tb.append(line)
        tb_text = "".join(filtered_tb) or "".join(tb_lines)
        return (
            f"❌ 代码执行异常\n"
            f"   类型: {type(exc).__name__}\n"
            f"   信息: {exc}\n"
            f"   堆栈:\n{tb_text}"
        )

    output = stdout_capture.getvalue()
    if output.strip():
        return f"✅ 代码执行成功\n\n[stdout]\n{output}"
    else:
        return "✅ 代码执行成功（无输出）"


# ===================================================================
# 工具 3: 多格式本地文档读取器
# ===================================================================

@tool
def document_reader(file_path: str) -> str:
    """
    读取 Docker 工作区中的文档文件并提取文本内容。
    支持 PDF、DOCX、CSV、TXT 四种常见格式。
    路径必须基于 /app/workspace/output 目录。

    Args:
        file_path: 文件路径（绝对路径，应位于 /app/workspace/output/ 下）

    Returns:
        文件的纯文本内容，或明确的错误描述
    """
    try:
        path = Path(file_path)
        abs_path = str(path.absolute())

        if not path.exists():
            return (
                f"❌ 文件不存在\n"
                f"   路径: {abs_path}\n"
                f"   提示: 请确认文件已上传至 Docker 工作区。"
            )

        if not path.is_file():
            return f"❌ 路径不是文件\n   路径: {abs_path}"

        # 限制文件大小（50MB）
        file_size = path.stat().st_size
        max_size = 50 * 1024 * 1024
        if file_size > max_size:
            return (
                f"❌ 文件过大 ({file_size / 1024 / 1024:.1f} MB)\n"
                f"   路径: {abs_path}\n"
                f"   最大支持: {max_size / 1024 / 1024} MB"
            )

        suffix = path.suffix.lower()

        # ── TXT ──
        if suffix == ".txt":
            try:
                import chardet
                raw = path.read_bytes()
                detected = chardet.detect(raw)
                encoding = detected.get("encoding", "utf-8") or "utf-8"
                text = raw.decode(encoding, errors="replace")
            except ImportError:
                text = path.read_text(encoding="utf-8", errors="replace")

            return (
                f"📄 文本文件内容\n"
                f"   路径: {abs_path}\n"
                f"   大小: {len(text)} 字符 / {file_size} 字节\n"
                f"{'=' * 50}\n"
                f"{text}"
            )

        # ── PDF ──
        elif suffix == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError:
                return f"❌ pypdf 库未安装，无法读取 PDF 文件。\n   路径: {abs_path}"

            try:
                reader = PdfReader(str(path))
                num_pages = len(reader.pages)
                text_parts = []
                for i, page in enumerate(reader.pages, 1):
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        text_parts.append(f"[第 {i}/{num_pages} 页]\n{page_text.strip()}")
                text = "\n\n".join(text_parts)

                if not text.strip():
                    return (
                        f"⚠️ PDF 文件无可提取的文本内容（可能是扫描件）\n"
                        f"   路径: {abs_path}\n"
                        f"   页数: {num_pages}\n"
                        f"   提示: 扫描件需要 OCR 处理，当前不支持。"
                    )

                max_chars = 10000
                if len(text) > max_chars:
                    text = text[:max_chars] + (
                        f"\n\n[System: 内容过长，已截断至 {max_chars} 字符。"
                        f"原始长度: {len(text)} 字符]"
                    )

                return (
                    f"📄 PDF 文件内容（{num_pages} 页）\n"
                    f"   路径: {abs_path}\n"
                    f"   大小: {file_size / 1024:.1f} KB\n"
                    f"{'=' * 50}\n"
                    f"{text}"
                )
            except Exception as e:
                return f"❌ PDF 读取失败: {str(e)}\n   路径: {abs_path}"

        # ── DOCX ──
        elif suffix == ".docx":
            try:
                from docx import Document
            except ImportError:
                return f"❌ python-docx 库未安装，无法读取 DOCX 文件。\n   路径: {abs_path}"

            try:
                doc = Document(str(path))
                paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                text = "\n".join(paragraphs)

                if not text.strip():
                    return (
                        f"⚠️ DOCX 文件为空或无可提取的文本\n"
                        f"   路径: {abs_path}"
                    )

                max_chars = 10000
                if len(text) > max_chars:
                    text = text[:max_chars] + (
                        f"\n\n[System: 内容过长，已截断至 {max_chars} 字符。"
                        f"原始长度: {len(text)} 字符]"
                    )

                return (
                    f"📄 DOCX 文件内容（{len(paragraphs)} 段落）\n"
                    f"   路径: {abs_path}\n"
                    f"   大小: {file_size / 1024:.1f} KB\n"
                    f"{'=' * 50}\n"
                    f"{text}"
                )
            except Exception as e:
                return f"❌ DOCX 读取失败: {str(e)}\n   路径: {abs_path}"

        # ── CSV ──
        elif suffix == ".csv":
            try:
                with open(str(path), "r", encoding="utf-8", errors="replace") as f:
                    sample = f.read(8192)
                    dialect = csv.Sniffer().sniff(sample)
                    f.seek(0)
                    reader = csv.reader(f, dialect)
                    rows = list(reader)
            except Exception:
                try:
                    raw_text = path.read_text(encoding="utf-8", errors="replace")
                    rows = [line.split(",") for line in raw_text.strip().split("\n") if line.strip()]
                except Exception as e2:
                    return f"❌ CSV 读取失败: {str(e2)}\n   路径: {abs_path}"

            if not rows:
                return f"⚠️ CSV 文件为空\n   路径: {abs_path}"

            max_rows = 100
            display_rows = rows[:max_rows]
            header = display_rows[0] if display_rows else []
            data_rows = display_rows[1:] if len(display_rows) > 1 else []

            output_parts = [
                f"📄 CSV 文件内容（共 {len(rows)} 行" + (
                    f"，显示前 {max_rows} 行）" if len(rows) > max_rows else "）"
                ),
                f"   路径: {abs_path}",
                f"   列数: {len(header)}",
                f"   分隔符: {getattr(dialect, 'delimiter', ',')}",
                f"{'=' * 50}",
            ]

            if header:
                output_parts.append(f"列名: {' | '.join(header)}")
                output_parts.append("---")

            for i, row in enumerate(data_rows, 1):
                output_parts.append(f"行 {i}: {' | '.join(row)}")

            if len(rows) > max_rows:
                output_parts.append(f"\n... 剩余 {len(rows) - max_rows} 行已省略 ...")

            return "\n".join(output_parts)

        else:
            return (
                f"❌ 不支持的文件格式: '{suffix}'\n"
                f"   路径: {abs_path}\n"
                f"   支持的格式: .pdf, .docx, .csv, .txt"
            )

    except PermissionError as e:
        return f"❌ 文件读取权限被拒绝\n   路径: {file_path}\n   错误: {e}"
    except Exception as e:
        logger.error(f"[UtilityTool] document_reader 异常: {e}", exc_info=True)
        return f"❌ 文件读取异常: {str(e)}\n   路径: {file_path}"


# ===================================================================
# 工具 4: 万能 HTTP 客户端请求
# ===================================================================

@tool
def http_client_request(
    method: str = "GET",
    url: str = "",
    headers: Optional[dict] = None,
    json_data: Optional[dict] = None,
) -> str:
    """
    发送通用 HTTP 请求到指定 API 端点。适用于测试外部 API、发送 Webhook、
    或与非标准第三方服务交互。

    Args:
        method: HTTP 方法（GET / POST / PUT / DELETE / PATCH / HEAD）
        url: 完整的目标 URL
        headers: 可选的 HTTP 请求头字典（如 {"Authorization": "Bearer xxx"}）
        json_data: 可选的 JSON 请求体字典（仅 POST/PUT/PATCH 有效）

    Returns:
        格式化后的 HTTP 响应（状态码 + 响应体 JSON/文本）
    """
    import httpx

    if not url:
        return "❌ 参数错误：url 不能为空，请提供完整的请求 URL。"

    method_upper = method.upper().strip()
    valid_methods = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD")
    if method_upper not in valid_methods:
        return (
            f"❌ 不支持的 HTTP 方法: '{method}'\n"
            f"   支持的方法: {', '.join(valid_methods)}"
        )

    safe_headers = dict(headers) if headers else {}
    if "User-Agent" not in safe_headers and "user-agent" not in safe_headers:
        safe_headers["User-Agent"] = "BaseAgent-HTTP-Client/1.0"

    try:
        logger.info(f"[UtilityTool] HTTP {method_upper} {url[:200]}")

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.request(
                method=method_upper,
                url=url,
                headers=safe_headers or None,
                json=json_data if method_upper in ("POST", "PUT", "PATCH") else None,
            )

        status = response.status_code
        status_category = f"{status // 100}xx"
        status_text_map = {
            "2xx": "success",
            "3xx": "redirect",
            "4xx": "client_error",
            "5xx": "server_error",
        }
        status_label = status_text_map.get(status_category, "unknown")

        content_type = response.headers.get("content-type", "")
        body_output = ""

        if "application/json" in content_type:
            try:
                resp_json = response.json()
                body_output = json.dumps(resp_json, ensure_ascii=False, indent=2)
            except (json.JSONDecodeError, ValueError):
                body_output = response.text[:3000]
        elif "text/" in content_type:
            body_output = response.text[:3000]
        else:
            body_output = f"[二进制数据] 大小: {len(response.content)} bytes"

        max_body = 5000
        if len(body_output) > max_body:
            body_output = body_output[:max_body] + (
                f"\n\n[System: 响应过长，已截断至 {max_body} 字符。"
                f"原始长度: {len(body_output)} 字符]"
            )

        result_parts = [
            f"🌐 HTTP {method_upper} 响应",
            f"   URL: {url}",
            f"   状态码: {status} ({status_label})",
            f"   响应头: {dict(response.headers)}"[:500],
            f"{'=' * 50}",
        ]
        if body_output:
            result_parts.append(body_output)

        return "\n\n".join(result_parts)

    except httpx.TimeoutException:
        return (
            f"❌ HTTP 请求超时（30 秒）\n"
            f"   方法: {method_upper}\n"
            f"   URL: {url}\n"
            f"   提示: 目标服务器响应较慢，请稍后重试。"
        )
    except httpx.ConnectError as e:
        return (
            f"❌ 无法连接到目标服务器\n"
            f"   方法: {method_upper}\n"
            f"   URL: {url}\n"
            f"   错误: {e}\n"
            f"   提示: 请检查 URL 是否正确以及网络是否可达。"
        )
    except httpx.RequestError as e:
        return (
            f"❌ HTTP 请求失败\n"
            f"   方法: {method_upper}\n"
            f"   URL: {url}\n"
            f"   错误: {e}"
        )
    except Exception as e:
        logger.error(f"[UtilityTool] http_client_request 异常: {e}", exc_info=True)
        return f"❌ HTTP 请求异常: {str(e)}"


# ===================================================================
# 工具 5: Markdown → HTML 转换器
# ===================================================================

@tool
def markdown_to_html(markdown_text: str) -> str:
    """
    将 Markdown 格式的文本渲染为整洁的 HTML 片段。
    适用于幻灯片制作、文章发布、富文本展示等场景。
    启用 extra 和 codehilite 扩展以支持表格、代码高亮等高级语法。

    Args:
        markdown_text: 要转换的 Markdown 原始文本

    Returns:
        渲染后的 HTML 字符串（包含基础 CSS 样式）
    """
    if not markdown_text or not markdown_text.strip():
        return "❌ 输入为空：请提供 Markdown 文本。"

    import markdown as md_lib

    try:
        html_body = md_lib.markdown(
            markdown_text,
            extensions=[
                "extra",
                "codehilite",
                "toc",
                "nl2br",
                "sane_lists",
            ],
        )

        full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    line-height: 1.6;
    color: #333;
    max-width: 900px;
    margin: 0 auto;
    padding: 20px;
}}
h1, h2, h3, h4, h5, h6 {{ margin-top: 1.5em; margin-bottom: 0.5em; color: #1a1a1a; font-weight: 600; }}
h1 {{ font-size: 2em; border-bottom: 2px solid #eee; padding-bottom: 0.3em; }}
h2 {{ font-size: 1.5em; border-bottom: 1px solid #eee; padding-bottom: 0.2em; }}
p {{ margin: 1em 0; }}
code {{ background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace; }}
pre code {{ display: block; padding: 12px 16px; overflow-x: auto; background: #f8f8f8; border: 1px solid #e0e0e0; border-radius: 4px; line-height: 1.45; }}
blockquote {{ margin: 1em 0; padding: 0.5em 1em; border-left: 4px solid #4a9eff; background: #f8fbff; color: #555; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
th {{ background: #f0f0f0; font-weight: 600; }}
tr:nth-child(even) {{ background: #fafafa; }}
ul, ol {{ margin: 0.5em 0; padding-left: 2em; }}
img {{ max-width: 100%; height: auto; }}
a {{ color: #4a9eff; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

        logger.info(f"[UtilityTool] markdown_to_html: 生成 {len(full_html)} 字符 HTML")
        return (
            f"✅ Markdown 转换成功\n"
            f"   输入: {len(markdown_text)} 字符\n"
            f"   输出: {len(full_html)} 字符 HTML\n"
            f"{'=' * 50}\n"
            f"{full_html}"
        )

    except Exception as e:
        logger.error(f"[UtilityTool] markdown_to_html 异常: {e}", exc_info=True)
        return f"❌ Markdown 转换失败: {str(e)}"


# ===================================================================
# 工具 6: 图片元数据与尺寸提取器
# ===================================================================

@tool
def image_metadata_extractor(image_path: str) -> str:
    """
    获取本地图片文件的基本信息（尺寸、格式、色彩模式、文件大小等），
    不将完整图片加载到内存。适用于前端排版前的图片信息预检。

    Args:
        image_path: 图片文件的绝对路径（应位于 /app/workspace/ 下）

    Returns:
        图片元数据的结构化描述，或错误信息
    """
    try:
        path = Path(image_path)
        abs_path = str(path.absolute())

        if not path.exists():
            return f"❌ 图片文件不存在\n   路径: {abs_path}\n   提示: 请确认文件已上传至工作区。"
        if not path.is_file():
            return f"❌ 路径不是文件\n   路径: {abs_path}"

        file_size = path.stat().st_size
        file_size_str = _format_file_size(file_size)

        from PIL import Image

        with Image.open(str(path)) as img:
            width, height = img.size
            img_format = img.format or "未知"
            img_mode = img.mode
            is_animated = getattr(img, "is_animated", False)
            n_frames = getattr(img, "n_frames", 1)

            exif_info = {}
            try:
                exif = img._exif if hasattr(img, "_exif") else img.getexif()
                if exif:
                    exif_tags = {
                        271: "make", 272: "model", 296: "resolution_unit",
                        282: "x_resolution", 283: "y_resolution",
                        306: "datetime", 36867: "original_datetime",
                    }
                    for tag_id, tag_name in exif_tags.items():
                        if tag_id in exif:
                            exif_info[tag_name] = str(exif[tag_id])
            except Exception:
                pass

        result_parts = [
            f"🖼️ 图片元数据",
            f"   路径: {abs_path}",
            f"   尺寸: {width} × {height} 像素",
            f"   格式: {img_format}",
            f"   色彩模式: {img_mode}",
            f"   文件大小: {file_size_str} ({file_size} bytes)",
        ]

        if is_animated:
            result_parts.append(f"   动画帧数: {n_frames} 帧")
        if exif_info:
            exif_lines = [f"   {k}: {v}" for k, v in exif_info.items()]
            result_parts.append(f"   EXIF 信息:")
            result_parts.extend(exif_lines)

        if height > 0:
            aspect_ratio = width / height
            result_parts.append(f"   宽高比: {aspect_ratio:.2f}")

        if width >= 1920 and height >= 1080:
            result_parts.append(f"   推荐用途: 高清壁纸/全屏展示")
        elif width >= 800 and height >= 600:
            result_parts.append(f"   推荐用途: 网页插图/文档配图")
        elif width >= 200 and height >= 200:
            result_parts.append(f"   推荐用途: 缩略图/小图标")
        else:
            result_parts.append(f"   推荐用途: 小尺寸图标/装饰元素")

        return "\n".join(result_parts)

    except ImportError:
        return f"❌ Pillow 库未安装，无法处理图片。\n   路径: {image_path}\n   请在 Docker 中安装: pip install Pillow"
    except (OSError, ValueError) as e:
        return f"❌ 无法识别图片文件\n   路径: {image_path}\n   错误: {e}\n   提示: 文件可能已损坏或不是图片格式。"
    except PermissionError as e:
        return f"❌ 文件读取权限被拒绝\n   路径: {image_path}\n   错误: {e}"
    except Exception as e:
        logger.error(f"[UtilityTool] image_metadata_extractor 异常: {e}", exc_info=True)
        return f"❌ 图片元数据提取异常: {str(e)}"


# ===================================================================
# 工具 7: 二维码生成器
# ===================================================================

@tool
def qr_code_generator(text: str, output_filename: str) -> str:
    """
    将任意文本或 URL 生成二维码 PNG 图片，保存到工作区并返回绝对路径。
    适用于快速分享链接、WiFi 配置、联系方式等场景。

    Args:
        text: 要编码到二维码中的文本或 URL
        output_filename: 输出文件名（不含扩展名，如 "my_qr"）

    Returns:
        保存成功的二维码图片绝对路径，或错误信息
    """
    if not text or not text.strip():
        return "❌ 参数错误：text 不能为空，请提供要编码的文本或 URL。"
    if not output_filename or not output_filename.strip():
        return "❌ 参数错误：output_filename 不能为空，请提供输出文件名。"

    import re
    safe_filename = re.sub(r'[^\w\-]', '_', output_filename.strip())
    if not safe_filename:
        safe_filename = "qrcode_output"

    try:
        import qrcode
    except ImportError:
        return f"❌ qrcode 库未安装，无法生成二维码。\n   请在 Docker 中安装: pip install qrcode[pil]"

    try:
        output_dir = Path("/app/workspace/output")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{safe_filename}.png"

        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(str(output_path), "PNG")

        abs_path = str(output_path.absolute())
        file_size = output_path.stat().st_size
        logger.info(f"[UtilityTool] qr_code_generator: 已生成 {abs_path} ({file_size} bytes)")

        return (
            f"✅ 二维码生成成功\n"
            f"   编码内容: {text[:200]}{'...' if len(text) > 200 else ''}\n"
            f"   输出路径: {abs_path}\n"
            f"   文件大小: {_format_file_size(file_size)} ({file_size} bytes)\n"
            f"   文件名: {safe_filename}.png\n"
            f"   状态: 已保存至工作区"
        )
    except PermissionError as e:
        return f"❌ 写入权限被拒绝\n   路径: /app/workspace/output/{safe_filename}.png\n   错误: {e}"
    except OSError as e:
        return f"❌ 文件写入错误\n   路径: /app/workspace/output/{safe_filename}.png\n   错误: {e}"
    except Exception as e:
        logger.error(f"[UtilityTool] qr_code_generator 异常: {e}", exc_info=True)
        return f"❌ 二维码生成异常: {str(e)}"


# ===================================================================
# 工具 8: 图片搜索（百度图片 AJAX 接口，无需 API Key）
# ===================================================================

@tool
def image_search(query: str, count: int = 3) -> str:
    """
    CRITICAL: When writing a technical blog/article, you MUST call this tool to get real images
    (architecture diagrams, flowcharts, etc.) for the article. Do NOT generate fake image URLs
    or skip images. Always insert the returned real image URLs into your article using
    ![alt text](image_url) format.

    搜索网络图片。返回包含图片链接的JSON数组，每个元素包含 original_url、alt_text 等字段。

    Args:
        query (str): 必填，图片搜索关键词（如 "微服务架构图"）。
        count (int): 返回数量，默认3张，最高20张。
    """
    import urllib.parse

    if not query or not query.strip():
        return json.dumps([{"error": "query 搜索关键词不能为空"}], ensure_ascii=False)

    safe_query = query.strip()
    safe_count = min(max(1, int(count)), 20)

    # 完整的浏览器指纹头，绕过百度 WAF
    _headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        "Referer": "https://image.baidu.com/",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    import httpx as _httpx
    # 使用持久会话自动管理 Cookie
    try:
        with _httpx.Client(headers=_headers, follow_redirects=True, timeout=10.0) as _client:
            # Step 1: 预热 —— 先访问 image.baidu.com 获取 Cookie
            _client.get("https://image.baidu.com/", timeout=5.0)

            # Step 2: 搜索
            _search_url = "https://image.baidu.com/search/acjson"
            _params = {
                "tn": "resultjson_com",
                "ipn": "rj",
                "word": safe_query,
                "pn": 0,
                "rn": safe_count,
                "ie": "utf-8",
                "oe": "utf-8",
            }
            logger.info(f"[UtilityTool] baidu_image_search: query='{safe_query}' count={safe_count}")
            _resp = _client.get(_search_url, params=_params)
            if _resp.status_code != 200:
                return json.dumps(
                    [{"error": f"百度图片接口返回 HTTP {_resp.status_code}，可能被反爬拦截"}],
                    ensure_ascii=False,
                )

            # Step 3: 解析 JSON
            try:
                _data = json.loads(_resp.text, strict=False)
            except json.JSONDecodeError as e:
                return json.dumps(
                    [{"error": f"百度图片接口返回非 JSON 数据: {str(e)}，响应前200字符: {_resp.text[:200]}"}],
                    ensure_ascii=False,
                )

            _images = []
            _seen = set()
            for _item in _data.get("data", []):
                if not _item or not isinstance(_item, dict) or len(_item) == 0:
                    continue
                _url = _item.get("thumbURL") or _item.get("middleURL") or _item.get("hoverURL")
                if not _url or _url in _seen:
                    continue
                _seen.add(_url)
                _title_raw = _item.get("fromPageTitleEnc", "") or _item.get("fromPageTitle", "") or ""
                _alt = ""
                try:
                    _alt = urllib.parse.unquote(_title_raw)
                except Exception:
                    _alt = _title_raw
                _images.append({
                    "original_url": _url,
                    "thumbnail_url": _item.get("thumbURL", _url),
                    "alt_text": _alt or safe_query,
                    "source_site": _item.get("fromURL", ""),
                    "width": _item.get("width", 0) or 0,
                    "height": _item.get("height", 0) or 0,
                })
                if len(_images) >= safe_count:
                    break

            if not _images:
                return json.dumps(
                    [{"error": f"未找到与「{safe_query}」相关的图片，响应长度={len(_resp.text)}"}],
                    ensure_ascii=False,
                )

            logger.info(f"[UtilityTool] baidu_image_search: {len(_images)} 张图片")
            return json.dumps(_images, ensure_ascii=False)

    except _httpx.TimeoutException:
        return json.dumps(
            [{"error": "图片搜索请求超时（10秒），请检查网络连接后重试"}],
            ensure_ascii=False,
        )
    except _httpx.ConnectError as e:
        _msg = str(e).replace('"', "'")
        return json.dumps(
            [{"error": f"图片搜索网络连接失败: {_msg}"}],
            ensure_ascii=False,
        )
    except _httpx.RequestError as e:
        _msg = str(e).replace('"', "'")
        return json.dumps(
            [{"error": f"图片搜索请求异常: {_msg}"}],
            ensure_ascii=False,
        )
    except Exception as e:
        logger.error(f"[UtilityTool] baidu_image_search 异常: {e}", exc_info=True)
        return json.dumps(
            [{"error": f"图片搜索内部异常: {str(e)}"}],
            ensure_ascii=False,
        )


# ===================================================================
# 辅助函数
# ===================================================================

def _format_file_size(size_bytes: int) -> str:
    """将字节数格式化为人类可读字符串。"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    else:
        return f"{size_bytes / 1024 / 1024 / 1024:.2f} GB"


# ===================================================================
# 模块 E: 全局工具注册
# ===================================================================

# 所有 utility 工具的列表（供 tool_manager 注册使用）
UTILITY_TOOLS = [
    web_fetch_content,
    python_executor,
    document_reader,
    http_client_request,
    image_search,
    markdown_to_html,
    image_metadata_extractor,
    qr_code_generator,
]


def register_all() -> int:
    """
    将所有 utility 工具注册到全局 ToolManager。

    在应用启动时调用（main.py 的 lifespan startup 阶段）。
    已注册的工具不会重复注册。

    Returns:
        成功注册的工具数量
    """
    from app.services.tool_manager import tool_manager, ToolDescriptor

    count = 0
    for tool_fn in UTILITY_TOOLS:
        name = tool_fn.name
        existing = tool_manager.get_tool(name)
        if existing:
            logger.debug(f"[UtilityTools] 工具已存在，跳过: {name}")
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
        logger.info(f"[UtilityTools] 已注册工具: {name}")

    if count > 0:
        logger.info(f"[UtilityTools] 注册完成: 新增 {count} 个工具")

    # ── 加载持久化配置 ──
    try:
        tool_manager._load_persisted_configs()
    except Exception:
        pass

    return count
"""
实用工具包 (Utility Tools)
============================
为 Agent 提供 7 个高效、工业级的落地实用工具：

  工具 1 - web_fetch_content:        网页全文本深度抓取 (httpx + BeautifulSoup)
  工具 2 - python_executor:          安全 Python 代码沙盒执行 (thread + exec)
  工具 3 - document_reader:          多格式本地文档读取 (PDF/DOCX/CSV/TXT)
  工具 4 - http_client_request:      万能 HTTP 客户端请求 (httpx)
  工具 5 - markdown_to_html:         Markdown → HTML 渲染 (markdown 库)
  工具 6 - image_metadata_extractor: 图片元数据与尺寸提取 (Pillow lazy mode)
  工具 7 - qr_code_generator:        二维码生成器 (qrcode 库)

注册方式:
  from app.tools.utility_tools import register_all
  count = register_all()  # 在 main.py 的 lifespan startup 中调用
"""

from app.tools.utility_tools.utility_tools import (
    web_fetch_content,
    python_executor,
    document_reader,
    http_client_request,
    image_search,
    markdown_to_html,
    image_metadata_extractor,
    qr_code_generator,
    UTILITY_TOOLS,
    register_all,
)

__all__ = [
    "web_fetch_content",
    "python_executor",
    "document_reader",
    "http_client_request",
    "image_search",
    "markdown_to_html",
    "image_metadata_extractor",
    "qr_code_generator",
    "UTILITY_TOOLS",
    "register_all",
]

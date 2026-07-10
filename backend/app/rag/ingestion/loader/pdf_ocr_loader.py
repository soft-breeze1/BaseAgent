"""
PDF OCR Loader with MinerU + PaddleOCR for Chinese document extraction.
v3.1 — 降低 OCR 触发阈值，避免数字 PDF 误触 OCR。

Architecture:
  1. 用 PyPDFLoader 先提取文字层
  2. 检测文本密度（中文字符占比 + 总字符数）
  3. <8% 文本密度 + 页数 ≤ 100 → 触发全页 OCR
  4. OCR 失败或异常 → 降级到 PyPDFLoader 结果
"""

import os
import tempfile
import logging
from typing import List, Optional

from langchain_core.documents import Document

logger = logging.getLogger(__name__)


class PDFOCREnhancedLoader:
    """
    Enhanced PDF loader with automatic OCR fallback for scanned/image-based PDFs.

    Detects low-text-density pages and triggers PaddleOCR when needed.
    Supports simplified Chinese, traditional Chinese, and English.

    v3.1 — OCR density threshold lowered from 0.30 to 0.08 to avoid triggering
    OCR on digital PDFs with decent text extraction (e.g. product manuals).
    """

    def __init__(
        self,
        file_path: str,
        ocr_threshold: float = 0.08,
        max_ocr_pages: int = 100,
        lang: str = "ch,en",
    ):
        """
        Args:
            file_path: Path to the PDF file.
            ocr_threshold: Text density threshold below which OCR is triggered (0.0-1.0).
                           v3.1: lowered to 0.08 to avoid false OCR triggers on digital PDFs.
            max_ocr_pages: Maximum pages to OCR (prevents timeout on huge docs).
            lang: OCR language: 'ch' (Chinese+English), 'en' (English only).
        """
        self.file_path = file_path
        self.ocr_threshold = ocr_threshold
        self.max_ocr_pages = max_ocr_pages
        self.lang = lang

    def load(self) -> List[Document]:
        """Load PDF with automatic OCR fallback."""
        # Step 1: Try PyPDFLoader first
        pdf_docs = self._load_with_pypdf()

        if not pdf_docs:
            logger.warning(f"PyPDFLoader returned empty for {self.file_path}, forcing OCR")
            return self._load_with_ocr()

        # Step 2: Check text density
        total_text = " ".join([d.page_content for d in pdf_docs if d.page_content])
        density = self._compute_text_density(total_text)

        trigger_ocr = density < self.ocr_threshold and len(pdf_docs) <= self.max_ocr_pages
        logger.info(
            f"PDF 文字密度: {density:.2%}, 阈值: {self.ocr_threshold:.2%}, "
            f"是否触发OCR: {trigger_ocr} "
            f"(file={os.path.basename(self.file_path)}, chars={len(total_text)}, pages={len(pdf_docs)})"
        )

        if trigger_ocr:
            logger.info(f"Text density {density:.1%} < threshold {self.ocr_threshold:.0%}, triggering OCR")
            try:
                ocr_docs = self._load_with_ocr()
                if ocr_docs:
                    ocr_text = " ".join([d.page_content for d in ocr_docs if d.page_content])
                    ocr_density = self._compute_text_density(ocr_text)
                    logger.info(f"OCR result density: {ocr_density:.1%} ({len(ocr_text)} chars)")
                    # Only use OCR if it produced more/better content
                    if len(ocr_text) > len(total_text) * 1.2 or ocr_density > density:
                        return ocr_docs
                    logger.info("OCR did not improve quality, keeping PyPDFLoader result")
            except Exception as e:
                logger.warning(f"OCR failed, falling back to PyPDFLoader: {e}")

        return pdf_docs

    def _load_with_pypdf(self) -> List[Document]:
        """Extract text using PyPDFLoader (fast, text-layer only)."""
        try:
            from langchain_community.document_loaders import PyPDFLoader

            loader = PyPDFLoader(self.file_path)
            return loader.load()
        except Exception as e:
            logger.warning(f"PyPDFLoader failed: {e}")
            return []

    def _load_with_ocr(self) -> List[Document]:
        """
        Extract text using MinerU + PaddleOCR pipeline.
        Falls back to paddleocr direct usage if magic-pdf is unavailable.
        """
        docs = []

        # Strategy A: Try magic-pdf (MinerU)
        try:
            return self._load_with_mineru()
        except ImportError:
            logger.info("magic-pdf not installed, falling back to direct PaddleOCR")
        except Exception as e:
            logger.warning(f"magic-pdf failed: {e}, falling back to direct PaddleOCR")

        # Strategy B: Direct PaddleOCR
        try:
            return self._load_with_paddleocr_direct()
        except Exception as e:
            logger.warning(f"Direct PaddleOCR also failed: {e}")
            return []

    def _load_with_mineru(self) -> List[Document]:
        """Use magic-pdf (MinerU) for OCR-enhanced PDF parsing."""
        from magic_pdf.pipe import UNIPipe
        from magic_pdf.pipe.ocr import OCRPipe

        # MinerU processes the PDF and returns structured markdown-like text
        pdf_bytes = open(self.file_path, "rb").read()

        try:
            # Try OCR pipe first (best for scanned docs)
            pipe = OCRPipe(pdf_bytes, lang=self.lang)
            pipe.pipe_classify()
            pipe.pipe_analyze()
            result = pipe.pipe_result()
            text = result.get("text", "")
        except Exception:
            # Fallback to unified pipe (mixed text + image)
            pipe = UNIPipe(pdf_bytes, lang=self.lang)
            pipe.pipe_classify()
            pipe.pipe_analyze()
            result = pipe.pipe_result()
            text = result.get("text", "")

        if text.strip():
            docs = [Document(page_content=text, metadata={"source": self.file_path, "loader": "mineru_ocr"})]
        return docs

    def _load_with_paddleocr_direct(self) -> List[Document]:
        """Use PaddleOCR directly page by page."""
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            logger.error("PaddleOCR not installed")
            return []

        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader

        ocr = PaddleOCR(
            use_angle_cls=True,
            lang=self.lang,
            show_log=False,
            use_gpu=False,
        )

        reader = PdfReader(self.file_path)
        docs = []
        total_pages = min(len(reader.pages), self.max_ocr_pages)

        for page_num in range(total_pages):
            try:
                page = reader.pages[page_num]
                # Extract page as image for OCR
                for image in page.images:
                    temp_path = None
                    try:
                        with tempfile.NamedTemporaryFile(
                            suffix=".png", delete=False
                        ) as f:
                            f.write(image.data)
                            temp_path = f.name

                        result = ocr.ocr(temp_path, cls=True)
                        if result and result[0]:
                            page_text = "\n".join(
                                [line[1][0] for line in result[0]]
                            )
                            if page_text.strip():
                                docs.append(
                                    Document(
                                        page_content=page_text,
                                        metadata={
                                            "source": self.file_path,
                                            "page": page_num + 1,
                                            "loader": "paddleocr",
                                        },
                                    )
                                )
                    finally:
                        if temp_path and os.path.exists(temp_path):
                            os.unlink(temp_path)
            except Exception as e:
                logger.warning(f"OCR failed on page {page_num + 1}: {e}")
                continue

        return docs

    @staticmethod
    def _compute_text_density(text: str) -> float:
        """
        Compute the ratio of meaningful Chinese + English characters
        vs total length (including spaces, punctuation, etc.).
        """
        if not text or len(text) < 10:
            return 0.0

        # Count meaningful characters (Chinese chars + ASCII letters + digits)
        meaningful = 0
        for c in text:
            if (
                "\u4e00" <= c <= "\u9fff"  # CJK (Chinese)
                or "\u3000" <= c <= "\u303f"  # CJK punctuation
                or "\uff00" <= c <= "\uffef"  # Fullwidth forms
                or "a" <= c.lower() <= "z"  # ASCII letters
                or "0" <= c <= "9"  # Digits
            ):
                meaningful += 1

        return meaningful / len(text) if len(text) > 0 else 0.0
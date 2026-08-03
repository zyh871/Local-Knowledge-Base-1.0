"""
Robust multi-format document loaders.

Replaces and extends the loaders in rag_chain.py with:
  * PDF   — pdfplumber (with PyPDF2 fallback) for table-aware extraction
  * DOCX  — paragraphs + table cells
  * PPTX  — slides with shape text + notes
  * XLSX  — sheet-by-sheet
  * CSV   — full content as Document
  * TXT/MD — utf-8 with fallback
  * Image — OCR via pytesseract
"""

import logging
import os
from typing import List

from langchain_core.documents import Document

logger = logging.getLogger(__name__)


def _meta(file_path: str) -> dict:
    return {"source": os.path.basename(file_path)}


def _safe_read_text(file_path: str, encoding: str = "utf-8") -> str:
    try:
        with open(file_path, "r", encoding=encoding) as f:
            return f.read()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="gbk", errors="ignore") as f:
            return f.read()


def load_text(file_path: str) -> List[Document]:
    return [Document(page_content=_safe_read_text(file_path), metadata=_meta(file_path))]


def load_markdown(file_path: str) -> List[Document]:
    return load_text(file_path)


def load_pdf(file_path: str) -> List[Document]:
    """Try pdfplumber first, then pypdf, then PyPDF2 as final fallback."""
    text_parts: List[str] = []

    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages):
                t = page.extract_text() or ""
                if t.strip():
                    text_parts.append(f"[Page {i + 1}]\n{t}")
        if text_parts:
            return [Document(page_content="\n\n".join(text_parts), metadata=_meta(file_path))]
    except ImportError as e:
        logger.warning(f"pdfplumber not available: {e}")
    except Exception as e:
        logger.error(f"pdfplumber failed for {file_path}: {e}")

    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        for i, page in enumerate(reader.pages):
            t = page.extract_text() or ""
            if t.strip():
                text_parts.append(f"[Page {i + 1}]\n{t}")
        if text_parts:
            return [Document(page_content="\n\n".join(text_parts), metadata=_meta(file_path))]
    except ImportError as e:
        logger.warning(f"pypdf not available: {e}")
    except Exception as e:
        logger.error(f"pypdf failed for {file_path}: {e}")

    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(file_path)
        for i, page in enumerate(reader.pages):
            t = page.extract_text() or ""
            if t.strip():
                text_parts.append(f"[Page {i + 1}]\n{t}")
        if text_parts:
            return [Document(page_content="\n\n".join(text_parts), metadata=_meta(file_path))]
    except ImportError as e:
        logger.warning(f"PyPDF2 not available: {e}")
    except Exception as e:
        logger.error(f"PyPDF2 also failed for {file_path}: {e}")

    if not text_parts:
        logger.error(f"All PDF parsers failed for {file_path} — no text extracted")
    return []


def load_docx(file_path: str) -> List[Document]:
    from docx import Document as DocxDoc
    doc = DocxDoc(file_path)
    parts: List[str] = []
    for p in doc.paragraphs:
        if p.text.strip():
            parts.append(p.text)
    for t in doc.tables:
        for row in t.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
            if row_text:
                parts.append(row_text)
    return [Document(page_content="\n\n".join(parts), metadata=_meta(file_path))]


def load_pptx(file_path: str) -> List[Document]:
    from pptx import Presentation
    prs = Presentation(file_path)
    parts: List[str] = []
    for i, slide in enumerate(prs.slides):
        chunk = [f"[Slide {i + 1}]"]
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                chunk.append(shape.text)
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            chunk.append("[Notes] " + slide.notes_slide.notes_text_frame.text)
        parts.append("\n".join(chunk))
    return [Document(page_content="\n\n".join(parts), metadata=_meta(file_path))]


def load_xlsx(file_path: str) -> List[Document]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        logger.error("openpyxl not installed; cannot read xlsx")
        return []
    wb = load_workbook(file_path, data_only=True, read_only=True)
    parts: List[str] = []
    for ws in wb.worksheets:
        parts.append(f"[Sheet] {ws.title}")
        for row in ws.iter_rows(values_only=True):
            row_vals = [str(c) if c is not None else "" for c in row]
            if any(v.strip() for v in row_vals):
                parts.append(" | ".join(row_vals))
    return [Document(page_content="\n\n".join(parts), metadata=_meta(file_path))]


def load_csv(file_path: str) -> List[Document]:
    import csv
    parts: List[str] = []
    try:
        with open(file_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                parts.append(" | ".join(c.strip() for c in row))
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="gbk", newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                parts.append(" | ".join(c.strip() for c in row))
    return [Document(page_content="\n".join(parts), metadata=_meta(file_path))]


def load_image_ocr(file_path: str) -> List[Document]:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        logger.warning("pytesseract/PIL not installed; cannot OCR image")
        return []
    try:
        img = Image.open(file_path)
        content = pytesseract.image_to_string(img, lang="chi_sim+eng")
        if not content.strip():
            return []
        return [Document(page_content=content, metadata=_meta(file_path))]
    except Exception as e:
        logger.error(f"OCR failed for {file_path}: {e}")
        return []


LOADER_MAPPING = {
    ".txt": load_text,
    ".md": load_markdown,
    ".pdf": load_pdf,
    ".docx": load_docx,
    ".pptx": load_pptx,
    ".ppt": load_pptx,
    ".xlsx": load_xlsx,
    ".xls": load_xlsx,
    ".csv": load_csv,
    ".jpg": load_image_ocr,
    ".jpeg": load_image_ocr,
    ".png": load_image_ocr,
    ".bmp": load_image_ocr,
    ".tiff": load_image_ocr,
}


def load_any(file_path: str) -> List[Document]:
    ext = os.path.splitext(file_path)[1].lower()
    fn = LOADER_MAPPING.get(ext)
    if not fn:
        logger.warning(f"Unsupported file type: {file_path}")
        return []
    try:
        docs = fn(file_path)
        for d in docs:
            d.metadata["source"] = os.path.basename(file_path)
            d.metadata.setdefault("file_type", ext.lstrip("."))
        return docs
    except Exception as e:
        logger.error(f"Failed to load {file_path}: {e}")
        return []


SUPPORTED_EXTENSIONS = list(LOADER_MAPPING.keys())

"""
Document processing package.
"""

from .long_text import (
    answer_against_text,
    chunk_text,
    detect_language_hint,
    summarize,
    translate,
)
from .parser import (
    LOADER_MAPPING,
    SUPPORTED_EXTENSIONS,
    load_any,
    load_csv,
    load_docx,
    load_image_ocr,
    load_markdown,
    load_pdf,
    load_pptx,
    load_text,
    load_xlsx,
)

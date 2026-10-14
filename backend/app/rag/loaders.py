"""文档加载与切分。"""

from __future__ import annotations

import os
from typing import List, Optional

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import get_settings
from app.core.logging import get_logger
from app.document.parser import LOADER_MAPPING, load_any

logger = get_logger(__name__)

__all__ = ["LOADER_MAPPING", "load_documents", "load_single_document", "split_documents"]


def load_documents(data_path: Optional[str] = None) -> List[Document]:
    """递归加载数据目录下所有受支持的文档。"""
    data_path = data_path or get_settings().paths.data_path
    docs: List[Document] = []
    if not os.path.exists(data_path):
        logger.warning(f"数据目录不存在: {data_path}")
        return docs

    for root, _, files in os.walk(data_path):
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in LOADER_MAPPING:
                continue
            full_path = os.path.join(root, filename)
            try:
                loaded = load_any(full_path)
                docs.extend(loaded)
                logger.info(f"成功加载文档: {full_path}, 页数: {len(loaded)}")
            except Exception as exc:  # noqa: BLE001  单个文件失败不应中断整体
                logger.error(f"加载文档失败: {full_path}, 错误: {exc}")
    return docs


def load_single_document(file_path: str) -> List[Document]:
    """按路径加载单个文档。"""
    from app.core.exceptions import AppError

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in LOADER_MAPPING:
        logger.warning(f"不支持的文件类型: {file_path}")
        return []
    try:
        docs = load_any(file_path)
        if not docs:
            size = os.path.getsize(file_path) if os.path.exists(file_path) else "N/A"
            logger.error(f"加载文档返回空结果: {file_path}, 文件大小: {size} bytes")
        return docs
    except AppError:
        # 携带可操作原因的领域异常（如 OCR 不可用）必须透传，
        # 否则调用方只能拿到空列表，把「环境缺组件」误报成「文件没内容」。
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"加载文档失败: {file_path}, 错误: {exc}", exc_info=True)
        return []


def split_documents(docs: List[Document]) -> List[Document]:
    """按配置的分块大小切分文档。"""
    if not docs:
        return []
    retrieval = get_settings().retrieval
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=retrieval.chunk_size,
        chunk_overlap=retrieval.chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "，", " ", ""],
    )
    return splitter.split_documents(docs)

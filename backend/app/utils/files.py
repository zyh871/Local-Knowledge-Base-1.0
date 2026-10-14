"""文件与上传相关工具。"""

from __future__ import annotations

import os
from typing import Optional

from app.core.exceptions import (
    FileTooLargeError,
    IllegalFilenameError,
    UnsupportedFileTypeError,
)

# 与旧 main.py 保持一致的限制值（可由 Settings 覆盖，此处为默认）
MAX_UPLOAD_SIZE = 50 * 1024 * 1024
MAX_IMAGE_SIZE = 20 * 1024 * 1024

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp", ".gif"}

_IMAGE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def image_mime(ext: str) -> str:
    return _IMAGE_MIME.get(ext.lower(), "image/jpeg")


def is_illegal_filename(name: str) -> bool:
    return ".." in name or "/" in name or "\\" in name


def sanitize_filename(name: Optional[str]) -> str:
    """校验并返回安全的基本文件名。"""
    if not name:
        raise IllegalFilenameError("请选择要上传的文件")
    base = os.path.basename(name)
    if base != name or is_illegal_filename(base):
        raise IllegalFilenameError("文件名包含非法字符")
    return base


def validate_extension(filename: str, allowed: set[str]) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed:
        raise UnsupportedFileTypeError(
            f"不支持的文件类型 '{ext}'，请上传: {', '.join(sorted(allowed))}"
        )
    return ext


def validate_image_upload(filename: str, size: int, max_size: int = MAX_IMAGE_SIZE) -> str:
    ext = validate_extension(filename, IMAGE_EXTENSIONS)
    if size > max_size:
        raise FileTooLargeError(
            f"图片过大 ({size // 1024 // 1024}MB)，最大支持 {max_size // 1024 // 1024}MB"
        )
    return ext


def save_upload(data_dir: str, filename: str, contents: bytes) -> str:
    """落盘并返回真实路径；同名文件自动加序号后缀（与旧行为一致）。"""
    os.makedirs(data_dir, exist_ok=True)
    file_path = os.path.join(data_dir, filename)
    if os.path.exists(file_path):
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(candidate := os.path.join(data_dir, f"{base}_{counter}{ext}")):
            counter += 1
        file_path = candidate
    with open(file_path, "wb") as handle:
        handle.write(contents)
    return file_path

"""Tesseract OCR 适配层 —— 可执行文件/语言包发现、可用性探测、文本后处理。

本模块解决旧实现的两个问题：

1. **发现靠 PATH，装了就白装。**
   `pytesseract` 默认只在 PATH 里找 `tesseract`；Windows 下装在
   `C:\\Program Files\\Tesseract-OCR` 时通常**不在** PATH 里，
   于是 OCR 静默失效。这里按「配置 → 常见安装路径 → PATH」顺序显式发现，
   并允许单独指定语言包目录（语言包常在用户目录，因为写入 Program Files
   需要管理员权限）。

2. **失败不可区分。**
   旧实现无论引擎缺失、缺语言包还是图片确实没文字，一律返回 `[]`，
   调用方只能笼统报「无法解析该文件内容」。现在这三类情况分开表达，
   由 `OcrUnavailableError` 把「环境问题」透传给用户。

设计约束：本模块**不在 import 期触碰 pytesseract / 不探测文件系统**，
所有探测都在显式调用时发生，避免拖慢启动、也便于测试注入。
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

from app.core.exceptions import OcrUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Windows 与类 Unix 的常见安装位置。顺序即优先级。
_WINDOWS_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)
_UNIX_CANDIDATES = (
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
)

# 语言包目录候选（相对路径基于可执行文件所在目录）
_TESSDATA_RELATIVE = ("tessdata", "../share/tessdata", "../share/tesseract-ocr/tessdata")
_TESSDATA_ABSOLUTE_UNIX = (
    "/usr/share/tessdata",
    "/usr/local/share/tessdata",
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/opt/homebrew/share/tessdata",
)

# 中文与全角标点之间的空白：Tesseract 的 chi_sim 模型会在汉字之间插入空格，
# 不清理会污染分块与向量检索（「本 地 知 识」与「本地知识」是两种 token）。
_CJK = r"\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef"
_CJK_SPACE_RE = re.compile(rf"(?<=[{_CJK}])[ \t]+(?=[{_CJK}])")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")

_probe_cache: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
#  发现
# ---------------------------------------------------------------------------


def _ocr_settings() -> Any:
    from app.core.config import get_ocr_settings

    return get_ocr_settings()


def _candidate_cmds() -> list[str]:
    out: list[str] = []
    env_cmd = os.getenv("TESSERACT_CMD", "").strip()
    if env_cmd:
        out.append(env_cmd)
    out.extend(_WINDOWS_CANDIDATES if sys.platform == "win32" else _UNIX_CANDIDATES)
    local = os.getenv("LOCALAPPDATA", "")
    if local:
        out.append(os.path.join(local, "Programs", "Tesseract-OCR", "tesseract.exe"))
    home = os.path.expanduser("~")
    out.append(os.path.join(home, "Tesseract-OCR", "tesseract.exe"))
    found = shutil.which("tesseract")
    if found:
        out.append(found)
    return out


def resolve_tesseract_cmd() -> Optional[str]:
    """返回可用的 tesseract 可执行文件绝对路径；找不到返回 None。"""
    configured = (_ocr_settings().tesseract_cmd or "").strip()
    if configured:
        return configured if os.path.isfile(configured) else None
    for candidate in _candidate_cmds():
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def resolve_tessdata_prefix(cmd: Optional[str] = None) -> Optional[str]:
    """返回含 traineddata 的语言包目录；找不到返回 None。

    留空时的推断顺序：配置 → 可执行文件同级的 tessdata → 系统标准位置 →
    用户目录下的 tessdata（追加语言包的常用落点）。
    """
    configured = (_ocr_settings().tessdata_prefix or "").strip()
    if configured:
        return configured if os.path.isdir(configured) else None

    candidates: list[str] = []
    if cmd:
        base = Path(cmd).resolve().parent
        candidates.extend(str(base / rel) for rel in _TESSDATA_RELATIVE)
    if sys.platform != "win32":
        candidates.extend(_TESSDATA_ABSOLUTE_UNIX)
    home = os.path.expanduser("~")
    candidates.append(os.path.join(home, "tessdata"))
    candidates.append(os.path.join(home, ".tessdata"))

    for candidate in candidates:
        if os.path.isdir(candidate) and any(
            name.endswith(".traineddata") for name in os.listdir(candidate)
        ):
            return candidate
    return None


def available_languages(tessdata_prefix: Optional[str]) -> list[str]:
    """列出语言包目录里实际存在的语言。"""
    if not tessdata_prefix or not os.path.isdir(tessdata_prefix):
        return []
    return sorted(
        name[: -len(".traineddata")]
        for name in os.listdir(tessdata_prefix)
        if name.endswith(".traineddata")
    )


def _pytesseract() -> Any:
    try:
        import pytesseract  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise OcrUnavailableError(
            "OCR 依赖未安装：缺少 pytesseract（pip install pytesseract Pillow）"
        ) from exc
    return pytesseract


def _config_string(tessdata_prefix: Optional[str]) -> str:
    """构造 tesseract 命令行附加参数。

    ⚠️ 路径**不能加引号** —— pytesseract 以列表方式传参，引号会被当成
    路径的一部分，报 `Error opening data file "C:\\...\\tessdata"/chi_sim.traineddata`。
    """
    if tessdata_prefix:
        return f"--tessdata-dir {tessdata_prefix}"
    return ""


# ---------------------------------------------------------------------------
#  探测
# ---------------------------------------------------------------------------


def probe(*, refresh: bool = False) -> dict[str, Any]:
    """探测 OCR 可用性。

    返回 dict（同时供启动日志与测试使用）：
        available / tesseract_cmd / tessdata_prefix / languages /
        missing_languages / version / reason
    """
    global _probe_cache
    if _probe_cache is not None and not refresh:
        return dict(_probe_cache)

    settings = _ocr_settings()
    result: dict[str, Any] = {
        "enabled": settings.enabled,
        "available": False,
        "tesseract_cmd": None,
        "tessdata_prefix": None,
        "languages": [],
        "missing_languages": list(settings.language_list),
        "version": None,
        "reason": None,
    }

    if not settings.enabled:
        result["reason"] = "OCR 已在配置中关闭（OCR_ENABLED=false）"
        _probe_cache = dict(result)
        return result

    cmd = resolve_tesseract_cmd()
    if not cmd:
        result["reason"] = (
            "未找到 tesseract 可执行文件；请安装 Tesseract OCR 或用 OCR_TESSERACT_CMD 指定路径"
        )
        _probe_cache = dict(result)
        return result
    result["tesseract_cmd"] = cmd

    tessdata = resolve_tessdata_prefix(cmd)
    result["tessdata_prefix"] = tessdata

    available = available_languages(tessdata)
    result["languages"] = available
    missing = [lang for lang in settings.language_list if lang not in available]
    result["missing_languages"] = missing

    if missing:
        result["reason"] = (
            f"缺少语言包 {missing}；请将对应 .traineddata 放入语言包目录"
            f"（当前：{tessdata or '未找到语言包目录'}）"
        )
        _probe_cache = dict(result)
        return result

    try:
        pytesseract = _pytesseract()
        pytesseract.pytesseract.tesseract_cmd = cmd
        result["version"] = str(pytesseract.get_tesseract_version())
    except OcrUnavailableError as exc:
        result["reason"] = str(exc)
        _probe_cache = dict(result)
        return result
    except Exception as exc:  # noqa: BLE001 - 版本探测失败不影响可用性判断
        logger.warning(f"Tesseract 版本探测失败: {exc}")

    result["available"] = True
    _probe_cache = dict(result)
    return result


def reset_probe_cache() -> None:
    """测试用：清空探测缓存。"""
    global _probe_cache
    _probe_cache = None


# ---------------------------------------------------------------------------
#  文本后处理
# ---------------------------------------------------------------------------


def normalize_cjk_spacing(text: str) -> str:
    """清理 OCR 文本的常见噪音。

    chi_sim 模型习惯在汉字之间插空格（`本 地 知 识`），若不处理，
    同一个词会变成不同的 token，直接劣化分块与向量检索效果。
    此处只清理**汉字/全角标点之间**的空白，汉字与拉丁字母之间的空格保留
    （那是真实分词边界）。
    """
    if not text:
        return ""
    cleaned = _CJK_SPACE_RE.sub("", text)
    cleaned = _MULTI_BLANK_RE.sub("\n\n", cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
#  OCR
# ---------------------------------------------------------------------------


def image_to_text(file_path: str) -> str:
    """对图片做 OCR，返回清理后的文本。

    未能识别出任何文字时返回空串（这是**正常**结果，不是错误）；
    引擎/语言包缺失时抛 `OcrUnavailableError`（这是**环境问题**，需要用户处理）。
    """
    settings = _ocr_settings()
    status = probe()
    if not status["available"]:
        raise OcrUnavailableError(f"OCR 不可用：{status['reason']}")

    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise OcrUnavailableError("OCR 依赖未安装：缺少 Pillow") from exc

    pytesseract = _pytesseract()
    pytesseract.pytesseract.tesseract_cmd = status["tesseract_cmd"]

    try:
        with Image.open(file_path) as img:
            raw = pytesseract.image_to_string(
                img,
                lang=settings.languages,
                config=_config_string(status["tessdata_prefix"]),
                timeout=settings.timeout_seconds,
            )
    except Exception as exc:  # noqa: BLE001 - OCR 失败原因多样，统一转领域异常
        logger.error(f"OCR 执行失败 {file_path}: {exc}")
        raise OcrUnavailableError(f"OCR 执行失败：{exc}") from exc

    return normalize_cjk_spacing(raw)

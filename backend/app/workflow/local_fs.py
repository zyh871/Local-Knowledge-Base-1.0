"""本地文件夹访问：目录浏览、设为工作区、在文件管理器中打开。

## 为什么需要这一层

工作区原本只是「服务端在 `WORKFLOW_WORKSPACE_ROOT` 下分配的一个隐藏目录」，
用户看不到也管不着。但这是一个**跑在自己机器上的本地知识库**，
用户的资料本来就散落在他自己的文件夹里 —— 强迫他把文件重传一遍是多余的。

于是允许把一个**本地文件夹**绑定为工作区：
  * 目录浏览让用户能直接挑文件夹，不必手打路径
  * 绑定后一键在系统文件管理器里打开
  * 绑定目录里的文件可以直接引用到任务里（见 `import_local_file`）

## 安全边界（务必清楚）

这是**有意开放**的本地文件系统读能力，前提是部署形态为「本机单用户」。
因此：
  * 只列出**名称 / 类型 / 大小 / 时间**，**绝不返回文件内容**
  * 不提供删除、改名、写入本地目录的能力
  * 可用 `WORKFLOW_FS_BROWSE=false` 整体关闭（多用户部署应关掉）

如果将来要把这个系统暴露到局域网/公网，**必须先关掉这个开关**：
任何登录用户都能枚举服务器磁盘，这在多用户场景下是不可接受的。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from app.core.exceptions import ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "LISTING_MAX_FILES",
    "ensure_local_dir",
    "import_local_file",
    "list_dir",
    "list_roots",
    "open_in_file_manager",
    "summarize_dir",
]

#: 单次列出的最大条目数 —— 目录里几万个文件时不能把响应撑爆
MAX_ENTRIES = 500


def _normalize(raw: str) -> str:
    """把用户输入规范成绝对路径。

    `expanduser` 必须做：用户很自然会输入 `~/Documents`，
    不展开会得到一个不存在的相对路径，然后报「目录不存在」—— 用户会以为是自己错了。
    """
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        raise ValidationError("路径不能为空")
    expanded = os.path.expanduser(os.path.expandvars(text))
    return os.path.abspath(expanded)


def ensure_local_dir(raw: str) -> str:
    """校验并返回一个可用的目录绝对路径。"""
    path = _normalize(raw)
    if not os.path.exists(path):
        raise ValidationError(f"目录不存在：{path}")
    if not os.path.isdir(path):
        raise ValidationError(f"这不是一个文件夹：{path}")
    return path


def list_roots() -> list[dict[str, str]]:
    """列出可浏览的起始位置。

    Windows 给盘符；类 Unix 给常见根（`/` 与家目录）——
    让用户从「我的文档」开始比从 `/` 开始顺手得多。
    """
    roots: list[dict[str, str]] = []
    if sys.platform.startswith("win"):
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZAB":
            candidate = f"{letter}:\\"
            if os.path.exists(candidate):
                roots.append({"name": f"{letter}:", "path": candidate})
    else:
        roots.append({"name": "/", "path": "/"})

    home_raw = os.path.expanduser("~")
    if home_raw and os.path.isdir(home_raw):
        roots.insert(0, {"name": "主目录", "path": home_raw})
    return roots


def _parent_of(path: str) -> Optional[str]:
    """上一级目录；已到根时返回 None。

    单独抽出来是为了可测：直接对 `C:\\` 或 `/` 调 `list_dir` 会去枚举整个根目录
    （慢，且可能因权限报错），而这里只需要路径运算。

    ⚠️ 必须**先 `abspath` 再取 dirname**，不能先 `rstrip`：
    `dirname("C:")` 会返回 `"C:"` —— 那是一个**盘符相对路径**，
    会被按当前工作目录解析。前端拿它去请求就会列出一个莫名其妙目录的内容。
    规范化之后，「父目录等于自己」正好覆盖所有根形式（`C:\\`、`/`）。
    """
    normalized = os.path.abspath(path)
    parent = os.path.dirname(normalized)
    if not parent or parent == normalized:
        return None
    return parent


def list_dir(raw: str, *, max_entries: int = MAX_ENTRIES) -> dict[str, Any]:
    """列出一个目录的直接子项（不递归）。

    返回 `parent` 让前端能做「上一级」——没有它用户只能在输入框里手改路径。
    """
    path = _normalize(raw)
    if not os.path.isdir(path):
        raise ValidationError(f"目录不存在：{path}")

    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        with os.scandir(path) as scanner:
            for item in scanner:
                if len(entries) >= max_entries:
                    truncated = True
                    break
                try:
                    is_dir = item.is_dir(follow_symlinks=False)
                    stat = item.stat(follow_symlinks=False)
                    size = 0 if is_dir else stat.st_size
                    mtime = stat.st_mtime
                except OSError:
                    # 单个条目读不到元信息（权限/竞态）不应让整个列表失败
                    is_dir, size, mtime = False, 0, 0.0
                if item.name.startswith("."):
                    # 隐藏文件默认不列：用户挑工作区时基本不需要看到它们
                    continue
                entries.append(
                    {
                        "name": item.name,
                        "path": os.path.join(path, item.name),
                        "is_dir": is_dir,
                        "size": size,
                        "mtime": mtime,
                    }
                )
    except PermissionError as exc:
        raise ValidationError(f"没有权限访问：{path}") from exc

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

    parent = _parent_of(path)

    return {
        "path": path,
        "name": os.path.basename(path.rstrip("\\/")) or path,
        "parent": parent,
        "entries": entries,
        "truncated": truncated,
    }


def open_in_file_manager(raw: str) -> str:
    """在系统文件管理器里打开目录，返回实际打开的路径。

    **刻意不返回命令输出**：「打开了哪个目录」是用户唯一关心的信息，
    把 stderr 之类的透出来只会让人以为出错了。
    """
    path = ensure_local_dir(raw)
    try:
        if sys.platform.startswith("win"):
            # 用 explorer 而不是 os.startfile：后者会在前台抢焦点并阻塞
            subprocess.Popen(["explorer", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"打开文件管理器失败 {path}: {exc}")
        raise ValidationError(f"无法打开文件管理器：{exc}") from exc

    logger.info(f"已在文件管理器中打开 {path}")
    return path


def import_local_file(source: str, target_dir: Path) -> dict[str, Any]:
    """把绑定目录里的一个文件**复制**进工作区，返回待登记的信息。

    为什么是复制而不是记住原路径：
    任务执行期间用户可能移动/重命名原文件，那时「引用」就断了，
    而且读原路径等于把整个磁盘变成可变输入。复制一份边界清晰、可校验 TTL。
    代价是占一份磁盘 —— 对临时上下文来说是划算的。
    """
    path = _normalize(source)
    if not os.path.isfile(path):
        raise ValidationError(f"文件不存在：{path}")

    target_dir.mkdir(parents=True, exist_ok=True)
    data = Path(path).read_bytes()
    return {"filename": os.path.basename(path), "data": data, "source": path}


#: 清单里最多列出多少个文件 —— 目录可能上千个，全塞进提示词既无意义又浪费上下文
LISTING_MAX_FILES = 60


def summarize_dir(raw: str, *, limit: int = LISTING_MAX_FILES) -> Optional[dict[str, Any]]:
    """给提示词用的目录摘要：**只有名称与大小，绝不含内容**。

    为什么只给清单而不是内容：把任意目录的内容喂进提示词是无界的
    （用户那个目录 300 KB，远超上下文窗口）。清单的作用是让模型知道
    「这里有什么、可以问用户要哪几个」，而不是让模型假装读过。

    目录不可达时返回 None —— 调用方据此当作「没有绑定文件夹」，
    绝不因为一个不可读的目录让整个澄清/规划失败。
    """
    try:
        path = ensure_local_dir(raw)
    except Exception:  # noqa: BLE001 - 目录没了/没权限都不该拖垮主流程
        logger.warning(f"工作区文件夹不可读，清单按空处理: {raw}")
        return None

    files: list[dict[str, Any]] = []
    dirs: list[str] = []
    total = 0
    #: 真实文件总数（不受 limit 影响）。
    #: 曾经这里是「保留数 + 1」的估算 —— 于是提示词会说「共 4 个文件」而实际有 8 个。
    #: 报给模型的数字必须是真的，否则它会基于错的规模做规划。
    file_count = 0
    truncated = False
    try:
        with os.scandir(path) as scanner:
            for item in scanner:
                if item.name.startswith("."):
                    continue
                # Office 打开文档时会生成 `~$xxx.docx` 这样的锁定文件：
                # 内容不是真文档、随时会消失，列给用户（或模型）都只有干扰
                if item.name.startswith("~$"):
                    continue
                try:
                    if item.is_dir(follow_symlinks=False):
                        dirs.append(item.name)
                        continue
                    size = item.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
                total += size
                file_count += 1
                if len(files) < limit:
                    # `path` 是给界面调「批量引用」用的（要传完整路径）。
                    # 提示词渲染只看 name/size（见 render_folder_listing），
                    # 因此路径不会进模型 —— 模型不需要也就不该知道。
                    files.append(
                        {
                            "name": item.name,
                            "size": size,
                            "path": os.path.join(path, item.name),
                        }
                    )
                else:
                    truncated = True
    except PermissionError:
        logger.warning(f"没有权限读取工作区文件夹: {path}")
        return None

    files.sort(key=lambda f: f["name"].lower())
    dirs.sort(key=str.lower)
    return {
        "path": path,
        "name": os.path.basename(path.rstrip("\\/")) or path,
        "files": files,
        "dirs": dirs,
        "total_bytes": total,
        "total_files": file_count,
        "truncated": truncated,
    }

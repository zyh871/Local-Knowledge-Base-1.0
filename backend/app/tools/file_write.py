"""file_write 工具 —— 把内容真正写成一个文件。

## 为什么必须有它

`app/core/config.py` 里一直写着 `require_approval_tools: "code_executor,file_write"`，
但 **`file_write` 从来不存在**。于是「让 AI 写个脚本保存成文件」这类需求
在整个工作流里没有任何一条通路：

* `code_executor` 明确禁止 `open(`（而且不该让代码自己决定往哪写）；
* `llm` / `synthesis` 步的产出只是一段字符串，永远不落盘。

用户看到的现象就是「运行过程中创建不了文件」。这个工具补上的正是这一条。

## 两条落点，语义不同

* `artifacts`（默认）—— 写进**本任务的产物目录**，随即出现在任务详情与分享页的
  产物列表里，可下载。这是"交付一个文件"的正常做法。
* `bound_folder` —— 写进工作区**已绑定**的本地文件夹。这是"直接落到我的目录里"。

⚠️ `bound_folder` 有一条**必须**遵守的规则：**没有绑定就直接拒绝**，
而不是"没绑定就不校验"。后者等于给未绑定的工作区开了任意路径后门。

## 写不进去时要说话

文件名已存在 → **不覆盖**，改名加序号并把改名事实回报给用户；
后缀不在白名单 / 超过大小上限 / 路径越界 → 明确拒绝并说明原因。
静默改名或静默截断都会让用户以为文件"没生成"。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from app.tools.base import BaseTool

logger = logging.getLogger(__name__)

#: 能写出来的文件类型。这是**写**的白名单，与"允许引用"的读白名单是两回事 ——
#: 读白名单里有 .pdf/.png 之类我们不可能生成的二进制格式；
#: 而这里要有 .py/.js 这类"代码交付物"，否则整个工具就没意义了。
DEFAULT_WRITE_EXTENSIONS: tuple[str, ...] = (
    ".bat",
    ".c",
    ".cfg",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".env.example",
    ".go",
    ".h",
    ".htm",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsv",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
)

#: 单文件默认上限（KB）。产物是"给人用的文件"，不是数据集 ——
#: 真需要搬运大文件应当走引用/知识库，而不是让模型吐几十兆文本。
DEFAULT_MAX_KB = 512

#: 保留的上下文键名。计划里的 args 若出现这些键会被忽略（见 normalize_tool_args）。
_CONTEXT_KEY = "_context"


def _norm_root(path: str) -> str:
    """统一成绝对路径；根目录也用 `abspath` 归一，避免 `C:` 这类
    "盘符相对路径"被按当前工作目录解析（P11 踩过）。"""
    return os.path.abspath(os.path.expanduser(path))


def _inside(root: str, candidate: str) -> bool:
    """`candidate` 是否真的落在 `root` 之内。

    用 `commonpath` 而不是 `startswith`：`C:\\a\\b2` 以 `C:\\a\\b` 为前缀，
    但不在它里面。跨盘符时 `commonpath` 会抛 ValueError，按"不在里面"处理。
    """
    try:
        return os.path.commonpath([root, candidate]) == root
    except ValueError:
        return False


def _unique_path(directory: str, filename: str) -> tuple[str, bool]:
    """不覆盖已有文件：重名就加 `_1`、`_2`…。返回 (路径, 是否被改名)。"""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    if not os.path.exists(candidate):
        return candidate, False
    for counter in range(1, 1000):
        candidate = os.path.join(directory, f"{base}_{counter}{ext}")
        if not os.path.exists(candidate):
            return candidate, True
    raise RuntimeError("同名文件过多，无法生成新文件名")


class FileWriteTool(BaseTool):
    """把文本内容写成文件（产物目录或已绑定的工作区文件夹）。"""

    @property
    def name(self) -> str:
        return "file_write"

    @property
    def description(self) -> str:
        return (
            "把一段文本内容写成文件。这是**唯一**能产出文件的工具，"
            "不要再用 code_executor 的 open() 去写文件（沙箱已禁止）。"
            "path 只写文件名（如 fib.py），不要带目录；"
            "content 给字面正文，或用 content_from_step 引用前面某一步的产出。"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件名，例如 fib.py。只写文件名，不要写目录或绝对路径。",
                },
                "content": {
                    "type": "string",
                    "description": "文件正文（与 content_from_step 二选一）。",
                },
                "content_from_step": {
                    "type": "integer",
                    "description": (
                        "把第几步的产出正文写入本文件。"
                        "适合「上一步写代码、这一步落盘」；比把代码重抄一遍更可靠。"
                    ),
                },
                "target": {
                    "type": "string",
                    "enum": ["artifacts", "bound_folder"],
                    "description": (
                        "artifacts（默认）= 任务产物目录，可在任务详情里下载；"
                        "bound_folder = 工作区已绑定的本地文件夹。"
                    ),
                },
            },
            "required": ["path"],
        }

    def execute(
        self, path: str = "", content: str = "", target: str = "artifacts", **kwargs
    ) -> Dict[str, Any]:
        context = kwargs.get(_CONTEXT_KEY)
        if not isinstance(context, dict):
            return {
                "success": False,
                "error": "缺少写入上下文（产物目录/绑定目录），无法确定写到哪",
            }

        filename = (path or "").strip().replace("\\", "/").split("/")[-1]
        if not filename:
            return {"success": False, "error": "path 不能为空，请给出文件名"}

        body = content if isinstance(content, str) else ""
        if not body.strip():
            return {
                "success": False,
                "error": (
                    f"没有可写入的正文。请直接把内容放进 content，"
                    f"或用 content_from_step 指定前面哪一步的产出要落盘。"
                ),
            }

        allowed = tuple(context.get("allowed_extensions") or DEFAULT_WRITE_EXTENSIONS)
        ext = os.path.splitext(filename)[1].lower()
        # `.env.example` 这种双段后缀按整体比对，否则永远匹配不上
        ext2 = "." + ".".join(filename.split(".")[-2:]).lower() if filename.count(".") >= 2 else ""
        if ext not in allowed and ext2 not in allowed:
            return {
                "success": False,
                "error": (
                    f"不支持写入「{ext or '（无后缀）'}」类型的文件。"
                    f"可写类型：{'、'.join(sorted(allowed))}"
                ),
            }

        max_bytes = int(context.get("max_bytes") or DEFAULT_MAX_KB * 1024)
        payload = body.encode("utf-8")
        if len(payload) > max_bytes:
            return {
                "success": False,
                "error": (
                    f"内容 {len(payload)} 字节，超过单次写入上限 {max_bytes} 字节"
                    f"（约 {max_bytes // 1024}KB）。请拆分，或改为引用文件/知识库。"
                ),
            }

        target_kind = (target or "artifacts").strip() or "artifacts"

        if target_kind == "bound_folder":
            if context.get("bound_allowed") is False:
                return {
                    "success": False,
                    "error": (
                        "本部署已关闭「写入绑定文件夹」"
                        "（WORKFLOW_WRITE_TO_BOUND_FOLDER=false）。"
                        "请改用 target=\"artifacts\"，文件会放在任务产物目录里供下载。"
                    ),
                }
            bound = str(context.get("bound_dir") or "").strip()
            # ★ 没有绑定就**直接拒绝**。写成"绑定了才校验"等于给未绑定的工作区
            #   开一条任意路径写入的后门。
            if not bound:
                return {
                    "success": False,
                    "error": "该工作区还没有绑定本地文件夹，无法写入。请先在工作区菜单里绑定。",
                }
            root = _norm_root(bound)
            if not os.path.isdir(root):
                return {
                    "success": False,
                    "error": f"绑定的文件夹不存在或不可访问：{bound}",
                }
        else:
            root = _norm_root(str(context.get("artifact_dir") or ""))
            if not root:
                return {"success": False, "error": "任务产物目录不可用"}

        candidate = _norm_root(os.path.join(root, filename))
        if not _inside(root, candidate):
            return {
                "success": False,
                "error": f"路径「{filename}」超出允许写入的范围（只能写文件名，不能带目录）",
            }

        os.makedirs(root, exist_ok=True)
        final_path, renamed = _unique_path(root, filename)

        try:
            with open(final_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(body)
        except OSError as exc:
            logger.error(f"写入文件失败: {final_path}: {exc}")
            return {"success": False, "error": f"写入文件失败：{exc}"}

        written_name = os.path.basename(final_path)
        size = os.path.getsize(final_path)
        where = "工作区绑定的文件夹" if target_kind == "bound_folder" else "任务产物目录"
        summary = f"已写入 {written_name}（{size} 字节）→ {where}"
        if renamed:
            summary += f"；同名文件已存在，为避免覆盖改名为 {written_name}"
        if final_path.lower().endswith((".py", ".js", ".ts", ".sh", ".ps1", ".bat")):
            summary += "。若需验证可运行性，请再加一步 code_executor 试跑。"

        logger.info(f"[file_write] {summary} ({final_path})")
        return {
            "success": True,
            "result": summary,
            "written": {
                "filename": written_name,
                "stored_path": final_path,
                "size_bytes": size,
                "target": target_kind,
                "renamed": renamed,
            },
        }


__all__ = ["FileWriteTool", "DEFAULT_MAX_KB", "DEFAULT_WRITE_EXTENSIONS"]

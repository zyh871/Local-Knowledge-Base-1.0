"""临时文件 → 任务上下文。

## 三层注入策略（设计文档 §9.3）

按**解析后的总字符数**自动选，避免两个极端：全都塞进 prompt 会撑爆上下文，
全都切块检索又会让小文件白白丢信息。

| 解析后总长 | 策略 |
|---|---|
| ≤ `inline_max` | 全文进 prompt |
| 更大 | 用 `chunk_text` 分块 → 按与目标的**关键词重合度**取 top 块 |
| 单文件超大 | 同上，但按文件配额限制，并在笔记里标注已截断 |

## 两个硬约束

1. **绝不写入向量库。** 临时文件只对当前 run 可见；一旦进 Chroma，
   用户在**对话模式**提问时会突然命中工作流的临时文件 —— 那是错误行为。
2. **分块召回不用嵌入模型**，只用关键词重合度。原因：嵌入是网络调用，
   会给「生成任务书」这个交互增加一处不可控的延迟与失败点，
   而这里的目标只是「把最相关的几段挑出来」，词面重合已经够用。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "build_file_context",
    "clear_text_cache",
    "read_file_text",
    "render_folder_listing",
]

# 单文件超过这个长度时，即使总长没超也要走分块（否则一个 20 万字文件会独占 prompt）
_PER_FILE_FULL_MAX = 60_000
_CHUNK_SIZE = 1200
_CHUNK_OVERLAP = 150
_CACHE_LIMIT = 32

_text_cache: dict[str, str] = {}

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[a-zA-Z0-9]{2,}")


def clear_text_cache() -> None:
    """测试用：清空解析缓存。"""
    _text_cache.clear()


def _grams(text: str) -> set[str]:
    """粗粒度词元集合。

    中文用 2-gram（「推理」→ 推/理 两字组合），英文与数字用整词。
    不做分词是因为这里只做**排序**，不需要语言学精度。
    """
    tokens: set[str] = set()
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            tokens.add(run)
        else:
            tokens.update(run[i : i + 2] for i in range(len(run) - 1))
    tokens.update(word.lower() for word in _WORD_RE.findall(text))
    return tokens


def read_file_text(stored_path: str, sha256: str = "") -> str:
    """读取并解析一个临时文件；结果按 (路径, 摘要) 缓存。

    缓存键带上 sha256 而不是只用路径 —— 路径可复用，内容变了必须重新解析，
    否则会出现「改了文件但上下文还是旧的」这种极难发现的错误。
    """
    key = f"{stored_path}|{sha256}"
    if key in _text_cache:
        return _text_cache[key]

    text = ""
    try:
        from app.document import load_any

        docs = load_any(stored_path)
        text = "\n".join(d.page_content for d in docs).strip()
    except Exception as exc:  # noqa: BLE001 - 解析失败按「无内容」处理，由上层给告警
        logger.warning(f"读取临时文件失败 {stored_path}: {exc}")

    if len(_text_cache) >= _CACHE_LIMIT:
        # 简单淘汰：清掉最早插入的一批，避免无限增长
        for old in list(_text_cache)[: _CACHE_LIMIT // 2]:
            _text_cache.pop(old, None)
    _text_cache[key] = text
    return text


def _split(text: str) -> list[str]:
    try:
        from app.document.long_text import chunk_text

        return chunk_text(text, chunk_size=_CHUNK_SIZE, overlap=_CHUNK_OVERLAP) or [text]
    except Exception:  # noqa: BLE001 - 分块器不可用时退化为定长切分
        step = _CHUNK_SIZE - _CHUNK_OVERLAP
        return [text[i : i + _CHUNK_SIZE] for i in range(0, len(text), step)]


def _rank_chunks(chunks: list[str], goal: str) -> list[str]:
    if not goal:
        return chunks
    query = _grams(goal)
    if not query:
        return chunks

    scored: list[tuple[float, int, str]] = []
    for index, chunk in enumerate(chunks):
        overlap = len(query & _grams(chunk))
        # 除以长度平方根：避免长块仅因「字数多」就永远排前
        score = overlap / (len(chunk) ** 0.5 or 1)
        scored.append((score, index, chunk))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [chunk for _, _, chunk in scored]


def _human_size(num: int) -> str:
    if num < 1024:
        return f"{num} B"
    if num < 1024 * 1024:
        return f"{num / 1024:.1f} KB"
    return f"{num / 1024 / 1024:.1f} MB"


def render_folder_listing(
    summary: Optional[dict[str, Any]],
    *,
    allowed: Optional[set[str] | list[str]] = None,
) -> str:
    """把工作区文件夹的摘要渲染成提示词里的一段。

    ## 为什么需要这段

    用户绑定了文件夹、然后说「整理**这个**文件夹里的文件」，是很自然的说法。
    但绑定文件夹**不等于**把内容交给了模型（内容需要显式引用进任务），
    而模型此前完全不知道文件夹存在 —— 于是它只能问「你说的『这个文件夹』
    是指哪个文件夹？」，用户则以为「程序访问不了我的本地文件」。
    真实案例，两端都卡在这里。

    给出**清单**（只有名称与大小）就能让澄清问题变准：
    「你是指 AI教程 里那 6 个 md 吗？要合并整理还是分别整理？」

    ## 为什么要传 `allowed`

    清单是给模型用来**问用户要文件**的，所以只该列**真的能被引用**的。
    实测：不过滤时模型会把 `打包.zip` 也列成选项 —— 用户点了也加不进来，
    等于把「点了才发现不支持」这个坑从界面搬到了提示词里。

    ## 为什么必须带这段免责说明

    给了清单，模型很容易顺势假设「那我能读到它们」并开始编造内容。
    所以这里明确写出：**清单而已，内容尚未加入，需要就先问用户要哪几个**。
    宁可用一句笨拙的强调，也不要一个凭空捏造的文件内容。
    """
    if not summary:
        return ""

    entries = list(summary.get("files", []))
    skipped = 0
    if allowed:
        exts = {e.lower() for e in allowed}
        kept: list[dict[str, Any]] = []
        for item in entries:
            name = str(item.get("name") or "")
            dot = name.rfind(".")
            ext = name[dot:].lower() if dot >= 0 else ""
            if ext in exts:
                kept.append(item)
            else:
                skipped += 1
        entries = kept

    if not entries and not summary.get("dirs"):
        return ""

    lines: list[str] = [
        f"【工作区文件夹「{summary.get('name', '')}」里有什么（**只是清单，内容尚未加入**）】"
    ]
    for item in entries:
        lines.append(f"- {item.get('name', '')}（{_human_size(int(item.get('size') or 0))}）")
    for name in summary.get("dirs", [])[:10]:
        lines.append(f"- [子文件夹] {name}")

    total_in_dir = int(summary.get("total_files") or 0)
    if summary.get("truncated"):
        # ⚠️ 截断时必须报**目录里的真实总数**，不能报已渲染的条数。
        # 曾经这里用了 `len(entries)`，于是 8 个文件被说成「共 3 个」——
        # 报给模型的规模数字必须是真话，否则它会基于错的量做规划。
        lines.append(
            f"（目录内共 {total_in_dir} 个文件，此处仅列出前 {len(entries)} 个。）"
        )
    else:
        lines.append(
            f"共 {len(entries)} 个可引用文件、合计 "
            f"{_human_size(sum(int(i.get('size') or 0) for i in entries))}"
            + (f"，另有 {len(summary['dirs'])} 个子文件夹" if summary.get("dirs") else "")
            + "。"
        )
        if skipped:
            lines.append(
                f"（另有 {skipped} 个文件类型不支持引用，已略过，不要向用户推荐它们。）"
            )
    if summary.get("dirs") and summary.get("truncated"):
        lines.append(f"（另有 {len(summary['dirs'])} 个子文件夹。）")
    lines.append(
        "⚠️ 以上只是文件清单，你**看不到这些文件的内容** —— 用户还没有把它们加入本次任务。"
        "如果任务需要其中的文件，请在澄清问题里明确问用户要哪几个（可以说出文件名），"
        "然后由用户添加；**不要假设你已读过、更不要编造它们的内容**。"
    )
    return "\n".join(lines)


def build_file_context(
    files: list[dict[str, Any]],
    *,
    goal: str,
    inline_max_chars: int = 8000,
) -> tuple[str, list[dict[str, Any]]]:
    """把临时文件拼装成上下文。

    `files` 为 `workflow_repo.list_run_files()` 的返回值。
    返回 `(context_text, notes)`，notes 供前端展示「哪些文件进了上下文、进了多少」。
    """
    if not files:
        return "", []

    loaded: list[tuple[dict[str, Any], str]] = []
    notes: list[dict[str, Any]] = []
    for record in files:
        if record.get("kind") == "artifact":
            continue  # 产物不是输入
        text = read_file_text(record.get("stored_path", ""), record.get("sha256", ""))
        loaded.append((record, text))
        if not text:
            notes.append(
                {
                    "id": record.get("id", ""),
                    "filename": record.get("filename", ""),
                    "chars": 0,
                    "injected_chars": 0,
                    "mode": "empty",
                }
            )

    usable = [(record, text) for record, text in loaded if text]
    if not usable:
        return "", notes

    total = sum(len(text) for _, text in usable)
    use_full = total <= inline_max_chars and all(
        len(text) <= _PER_FILE_FULL_MAX for _, text in usable
    )

    parts: list[str] = []
    if use_full:
        for record, text in usable:
            parts.append(f"【文件：{record.get('filename', '')}】\n{text}")
            notes.append(
                {
                    "id": record.get("id", ""),
                    "filename": record.get("filename", ""),
                    "chars": len(text),
                    "injected_chars": len(text),
                    "mode": "full",
                }
            )
    else:
        # 每个文件分到等额预算，避免一个大文件把其他文件挤掉
        budget = max(600, inline_max_chars // max(1, len(usable)))
        for record, text in usable:
            chunks = _rank_chunks(_split(text), goal)
            picked: list[str] = []
            used = 0
            for chunk in chunks:
                if used + len(chunk) > budget and picked:
                    break
                picked.append(chunk)
                used += len(chunk)
            body = "\n…\n".join(picked)
            suffix = "（已按相关性截取）" if used < len(text) else ""
            parts.append(f"【文件：{record.get('filename', '')}】{suffix}\n{body}")
            notes.append(
                {
                    "id": record.get("id", ""),
                    "filename": record.get("filename", ""),
                    "chars": len(text),
                    "injected_chars": used,
                    "mode": "retrieved" if used < len(text) else "full",
                }
            )

    context = "\n\n".join(parts)
    # 硬上限：预算是估算，模型输出不可控，这里做最后一道闸
    hard_cap = int(inline_max_chars * 1.5)
    if len(context) > hard_cap:
        context = context[:hard_cap] + "\n（参考文件内容已截断）"
    return context, notes

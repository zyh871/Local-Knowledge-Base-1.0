"""引用与链接的规范化（设计 §10.2）。

需求 4 要的是「**真实可访问**的搜索链接，而非仅生成内容」。仅靠提示词里写
「请引用来源」是不够的 —— 模型会自己编 URL，而且编得很像真的。因此这里实现三条**机械**保证：

  1. **链接只能来自 Tavily 响应的 `url` 字段**（调用方只把 Tavily 的 href 传进来）
  2. **模型只允许用 `[1]` 编号引用**，正文里出现的任何 URL 一律剔除并告警 ——
     这条是硬保证：模型无法通过"自己写一个链接"来绕过第 1 条
  3. 可达性标记见 `reachability.py`

纯逻辑，不依赖网络与图，因此可以完整单测。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "dedupe_sources",
    "number_sources",
    "render_footer",
    "render_sources_block",
    "sanitize_citations",
]

# 链接匹配：**单次扫描，两个分支**。
#
#  1. 带 scheme 的完整 URL
#  2. 裸域名（模型很爱写 `example.com/p/123` 这种）
#
# 必须用一条正则做一次性替换，而不是"先替换 URL 再替换裸域名"：
# 后者会在已保留的 URL 内部再次匹配到裸域名（`https://a.example/x` → `a.example/x`），
# 而裸域名比对用的是不带 scheme 的形式，于是**把好链接误删**。
#
# 也刻意**不用 TLD 白名单**：白名单永远不全（`example`、`local`、新顶级域都会漏），
# 而漏判的后果是假链接留在正文里。真正的判据是"看着像链接"——
# 有路径、或带 www. 前缀，这样的东西不可能是普通行文。
_LINK_RE = re.compile(
    r"https?://[^\s<>\"'）)】\]，。；、]+"
    r"|(?<![\w.@])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}"
    r"(?:/[^\s<>\"'）)】\]，。；、]*)?",
    re.IGNORECASE,
)


def dedupe_sources(sources: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """按 href 去重，保持首次出现的顺序。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in sources or []:
        if not isinstance(item, dict):
            continue
        href = str(item.get("href") or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        out.append(item)
    return out


def number_sources(sources: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """给来源补上从 1 开始的编号，供 `[1]` 引用与前端锚点使用。"""
    out = []
    for index, item in enumerate(dedupe_sources(sources), start=1):
        out.append({**item, "ref": index})
    return out


def render_sources_block(sources: Optional[list[dict[str, Any]]]) -> str:
    """把来源渲染成拼进提示词的编号清单。

    刻意**带上 href**：模型需要看到链接长什么样，才更可能老实引用而不是自己编。
    但同时在指令里明确禁止输出它们 —— 输出与否由 `sanitize_citations` 兜底。
    """
    items = number_sources(sources)
    if not items:
        return ""
    lines = []
    for item in items:
        title = str(item.get("title") or "").strip() or "（无标题）"
        snippet = str(item.get("snippet") or "").strip()
        snippet = f"\n   {snippet[:200]}" if snippet else ""
        lines.append(f"【来源 {item['ref']}】{title} — {item.get('href')}{snippet}")
    return "\n".join(lines)


def render_footer(sources: Optional[list[dict[str, Any]]]) -> str:
    """Markdown 脚注形式的来源清单（供交付物使用）。"""
    items = number_sources(sources)
    if not items:
        return ""
    lines = ["", "---", "", "**参考链接**", ""]
    for item in items:
        title = str(item.get("title") or item.get("href") or "").strip()
        lines.append(f"{item['ref']}. [{title}]({item.get('href')})")
    return "\n".join(lines)


def sanitize_citations(
    text: str, sources: Optional[list[dict[str, Any]]]
) -> tuple[str, list[str]]:
    """剔除正文里**不在来源集合内**的链接。

    返回 `(净化后的正文, 被剔除的链接)`。

    这是需求 4 里「真实可访问链接」的硬保证：模型即兴写出的 URL 会被删掉，
    留下的只能是 Tavily 真正返回过的地址。**宁可少一个链接，也不要一个假链接** ——
    假链接比没有链接更糟：用户点开是 404，会连带怀疑整份产出。
    """
    if not text:
        return text, []

    allowed = {str(s.get("href") or "").strip() for s in dedupe_sources(sources)}
    allowed = {href for href in allowed if href}
    # 裸域名形式的允许集合，供第 2 分支比对
    allowed_hosts = {
        href.split("://", 1)[1] for href in allowed if "://" in href
    }
    removed: list[str] = []

    def _replace(match: re.Match) -> str:
        text_matched = match.group(0)
        if text_matched.lower().startswith(("http://", "https://")):
            # 结尾标点属于句子，不属于链接
            stripped = text_matched.rstrip(".,;:!?、，。；：！？")
            if stripped in allowed:
                return text_matched
            removed.append(stripped)
            return ""

        # 裸域名分支：只有"看着像链接"的才处理（带路径或 www. 前缀），
        # 否则普通行文里的域名提及会被误删
        host = text_matched
        if "/" not in host and not host.lower().startswith("www."):
            return text_matched
        if host in allowed_hosts or f"www.{host}" in allowed_hosts:
            return text_matched
        removed.append(host)
        return ""

    cleaned = _LINK_RE.sub(_replace, text)

    # 收尾：清理因删除产生的空括号 / 多余空格
    cleaned = re.sub(r"[（(]\s*[）)]", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    if removed:
        logger.warning(
            f"[非法链接剔除] 共剔除 {len(removed)} 个不在来源集合内的链接: "
            f"{removed[:5]}"
        )
    return cleaned, removed

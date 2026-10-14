"""交付物组装（设计 §13）。

## 为什么交付物是**确定性拼装**而不是再调一次 LLM

最后一步（`synthesis`）已经产出了结论文本，交付物的职责是把
「目标 / 约束 / 每一步做了什么 / 结论 / 参考链接」整理成一份可带走的文档。
这件事**不需要模型**：

  * 确定性 → 同样输入永远同样输出，可以单测
  * 不花钱、不耗时、不会失败 —— 而「交付物生成失败」是用户最不能接受的失败
  * 模型该发挥的地方（写内容）已经在步骤里发挥过了

代价是文档读起来像"整理稿"而不是"润色稿"。这个取舍是有意的：
**交付物的价值是完整与可信，不是文采。**

## 参考链接只来自 steps 的 sources

与 §10.2 一致：正文里的 `[n]` 编号引用会在这里被解析成真实链接。
编号对不上来源时**保留原文**，不做猜测 —— 猜错了一个链接比没有链接更糟。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.core.logging import get_logger
from app.workflow.citation import number_sources

logger = get_logger(__name__)

__all__ = [
    "build_deliverable",
    "collect_final_sources",
    "conclusion_of",
    "deliverable_filename",
]

_REF_RE = re.compile(r"\[(\d{1,2})\]")
_STEP_STATUS_LABELS = {
    "done": "已完成",
    "skipped": "已跳过",
    "failed": "失败",
    "pending": "未执行",
    "running": "执行中",
    "approved": "已批准",
    "awaiting_approval": "待确认",
    "rejected": "已驳回",
}


def collect_final_sources(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """汇总全流程的来源（按步骤顺序去重编号）。

    与 `executor.collect_sources()`、前端 `pooledSources` 是**同一条规则** ——
    三处必须一致，否则文档里的 `[1]` 会指到另一个链接。
    """
    pooled: list[dict[str, Any]] = []
    for step in sorted(steps, key=lambda s: s.get("idx", 0)):
        output = step.get("output") or {}
        sources = output.get("sources")
        if isinstance(sources, list):
            pooled.extend(item for item in sources if isinstance(item, dict))
    return number_sources(pooled)


def _resolve_refs(text: str, sources: list[dict[str, Any]]) -> str:
    """把正文里的 `[n]` 变成 Markdown 链接；对不上就原样保留。"""

    def _replace(match: re.Match) -> str:
        index = int(match.group(1))
        if 1 <= index <= len(sources):
            item = sources[index - 1]
            title = str(item.get("title") or item.get("href") or "").strip()
            return f"[[{index}]]({item.get('href')})"
        return match.group(0)

    return _REF_RE.sub(_replace, text)


def deliverable_filename(goal: str, *, suffix: str = "交付物") -> str:
    """由目标派生一个安全的文件名（去掉路径分隔符与过长部分）。"""
    cleaned = re.sub(r"[\\/:*?\"<>|\n\r\t]+", " ", goal or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = suffix
    # 按字符截断：中文标题 24 字已经足够识别，太长会让文件系统难处理
    return f"{cleaned[:24]}.md"


def conclusion_of(steps: list[dict[str, Any]]) -> tuple[Optional[int], str]:
    """判定「结论」是第几步、正文是什么。返回 `(idx, 正文)`。

    规则：优先最后一个有产出的 `synthesis` 步；没有就用最后一个有产出的步骤。

    ⚠️ 抽成公共函数是因为**交付物与分享页必须用同一套规则**：
    分享页也要避免把结论在「执行明细」里重复展示一遍，
    而它需要知道"哪一步的正文已经作为结论出现过了"。
    两处各写一套判断必然漂移，最终表现为「页面和文件说的不是同一件事」。
    """
    fallback: Optional[tuple[int, str]] = None
    for step in sorted(steps, key=lambda s: s.get("idx", 0))[::-1]:
        body = str((step.get("output") or {}).get("summary") or "").strip()
        if not body:
            continue
        idx = int(step.get("idx") or 0)
        if step.get("kind") == "synthesis":
            return idx, body
        if fallback is None:
            fallback = (idx, body)
    return fallback if fallback else (None, "")


def build_deliverable(
    plan: Optional[dict[str, Any]],
    steps: list[dict[str, Any]],
    *,
    run_title: str = "",
    raw_input: str = "",
) -> str:
    """把任务书与各步产出拼成一份 Markdown 交付物。"""
    plan = plan or {}
    ordered = sorted(steps, key=lambda s: s.get("idx", 0))
    sources = collect_final_sources(ordered)

    lines: list[str] = []
    goal = str(plan.get("goal") or run_title or raw_input or "任务交付物").strip()
    lines.append(f"# {goal}")
    lines.append("")

    audience = str(plan.get("audience") or "").strip()
    if audience:
        lines.append(f"**受众 / 用途**：{audience}  ")
    if raw_input:
        lines.append(f"**原始需求**：{raw_input}  ")
    done = sum(1 for s in ordered if s.get("status") == "done")
    truncated_count = sum(1 for s in ordered if (s.get("output") or {}).get("truncated"))
    summary_line = f"**执行情况**：{done} / {len(ordered)} 步完成"
    if truncated_count:
        # 放在最顶部：读的人该在第一眼就知道这份交付物有段落是没写完的
        summary_line += f"，其中 **{truncated_count} 步输出被截断**（下文已标注）"
    lines.append(summary_line + "  ")
    lines.append("")

    constraints = plan.get("constraints") or []
    if constraints:
        lines.append("## 约束")
        lines.append("")
        for item in constraints:
            lines.append(f"- {item}")
        lines.append("")

    assumptions = plan.get("assumptions") or []
    if assumptions:
        lines.append("## 假设")
        lines.append("")
        for item in assumptions:
            lines.append(f"- {item}")
        lines.append("")

    # 结论：与分享页共用同一套判定（见 conclusion_of）
    conclusion_idx, conclusion_text = conclusion_of(ordered)

    if conclusion_text:
        lines.append("## 结论")
        lines.append("")
        lines.append(_resolve_refs(conclusion_text, sources))
        lines.append("")

    lines.append("## 执行明细")
    lines.append("")
    for step in ordered:
        status = _STEP_STATUS_LABELS.get(step.get("status", ""), step.get("status", ""))
        lines.append(f"### {step.get('idx')}. {step.get('title') or '（无标题）'}")
        lines.append("")
        lines.append(f"- 类型：{step.get('kind')}  ")
        lines.append(f"- 状态：{status}  ")
        action = step.get("action") or {}
        if action.get("tool"):
            lines.append(f"- 动作：`{action.get('tool')}`  ")
        output = step.get("output") or {}
        # ⚠️ 截断必须标出来。此前这里只写状态，于是「模型没写完」的部分
        # 被当成完整内容交付 —— 读的人无从分辨。
        if output.get("truncated"):
            lines.append("- ⚠️ **此段可能不完整**（模型输出被截断）  ")
        body = str(output.get("summary") or "").strip()
        if body:
            if conclusion_text and step.get("idx") == conclusion_idx:
                # ★ 不重复：这一段已经作为「结论」完整出现在上面。
                # 曾经这里把同一个 synthesis 步的正文再列一遍，
                # 实测让交付物里约 1/3 的文本是重复的（59,343 字符的交付物里
                # 有 18,634 字符来自这一步）。
                lines.append("")
                lines.append("_（正文见上方「结论」，此处不再重复。）_")
            else:
                lines.append("")
                lines.append(_resolve_refs(body, sources))
        elif step.get("error"):
            lines.append(f"- 失败原因：{step['error']}  ")
        if step.get("retry_count"):
            lines.append(f"- 重试次数：{step['retry_count']}  ")
        lines.append("")

    if sources:
        lines.append("## 参考链接")
        lines.append("")
        for item in sources:
            title = str(item.get("title") or "").strip() or item.get("href", "")
            flag = ""
            if item.get("reachable") is False:
                flag = "（探测时可能已失效）"
            lines.append(f"{item['ref']}. [{title}]({item.get('href')}){flag}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_本文件由 AI 工作流自动生成；链接均取自检索结果，未校验可达性的条目标为「未校验」。_")
    lines.append("")
    return "\n".join(lines)

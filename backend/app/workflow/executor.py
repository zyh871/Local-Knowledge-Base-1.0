"""单步执行：提示词组装、工具结果归一化、执行策略判定。

与 `planner.py` 同样的分层意图 —— 这里只有纯逻辑，不依赖 graph / services /
repositories，因此可以在没有任何图与数据库的环境下完整单测。

## 为什么要把「上一步的产出」拼进提示词

工作流的价值在于**步骤之间真的有依赖**。如果每一步都是独立的一次生成，
它就不是工作流，只是把一次长回答切成了几段。因此这一步的提示词里必须带上
前面各步的产出摘要，否则第 3 步不会知道第 1、2 步发现了什么。

## 为什么工具结果要归一化

`web_search` 的返回值里带 `sources`（Tavily 原始 `url`），这是**需求 4「真实可访问
链接」的唯一来源**。归一化的职责就是把这些字段原样保留到 `output.sources`，
而不是让内容散落在一个大字符串里 —— 后者前端拿不到结构化的链接。
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.logging import get_logger
from app.workflow.citation import number_sources

logger = get_logger(__name__)

__all__ = [
    "MAX_PRIOR_OUTPUT_CHARS",
    "MAX_PRIOR_OUTPUT_CHARS_CODE",
    "STEP_SYSTEM_PROMPT",
    "build_step_prompt",
    "collect_sources",
    "normalize_tool_output",
    "plan_step_shape",
    "prior_budget",
    "prior_context",
    "search_calls_used",
    "settled_steps",
    "tool_names",
    "tool_required_args",
]


def tool_names() -> list[str]:
    """当前注册的工具名（供提示词约束模型、供前端渲染下拉）。"""
    try:
        from app.tools.base import get_registry

        return sorted(get_registry().list_names())
    except Exception as exc:  # noqa: BLE001 - 工具注册失败不应阻断整个流程
        logger.warning(f"读取工具清单失败: {exc}")
        return []


def tool_required_args() -> dict[str, list[str]]:
    """工具名 → 必填参数名。

    用于在**审批阶段**就指出「这一步的参数不全」，而不是等执行到那里才报错 ——
    那时候前面几步的时间与 token 已经花掉了，而且「哪一步有问题」
    本该在计划里就能看到。
    """
    try:
        from app.tools.base import get_registry

        schemas = get_registry().to_function_schemas()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取工具 schema 失败: {exc}")
        return {}
    out: dict[str, list[str]] = {}
    for schema in schemas:
        params = schema.get("parameters") or {}
        required = [str(x) for x in (params.get("required") or [])]
        if required:
            out[str(schema.get("name", ""))] = required
    return out

# 单步产出回填给后续步骤时的截断长度。不截断的话，第 6 步的提示词里
# 会塞进前 5 步的全部原文，上下文迅速失控。
MAX_PRIOR_OUTPUT_CHARS = 1200
#: 涉及代码时的回填上限。
#:
#: 1200 字符对散文够用，对代码却是"腰斩" —— 一段 60 行的脚本轻松超过 1200 字符，
#: 后续步骤拿到半截代码，只能基于残缺上下文继续编。
#: 「代码类的产出必须整段可见」是让多步代码任务能跑通的前提。
MAX_PRIOR_OUTPUT_CHARS_CODE = 8000
# 工具原始返回落地时的截断长度
MAX_TOOL_SUMMARY_CHARS = 6000

STEP_SYSTEM_PROMPT = """你正在执行一个多步骤任务中的**第 {idx} 步（共 {total} 步）**。

【任务目标】
{goal}

【受众 / 用途】
{audience}

【硬约束】
{constraints}

【前序步骤的产出】
{prior}

{sources_block}{context_block}【你这一步要做的事】
{title}
{intent_line}

要求：
- 只输出**这一步**的产出。不要复述整体计划，不要写「接下来我会…」，不要预告后续步骤。
- 如果前序产出已经包含所需信息，直接基于它完成本步，不要再要求补充信息。
- 输出使用与目标一致的语言。{style_hint}

【引用规则（必须遵守）】
- 需要标注信息出处时，**只能**写成 `[1]`、`[2]` 这样的编号引用，编号对应上面的【来源 N】。
- **绝对不要输出任何 URL 字符串**，也不要写 Markdown 链接语法 `[文字](链接)`。
  后端会自动把编号渲染成可点击链接；你自己写的链接会被系统剔除，等于白写。
- 只能引用上面列出的来源。没有列出的来源就是不存在，不要凭记忆补一个。"""


def collect_sources(
    steps: list[dict[str, Any]],
    *,
    include: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """汇总前序步骤搜到的来源，按 href 去重并编号。

    步骤之间有依赖：第 1 步搜到的链接，第 3 步写正文时要能引用到。
    不汇总的话，后面每一步都得重新搜一遍 —— 既费配额又会让引用对不上。
    """
    pooled: list[dict[str, Any]] = []
    for step in steps or []:
        status = step.get("status")
        if status not in ("done", "running"):
            continue
        output = step.get("output") or {}
        sources = output.get("sources")
        if isinstance(sources, list):
            pooled.extend(item for item in sources if isinstance(item, dict))
    if include:
        extra = include.get("sources")
        if isinstance(extra, list):
            pooled.extend(item for item in extra if isinstance(item, dict))
    return number_sources(pooled)



def _join_list(values: Any, default: str = "（无）") -> str:
    if not values:
        return default
    if isinstance(values, str):
        return values.strip() or default
    try:
        items = [str(v).strip() for v in values if str(v).strip()]
    except TypeError:
        return default
    return "；".join(items) if items else default


def prior_context(steps: list[dict[str, Any]], *, max_chars: int = MAX_PRIOR_OUTPUT_CHARS) -> str:
    """把已完成步骤的产出拼成上下文。

    只取 `done` 的步骤；失败/跳过的步骤会以一行说明出现 ——
    后续步骤需要知道「前面这步没做成」，否则会基于不存在的输入继续编。
    """
    lines: list[str] = []
    for step in steps:
        status = step.get("status")
        if status == "done":
            output = step.get("output") or {}
            summary = str(output.get("summary") or "").strip()
            if not summary:
                continue
            if len(summary) > max_chars:
                summary = summary[:max_chars] + "…（已截断）"
            lines.append(f"· 第 {step.get('idx')} 步「{step.get('title', '')}」的产出：\n{summary}")
        elif status == "skipped":
            lines.append(f"· 第 {step.get('idx')} 步「{step.get('title', '')}」被跳过，没有产出。")
        elif status == "failed":
            lines.append(
                f"· 第 {step.get('idx')} 步「{step.get('title', '')}」执行失败"
                f"（{step.get('error') or '未知原因'}），没有产出。"
            )
    return "\n\n".join(lines) if lines else "（这是第一步，暂无前序产出）"


def prior_budget(step: dict[str, Any], steps: list[dict[str, Any]]) -> int:
    """本步回填前序产出时用多大上限。

    判据是「这一步或前面某一步在跟代码打交道」：只要出现代码围栏，
    或者本步就是 `file_write`，就放宽到 `MAX_PRIOR_OUTPUT_CHARS_CODE`。
    代码被腰斩是**静默失效**——模型不会报错，它会基于残缺的代码继续写，
    产出一个"看起来对、跑不通"的东西。
    """
    action = step.get("action") or {}
    if step.get("kind") == "tool" and action.get("tool") in ("file_write", "code_executor"):
        return MAX_PRIOR_OUTPUT_CHARS_CODE
    for item in steps or []:
        summary = str((item.get("output") or {}).get("summary") or "")
        if "```" in summary:
            return MAX_PRIOR_OUTPUT_CHARS_CODE
    return MAX_PRIOR_OUTPUT_CHARS


def build_step_prompt(
    *,
    plan: dict[str, Any],
    step: dict[str, Any],
    steps: list[dict[str, Any]],
    file_context: str = "",
) -> str:
    """组装单步执行的提示词（仅用于 `llm` / `synthesis` 步）。"""
    action = step.get("action") or {}
    intent = str(action.get("prompt_ref") or "").strip()
    title = str(step.get("title") or "").strip() or f"第 {step.get('idx')} 步"

    total = len(steps) or 1
    is_last = step.get("idx") == total
    style_hint = ""
    if step.get("kind") == "synthesis" or is_last:
        style_hint = (
            "\n- 这是最后一步：请直接给出**可交付的最终内容**，"
            "不要再提出问题或列出后续动作。"
        )

    context_block = ""
    if file_context:
        context_block = f"【参考文件内容】\n{file_context}\n\n"

    from app.workflow.citation import render_sources_block

    sources = collect_sources(steps)
    sources_block = (
        f"【可用来源（**只能引用这些**）】\n{render_sources_block(sources)}\n\n"
        if sources
        else "【可用来源】\n（本次没有可引用的检索结果，请勿编造链接）\n\n"
    )

    return STEP_SYSTEM_PROMPT.format(
        idx=step.get("idx", 1),
        total=total,
        goal=str(plan.get("goal") or "").strip() or "（未指定）",
        audience=str(plan.get("audience") or "").strip() or "（未指定）",
        constraints=_join_list(plan.get("constraints")),
        prior=prior_context(steps, max_chars=prior_budget(step, steps)),
        sources_block=sources_block,
        context_block=context_block,
        title=title,
        intent_line=f"（本步意图：{intent}）" if intent and intent != title else "",
        style_hint=style_hint,
    )


def normalize_tool_output(name: str, result: Any) -> dict[str, Any]:
    """把工具返回值归一化成 `{summary, sources, data}`。

    `sources` 必须**原样保留**：里面是 Tavily 返回的真实 `url`，
    前端要靠它渲染可点击链接（需求 4）。绝不在这里做拼字符串的裁剪。
    """
    if not isinstance(result, dict):
        return {"summary": str(result)[:MAX_TOOL_SUMMARY_CHARS]}

    sources = result.get("sources")
    sources = sources if isinstance(sources, list) else []

    if not result.get("success", True):
        return {
            "summary": str(result.get("error") or "工具执行失败"),
            "sources": sources,
            "failed": True,
        }

    text = result.get("result")
    if text is None:
        # 有些工具只回结构体，没有 result 字段
        text = result.get("output") or result.get("data") or ""
    summary = text if isinstance(text, str) else str(text)
    summary = summary.strip()
    if len(summary) > MAX_TOOL_SUMMARY_CHARS:
        summary = summary[:MAX_TOOL_SUMMARY_CHARS] + "\n…（工具输出已截断）"

    output: dict[str, Any] = {"summary": summary}
    if sources:
        output["sources"] = sources
    if result.get("instant_answer"):
        output["instant_answer"] = result["instant_answer"]
    if not summary and not sources:
        output["summary"] = f"（{name} 没有返回可用内容）"
    return output


def search_calls_used(steps: list[dict[str, Any]]) -> int:
    """统计本 run 已经用掉的联网搜索次数（含当前步）。"""
    count = 0
    for step in steps:
        action = step.get("action") or {}
        if action.get("tool") != "web_search":
            continue
        if step.get("status") in ("done", "running", "approved"):
            count += 1
    return count


def needs_gate(
    *,
    step: dict[str, Any],
    mode: str,
    forced_tools: list[str],
) -> bool:
    """这一步执行前是否需要人工确认（**唯一**的策略实现）。

    规则（设计 §7.2）：
      * 人工模式 + `requires_approval` → 需要
      * 命中强制审批白名单的工具 → **无论什么模式都需要**

    服务端用它做准入判定，同时把这个结果下发给前端用于展示 ——
    必须是同一份实现，否则前端会「以为可以自动跑」而连续撞 409。
    """
    action = step.get("action") or {}
    tool = action.get("tool")
    if tool and tool in forced_tools:
        return True
    return mode == "manual" and bool(step.get("requires_approval"))


def plan_step_shape(step: dict[str, Any]) -> dict[str, Any]:
    """把 `steps` 表的一行收敛成**任务书里**的步骤形状。

    `run.plan.steps` 与 `steps` 表是同一份数据的两种投影，必须保持同步 ——
    否则「界面上的任务书」与「实际会被执行的步骤」会不一致，
    而这正是最难被发现的一类错误。所有写 `steps` 表的地方都要用它回填 `plan`。
    """
    return {
        "idx": step.get("idx", 0),
        "kind": step.get("kind", "llm"),
        "title": step.get("title", ""),
        "action": step.get("action") or {},
        "requires_approval": bool(step.get("requires_approval")),
        "status": step.get("status", "pending"),
    }


def settled_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """已终结（done / skipped）的步骤。

    重新规划时要保留它们 —— 否则「驳回第 3 步重规划」会把前两步已经跑出来的
    产出一起丢掉，用户得从头再等一遍。
    """
    return [s for s in steps if s.get("status") in ("done", "skipped")]


def runnable_next_step(
    steps: list[dict[str, Any]],
    *,
    mode: str = "manual",
    forced_tools: tuple[str, ...] | list[str] = (),
) -> Optional[dict[str, Any]]:
    """按顺序找下一个**现在就能执行**的步骤，找不到返回 None。

    两条判定都要考虑，缺一不可：
      * **顺序**：只向前走。遇到未终结的步骤就停下，不跳到后面 ——
        步骤之间有依赖，跳步会产出基于缺失输入的结果。
      * **门禁**：一个 `pending` 的步骤若需要人工确认，它现在**执行不了**，
        因此返回 None 而不是它。否则前端会拿到一个必然撞 409 的序号，
        表现为「按钮能点但总报错」。
    """
    forced = list(forced_tools)
    for step in sorted(steps, key=lambda s: s.get("idx", 0)):
        status = step.get("status")
        # `skipped` 与 `done` 同样是**已终结**。曾经漏掉这一支，
        # 导致跳过中间某步之后，后面所有步骤再也拿不到执行许可（整个计划卡死）。
        if status in ("done", "skipped"):
            continue
        if status == "approved":
            return step
        if status == "pending":
            if needs_gate(step=step, mode=mode, forced_tools=forced):
                return None  # 在等人工确认
            return step
        return None  # awaiting_approval / running / failed / rejected
    return None

"""需求澄清：结构化问题卡。

## 为什么是「问题卡」而不是自由追问

自由追问的形式（「你能说得更具体一点吗？」）有两个问题：用户不知道要说到什么程度，
模型也不知道该在哪停。改成**结构化问题卡**之后：

  * 每题给 2-4 个选项 → 用户点一下就行，输入成本从「组织语言」降到「做选择」
  * **每题必带推荐答案** → 把澄清从「审讯」变成「确认」，
    这直接对应需求里说的「像 `/grill-me` 这类技能」—— 该技能的交互契约就是
    「先给推荐答案，再提问」
  * 有明确的**终止条件**（三项信息齐备），不会无限问下去

## 与规划一样的分层

宽容解析（模型可能给出各种形状）+ 严格校验（收敛成固定结构、修正留痕）。
两者都**不依赖** graph / services，因此可以完整单测。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.core.exceptions import ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "CLARIFY_SYSTEM_PROMPT",
    "MAX_QUESTIONS_PER_ROUND",
    "build_clarify_prompt",
    "format_clarifications",
    "normalize_questions",
]

#: 单轮最多问几题。再多就变成填表了 —— 用户会直接点「跳过」。
MAX_QUESTIONS_PER_ROUND = 3
MAX_OPTIONS_PER_QUESTION = 5


class ClarifyParseError(ValidationError):
    code = "clarify_parse_failed"
    message = "无法从模型输出中解析出澄清问题"


CLARIFY_SYSTEM_PROMPT = """你是需求澄清助手。用户给了一个任务目标，你负责判断信息是否足够；
不足时，提出**最关键的**几个问题。

输出格式（**严格按这个顺序**）：

1. 先用 1-2 句话说明你还缺什么（如果信息已经够了，就说明你的判断）。
2. 然后输出一个 ```json 代码块：

```json
{
  "sufficient": false,
  "questions": [
    {
      "question": "给用户看的问题，一句话",
      "options": [
        {"label": "给用户看的选项文字", "value": "简短标识"},
        {"label": "另一个选项", "value": "another"}
      ],
      "recommended": "one_of_the_values",
      "reason": "为什么推荐这个（一句话）"
    }
  ]
}
```

规则：
- **每个问题都必须给出 `recommended`**，取值为该题某个选项的 `value`。
  推荐必须基于你对目标的理解，不要敷衍地选第一个。
- 每题给 2-4 个选项，覆盖常见的取舍。用户还能自由输入，所以选项不必穷尽。
- **只问真正影响执行结果的问题**。不要问「你想用什么格式」这类你可以合理假设的事 ——
  拿不准就按最合理的默认做，然后写进任务书的 assumptions。
- 最多 $max_questions 个问题。**宁可少问，不要凑数**。
- 如果信息已经足够（你知道要产出什么、给谁看、有什么约束），把 `sufficient` 设为 true，
  `questions` 留空数组。
- 已经问过的内容不要重复问。"""


def build_clarify_prompt(
    *,
    raw_input: str,
    file_summaries: Optional[list[dict[str, Any]]] = None,
    folder_listing: str = "",
    history: Optional[list[dict[str, Any]]] = None,
    round_no: int = 1,
    max_rounds: int = 3,
) -> str:
    """组装澄清提示词。

    `history` 是此前的问答轮次 —— 带上它才能做到「已经问过的不要重复问」，
    否则第二轮会换种说法再问一遍同样的事，用户会立刻失去耐心。

    ⚠️ **刻意不把工作区名放进提示词。** 工作区名是归类标签，不是任务主题。
    实测：原始输入为「帮我做一份调研」（没有任何主题）时，模型会抓住上下文里
    唯一的实体名词 —— 工作区名 —— 把它当成调研对象，产出
    「了解用户对 P4 界面的满意度」这种凭空捏造的主题。
    加一句「不是任务主题」的否定指令压不住它（模型总得有东西可讲）。
    移除这个误导源之后，模型会正确地反问「这份调研的主题是什么？」——
    那才是澄清该做的事。
    """
    parts: list[str] = [
        CLARIFY_SYSTEM_PROMPT.replace("$max_questions", str(MAX_QUESTIONS_PER_ROUND))
    ]

    meta: list[str] = [f"这是第 {round_no} / {max_rounds} 轮澄清。"]
    if file_summaries:
        lines = ["用户提供了以下参考文件："]
        for item in file_summaries:
            chars = item.get("parsed_chars")
            lines.append(
                f"- {item.get('filename', '')}"
                + (f"（已解析 {chars} 字）" if chars else "（未能解析出文本）")
            )
        meta.append("\n".join(lines))

    if history:
        asked = format_clarifications(history)
        if asked:
            meta.append(f"【已经问过并得到答复的内容】\n{asked}")

    parts.append("【背景】\n" + "\n".join(meta))
    parts.append(f"【任务目标】\n{raw_input.strip()}")
    # 工作区文件夹的**清单**（只有名称与大小，内容未加入）。
    # 没有它，用户说「整理这个文件夹里的文件」时模型只能反问「哪个文件夹？」
    if folder_listing:
        parts.append(folder_listing)
    return "\n\n".join(parts)


def format_clarifications(rounds: Optional[list[dict[str, Any]]]) -> str:
    """把澄清轮次压成可读文本。

    两个用途：拼进澄清提示词（避免重复提问）、拼进任务书提示词（让计划体现用户的选择）。
    """
    if not rounds:
        return ""
    lines: list[str] = []
    for item in rounds:
        answers = item.get("answers") or {}
        for question in item.get("questions") or []:
            qid = question.get("id")
            answer = answers.get(qid) or {}
            text = str(answer.get("text") or "").strip()
            value = str(answer.get("value") or "").strip()
            chosen = text or value
            if not chosen:
                continue
            label = chosen
            for option in question.get("options") or []:
                if value and option.get("value") == value and not text:
                    label = str(option.get("label") or value)
                    break
            lines.append(f"· {question.get('question', '')}\n  → 用户选择：{label}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
#  严格校验
# ---------------------------------------------------------------------------


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return ""


def _slugify(value: str, index: int) -> str:
    """把选项文字压成简短标识。"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "_", value).strip("_")
    return cleaned[:24] or f"opt{index}"


def _normalize_options(raw: Any) -> list[dict[str, str]]:
    """把选项收敛成 `[{label, value}]`。

    模型可能给字符串数组、也可能给对象数组（`{label,value}` / `{text}`）。
    字符串形式的 value 由 label 派生 —— 要求模型自己保证两者一致是多余的负担。
    """
    options: list[dict[str, str]] = []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return options

    for index, item in enumerate(raw, start=1):
        if isinstance(item, dict):
            label = _as_str(item.get("label") or item.get("text") or item.get("title"))
            value = _as_str(item.get("value") or item.get("id"))
        else:
            label = _as_str(item)
            value = ""
        if not label and not value:
            continue
        label = label or value
        value = value or _slugify(label, index)
        options.append({"label": label, "value": value})
        if len(options) >= MAX_OPTIONS_PER_QUESTION:
            break
    return options


def normalize_questions(
    raw: dict[str, Any],
    *,
    max_questions: int = MAX_QUESTIONS_PER_ROUND,
    allow_freeform: bool = True,
) -> tuple[bool, list[dict[str, Any]], list[str]]:
    """把模型输出收敛成 (sufficient, questions, issues)。

    `sufficient=True` 表示「信息已足够」—— 调用方据此结束澄清、直接进入规划。
    """
    issues: list[str] = []
    if not isinstance(raw, dict):
        raise ClarifyParseError("澄清结果不是一个 JSON 对象")

    # sufficient 的键名各家模型写法不一，都兜住
    flag = raw.get("sufficient")
    if flag is None:
        flag = raw.get("complete", raw.get("done", raw.get("enough")))
    sufficient = bool(flag) if isinstance(flag, bool) else str(flag or "").lower() in (
        "true",
        "1",
        "yes",
        "是",
    )

    raw_questions = raw.get("questions")
    if raw_questions is None:
        raw_questions = raw.get("items", raw.get("asks", raw.get("clarifications")))
    if isinstance(raw_questions, dict):
        raw_questions = list(raw_questions.values())
    if raw_questions is None:
        raw_questions = []
    if not isinstance(raw_questions, (list, tuple)):
        raise ClarifyParseError("澄清问题的格式不正确")

    if len(raw_questions) > max_questions:
        issues.append(
            f"模型给出了 {len(raw_questions)} 个问题，超过单轮上限 {max_questions}，已保留前 {max_questions} 个"
        )
        raw_questions = list(raw_questions)[:max_questions]

    questions: list[dict[str, Any]] = []
    for index, item in enumerate(raw_questions, start=1):
        if not isinstance(item, dict):
            issues.append(f"第 {index} 个问题不是对象，已跳过")
            continue

        text = _as_str(
            item.get("question") or item.get("text") or item.get("title") or item.get("q")
        )
        if not text:
            issues.append(f"第 {index} 个问题没有内容，已跳过")
            continue

        options = _normalize_options(item.get("options") or item.get("choices"))
        qid = f"q{len(questions) + 1}"
        recommended = _as_str(item.get("recommended") or item.get("recommend") or item.get("default"))

        if options:
            values = {opt["value"] for opt in options}
            if recommended not in values:
                # 推荐值必须落在选项里，否则前端点不到它
                fallback = options[0]["value"]
                if recommended:
                    issues.append(
                        f"第 {index} 题的推荐答案「{recommended}」不在选项里，已改为第一个选项"
                    )
                recommended = fallback
        elif recommended:
            # 没有选项但有推荐 → 把推荐本身变成唯一选项
            options = [{"label": recommended, "value": _slugify(recommended, 1)}]
            recommended = options[0]["value"]
        elif not allow_freeform:
            issues.append(f"第 {index} 题没有选项，已跳过")
            continue

        if not options and not allow_freeform:
            continue

        question: dict[str, Any] = {
            "id": qid,
            "question": text,
            "options": options,
        }
        if recommended:
            question["recommended"] = recommended
        reason = _as_str(item.get("reason") or item.get("why"))
        if reason:
            question["reason"] = reason
        questions.append(question)

    # 一个问题都没解析出来但声明了 sufficient → 尊重声明；
    # 否则视为解析失败，不能静默当作「够了」把用户直接推到规划阶段
    if not questions and not sufficient:
        raise ClarifyParseError("模型没有给出任何可用的澄清问题")

    return sufficient, questions, issues

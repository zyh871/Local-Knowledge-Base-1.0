"""任务书生成：宽容解析 + 严格校验。

## 为什么是「宽容解析 + 严格校验」而不是「强约束 schema」

让 LLM 按 JSON Schema 硬产出，在**信息不足**时它会为了填满字段而编造内容 ——
而任务书的价值恰恰在于「明确说出哪里还没想清楚」。因此这里的策略是：

  1. **提示词只给形状与示例**，允许模型先讲一段推理再给 JSON；
  2. **解析宽容**：剥掉 ``` 围栏、容忍前后废话、容忍键名变体
     （`steps` / `plan` / `tasks`）、容忍 `constraints` 是字符串而非数组、
     容忍尾随逗号；
  3. **校验严格**：任何偏离都在 `normalize_plan()` 里被**显式修正**，
     并留下一条人类可读的 `issues`。

★ 关键点：**修正必须被看见**。静默纠正是危险的 —— 用户会以为自己在批准
模型原本的计划，实际上批准的是被我们改写过的版本。所以每个修正都进 `issues`，
前端必须展示。

## 分层

本模块不依赖 graph / services / api / repositories，只用标准库与
`app.core.exceptions`，所以解析与校验逻辑可以脱离一切运行时单测。
"""

from __future__ import annotations

import json
import re
from string import Template
from typing import Any, Optional

from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.workflow.tool_args import normalize_tool_args

logger = get_logger(__name__)

__all__ = [
    "STEP_KIND_ALIASES",
    "ReasoningSplitter",
    "build_plan_prompt",
    "extract_json_object",
    "normalize_plan",
]

STEP_KINDS = ("tool", "llm", "synthesis", "unplanned")

# 模型常见的越界说法 → 收敛到我们的四类
STEP_KIND_ALIASES: dict[str, str] = {
    "tool": "tool",
    "tool_call": "tool",
    "function": "tool",
    "search": "tool",
    "retrieval": "tool",
    "retrieve": "tool",
    "query": "tool",
    "llm": "llm",
    "generate": "llm",
    "generation": "llm",
    "write": "llm",
    "draft": "llm",
    "analysis": "llm",
    "analyses": "llm",
    "reasoning": "llm",
    "synthesis": "synthesis",
    "summarize": "synthesis",
    "summary": "synthesis",
    "final": "synthesis",
    "deliver": "synthesis",
    "report": "synthesis",
    "unplanned": "unplanned",
}

# 工具名变体（模型有时会写成 web-search / 联网搜索）
TOOL_ALIASES: dict[str, str] = {
    "web_search": "web_search",
    "websearch": "web_search",
    "web-search": "web_search",
    "search_web": "web_search",
    "联网搜索": "web_search",
    "搜索": "web_search",
    "knowledge_query": "knowledge_query",
    "kb_search": "knowledge_query",
    "knowledge_base": "knowledge_query",
    "知识库检索": "knowledge_query",
    "knowledge": "knowledge_query",
    "calculator": "calculator",
    "calc": "calculator",
    "计算器": "calculator",
    "code_executor": "code_executor",
    "code": "code_executor",
    "python": "code_executor",
    "代码执行": "code_executor",
}


class PlanParseError(ValidationError):
    code = "plan_parse_failed"
    message = "无法从模型输出中解析出任务书"


# ---------------------------------------------------------------------------
#  提示词
# ---------------------------------------------------------------------------

# 注意：用 string.Template 而不是 str.format ——
# 提示词里含 JSON 示例，`{}` 会被 format 当成占位符并抛 KeyError。
PLAN_SYSTEM_PROMPT = Template("""你是任务规划助手。用户会给你一个目标，你要把它拆成可以逐步执行的步骤清单。

输出格式（**严格按这个顺序**）：

1. 先用 2-4 句话说明你的规划思路，包括你对目标的理解和你的取舍。
2. 然后输出一个 ```json 代码块，内容如下：

```json
{
  "goal": "<一句话说明最终要产出什么>",
  "audience": "<这份产物的读者或用途；不确定就写最可能的>",
  "constraints": ["<硬约束，例如字数/格式/语言；没有就留空数组>"],
  "assumptions": ["<你为了让计划成立而做的假设；目标明确就留空数组>"],
  "steps": [
    {
      "kind": "tool | llm | synthesis",
      "title": "<一句话说清这一步要做什么（给人看，不要写代码）>",
      "action": {"tool": "工具名", "args": {"参数名": "参数值"}},
      "requires_approval": false
    }
  ]
}
```

**上面 `<>` 里的是字段格式示意，不是内容。** 必须把整个 `"<...>"` 换成真实值，
不要把尖括号、也不要示意文字留在结果里。数组的每个元素都是一个**字符串或对象**，
不要写成 `["键": "值"]` 这种形式（那不是合法 JSON）。

规则：
- `steps` 里 `kind` 的取值：
  - `tool` —— 调用一个工具，`action` 必须是 `{"tool": 名字, "args": {...}}`
  - `llm` —— 一次纯生成（起草、抽取、分析），`action` 写成 `{"prompt_ref": "这一步的意图"}`
  - `synthesis` —— 汇总出最终交付内容
- **可用的工具只有这些**（不要发明新工具）：$tools
- **工具的用途要匹配**：`calculator` 只做数学表达式求值（必须给出 `expression`），
  `web_search` 只做联网检索，`knowledge_query` 只查本地知识库。
  不要用 `calculator` 去建表、统计或整理文本 —— 那些是 `llm` / `synthesis` 步的事。
- `action` 必须是**声明式**的：写清楚「将要做什么」，而不是「做过什么」。
  例如 `{"tool": "web_search", "args": {"query": "2026 国产大模型定价"}}`，
  不要写 `{"tool": "web_search", "args": {"query": "搜索一下相关的信息"}}`。
- 步骤总数不超过 $max_steps 步。**不要为了凑数而拆碎**，一步能做完的事不要拆成三步。
- **不要为了「检查 / 确认 / 审查」单独拆出步骤**。这类步骤没有产出，
  只会把审批次数翻倍。要把关就在对应的产出步骤里说明验收点，写进 `constraints`。
- `code_executor` 只用于真正的计算或数据处理。它**不能写文件**
  （沙箱已禁止 `open()`），也不要用它做存在性检查。
- **要产出文件（脚本、配置、文档…）时用 `file_write`，不要绕道 `code_executor`。**
  推荐写成两步，既省 token 又不丢内容：
  1. `kind: "llm"` —— 写出完整内容（代码要完整，不要用省略号占位）；
  2. `kind: "tool"` + `file_write` —— `{"path": "fib.py", "content_from_step": <第 1 步的序号>}`。
  `path` 只写文件名，不要写目录；`file_write` 会把文件写进任务产物目录供用户下载。
- 如果用户要求「代码能跑」，在 `file_write` 之后再加一步 `code_executor` 做冒烟测试。
- 只把真正需要人工确认的步骤标 `requires_approval: true`（例如会写文件、会执行代码、
  或者方向不确定需要人把关的），其余保持 false。
- 如果目标里有不清楚的地方，不要追问，把你的处理方式写进 `assumptions`。""")


def build_plan_prompt(
    *,
    raw_input: str,
    mode: str,
    force_search: bool,
    tools: list[str],
    max_steps: int,
    file_summaries: Optional[list[dict[str, Any]]] = None,
    file_context: str = "",
    folder_listing: str = "",
    clarifications: str = "",
    prior_steps: str = "",
    replan_from: int = 0,
    reject_comment: str = "",
) -> str:
    """组装任务书生成的提示词。

    `clarifications` 是澄清阶段的问答摘要（`clarifier.format_clarifications()`）——
    **必须带进来**，否则用户前面花时间做的选择会被整个丢掉，
    计划又回到了「基于原始一句话」的水平。这也是需求 1 里
    「优化用户输入为可执行任务描述」的实际落点。

    ⚠️ 同 `clarifier`：**不放工作区名**。它是归类标签而非主题，
    在原始输入没有具体主题时会被模型当成主题。

    `prior_steps` / `replan_from` / `reject_comment` 用于**从某一步起重新规划**：
    已完成的前序步骤必须告诉模型（否则它会重复规划已经做过的事），
    并且要说明新的计划只需覆盖第 `replan_from` 步及之后。
    """
    parts: list[str] = [
        PLAN_SYSTEM_PROMPT.substitute(
            tools="、".join(tools) or "（无）", max_steps=max_steps
        )
    ]

    meta: list[str] = [f"执行模式：{'人工逐步审查' if mode == 'manual' else 'AI 自动执行'}"]
    if force_search:
        meta.append("已开启联网搜索，计划中应包含使用 web_search 的步骤")

    if prior_steps:
        meta.append(
            "【已经完成的前序步骤（**不要重复规划它们**）】\n"
            f"{prior_steps}\n"
            "这些步骤的产出已经拿到，新计划应当**基于它们继续**，而不是从头再来。"
        )

    if replan_from:
        lines = [f"任务执行到第 {replan_from} 步时被用户驳回，现在要**重新规划第 {replan_from} 步及之后的部分**。"]
        if reject_comment:
            lines.append(f"用户的意见（必须遵循）：{reject_comment}")
        lines.append(
            f"新计划的第 1 条步骤对应**原计划的第 {replan_from} 步**，"
            "不要重新列出已完成的前序步骤。"
        )
        meta.append("【重新规划】\n" + "\n".join(lines))

    if clarifications:
        meta.append(f"【需求澄清（用户已经确认过的选择，不要再做相反假设）】\n{clarifications}")

    if file_summaries:
        lines = ["用户为本任务提供了以下参考文件（只作本次任务的临时上下文）："]
        for item in file_summaries:
            name = item.get("filename", "")
            chars = item.get("parsed_chars")
            if chars:
                lines.append(f"- {name}（已解析 {chars} 字）")
            else:
                lines.append(f"- {name}（未能解析出文本，不要依赖它）")
        meta.append("\n".join(lines))

    if file_context:
        meta.append(f"参考文件内容摘录：\n{file_context}")

    # 工作区文件夹的**清单**（名称与大小，内容未加入）。
    # 放在文件摘要之后：先讲「已经给了什么」，再讲「还有什么没给」。
    if folder_listing:
        meta.append(folder_listing)

    if meta:
        parts.append("【背景】\n" + "\n".join(meta))

    parts.append(f"【目标】\n{raw_input.strip()}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
#  流式分道：把「推理文本」与「JSON」分开
# ---------------------------------------------------------------------------


class ReasoningSplitter:
    """把模型输出按「推理段」与「JSON 段」分流。

    为什么需要它：我们把提示词设计成「先讲思路，再给 JSON」，
    但直接把整段（含 JSON）当"思考过程"推给前端，用户看到的会是一大坨花括号。
    这里在**流式过程中**就完成分流：

      * 看到 ``` 围栏 → 切到 json 段
      * 看到行首的 `{`（模型没加围栏时）→ 切到 json 段
      * 切过去之后，推理段不再增长

    这样前端可以把推理文本直接渲染成思考流，JSON 只用于解析。
    """

    def __init__(self) -> None:
        self._reasoning: list[str] = []
        self._json: list[str] = []
        self._all: list[str] = []
        self._in_json = False
        self._pending = ""  # 用于跨 chunk 匹配 ``` 与行首 {

    def feed(self, text: str) -> list[tuple[str, str]]:
        """喂入一个分片，返回 [(通道, 文本)]，通道为 "reasoning" / "json"。"""
        out: list[tuple[str, str]] = []
        if not text:
            return out
        self._all.append(text)

        if self._in_json:
            self._json.append(text)
            out.append(("json", text))
            return out

        self._pending += text

        fence = self._pending.find("```")
        brace = self._detect_line_start_brace(self._pending)
        cut: Optional[int] = None
        if fence != -1 and (brace is None or fence <= brace):
            cut = fence
        elif brace is not None:
            cut = brace

        if cut is None:
            # 还没看到分界，先全部当推理吐出去；只保留极少数尾部字符以便跨 chunk 匹配
            keep = 3
            if len(self._pending) > keep:
                chunk = self._pending[:-keep]
                self._pending = self._pending[-keep:]
                self._reasoning.append(chunk)
                out.append(("reasoning", chunk))
            return out

        head = self._pending[:cut]
        tail = self._pending[cut:]
        if head:
            self._reasoning.append(head)
            out.append(("reasoning", head))

        self._pending = ""
        self._in_json = True
        if tail:
            self._json.append(tail)
            out.append(("json", tail))
        return out

    @staticmethod
    def _detect_line_start_brace(text: str) -> Optional[int]:
        """找「行首（允许前导空白）的 {」——没有围栏时的 JSON 起点。

        正则一定以 `{` 结尾，因此 `start + len - 1` 就是 `{` 的下标；
        `cut` 取该下标，JSON 段因此包含 `{` 本身。
        """
        match = re.search(r"(?:^|\n)[ \t]*\{", text)
        if not match:
            return None
        return match.start() + len(match.group(0)) - 1

    def finish(self) -> list[tuple[str, str]]:
        """收尾：把残留的待匹配字符按当前通道吐出。"""
        if not self._pending:
            return []
        text, self._pending = self._pending, ""
        channel = "json" if self._in_json else "reasoning"
        (self._json if self._in_json else self._reasoning).append(text)
        return [(channel, text)]

    @property
    def reasoning(self) -> str:
        return "".join(self._reasoning).strip()

    @property
    def json_text(self) -> str:
        return "".join(self._json).strip()

    @property
    def raw_output(self) -> str:
        """完整原始输出。

        分道可能判断失误（例如模型把 JSON 写在同一行的句子里），
        此时 `json_text` 为空，调用方应回退到完整输出去解析。
        """
        return "".join(self._all).strip()


# ---------------------------------------------------------------------------
#  宽容解析
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _iter_balanced_objects(text: str):
    """扫描出所有**括号配对**的 JSON 对象子串（跳过字符串内的括号）。"""
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start : index + 1]
                    start = -1


def _repair_array_keyvalue(text: str) -> str:
    """修掉「数组元素被写成 `"键": 值`」这种走样。

    实测的高频失败：提示词示例里的占位字符串被模型原样抄下来，
    它想追加真实值时写成了 `["占位文本": "真实约束"]` ——
    数组元素不可能是键值对，直接就是语法错（`Expecting ',' delimiter`），
    整个任务书报废。命中率不低，值得机械修掉。

    用括号栈判断上下文：**只在最近容器是数组时**删掉那个 `"键":`。
    对象里的 `"键": 值` 完全合法，绝不能碰 —— 所以不能简单地全局正则替换。
    """
    n = len(text)
    stack: list[str] = []
    spans: list[tuple[int, int]] = []
    string_start = -1
    in_string = False
    escape = False

    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                # 字符串后紧跟冒号，且当前直接处在数组里 → 是走样的「键」
                if (
                    j < n
                    and text[j] == ":"
                    and stack
                    and stack[-1] == "["
                    and string_start >= 0
                ):
                    spans.append((string_start, j + 1))
            continue

        if ch == '"':
            in_string = True
            string_start = i
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}":
            if stack:
                stack.pop()

    if not spans:
        return text

    out: list[str] = []
    cursor = 0
    for start, end in spans:
        out.append(text[cursor:start])
        cursor = end
    out.append(text[cursor:])
    repaired = "".join(out)

    # 删除后可能留下 `[,` / `, ,` 这类空位，顺手清掉
    repaired = re.sub(r"\[\s*,", "[", repaired)
    repaired = re.sub(r",\s*,", ",", repaired)
    return repaired


def extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出里宽容地取出第一个可解析的 JSON 对象。

    依次尝试：围栏内的内容 → 括号配对扫描 → 去尾随逗号 → 修数组键值对走样。
    全部失败才抛 `PlanParseError`。
    """
    if not text or not text.strip():
        raise PlanParseError("模型没有输出任何内容")

    candidates: list[str] = []
    for match in _FENCE_RE.finditer(text):
        inner = match.group(1).strip()
        if inner:
            candidates.append(inner)
    # 整段也要试：模型可能只给一个裸数组（`[{...},{...}]`），
    # 若先扫内层对象，会只取到数组里的第一个元素而丢掉其余。
    candidates.append(text.strip())
    candidates.extend(_iter_balanced_objects(text))

    for candidate in candidates:
        # 逐个升级修复力度：原样 → 去尾随逗号 → 再修数组键值对走样
        without_commas = _TRAILING_COMMA_RE.sub(r"\1", candidate)
        variants = [candidate, without_commas, _repair_array_keyvalue(without_commas)]
        for attempt in variants:
            try:
                parsed = json.loads(attempt)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict):
                return parsed
            if parsed and isinstance(parsed, list) and all(
                isinstance(x, dict) for x in parsed
            ):
                # 模型只给了步骤数组，补成 {steps: [...]}
                return {"steps": parsed}

    logger.warning(f"任务书解析失败，输出长度 {len(text)}，开头：{text[:200]!r}")
    raise PlanParseError("模型输出的不是合法 JSON，无法解析出任务书")


# ---------------------------------------------------------------------------
#  严格校验
# ---------------------------------------------------------------------------


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return default


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        # 中文分号/句号断句；顺带把列表符号与尾部标点清掉，
        # 否则会出现「中文；」这种把分隔符带进内容的结果
        parts = re.split(r"[;\n]|(?<=[。；])", value)
        cleaned = [p.strip(" -•·*\t；;。，,") for p in parts]
        return [p for p in cleaned if p]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            text = _as_str(item) if not isinstance(item, dict) else _as_str(
                item.get("text") or item.get("value") or item.get("content")
            )
            text = text.strip(" -•·*\t；;。，,")
            if text:
                out.append(text)
        return out
    return []


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "是", "需要", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _first_key(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


def _is_blank(value: Any) -> bool:
    """参数是否「等于没给」。

    只把 None 与空/纯空白字符串算作缺失；`0`、`[]` 这类值本身是合法入参，
    算作已提供 —— 否则会把正常调用误报成缺参数。
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _normalize_kind(value: Any) -> tuple[str, Optional[str]]:
    """返回 (kind, issue)。issue 为 None 表示无需修正。

    issue 直接把「原文 → 修正值」写进去 —— 前端要展示给用户看，
    只说"已修正"不够，必须说清改成了什么。
    """
    text = _as_str(value).lower()
    if not text:
        return "llm", "缺少步骤类型，已按生成步骤（llm）处理"
    if text in STEP_KINDS:
        return text, None
    mapped = STEP_KIND_ALIASES.get(text)
    if mapped:
        return mapped, f"类型「{text}」已规范化为「{mapped}」"
    return "llm", f"类型「{text}」无法识别，已按生成步骤（llm）处理"


def _normalize_tool(value: Any, available: set[str]) -> tuple[str, Optional[str]]:
    """返回 (工具名, issue)；工具名为空字符串表示这个工具不可用。"""
    text = _as_str(value).lower()
    if not text:
        return "", None
    if text in available:
        return text, None
    mapped = TOOL_ALIASES.get(text)
    if mapped and mapped in available:
        return mapped, f"工具名「{text}」已规范化为「{mapped}」"
    return "", f"工具「{text}」不在可用清单里"


def normalize_plan(
    raw: dict[str, Any],
    *,
    available_tools: list[str],
    max_steps: int,
    fallback_goal: str = "",
    file_refs: Optional[list[str]] = None,
    forced_approval_tools: Optional[list[str]] = None,
    tool_required_args: Optional[dict[str, list[str]]] = None,
) -> tuple[dict[str, Any], list[str]]:
    """把模型给的任意形状收敛成严格的任务书。

    返回 `(plan, issues)`。**issues 必须展示给用户** ——
    静默修正会让用户批准的其实不是模型原本的计划。
    """
    issues: list[str] = []
    tools = {t for t in (available_tools or [])}
    forced = {t for t in (forced_approval_tools or [])}

    if not isinstance(raw, dict):
        raise PlanParseError("任务书不是一个 JSON 对象")

    goal = _as_str(_first_key(raw, ("goal", "objective", "target", "name", "title")))
    if not goal:
        goal = fallback_goal.strip()
        if goal:
            issues.append("模型未给出目标，已用原始输入回填")
    if not goal:
        raise PlanParseError("任务书缺少目标（goal），且没有可回填的原始输入")

    audience = _as_str(_first_key(raw, ("audience", "reader", "for", "user")))
    constraints = _as_str_list(_first_key(raw, ("constraints", "requirements", "restrictions")))
    assumptions = _as_str_list(_first_key(raw, ("assumptions", "hypothesis", "notes")))

    raw_steps = _first_key(raw, ("steps", "plan", "tasks", "actions", "todo"))
    if isinstance(raw_steps, dict):
        raw_steps = list(raw_steps.values())
    if not isinstance(raw_steps, (list, tuple)):
        raise PlanParseError("任务书缺少步骤清单（steps）")
    if not raw_steps:
        raise PlanParseError("任务书的步骤清单为空，无法审批")

    if len(raw_steps) > max_steps:
        issues.append(
            f"步骤数 {len(raw_steps)} 超出上限 {max_steps}，已截断为前 {max_steps} 步"
        )
        raw_steps = list(raw_steps)[:max_steps]

    steps: list[dict[str, Any]] = []
    for offset, item in enumerate(raw_steps, start=1):
        if not isinstance(item, dict):
            issues.append(f"第 {offset} 步不是对象，已跳过")
            continue

        raw_kind_value = _first_key(item, ("kind", "type", "category"))
        raw_kind_text = _as_str(raw_kind_value).lower()
        kind, kind_issue = _normalize_kind(raw_kind_value)

        # 模型有时把**工具名**写进 kind（实测：`kind: "knowledge_query"`）。
        # 这是可恢复的偏差 —— 直接降级成 llm 会丢掉「它想调用这个工具」的本意，
        # 因此这里认出来并还原成工具调用。
        implied_tool = ""
        if kind_issue and raw_kind_text in tools:
            kind = "tool"
            implied_tool = raw_kind_text
            kind_issue = f"类型写成了工具名「{raw_kind_text}」，已按工具调用处理"

        if kind_issue:
            issues.append(f"第 {offset} 步：{kind_issue}")

        title = _as_str(_first_key(item, ("title", "name", "summary", "description")))
        if not title:
            issues.append(f"第 {offset} 步缺少标题，已自动补全")

        raw_action = _first_key(item, ("action", "tool_call", "call", "params", "input"))
        if implied_tool and not isinstance(raw_action, dict):
            # 参数可能被平铺在步骤里（`{"query": "..."}`），一并收进来
            flat_args = item.get("args")
            if not isinstance(flat_args, dict):
                flat_args = {
                    k: v
                    for k, v in item.items()
                    if k
                    not in (
                        "kind",
                        "type",
                        "category",
                        "title",
                        "name",
                        "summary",
                        "description",
                        "requires_approval",
                        "needs_approval",
                        "require_approval",
                        "approval",
                        "idx",
                        "id",
                    )
                }
            raw_action = {"tool": implied_tool, "args": flat_args}
        action: dict[str, Any] = {}

        if kind == "tool":
            if not isinstance(raw_action, dict):
                # tool 步没有 action 就退化成 llm，避免执行期调用不存在的工具
                kind = "llm"
                issues.append(f"第 {offset} 步声明为工具调用但缺少 action，已改为生成步骤（llm）")
            else:
                tool_name, tool_issue = _normalize_tool(
                    raw_action.get("tool") or raw_action.get("name") or raw_action.get("tool_name"),
                    tools,
                )
                if not tool_name:
                    kind = "llm"
                    issues.append(
                        f"第 {offset} 步引用了不可用的工具，已改为生成步骤（llm）"
                        + (f"：{tool_issue}" if tool_issue else "")
                    )
                else:
                    if tool_issue:
                        issues.append(f"第 {offset} 步：{tool_issue}")
                    args = raw_action.get("args") if isinstance(raw_action.get("args"), dict) else {}
                    # 键名归位：模型把代码写进 `expression` 而工具要 `code` 是常态，
                    # 不是异常。归位动作要写进 issues —— 静默替换会让用户
                    # 没法解释「我明明写的是 expression」。
                    args, arg_notes = normalize_tool_args(tool_name, args)
                    for note in arg_notes:
                        issues.append(f"第 {offset} 步：{note}")
                    action = {"tool": tool_name, "args": args}

                    # 必填参数缺失要在**审批阶段**就暴露。
                    # 否则用户批准完，跑到这一步才报「表达式不能为空」——
                    # 那时候已经浪费了前面几步的时间与 token，
                    # 而且「哪一步有问题」这个信息本该在计划里就能看到。
                    required = (tool_required_args or {}).get(tool_name) or []
                    has_step_ref = (
                        tool_name == "file_write"
                        and not _is_blank(args.get("content_from_step"))
                    )
                    missing = [
                        name
                        for name in required
                        if _is_blank(args.get(name))
                        # file_write 的正文可以由 content_from_step 提供
                        and not (has_step_ref and name == "content")
                    ]
                    if missing:
                        issues.append(
                            f"第 {offset} 步：{tool_name} 缺少必填参数"
                            f"{'、'.join(missing)}，执行到这里会失败"
                        )

        if kind != "tool" and not action:
            action = {
                "prompt_ref": _as_str(
                    _first_key(item, ("intent", "prompt", "prompt_ref", "task"))
                )
                or title
            }

        if not title:
            title = action.get("tool") or action.get("prompt_ref") or f"第 {offset} 步"

        requires_approval = _as_bool(
            _first_key(item, ("requires_approval", "needs_approval", "require_approval", "approval"))
        )
        tool_in_action = action.get("tool", "")
        if tool_in_action and tool_in_action in forced and not requires_approval:
            requires_approval = True
            issues.append(
                f"第 {offset} 步用到「{tool_in_action}」，属于必须人工确认的工具，"
                f"已强制标记为需要审批"
            )

        steps.append(
            {
                "idx": len(steps) + 1,
                "kind": kind,
                "title": title,
                "action": action,
                "requires_approval": requires_approval,
                "status": "pending",
            }
        )

    if not steps:
        raise PlanParseError("任务书里没有可执行的步骤")

    refs = list(file_refs or [])
    incoming_refs = raw.get("file_refs")
    if isinstance(incoming_refs, (list, tuple)):
        unknown = [str(x) for x in incoming_refs if str(x) not in refs]
        if unknown:
            issues.append(f"忽略了对不存在文件的引用：{', '.join(unknown[:5])}")

    return (
        {
            "goal": goal,
            "audience": audience,
            "constraints": constraints,
            "assumptions": assumptions,
            "file_refs": refs,
            "steps": steps,
        },
        issues,
    )

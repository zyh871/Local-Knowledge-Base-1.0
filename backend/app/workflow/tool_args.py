"""工具参数的**归位**与**引用解析** —— 计划里写的东西，执行时要能真的用。

## 为什么需要这一层

实测发生过的事：用户要「写脚本保存为 fib.py」，模型把代码放进了
`args.expression`，而 `code_executor` 要的是 `code`。结果：

* 计划期只留下一行 `issues`：「第 2 步：code_executor 缺少必填参数code」；
* 用户没注意 → 批准 → 跑到那一步才失败：「代码不能为空」。

**模型的键名变体是常态，不是异常。** 与其在提示词里反复恳求它写对，
不如在这里做一次显式归位，并把归位动作写进 `issues`（修正必须被看见）。

## 为什么 `content_from_step` 在这里解析

「把第 1 步写的代码落盘」是本项目最自然的两步式产出。若要求模型把代码
**再抄一遍**进计划的 JSON 里，会同时踩两个坑：代码里的换行与引号极易破坏
JSON；而且白白多花一次输出 token。用步骤编号引用，既稳又省。

解析需要拿到 `steps`，而工具本身拿不到 —— 所以放在这一层，
由执行节点在调工具前统一处理。
"""

from __future__ import annotations

import os
import re
from typing import Any

__all__ = [
    "CODE_EXTENSIONS",
    "MARKDOWN_EXTENSIONS",
    "MAX_REFERENCED_CHARS",
    "TOOL_ARG_ALIASES",
    "extract_code_body",
    "normalize_tool_args",
    "resolve_tool_args",
]

#: 工具名 → {模型常写的键: 工具真正接受的键}
TOOL_ARG_ALIASES: dict[str, dict[str, str]] = {
    "code_executor": {
        "expression": "code",
        "script": "code",
        "code_str": "code",
        "code_text": "code",
        "snippet": "code",
        "source": "code",
        "python": "code",
        "content": "code",
        "body": "code",
        "text": "code",
    },
    "calculator": {
        "expr": "expression",
        "formula": "expression",
        "math": "expression",
        "input": "expression",
        "code": "expression",
    },
    "web_search": {
        "q": "query",
        "keyword": "query",
        "keywords": "query",
        "search": "query",
        "question": "query",
        "text": "query",
    },
    "knowledge_query": {
        "q": "query",
        "keyword": "query",
        "question": "query",
        "search": "query",
        "text": "query",
        "input": "query",
    },
    "file_write": {
        "filename": "path",
        "file_name": "path",
        "name": "path",
        "file": "path",
        "target_path": "path",
        "content_ref": "content_from_step",
        "from_step": "content_from_step",
        "step": "content_from_step",
        "content_source": "content_from_step",
        "body": "content",
        "text": "content",
        "data": "content",
    },
}

#: 从某一步引用正文时的长度上限。产物文件本身可以更大（由写入上限管），
#: 但一步的产出被下游整段搬运时必须有上限，否则第 10 步的上下文会爆。
MAX_REFERENCED_CHARS = 20000

#: 「这是代码文件」的后缀。落盘前要把 markdown 围栏剥掉。
#: 不剥的后果实测过：`fib.py` 的第一行是 ```` ```python ````，
#: **文件根本跑不起来** —— 用户拿到的正是"生成的代码无法正常使用"。
CODE_EXTENSIONS = frozenset(
    {
        ".bat", ".c", ".cpp", ".cs", ".css", ".go", ".h", ".htm", ".html",
        ".ini", ".java", ".js", ".json", ".jsx", ".php", ".ps1", ".py",
        ".rb", ".rs", ".sh", ".sql", ".toml", ".ts", ".tsx", ".vue",
        ".xml", ".yaml", ".yml",
    }
)

#: 整个字符串就是**一个**围栏块（前后没有别的文字）
_SOLE_FENCE_RE = re.compile(r"^\s*```[^\n]*\n(.*?)\n?```\s*$", re.S)
#: 任意围栏块
_ANY_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.S)


#: markdown 家族：围栏在它们里是**内容**而不是包装，绝不要剥。
MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown", ".mdx"})


def extract_code_body(text: str, path: str) -> tuple[str, str | None]:
    """把 markdown 围栏剥掉，返回 `(正文, 说明或 None)`。

    规则与取舍：

      1. `.md` 家族一律不动 —— 在 markdown 里围栏是内容本身，剥掉是破坏。
      2. 整段就是**一个**围栏块 → 里面的内容就是正文（模型只回了代码，最常见）。
      3. 目标是**代码类**后缀且只有一个围栏块 → 取围栏内的内容，
         围栏外的"以上就是实现，运行方式如下…"这类说明**不应该**进 .py 文件。

    其它情况一律不动 —— 拿不准时宁可多留，也不要静默删掉用户的内容。
    """
    if not text:
        return text, None

    ext = os.path.splitext(path or "")[1].lower()
    if ext in MARKDOWN_EXTENSIONS:
        return text, None

    sole = _SOLE_FENCE_RE.match(text.strip())
    # 非贪婪匹配会把"两段 fenced 代码"也吞成"一个围栏块"（中间那个 ``` 被当成内容）。
    # 多一段围栏说明内容不止一段代码，剥掉反而丢东西，所以要求内部不再含围栏。
    if sole and "```" not in sole.group(1):
        return sole.group(1).strip("\n"), "已去掉代码围栏（```）"

    if ext in CODE_EXTENSIONS:
        found = _ANY_FENCE_RE.findall(text)
        if len(found) == 1:
            return (
                found[0].strip("\n"),
                "已从代码围栏中取出代码（围栏外的说明文字未写入该文件）",
            )
    return text, None


def normalize_tool_args(tool_name: str, args: Any) -> tuple[dict[str, Any], list[str]]:
    """把模型写的键名归位成工具真正接受的键。

    返回 `(args, notes)`。**只在目标键位空缺时才覆盖** ——
    如果模型同时写了 `code` 和 `expression`，以正确的那个为准，并说明忽略了谁。
    """
    if not isinstance(args, dict):
        return {}, []
    # 保留键（下划线开头）绝不来自计划 —— `_context` 这类由服务端注入，
    # 让模型能指定它等于把"写到哪"交给模型决定。
    out = {k: v for k, v in args.items() if not str(k).startswith("_")}
    aliases = TOOL_ARG_ALIASES.get(tool_name) or {}
    if not aliases:
        return out, []

    notes: list[str] = []
    for wrong, right in aliases.items():
        if wrong not in out:
            continue
        value = out.pop(wrong)
        if right in out and out[right] not in (None, ""):
            notes.append(
                f"参数「{wrong}」与「{right}」同时出现，已忽略「{wrong}」"
            )
            continue
        out[right] = value
        notes.append(f"参数「{wrong}」已按「{right}」处理")
    return out, notes


def resolve_tool_args(
    tool_name: str, args: Any, steps: list[dict[str, Any]] | None
) -> tuple[dict[str, Any], list[str]]:
    """归位 + 解析 `content_from_step`，返回 `(args, notes)`。

    `notes` 是**给用户看的**：改了哪个键、从哪一步搬了多少字。
    静默替换会让「我明明写的是 expression」无从解释。
    """
    resolved, notes = normalize_tool_args(tool_name, args)
    # 引用键不属于任何工具的入参，一律剥掉 —— 不然它会作为未知 kwarg
    # 传进工具，测试和排障时都容易误判"这参数生效了"。
    ref = resolved.pop("content_from_step", None)
    if tool_name != "file_write":
        return resolved, notes

    if ref not in (None, "", []):
        try:
            idx = int(ref)
        except (TypeError, ValueError):
            notes.append(f"content_from_step「{ref}」不是步骤序号，已忽略")
            return _finish_write_args(resolved, notes)

        step = next(
            (s for s in (steps or []) if int(s.get("idx") or 0) == idx), None
        )
        if step is None:
            notes.append(f"content_from_step={idx}：找不到第 {idx} 步，内容未取到")
            return _finish_write_args(resolved, notes)

        output = step.get("output") or {}
        body = str(output.get("summary") or "").strip()
        if not body:
            notes.append(
                f"content_from_step={idx}：第 {idx} 步没有产出内容"
                f"（状态 {step.get('status')}），没有可写入的正文"
            )
            return _finish_write_args(resolved, notes)

        if len(body) > MAX_REFERENCED_CHARS:
            notes.append(
                f"第 {idx} 步的产出有 {len(body)} 字符，"
                f"超过单次引用上限 {MAX_REFERENCED_CHARS}，已截断"
            )
            body = body[:MAX_REFERENCED_CHARS]

        if resolved.get("content"):
            notes.append(f"已用第 {idx} 步的产出覆盖字面量 content")
        else:
            notes.append(f"正文取自第 {idx} 步的产出（{len(body)} 字符）")
        resolved["content"] = body

    return _finish_write_args(resolved, notes)


def _finish_write_args(
    resolved: dict[str, Any], notes: list[str]
) -> tuple[dict[str, Any], list[str]]:
    """落盘前的最后一道加工：把 markdown 围栏剥掉。

    这一步不能省。模型被训练成"用围栏输出代码"，所以它写进文件的
    第一行常常是 ```` ```python ```` —— 那个文件**根本无法运行**。
    用户看到的正是"AI 写的代码用不了"，而原因藏在文件的第一行里。
    """
    content = resolved.get("content")
    if isinstance(content, str) and content.strip():
        cleaned, note = extract_code_body(content, str(resolved.get("path") or ""))
        if note:
            resolved["content"] = cleaned
            notes.append(note)
    return resolved, notes

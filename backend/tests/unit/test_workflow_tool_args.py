"""工具参数的归位与引用解析。

起因是一段真实失败：用户要「写脚本保存为 fib.py」，模型把代码放进了
`args.expression` 而 `code_executor` 要的是 `code` —— 计划期只留下一行 issues，
用户没注意就批准了，跑到那一步才失败「代码不能为空」。

**模型的键名变体是常态。** 与其在提示词里反复恳求它写对，
不如在解析层做一次显式归位，并把归位动作写进 notes（修正必须被看见）。
"""

from __future__ import annotations

import pytest

from app.workflow.tool_args import (
    MAX_REFERENCED_CHARS,
    extract_code_body,
    normalize_tool_args,
    resolve_tool_args,
)


class TestExtractCodeBody:
    """★ 落盘前必须把 markdown 围栏剥掉。

    不剥的后果实测过：`fib.py` 第一行是 ```` ```python ````，
    文件**根本跑不起来** —— 用户拿到的正是"生成的代码无法正常使用"。
    """

    def test_sole_fence_is_unwrapped(self):
        body, note = extract_code_body("```python\nprint(1)\n```", "a.py")
        assert body == "print(1)"
        assert note and "围栏" in note

    def test_sole_fence_without_language_tag(self):
        body, _ = extract_code_body("```\nprint(1)\n```", "a.py")
        assert body == "print(1)"

    def test_surrounding_whitespace_is_tolerated(self):
        body, _ = extract_code_body("\n\n```python\nx = 1\n```\n\n", "a.py")
        assert body == "x = 1"

    def test_code_extension_takes_inner_block_and_drops_prose(self):
        text = "实现如下：\n\n```python\nx = 1\n```\n\n运行方式：python a.py"
        body, note = extract_code_body(text, "a.py")
        assert body == "x = 1"
        assert note and "说明文字" in note

    def test_markdown_target_is_never_touched(self):
        """★ 在 markdown 里围栏是**内容**而不是包装，剥掉就是破坏。

        判据必须包含文件类型：同一个字符串往 .py 里写要剥、
        往 .md 里写要留 —— 只看内容不看后缀的做法一定会错一边。
        """
        text = "```python\nx = 1\n```"
        body, note = extract_code_body(text, "notes.md")
        assert body == text
        assert note is None

        multi = "说明：\n\n```python\nx = 1\n```\n\n再来一段\n\n```\ny = 2\n```"
        kept, note2 = extract_code_body(multi, "notes.md")
        assert kept == multi
        assert note2 is None

    def test_sole_fence_in_non_code_text_file_is_unwrapped(self):
        body, note = extract_code_body("```\nhello\n```", "a.txt")
        assert body == "hello"
        assert note

    def test_multiple_fences_in_code_file_are_left_alone(self):
        """两个围栏说明内容不是"一段代码"，剥了反而丢东西。"""
        text = "```python\na = 1\n```\n\n```python\nb = 2\n```"
        body, note = extract_code_body(text, "a.py")
        assert note is None
        assert body == text

    def test_plain_text_untouched(self):
        body, note = extract_code_body("hello", "a.txt")
        assert body == "hello"
        assert note is None

    def test_empty_is_safe(self):
        assert extract_code_body("", "a.py") == ("", None)


class TestFenceStrippedOnResolve:
    STEPS = [
        {
            "idx": 1,
            "status": "done",
            "output": {"summary": "```python\ndef fib(n):\n    return n\n```"},
        }
    ]

    def test_referenced_content_has_no_fence(self):
        args, notes = resolve_tool_args(
            "file_write", {"path": "fib.py", "content_from_step": 1}, self.STEPS
        )
        assert args["content"] == "def fib(n):\n    return n"
        assert not args["content"].startswith("```")
        assert any("围栏" in n for n in notes)

    def test_literal_content_also_stripped(self):
        """模型把代码直接写在 args 里时同样带围栏 —— 也要剥。"""
        args, notes = resolve_tool_args(
            "file_write", {"path": "a.py", "content": "```python\nx = 1\n```"}, []
        )
        assert args["content"] == "x = 1"
        assert any("围栏" in n for n in notes)

    def test_non_code_target_is_not_stripped_when_prose_present(self):
        steps = [
            {
                "idx": 1,
                "status": "done",
                "output": {"summary": "结论：\n\n```\ncode\n```\n\n补充说明。"},
            }
        ]
        args, _ = resolve_tool_args(
            "file_write", {"path": "report.md", "content_from_step": 1}, steps
        )
        assert "补充说明" in args["content"]


class TestNormalizeToolArgs:
    def test_expression_becomes_code(self):
        args, notes = normalize_tool_args("code_executor", {"expression": "print(1)"})
        assert args == {"code": "print(1)"}
        assert notes and "expression" in notes[0] and "code" in notes[0]

    @pytest.mark.parametrize(
        "wrong", ["script", "code_str", "code_text", "snippet", "source", "python"]
    )
    def test_other_code_aliases(self, wrong):
        args, _ = normalize_tool_args("code_executor", {wrong: "x"})
        assert args == {"code": "x"}

    def test_correct_key_wins_when_both_present(self):
        """两个键都在时以正确的为准，并说明忽略了谁 —— 不能悄悄丢掉一个。"""
        args, notes = normalize_tool_args(
            "code_executor", {"code": "right", "expression": "wrong"}
        )
        assert args == {"code": "right"}
        assert any("忽略" in n for n in notes)

    def test_blank_correct_key_is_replaced(self):
        args, _ = normalize_tool_args(
            "code_executor", {"code": "", "expression": "real"}
        )
        assert args == {"code": "real"}

    def test_already_correct_leaves_no_notes(self):
        args, notes = normalize_tool_args("code_executor", {"code": "print(1)"})
        assert args == {"code": "print(1)"}
        assert notes == []

    def test_unknown_tool_passes_through(self):
        args, notes = normalize_tool_args("no_such_tool", {"a": 1})
        assert args == {"a": 1}
        assert notes == []

    def test_non_dict_is_safe(self):
        assert normalize_tool_args("code_executor", None) == ({}, [])

    def test_web_search_query_aliases(self):
        args, _ = normalize_tool_args("web_search", {"q": "天气"})
        assert args == {"query": "天气"}

    def test_calculator_expression_aliases(self):
        args, _ = normalize_tool_args("calculator", {"expr": "1+1"})
        assert args == {"expression": "1+1"}

    def test_file_write_path_aliases(self):
        args, _ = normalize_tool_args("file_write", {"filename": "a.py"})
        assert args == {"path": "a.py"}


class TestResolveContentFromStep:
    STEPS = [
        {
            "idx": 1,
            "status": "done",
            "output": {"summary": "```python\nprint('hi')\n```"},
        },
        {"idx": 2, "status": "pending", "output": None},
        {"idx": 3, "status": "failed", "output": None, "error": "boom"},
    ]

    def test_pulls_body_from_step(self):
        args, notes = resolve_tool_args(
            "file_write", {"path": "fib.py", "content_from_step": 1}, self.STEPS
        )
        # 落盘前会剥掉 markdown 围栏 —— 写进 .py 的必须是能直接跑的代码
        assert args["content"] == "print('hi')"
        assert "content_from_step" not in args, "引用键不能原样传给工具"
        assert any("第 1 步" in n for n in notes)

    def test_string_step_number_works(self):
        """模型经常把序号写成字符串 —— 不该因此失败。"""
        args, _ = resolve_tool_args(
            "file_write", {"path": "a.py", "content_from_step": "1"}, self.STEPS
        )
        assert "print('hi')" in args["content"]

    def test_step_without_output_explains_why(self):
        args, notes = resolve_tool_args(
            "file_write", {"path": "a.py", "content_from_step": 2}, self.STEPS
        )
        assert "content" not in args
        assert notes and "没有产出" in notes[0]

    def test_missing_step_explains_why(self):
        args, notes = resolve_tool_args(
            "file_write", {"path": "a.py", "content_from_step": 9}, self.STEPS
        )
        assert "content" not in args
        assert notes and "找不到" in notes[0]

    def test_bad_reference_is_ignored_not_crash(self):
        args, notes = resolve_tool_args(
            "file_write", {"path": "a.py", "content_from_step": "第一步"}, self.STEPS
        )
        assert "content" not in args
        assert notes

    def test_long_reference_is_truncated_and_reported(self):
        long_steps = [
            {"idx": 1, "status": "done", "output": {"summary": "x" * (MAX_REFERENCED_CHARS + 500)}}
        ]
        args, notes = resolve_tool_args(
            "file_write", {"path": "a.txt", "content_from_step": 1}, long_steps
        )
        assert len(args["content"]) == MAX_REFERENCED_CHARS
        assert any("截断" in n for n in notes)

    def test_literal_content_is_overridden_but_said(self):
        args, notes = resolve_tool_args(
            "file_write",
            {"path": "a.py", "content": "old", "content_from_step": 1},
            self.STEPS,
        )
        assert "print('hi')" in args["content"]
        assert any("覆盖" in n for n in notes)

    def test_alias_plus_reference_together(self):
        """真实路径：模型既写错键名，又用引用来给正文。"""
        args, notes = resolve_tool_args(
            "file_write",
            {"filename": "fib.py", "content_ref": 1},
            self.STEPS,
        )
        assert args["path"] == "fib.py"
        assert "print('hi')" in args["content"]
        assert len(notes) >= 2

    def test_other_tools_ignore_content_from_step(self):
        args, _ = resolve_tool_args(
            "code_executor", {"code": "print(1)", "content_from_step": 1}, self.STEPS
        )
        assert args == {"code": "print(1)"}

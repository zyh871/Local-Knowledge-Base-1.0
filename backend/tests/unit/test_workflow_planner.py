"""任务书「宽容解析 + 严格校验」的单元测试。

这一层是 P2 最危险的地方：模型输出不可控，而任务书是要被**人工审批**的制品。
两类错误都会造成实质伤害：
  * 解析太严 → 模型只是多加了句废话，整个任务就失败了
  * 校验太松 → 用户批准了一份「看起来是模型给的、实际被悄悄改过」的计划
因此这里逐个覆盖：畸形输入能救回 / 修正必须留痕。
"""

from __future__ import annotations

import pytest

from app.workflow.planner import (
    ReasoningSplitter,
    build_plan_prompt,
    extract_json_object,
    normalize_plan,
)

TOOLS = ["web_search", "knowledge_query", "calculator", "code_executor"]


# ---------------------------------------------------------------------------
#  宽容解析
# ---------------------------------------------------------------------------


class TestExtractJsonObject:
    def test_plain_json(self):
        assert extract_json_object('{"goal": "x"}') == {"goal": "x"}

    def test_fenced_json(self):
        text = '思路说明。\n\n```json\n{"goal": "写报告"}\n```\n'
        assert extract_json_object(text) == {"goal": "写报告"}

    def test_fence_without_language_tag(self):
        assert extract_json_object('```\n{"goal": "y"}\n```') == {"goal": "y"}

    def test_prose_before_and_after(self):
        text = '我先说说思路，然后这是计划：{"goal": "z", "steps": []} 以上。'
        assert extract_json_object(text) == {"goal": "z", "steps": []}

    def test_trailing_comma_tolerated(self):
        text = '{"goal": "a", "steps": [],}'
        assert extract_json_object(text) == {"goal": "a", "steps": []}

    def test_braces_inside_string_do_not_break_matching(self):
        text = '{"goal": "含 { 花括号 } 的目标", "steps": []}'
        assert extract_json_object(text)["goal"] == "含 { 花括号 } 的目标"

    # ── 「数组元素写成键值对」的走样（真实发生过的失败）──────────

    def test_array_keyvalue_slip_repaired(self):
        """★ 回归：实测的高频失败。

        模型把提示词示例里的占位字符串原样抄下来，又想追加真实值，
        于是写成 `["占位": "真实值"]` —— 数组元素不可能是键值对，直接语法错，
        整个任务书报废（一度 6/6 全败）。
        """
        text = (
            '```json\n'
            '{\n'
            '  "goal": "总结代码评审建议",\n'
            '  "constraints": ["格式/长度/语言/时间范围等硬约束": "建议需简洁明了，不超过100字"],\n'
            '  "steps": [{"kind": "llm", "title": "写"}]\n'
            '}\n```'
        )
        parsed = extract_json_object(text)
        assert parsed["constraints"] == ["建议需简洁明了，不超过100字"]
        assert parsed["goal"] == "总结代码评审建议"

    def test_array_keyvalue_slip_after_a_normal_element(self):
        text = '{"constraints": ["真实约束一", "占位": "真实约束二"], "steps": []}'
        assert extract_json_object(text)["constraints"] == ["真实约束一", "真实约束二"]

    def test_object_keyvalue_is_never_touched(self):
        """对象里的 `键: 值` 完全合法 —— 修复必须靠括号栈区分上下文，
        用全局正则替换会把正常 JSON 改坏。"""
        text = '{"goal": "g", "action": {"tool": "web_search", "args": {"query": "x"}}}'
        parsed = extract_json_object(text)
        assert parsed["action"]["tool"] == "web_search"
        assert parsed["action"]["args"]["query"] == "x"

    def test_array_of_objects_untouched(self):
        text = '{"steps": [{"kind": "llm", "title": "a"}, {"kind": "tool", "title": "b"}]}'
        parsed = extract_json_object(text)
        assert [s["title"] for s in parsed["steps"]] == ["a", "b"]

    def test_colon_inside_string_not_confused(self):
        """字符串内部的冒号不能被当成「键值对走样」。"""
        text = '{"constraints": ["时间范围：2 周"], "steps": []}'
        assert extract_json_object(text)["constraints"] == ["时间范围：2 周"]

    def test_escaped_quote_inside_string(self):
        text = r'{"goal": "带 \" 引号", "steps": []}'
        assert extract_json_object(text)["goal"] == '带 " 引号'

    def test_bare_array_wrapped_into_steps(self):
        text = '[{"kind": "llm", "title": "一步"}]'
        assert "steps" in extract_json_object(text)

    def test_garbage_raises(self):
        from app.workflow.planner import PlanParseError

        with pytest.raises(PlanParseError):
            extract_json_object("模型今天不想给 JSON。")

    def test_empty_raises(self):
        from app.workflow.planner import PlanParseError

        with pytest.raises(PlanParseError):
            extract_json_object("   ")


# ---------------------------------------------------------------------------
#  推理 / JSON 分道
# ---------------------------------------------------------------------------


class TestReasoningSplitter:
    def _feed_all(self, splitter, text, size=1):
        """按固定分片喂入 —— 分界点落在分片中间是最容易出 bug 的场景。"""
        events = []
        for i in range(0, len(text), size):
            events.extend(splitter.feed(text[i : i + size]))
        events.extend(splitter.finish())
        return events

    def test_fenced_flow_char_by_char(self):
        splitter = ReasoningSplitter()
        text = '我打算先检索再汇总。\n\n```json\n{"goal": "报告", "steps": []}\n```'
        events = self._feed_all(splitter, text)

        assert splitter.reasoning == "我打算先检索再汇总。"
        assert extract_json_object(splitter.json_text)["goal"] == "报告"
        # 推理段里绝不能混进 JSON
        assert "{" not in splitter.reasoning

    def test_no_fence_line_start_brace(self):
        splitter = ReasoningSplitter()
        text = '先说明思路。\n{"goal": "报告", "steps": []}'
        self._feed_all(splitter, text)
        assert splitter.reasoning == "先说明思路。"
        assert extract_json_object(splitter.json_text)["goal"] == "报告"

    def test_raw_output_keeps_everything(self):
        splitter = ReasoningSplitter()
        text = '思路。\n{"a": 1}'
        self._feed_all(splitter, text)
        assert splitter.raw_output == text

    def test_reasoning_only_has_no_json(self):
        splitter = ReasoningSplitter()
        self._feed_all(splitter, "只有推理，没有 JSON。")
        assert splitter.reasoning == "只有推理，没有 JSON。"
        assert splitter.json_text == ""

    def test_chunked_stream_reassembles_identically(self):
        """不同分片大小必须得到同一结果 —— 否则线上表现会随网络抖动而变。"""
        text = '思路A。\n\n```json\n{"goal": "G", "steps": [{"kind": "llm"}]}\n```'
        results = []
        for size in (1, 2, 3, 7, 1000):
            splitter = ReasoningSplitter()
            self._feed_all(splitter, text, size=size)
            results.append((splitter.reasoning, splitter.json_text))
        assert len(set(results)) == 1


# ---------------------------------------------------------------------------
#  严格校验
# ---------------------------------------------------------------------------


def _norm(raw, **kwargs):
    params = {
        "available_tools": TOOLS,
        "max_steps": 12,
        "fallback_goal": "原始输入",
        "forced_approval_tools": ["code_executor", "file_write"],
    }
    params.update(kwargs)
    return normalize_plan(raw, **params)


class TestNormalizePlanBasic:
    def test_happy_path(self):
        raw = {
            "goal": "产出对比报告",
            "audience": "技术决策者",
            "constraints": ["中文", "含价格表"],
            "assumptions": [],
            "steps": [
                {
                    "kind": "tool",
                    "title": "搜索定价",
                    "action": {"tool": "web_search", "args": {"query": "2026 定价"}},
                },
                {"kind": "synthesis", "title": "整合报告", "requires_approval": True},
            ],
        }
        plan, issues = _norm(raw)
        assert issues == []
        assert plan["goal"] == "产出对比报告"
        assert [s["idx"] for s in plan["steps"]] == [1, 2]
        assert plan["steps"][0]["action"]["tool"] == "web_search"
        assert plan["steps"][1]["requires_approval"] is True
        assert all(s["status"] == "pending" for s in plan["steps"])

    def test_missing_goal_falls_back_to_raw_input_with_issue(self):
        plan, issues = _norm({"steps": [{"kind": "llm", "title": "做事"}]})
        assert plan["goal"] == "原始输入"
        assert any("目标" in i for i in issues)

    def test_missing_goal_and_no_fallback_raises(self):
        from app.workflow.planner import PlanParseError

        with pytest.raises(PlanParseError):
            _norm({"steps": [{"kind": "llm", "title": "x"}]}, fallback_goal="")

    def test_empty_steps_raises(self):
        from app.workflow.planner import PlanParseError

        with pytest.raises(PlanParseError):
            _norm({"goal": "g", "steps": []})

    def test_steps_missing_raises(self):
        from app.workflow.planner import PlanParseError

        with pytest.raises(PlanParseError):
            _norm({"goal": "g"})

    def test_constraints_as_single_string(self):
        plan, _ = _norm(
            {
                "goal": "g",
                "constraints": "中文；不超过 2000 字",
                "steps": [{"kind": "llm", "title": "x"}],
            }
        )
        assert plan["constraints"] == ["中文", "不超过 2000 字"]

    def test_constraints_as_object_list(self):
        plan, _ = _norm(
            {
                "goal": "g",
                "constraints": [{"text": "中文"}, {"value": "简洁"}],
                "steps": [{"kind": "llm", "title": "x"}],
            }
        )
        assert plan["constraints"] == ["中文", "简洁"]

    def test_steps_as_dict_is_flattened(self):
        plan, _ = _norm(
            {"goal": "g", "steps": {"1": {"kind": "llm", "title": "A"}, "2": {"kind": "llm", "title": "B"}}}
        )
        assert [s["title"] for s in plan["steps"]] == ["A", "B"]


class TestNormalizePlanCorrections:
    """每个修正都必须留下 issue —— 这是「宽容解析」不至于变成「静默篡改」的保证。"""

    def test_kind_alias_mapped_with_issue(self):
        """`search` 这类别名要收敛到 `tool`，且必须留痕。

        注意必须带上合法 action —— 否则会被「tool 步缺 action」的规则
        降级成 llm，别名映射就观察不到了。
        """
        raw = {
            "goal": "g",
            "steps": [
                {
                    "kind": "search",
                    "title": "搜",
                    "action": {"tool": "web_search", "args": {"query": "x"}},
                }
            ],
        }
        plan, issues = _norm(raw)
        assert plan["steps"][0]["kind"] == "tool"
        assert any("类型" in i and "规范化" in i for i in issues)

    def test_kind_written_as_tool_name_is_recovered(self):
        """实测偏差：模型把工具名写进 kind（`kind: "knowledge_query"`）。

        直接降级成 llm 虽然安全，但会丢掉「它想调用这个工具」的本意 ——
        这是可恢复的，应当还原成工具调用。
        """
        raw = {
            "goal": "g",
            "steps": [{"kind": "knowledge_query", "title": "查内部文档", "query": "定价"}],
        }
        plan, issues = _norm(raw)
        step = plan["steps"][0]
        assert step["kind"] == "tool"
        assert step["action"]["tool"] == "knowledge_query"
        # 平铺在步骤里的参数要一并收进 args
        assert step["action"]["args"].get("query") == "定价"
        assert any("工具名" in i for i in issues)

    def test_kind_written_as_unknown_tool_still_downgrades(self):
        """不在白名单里的名字不能被"恢复"成工具调用。"""
        raw = {"goal": "g", "steps": [{"kind": "send_email", "title": "发邮件"}]}
        plan, issues = _norm(raw)
        assert plan["steps"][0]["kind"] == "llm"
        assert "tool" not in plan["steps"][0]["action"]
        assert any("无法识别" in i for i in issues)

    def test_unknown_kind_defaults_to_llm_with_issue(self):
        plan, issues = _norm({"goal": "g", "steps": [{"kind": "魔法", "title": "x"}]})
        assert plan["steps"][0]["kind"] == "llm"
        assert any("类型" in i and "无法识别" in i and "魔法" in i for i in issues)

    def test_unavailable_tool_downgraded_to_llm_with_issue(self):
        raw = {
            "goal": "g",
            "steps": [
                {
                    "kind": "tool",
                    "title": "用不存在的工具",
                    "action": {"tool": "send_email", "args": {}},
                }
            ],
        }
        plan, issues = _norm(raw)
        step = plan["steps"][0]
        # 关键：不能留下一个执行期必然失败的工具调用
        assert step["kind"] == "llm"
        assert "tool" not in step["action"]
        assert any("不可用" in i for i in issues)

    def test_tool_name_variant_normalized_with_issue(self):
        raw = {
            "goal": "g",
            "steps": [
                {"kind": "tool", "title": "搜", "action": {"tool": "web-search", "args": {"query": "x"}}}
            ],
        }
        plan, issues = _norm(raw)
        assert plan["steps"][0]["action"]["tool"] == "web_search"
        assert any("规范化" in i for i in issues)

    def test_tool_step_without_action_downgraded(self):
        plan, issues = _norm({"goal": "g", "steps": [{"kind": "tool", "title": "x"}]})
        assert plan["steps"][0]["kind"] == "llm"
        assert any("缺少 action" in i for i in issues)

    def test_forced_approval_tool_flagged_with_issue(self):
        raw = {
            "goal": "g",
            "steps": [
                {
                    "kind": "tool",
                    "title": "跑一段代码",
                    "action": {"tool": "code_executor", "args": {"code": "print(1)"}},
                    "requires_approval": False,
                }
            ],
        }
        plan, issues = _norm(raw)
        assert plan["steps"][0]["requires_approval"] is True
        assert any("必须人工确认" in i for i in issues)

    def test_step_count_over_limit_truncated_with_issue(self):
        raw = {
            "goal": "g",
            "steps": [{"kind": "llm", "title": f"第{i}步"} for i in range(1, 21)],
        }
        plan, issues = _norm(raw, max_steps=5)
        assert len(plan["steps"]) == 5
        assert [s["idx"] for s in plan["steps"]] == [1, 2, 3, 4, 5]
        assert any("超出上限" in i for i in issues)

    def test_missing_title_autofilled_with_issue(self):
        raw = {
            "goal": "g",
            "steps": [{"kind": "tool", "action": {"tool": "calculator", "args": {"expression": "1+1"}}}],
        }
        plan, issues = _norm(raw)
        assert plan["steps"][0]["title"] == "calculator"
        assert any("缺少标题" in i for i in issues)

    def test_non_dict_step_skipped_with_issue(self):
        raw = {"goal": "g", "steps": ["字符串步骤", {"kind": "llm", "title": "正常"}]}
        plan, issues = _norm(raw)
        assert len(plan["steps"]) == 1
        assert any("不是对象" in i for i in issues)

    def test_llm_step_without_prompt_ref_gets_title(self):
        plan, _ = _norm({"goal": "g", "steps": [{"kind": "llm", "title": "起草报告"}]})
        assert plan["steps"][0]["action"] == {"prompt_ref": "起草报告"}

    def test_missing_required_tool_arg_flagged_at_approval(self):
        """★ 真实案例：模型把「创建对比表」规划成 calculator 步骤但没给 expression，
        结果跑到那一步才报「表达式不能为空」。这个错误本该在**审批任务书时**
        就让用户看到 —— 那时还没花掉前几步的时间与 token。
        """
        raw = {
            "goal": "g",
            "steps": [
                {
                    "kind": "tool",
                    "title": "创建价格对比表",
                    "action": {"tool": "calculator", "args": {}},
                }
            ],
        }
        plan, issues = _norm(
            raw, tool_required_args={"calculator": ["expression"]}
        )
        # 不阻断（用户可以在编辑器里补上参数），但必须留痕
        assert plan["steps"][0]["kind"] == "tool"
        assert any("缺少必填参数" in i and "expression" in i for i in issues)

    def test_present_required_tool_arg_not_flagged(self):
        raw = {
            "goal": "g",
            "steps": [
                {
                    "kind": "tool",
                    "title": "算一下",
                    "action": {"tool": "calculator", "args": {"expression": "1+1"}},
                }
            ],
        }
        _, issues = _norm(raw, tool_required_args={"calculator": ["expression"]})
        assert not any("缺少必填参数" in i for i in issues)

    def test_empty_string_arg_counts_as_missing(self):
        raw = {
            "goal": "g",
            "steps": [
                {
                    "kind": "tool",
                    "title": "算一下",
                    "action": {"tool": "calculator", "args": {"expression": "   "}},
                }
            ],
        }
        _, issues = _norm(raw, tool_required_args={"calculator": ["expression"]})
        assert any("缺少必填参数" in i for i in issues)

    def test_file_refs_filtered_to_known(self):
        plan, issues = _norm(
            {"goal": "g", "file_refs": ["rf_a", "rf_ghost"], "steps": [{"kind": "llm", "title": "x"}]},
            file_refs=["rf_a"],
        )
        assert plan["file_refs"] == ["rf_a"]
        assert any("不存在" in i for i in issues)

    def test_all_steps_invalid_raises(self):
        from app.workflow.planner import PlanParseError

        with pytest.raises(PlanParseError):
            _norm({"goal": "g", "steps": [1, 2, 3]})


class TestPlanPrompt:
    def test_prompt_lists_tools_and_limits(self):
        prompt = build_plan_prompt(
            raw_input="对比定价",
            mode="manual",
            force_search=True,
            tools=TOOLS,
            max_steps=8,
        )
        assert "web_search" in prompt
        assert "8" in prompt
        assert "对比定价" in prompt
        assert "联网搜索" in prompt

    def test_prompt_mentions_unparsed_files(self):
        prompt = build_plan_prompt(
            raw_input="x",
            mode="auto",
            force_search=False,
            tools=TOOLS,
            max_steps=5,
            file_summaries=[{"filename": "a.png", "parsed_chars": None}],
        )
        assert "a.png" in prompt
        assert "未能解析" in prompt

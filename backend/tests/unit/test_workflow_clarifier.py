"""澄清问题卡的纯逻辑测试。

重点在两处：
  * **每题必带可用的推荐答案** —— 推荐值必须落在选项里，否则前端点不到它，
    整个「确认而非审讯」的交互契约就断了
  * **不能把解析失败误判成「信息已足够」** —— 那会让用户在毫无察觉的情况下跳过澄清
"""

from __future__ import annotations

import pytest

from app.workflow.clarifier import (
    ClarifyParseError,
    build_clarify_prompt,
    format_clarifications,
    normalize_questions,
)


class TestNormalizeOptions:
    def test_string_options_get_derived_values(self):
        sufficient, questions, issues = normalize_questions(
            {"sufficient": False, "questions": [{"question": "读者是谁？", "options": ["技术决策者", "管理层"]}]}
        )
        assert not sufficient
        q = questions[0]
        assert [o["label"] for o in q["options"]] == ["技术决策者", "管理层"]
        assert all(o["value"] for o in q["options"])
        assert issues == []

    def test_dict_options_keep_value(self):
        _, questions, _ = normalize_questions(
            {
                "questions": [
                    {
                        "question": "范围？",
                        "options": [{"label": "只看国产", "value": "cn"}],
                    }
                ]
            }
        )
        assert questions[0]["options"][0] == {"label": "只看国产", "value": "cn"}

    def test_recommended_must_be_in_options(self):
        """推荐值落到选项之外时前端点不到它 —— 必须回落并留痕。"""
        _, questions, issues = normalize_questions(
            {
                "questions": [
                    {
                        "question": "读者？",
                        "options": [
                            {"label": "技术", "value": "tech"},
                            {"label": "管理层", "value": "exec"},
                        ],
                        "recommended": "不存在的值",
                    }
                ]
            }
        )
        assert questions[0]["recommended"] == "tech"
        assert any("推荐答案" in i for i in issues)

    def test_recommended_without_options_becomes_single_option(self):
        _, questions, _ = normalize_questions(
            {"questions": [{"question": "什么时候要？", "recommended": "本周内"}]}
        )
        q = questions[0]
        assert len(q["options"]) == 1
        assert q["recommended"] == q["options"][0]["value"]

    def test_options_capped(self):
        _, questions, _ = normalize_questions(
            {"questions": [{"question": "?", "options": [f"opt{i}" for i in range(20)]}]}
        )
        assert len(questions[0]["options"]) <= 5

    def test_reason_kept(self):
        _, questions, _ = normalize_questions(
            {"questions": [{"question": "?", "options": ["a"], "reason": "因为你要做选型"}]}
        )
        assert questions[0]["reason"] == "因为你要做选型"


class TestNormalizeQuestions:
    def test_ids_are_reassigned_sequentially(self):
        _, questions, _ = normalize_questions(
            {
                "questions": [
                    {"id": "乱写的", "question": "A", "options": ["x"]},
                    {"id": "another", "question": "B", "options": ["y"]},
                ]
            }
        )
        assert [q["id"] for q in questions] == ["q1", "q2"]

    def test_questions_as_dict_values(self):
        _, questions, _ = normalize_questions(
            {"questions": {"a": {"question": "A", "options": ["x"]}}}
        )
        assert [q["question"] for q in questions] == ["A"]

    def test_alternate_key_names(self):
        _, questions, _ = normalize_questions(
            {"items": [{"question": "A", "options": ["x"]}]}
        )
        assert len(questions) == 1

    def test_too_many_questions_truncated(self):
        _, questions, issues = normalize_questions(
            {"questions": [{"question": f"Q{i}", "options": ["x"]} for i in range(6)]},
            max_questions=3,
        )
        assert len(questions) == 3
        assert any("超过单轮上限" in i for i in issues)

    @pytest.mark.parametrize("value", [True, "true", "True", "1", "yes", "是"])
    def test_sufficient_variants(self, value):
        sufficient, questions, _ = normalize_questions(
            {"sufficient": value, "questions": []}
        )
        assert sufficient is True
        assert questions == []

    def test_sufficient_via_alias_key(self):
        sufficient, _, _ = normalize_questions({"complete": True, "questions": []})
        assert sufficient is True

    def test_empty_questions_without_sufficient_raises(self):
        """★ 关键：解析不出问题、又没声明足够时，**不能**当作「够了」——
        否则用户会在毫无察觉的情况下被跳过澄清。
        """
        with pytest.raises(ClarifyParseError):
            normalize_questions({"sufficient": False, "questions": []})

    def test_missing_question_text_skipped(self):
        _, questions, issues = normalize_questions(
            {"questions": [{"options": ["x"]}, {"question": "有效", "options": ["y"]}]}
        )
        assert [q["question"] for q in questions] == ["有效"]
        assert any("没有内容" in i for i in issues)

    def test_non_dict_question_skipped(self):
        _, questions, issues = normalize_questions(
            {"questions": ["字符串", {"question": "有效", "options": ["y"]}]}
        )
        assert len(questions) == 1
        assert any("不是对象" in i for i in issues)

    def test_non_dict_payload_raises(self):
        with pytest.raises(ClarifyParseError):
            normalize_questions(["不是对象"])  # type: ignore[arg-type]


class TestFormatClarifications:
    ROUNDS = [
        {
            "round": 1,
            "questions": [
                {
                    "id": "q1",
                    "question": "读者是谁？",
                    "options": [
                        {"label": "技术决策者", "value": "tech"},
                        {"label": "管理层", "value": "exec"},
                    ],
                }
            ],
            "answers": {"q1": {"value": "tech", "text": ""}},
        }
    ]

    def test_resolves_option_label(self):
        text = format_clarifications(self.ROUNDS)
        assert "读者是谁？" in text
        # 展示给人看的是 label，不是内部 value
        assert "技术决策者" in text
        assert "value" not in text

    def test_freeform_text_wins_over_value(self):
        rounds = [
            {
                "round": 1,
                "questions": [
                    {"id": "q1", "question": "?", "options": [{"label": "A", "value": "a"}]}
                ],
                "answers": {"q1": {"value": "a", "text": "其实我要的是别的"}},
            }
        ]
        text = format_clarifications(rounds)
        assert "其实我要的是别的" in text
        assert "A" not in text

    def test_unanswered_questions_omitted(self):
        rounds = [
            {
                "round": 1,
                "questions": [
                    {"id": "q1", "question": "答了的", "options": []},
                    {"id": "q2", "question": "没答的", "options": []},
                ],
                "answers": {"q1": {"value": "x", "text": ""}},
            }
        ]
        text = format_clarifications(rounds)
        assert "答了的" in text
        assert "没答的" not in text

    def test_empty(self):
        assert format_clarifications([]) == ""
        assert format_clarifications(None) == ""


class TestBuildClarifyPrompt:
    def test_includes_goal_and_round(self):
        prompt = build_clarify_prompt(raw_input="做一份对比报告", round_no=2, max_rounds=3)
        assert "做一份对比报告" in prompt
        assert "第 2 / 3 轮" in prompt

    def test_includes_previous_answers_to_avoid_repeats(self):
        """不带历史的话，第二轮会换种说法再问一遍同样的事。"""
        prompt = build_clarify_prompt(
            raw_input="x",
            history=[
                {
                    "round": 1,
                    "questions": [{"id": "q1", "question": "读者是谁？", "options": []}],
                    "answers": {"q1": {"value": "", "text": "技术决策者"}},
                }
            ],
        )
        assert "已经问过并得到答复的内容" in prompt
        assert "技术决策者" in prompt

    def test_mentions_unparsed_files(self):
        prompt = build_clarify_prompt(
            raw_input="x",
            file_summaries=[{"filename": "a.png", "parsed_chars": None}],
        )
        assert "未能解析" in prompt

    def test_workspace_name_never_leaks_into_prompt(self):
        """★ 回归：工作区名是归类标签，不是任务主题。

        实测过的一类真实故障：原始输入为「帮我做一份调研」（无主题）时，
        模型会抓住上下文里唯一的实体名词 —— 工作区名 —— 当成调研对象，
        产出「了解用户对 P4 界面的满意度」这种凭空捏造的主题。
        加否定指令压不住它，唯一可靠的做法是**根本不提供这个信息**。
        """
        prompt = build_clarify_prompt(raw_input="帮我做一份调研")
        assert "工作区" not in prompt
        # 签名里也不应再接受这个参数，防止有人顺手加回去
        import inspect

        params = inspect.signature(build_clarify_prompt).parameters
        assert "workspace_name" not in params

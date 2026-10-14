"""交付物去重与截断标记。

两块都是「不修也不会报错，但会让读的人被误导」的问题：

  * **重复** —— `build_deliverable` 先写「结论」= 最后一个 synthesis 步的正文，
    然后「执行明细」又把每一步（含那个 synthesis）原文列一遍。
    实测某次交付物 59,343 字符里有 18,634 字符是同一段文本，读者要读两遍。

  * **截断** —— `output.truncated` 会落库，但明细只写状态，
    于是「模型没写完」的部分被当成完整内容交付，读的人无从分辨。

这两条共同的要求是：**不确定性必须摆在明面上**。
"""

from __future__ import annotations

from app.workflow.delivery import build_deliverable, conclusion_of

PLAN = {"goal": "整理教程", "audience": "新手", "constraints": [], "assumptions": []}


def _step(idx, kind, title, summary="", status="done", **output_extra):
    return {
        "idx": idx,
        "kind": kind,
        "title": title,
        "status": status,
        "action": {},
        "output": {"summary": summary, **output_extra} if summary else (output_extra or None),
    }


CONCLUSION = "最终结论：应先用 md 再导出。" * 3
STEP2_BODY = "第二步的中间产物。" * 3


class TestConclusionOf:
    def test_prefers_last_synthesis(self):
        steps = [
            _step(1, "llm", "分析", "第一步产出"),
            _step(2, "synthesis", "汇总", CONCLUSION),
        ]
        idx, text = conclusion_of(steps)
        assert idx == 2
        assert text == CONCLUSION

    def test_falls_back_to_last_step_with_output(self):
        steps = [_step(1, "llm", "分析", "第一步产出"), _step(2, "llm", "收尾", "最后产出")]
        idx, text = conclusion_of(steps)
        assert idx == 2 and text == "最后产出"

    def test_skips_steps_without_output(self):
        steps = [_step(1, "llm", "分析", "有产出"), _step(2, "tool", "查一下"), _step(3, "llm", "空步")]
        idx, _ = conclusion_of(steps)
        assert idx == 1

    def test_empty_returns_none(self):
        assert conclusion_of([]) == (None, "")


class TestDeliverableDeduplication:
    def test_conclusion_body_appears_only_once(self):
        """★ 同一段正文在交付物里只能出现一次。"""
        steps = [
            _step(1, "llm", "分析", STEP2_BODY),
            _step(2, "synthesis", "汇总", CONCLUSION),
        ]
        text = build_deliverable(PLAN, steps)
        assert text.count(CONCLUSION) == 1, "结论被重复写入了"

    def test_pointer_replaces_duplicate(self):
        steps = [
            _step(1, "llm", "分析", STEP2_BODY),
            _step(2, "synthesis", "汇总", CONCLUSION),
        ]
        text = build_deliverable(PLAN, steps)
        assert "## 结论" in text
        assert "## 执行明细" in text
        # 明细里那一步要留下可循的指路，而不是凭空少一段
        assert "正文见上方「结论」" in text
        # 其他步骤的正文照常完整保留
        assert text.count(STEP2_BODY) == 1

    def test_other_steps_kept_verbatim(self):
        steps = [
            _step(1, "llm", "甲", "甲的内容"),
            _step(2, "llm", "乙", "乙的内容"),
            _step(3, "synthesis", "丙", "丙的结论"),
        ]
        text = build_deliverable(PLAN, steps)
        assert text.count("甲的内容") == 1
        assert text.count("乙的内容") == 1
        assert text.count("丙的结论") == 1

    def test_no_conclusion_means_no_pointer(self):
        """全都没有产出时不该出现「见上方结论」这种指向不存在内容的指引。"""
        steps = [_step(1, "llm", "空", "", status="failed", error="炸了")]
        text = build_deliverable(PLAN, steps)
        assert "## 结论" not in text
        assert "正文见上方" not in text


class TestTruncationMarked:
    def test_truncated_step_marked_in_body(self):
        steps = [
            _step(1, "llm", "分析", "半截内容", truncated=True),
            _step(2, "synthesis", "汇总", CONCLUSION),
        ]
        text = build_deliverable(PLAN, steps)
        assert "此段可能不完整" in text

    def test_truncated_count_in_header(self):
        """★ 放在最顶部：读的人该在第一眼就知道有段落没写完。"""
        steps = [
            _step(1, "llm", "甲", "半截", truncated=True),
            _step(2, "llm", "乙", "也半截", truncated=True),
            _step(3, "synthesis", "丙", CONCLUSION),
        ]
        text = build_deliverable(PLAN, steps)
        header = text.split("## 约束")[0]
        assert "2 步输出被截断" in header
        assert header.index("被截断") < header.index("## 结论")

    def test_not_truncated_no_marker(self):
        steps = [_step(1, "synthesis", "汇总", CONCLUSION)]
        text = build_deliverable(PLAN, steps)
        assert "此段可能不完整" not in text
        assert "被截断" not in text

    def test_truncated_conclusion_still_marked(self):
        """结论本身被截断时也要标 —— 它正是读者最当真的那段。"""
        steps = [_step(1, "synthesis", "汇总", CONCLUSION, truncated=True)]
        text = build_deliverable(PLAN, steps)
        assert "1 步输出被截断" in text
        assert "此段可能不完整" in text

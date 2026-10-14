"""交付物组装与 TTL 清理的纯逻辑测试。

两块代码出错的后果都很重：
  * 交付物 —— 用户会把它当成最终成果带走，链接指错或结论缺失影响面很大
  * 清理   —— **误删用户文件不可逆**

因此这里逐条覆盖，尤其是「不能删什么」。
"""

from __future__ import annotations

from app.workflow.cleanup import plan_cleanup
from app.workflow.delivery import (
    build_deliverable,
    collect_final_sources,
    deliverable_filename,
)

PLAN = {
    "goal": "产出定价对比",
    "audience": "技术决策者",
    "constraints": ["中文", "含价格表"],
    "assumptions": ["均取标准档"],
}

SOURCES_A = [
    {"title": "官方定价页", "href": "https://a.example/pricing", "reachable": True},
    {"title": "社区讨论", "href": "https://b.example/t", "reachable": False},
]


def _step(idx, kind, title, status="done", summary="", sources=None, **extra):
    step = {
        "idx": idx,
        "kind": kind,
        "title": title,
        "status": status,
        "action": {},
        "output": None,
    }
    if summary or sources:
        step["output"] = {"summary": summary}
        if sources:
            step["output"]["sources"] = sources
    step.update(extra)
    return step


# ---------------------------------------------------------------------------
#  交付物
# ---------------------------------------------------------------------------


class TestDeliverableFilename:
    def test_strips_path_separators(self):
        name = deliverable_filename("2026/Q1 报告: 定价")
        assert "/" not in name and ":" not in name
        assert name.endswith(".md")

    def test_truncates_long_goal(self):
        name = deliverable_filename("很长的目标" * 20)
        assert len(name) <= 27

    def test_falls_back_when_empty(self):
        assert deliverable_filename("").endswith(".md")


class TestCollectFinalSources:
    def test_pools_and_numbers_across_steps(self):
        steps = [
            _step(1, "tool", "搜", sources=SOURCES_A),
            _step(2, "tool", "再搜", sources=[SOURCES_A[0], {"href": "https://c.example"}]),
        ]
        out = collect_final_sources(steps)
        assert [s["href"] for s in out] == [
            "https://a.example/pricing",
            "https://b.example/t",
            "https://c.example",
        ]
        assert [s["ref"] for s in out] == [1, 2, 3]

    def test_order_follows_step_idx_not_list_order(self):
        steps = [
            _step(2, "tool", "后", sources=[{"href": "https://second.example"}]),
            _step(1, "tool", "先", sources=[{"href": "https://first.example"}]),
        ]
        assert collect_final_sources(steps)[0]["href"] == "https://first.example"

    def test_ignores_steps_without_sources(self):
        assert collect_final_sources([_step(1, "llm", "写", summary="x")]) == []


class TestBuildDeliverable:
    def test_contains_goal_constraints_and_conclusion(self):
        steps = [
            _step(1, "tool", "搜索定价", summary="找到三家", sources=SOURCES_A),
            _step(2, "synthesis", "汇总", summary="结论：A 家最便宜 [1]"),
        ]
        doc = build_deliverable(PLAN, steps, raw_input="对比定价")
        assert "# 产出定价对比" in doc
        assert "技术决策者" in doc
        assert "- 中文" in doc
        assert "结论：A 家最便宜" in doc
        assert "## 执行明细" in doc
        assert "搜索定价" in doc

    def test_resolves_numbered_citations_to_real_links(self):
        steps = [
            _step(1, "tool", "搜", summary="s", sources=SOURCES_A),
            _step(2, "synthesis", "汇总", summary="见 [1] 与 [2]"),
        ]
        doc = build_deliverable(PLAN, steps)
        assert "[[1]](https://a.example/pricing)" in doc
        assert "[[2]](https://b.example/t)" in doc

    def test_out_of_range_citation_left_untouched(self):
        """★ 编号对不上来源时保留原文 —— 猜错一个链接比没有链接更糟。"""
        steps = [
            _step(1, "tool", "搜", summary="s", sources=SOURCES_A),
            _step(2, "synthesis", "汇总", summary="见 [9]"),
        ]
        doc = build_deliverable(PLAN, steps)
        assert "[9]" in doc
        assert "[[9]]" not in doc

    def test_no_citation_syntax_when_no_sources(self):
        steps = [_step(1, "synthesis", "汇总", summary="见 [1]")]
        doc = build_deliverable(PLAN, steps)
        assert "[1]" in doc  # 原样保留

    def test_marks_unreachable_source(self):
        steps = [
            _step(1, "tool", "搜", summary="s", sources=SOURCES_A),
            _step(2, "synthesis", "汇总", summary="x"),
        ]
        doc = build_deliverable(PLAN, steps)
        assert "可能需要校验" not in doc
        assert "可能已失效" in doc

    def test_reference_section_numbered(self):
        steps = [
            _step(1, "tool", "搜", summary="s", sources=SOURCES_A),
            _step(2, "synthesis", "汇总", summary="x"),
        ]
        doc = build_deliverable(PLAN, steps)
        assert "1. [官方定价页](https://a.example/pricing)" in doc
        assert "2. [社区讨论](https://b.example/t)" in doc

    def test_skipped_and_failed_steps_are_recorded(self):
        """交付物要如实反映执行情况 —— 跳过与失败都必须写进去。"""
        steps = [
            _step(1, "llm", "第一步", status="done", summary="ok"),
            _step(2, "llm", "第二步", status="skipped"),
            _step(3, "llm", "第三步", status="failed", error="超时"),
        ]
        doc = build_deliverable(PLAN, steps)
        assert "已跳过" in doc
        assert "失败" in doc and "超时" in doc
        assert "1 / 3 步完成" in doc

    def test_conclusion_prefers_synthesis_step(self):
        steps = [
            _step(1, "llm", "中间产物", summary="这是中间产物"),
            _step(2, "synthesis", "最终结论", summary="这是最终结论"),
        ]
        doc = build_deliverable(PLAN, steps)
        conclusion = doc.split("## 结论")[1].split("## 执行明细")[0]
        assert "这是最终结论" in conclusion
        assert "这是中间产物" not in conclusion

    def test_handles_empty_plan_and_steps(self):
        doc = build_deliverable(None, [], raw_input="只有一个需求")
        assert "只有一个需求" in doc
        assert "## 结论" not in doc

    def test_tool_step_action_shown(self):
        steps = [
            {
                "idx": 1,
                "kind": "tool",
                "title": "搜索",
                "status": "done",
                "action": {"tool": "web_search", "args": {"query": "x"}},
                "output": {"summary": "结果"},
                "retry_count": 2,
            }
        ]
        doc = build_deliverable(PLAN, steps)
        assert "`web_search`" in doc
        assert "重试次数：2" in doc


# ---------------------------------------------------------------------------
#  TTL 清理（重点：不能删什么）
# ---------------------------------------------------------------------------


class TestPlanCleanup:
    def test_expired_file_is_deleted(self):
        plan = plan_cleanup(
            expired=[{"id": "rf1", "stored_path": "/w/files/a.txt"}],
            known_paths=["/w/files/a.txt"],
            disk_files=["/w/files/a.txt"],
        )
        assert plan["delete_files"] == ["/w/files/a.txt"]
        assert plan["delete_record_ids"] == ["rf1"]

    def test_record_removed_even_if_file_already_gone(self):
        """文件已不在时记录仍要清掉，否则它会永远留在过期列表里。"""
        plan = plan_cleanup(
            expired=[{"id": "rf1", "stored_path": "/w/files/gone.txt"}],
            known_paths=[],
            disk_files=[],
        )
        assert plan["delete_files"] == []
        assert plan["missing_files"] == ["/w/files/gone.txt"]
        assert plan["delete_record_ids"] == ["rf1"]

    def test_not_expired_file_is_never_touched(self):
        """★ 最重要的一条：没到期的文件一个都不能删。"""
        plan = plan_cleanup(
            expired=[],
            known_paths=["/w/files/keep.txt"],
            disk_files=["/w/files/keep.txt"],
        )
        assert plan["delete_files"] == []
        assert plan["orphan_files"] == []

    def test_orphan_files_detected(self):
        """磁盘有、库里没有的僵尸文件必须清掉 —— 否则永远不会被任何流程处理。"""
        plan = plan_cleanup(
            expired=[],
            known_paths=["/w/files/known.txt"],
            disk_files=["/w/files/known.txt", "/w/files/orphan.txt"],
        )
        assert plan["orphan_files"] == ["/w/files/orphan.txt"]

    def test_limit_truncates_and_flags(self):
        expired = [{"id": f"rf{i}", "stored_path": f"/w/{i}"} for i in range(10)]
        plan = plan_cleanup(expired=expired, known_paths=[], disk_files=[], limit=3)
        assert len(plan["delete_record_ids"]) == 3
        assert plan["truncated"] is True

    def test_no_truncation_flag_when_within_limit(self):
        plan = plan_cleanup(
            expired=[{"id": "rf1", "stored_path": "/w/1"}],
            known_paths=[],
            disk_files=[],
            limit=10,
        )
        assert plan["truncated"] is False

    def test_record_without_path_still_removed(self):
        plan = plan_cleanup(
            expired=[{"id": "rf1", "stored_path": ""}], known_paths=[], disk_files=[]
        )
        assert plan["delete_record_ids"] == ["rf1"]
        assert plan["delete_files"] == []

    def test_malformed_records_ignored(self):
        plan = plan_cleanup(expired=[None, "x"], known_paths=[], disk_files=[])
        assert plan["delete_record_ids"] == []

    def test_empty_everything(self):
        plan = plan_cleanup(expired=[], known_paths=[], disk_files=[])
        assert plan == {
            "delete_files": [],
            "delete_record_ids": [],
            "missing_files": [],
            "orphan_files": [],
            "truncated": False,
        }

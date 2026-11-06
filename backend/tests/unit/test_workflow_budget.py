"""工作流优化（P5/P6）的行为契约。

P6：单 run token 预算 —— 超限要**在执行前**拦下，不能等跑完才发现。
P5：失败步骤可以原地重试 —— 否则 next_runnable_idx 变 null，「继续执行」消失。
"""

from app.core.config import get_workflow_settings
from app.workflow.executor import budget_exceeded, runnable_next_step


class TestTokenBudget:
    def test_default_budget_is_finite(self):
        """默认必须有上限，否则一个任务能烧掉整月额度。"""
        assert get_workflow_settings().max_tokens_per_run > 0

    def test_zero_limit_means_unlimited(self):
        assert budget_exceeded(999_999, 0) is False
        assert budget_exceeded(999_999, -1) is False

    def test_under_limit(self):
        assert budget_exceeded(100, 200) is False

    def test_at_limit_stops(self):
        """恰好等于上限就要停：否则每步都「还没超」直到把它撑爆。"""
        assert budget_exceeded(200, 200) is True

    def test_over_limit(self):
        assert budget_exceeded(201, 200) is True


class TestFailedStepResume:
    """失败步必须能续跑，这是 P5 的核心。"""

    def test_failed_step_is_runnable(self):
        steps = [
            {"idx": 1, "status": "done"},
            {"idx": 2, "status": "failed"},
            {"idx": 3, "status": "pending"},
        ]
        nxt = runnable_next_step(steps)
        assert nxt is not None
        assert nxt["idx"] == 2, "失败步应原地重试，而不是跳过它继续"

    def test_done_steps_still_skipped(self):
        steps = [
            {"idx": 1, "status": "done"},
            {"idx": 2, "status": "skipped"},
            {"idx": 3, "status": "pending"},
        ]
        assert runnable_next_step(steps)["idx"] == 3

    def test_rejected_step_does_not_auto_run(self):
        """用户明确拒绝的步骤不能被自动重跑。"""
        steps = [
            {"idx": 1, "status": "rejected"},
            {"idx": 2, "status": "pending"},
        ]
        assert runnable_next_step(steps) is None

    def test_awaiting_approval_blocks(self):
        steps = [
            {"idx": 1, "status": "awaiting_approval"},
            {"idx": 2, "status": "pending"},
        ]
        assert runnable_next_step(steps) is None

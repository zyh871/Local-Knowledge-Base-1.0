"""P2 真机验证：任务书生成（真实 LLM）→ 编辑 → 审批 / 驳回。

不做 UI 断言，只打接口 —— 用于确认「模型真的能产出可审批的任务书」，
以及状态机在真实往返下的一致性。UI 部分由 Playwright 脚本覆盖。

用法：
    venv/Scripts/python.exe scripts/verify_p2_plan.py [baseUrl]

结束时会删掉自己创建的工作区，不留残余数据。
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
USERNAME = "admin"
PASSWORD = "admin123"
WS_NAME = "P2真机验证"

failures: list[str] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  → {extra}" if extra else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=300.0, trust_env=False)

    token = client.post(
        "/token",
        data={"username": USERNAME, "password": PASSWORD},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print(f"已登录，token 长度 {len(token)}")

    caps = client.get("/workflow/capabilities", headers=headers).json()
    check(bool(caps.get("tools")), "capabilities 下发了工具清单", str(caps.get("tools")))
    check(caps.get("max_steps") == 12, "步骤上限下发", str(caps.get("max_steps")))

    # ── 清理历史遗留 ────────────────────────────────────────────────
    for item in client.get("/workflow/workspaces", headers=headers).json()["workspaces"]:
        if item["name"] == WS_NAME:
            client.delete(f"/workflow/workspaces/{item['id']}", headers=headers)
            print(f"清理历史工作区 {item['id']}")

    workspace = client.post(
        "/workflow/workspaces", json={"name": WS_NAME}, headers=headers
    ).json()

    try:
        # ── 生成任务书 ─────────────────────────────────────────────
        run = client.post(
            "/workflow/runs",
            json={
                "workspace_id": workspace["id"],
                "raw_input": "把这段产品需求整理成一份包含验收标准的开发任务清单",
            },
            headers=headers,
        ).json()
        print(f"\n任务 {run['id']} 已创建（status={run['status']}）")

        events: list[dict[str, Any]] = []
        with client.stream(
            "POST", f"/workflow/runs/{run['id']}/plan/stream", headers=headers
        ) as resp:
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        kinds: dict[str, int] = {}
        for event in events:
            kinds[event["type"]] = kinds.get(event["type"], 0) + 1
        print(f"事件统计：{kinds}")

        thinking = "".join(
            e["content"].get("text", "") for e in events if e["type"] == "thinking_delta"
        )
        check(kinds.get("thinking_delta", 0) > 0, "有推理流输出")
        # 这是分道逻辑的关键断言：推理段不能混入 JSON
        check("{" not in thinking, "推理流里没有 JSON 花括号")
        check(events[0]["content"]["phase"] == "plan", "首个事件是 plan 相位开始")

        final = [e for e in events if e["type"] == "final"][-1]["content"]
        check(final.get("ok") is True, "生成成功", str(final.get("status")))
        print(f"    使用模型：{final.get('model')}")
        print(f"    推理长度：{len(thinking)} 字")

        plan = final.get("plan") or {}
        check(bool(plan.get("goal")), "任务书有目标", str(plan.get("goal"))[:60])
        steps = plan.get("steps") or []
        check(1 <= len(steps) <= caps["max_steps"], f"步骤数在 1..{caps['max_steps']} 内", str(len(steps)))
        print("    步骤：")
        for step in steps:
            action = json.dumps(step["action"], ensure_ascii=False)
            print(
                f"      {step['idx']}. [{step['kind']}] {step['title']}"
                f"  approval={step['requires_approval']}  {action[:80]}"
            )

        detail = client.get(f"/workflow/runs/{run['id']}", headers=headers).json()
        check(detail["status"] == "awaiting_approval", "状态迁移到待审批", detail["status"])
        check(len(detail["steps"]) == len(steps), "steps 表与任务书一致")

        # ── 编辑后保存（同时验证白名单兜底）──────────────────────
        # 替换**最后一步**而不是追加：追加会先撞上 max_steps 截断，
        # 非法步骤根本进不了校验，断言就变成假通过。
        edited_steps = [dict(s) for s in steps]
        edited_steps[-1] = {
            "kind": "tool",
            "title": "非法工具应被降级",
            "action": {"tool": "rm_rf", "args": {"path": "/"}},
        }
        saved = client.put(
            f"/workflow/runs/{run['id']}/plan",
            json={**plan, "steps": edited_steps},
            headers=headers,
        ).json()
        last = saved["plan"]["steps"][-1]
        check(last["kind"] != "tool", "手改的非法工具被降级", last["kind"])
        check(
            any("不可用" in i for i in saved["issues"]),
            "修正留下了 issue",
            str(saved["issues"]),
        )

        # ── 驳回 ──────────────────────────────────────────────────
        rejected = client.post(
            f"/workflow/runs/{run['id']}/reject",
            json={"comment": "第一步的关键词太宽泛，请收窄"},
            headers=headers,
        ).json()
        check(rejected["run"]["status"] == "planning", "驳回后回到待规划", rejected["run"]["status"])
        check(bool(rejected["run"]["plan"]), "驳回保留任务书供继续编辑")

        # ── 批准 ──────────────────────────────────────────────────
        approved = client.post(
            f"/workflow/runs/{run['id']}/approve", headers=headers
        ).json()
        check(approved["run"]["status"] == "approved", "批准后状态为已批准", approved["run"]["status"])
        check(
            approved["run"]["reject_comment"] is None,
            "批准清掉了旧的驳回意见",
        )

        # ── 已批准后不能再重新生成 ────────────────────────────────
        again = client.post(
            f"/workflow/runs/{run['id']}/plan/stream", headers=headers
        )
        last_event = [
            json.loads(line[6:])
            for line in again.text.split("\n\n")
            if line.startswith("data: ")
        ][-1]
        check(last_event["content"]["ok"] is False, "已批准后拒绝重新生成")

    finally:
        client.delete(f"/workflow/workspaces/{workspace['id']}", headers=headers)
        print(f"\n已清理工作区 {workspace['id']}")

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{' | '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""P5 真机验证：驳回某一步 → 保留前序产出 → 从该步重新规划。

走真实 LLM。重点验证「接口测不出」的那件事：
**驳回第 N 步时，前 N-1 步的产出是否真的还在** —— 它决定了用户是否会白等一遍。

同时验证：终止任务保留产出、执行中切换模式立即生效。

用法：
    venv/Scripts/python.exe scripts/verify_p5_control.py [baseUrl]
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
WS_NAME = "P5真机验证"

failures: list[str] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  → {extra}" if extra else ""))
    if not ok:
        failures.append(label)


def stream(client: httpx.Client, path: str, headers: dict[str, str]):
    with client.stream("POST", path, headers=headers) as resp:
        return [
            json.loads(line[6:])
            for line in resp.iter_lines()
            if line.startswith("data: ")
        ]


def final_of(events: list[dict[str, Any]]) -> dict[str, Any]:
    finals = [e for e in events if e["type"] == "final"]
    return finals[-1]["content"] if finals else {}


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=600.0, trust_env=False)
    token = client.post(
        "/token",
        data={"username": "admin", "password": "admin123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("已登录\n")

    for item in client.get("/workflow/workspaces", headers=headers).json()["workspaces"]:
        if item["name"] == WS_NAME:
            client.delete(f"/workflow/workspaces/{item['id']}", headers=headers)

    workspace = client.post(
        "/workflow/workspaces", json={"name": WS_NAME}, headers=headers
    ).json()

    def detail(run_id: str) -> dict:
        return client.get(f"/workflow/runs/{run_id}", headers=headers).json()

    try:
        # ── 准备：一个至少 4 步的计划 ──────────────────────────
        run = client.post(
            "/workflow/runs",
            json={
                "workspace_id": workspace["id"],
                "raw_input": (
                    "写一份「小团队如何引入代码评审」的说明，"
                    "分别覆盖：为什么要做、具体流程、常见阻力与应对"
                ),
                "mode": "auto",
            },
            headers=headers,
        ).json()
        plan = final_of(stream(client, f"/workflow/runs/{run['id']}/plan/stream", headers))
        check(plan.get("ok") is True, "任务书生成成功", str(plan.get("error") or ""))
        steps = (plan.get("plan") or {}).get("steps") or []
        print(f"    计划 {len(steps)} 步：")
        for s in steps:
            print(f"      {s['idx']}. [{s['kind']}] {s['title']}")
        if len(steps) < 3:
            print("    （步骤太少，补一步以便验证驳回第三条之后仍有后续）")

        client.post(f"/workflow/runs/{run['id']}/approve", headers=headers)

        # ── 1. 执行前两步 ──────────────────────────────────────
        print("\n=== 1) 执行前两步 ===")
        for idx in (1, 2):
            result = final_of(
                stream(client, f"/workflow/runs/{run['id']}/steps/{idx}/execute", headers)
            )
            check(result.get("ok") is True, f"步骤 {idx} 执行成功", str(result.get("error") or ""))

        before = detail(run["id"])
        done_before = [s for s in before["steps"] if s["status"] == "done"]
        check(len(done_before) == 2, "前两步已完成", str(len(done_before)))
        snapshot = {
            s["idx"]: {"id": s["id"], "output": s.get("output"), "title": s["title"]}
            for s in done_before
        }
        for idx, item in snapshot.items():
            print(f"    步骤 {idx}「{item['title']}」产出 {len((item['output'] or {}).get('summary', ''))} 字")

        # ── 2. 驳回第 3 步 → 重新规划 ──────────────────────────
        print("\n=== 2) 驳回第 3 步并重新规划 ===")
        rejected = client.post(
            f"/workflow/runs/{run['id']}/steps/3/reject",
            json={"comment": "不要分成三个小节写，改成一张对照表：场景 / 做法 / 常见坑"},
            headers=headers,
        )
        check(rejected.status_code == 200, "驳回成功", rejected.text[:120])
        body = rejected.json()
        check(body["preserved"] == 2, "保留了前 2 步", str(body["preserved"]))
        check(body["removed"] >= 1, "清掉了第 3 步及之后", str(body["removed"]))
        check(body["run"]["status"] == "planning", "状态回到待规划", body["run"]["status"])
        check(
            "对照表" in (body["run"].get("reject_comment") or ""),
            "驳回意见已记录",
        )

        replanned = final_of(
            stream(client, f"/workflow/runs/{run['id']}/plan/stream", headers)
        )
        check(replanned.get("ok") is True, "重新规划成功", str(replanned.get("error") or ""))

        after = detail(run["id"])
        after_by_idx = {s["idx"]: s for s in after["steps"]}
        check(len(after["steps"]) >= 3, "重新规划后仍有多步", str(len(after["steps"])))

        # ★ 核心断言：前两步的行与产出必须原样还在
        for idx, item in snapshot.items():
            current = after_by_idx.get(idx)
            check(current is not None, f"步骤 {idx} 仍在时间线上")
            if current:
                check(current["id"] == item["id"], f"步骤 {idx} 的行没有被重建")
                check(current["status"] == "done", f"步骤 {idx} 仍是已完成", current["status"])
                check(
                    current.get("output") == item["output"],
                    f"★ 步骤 {idx} 的产出没有丢失",
                    f"{len((current.get('output') or {}).get('summary', ''))} 字",
                )

        # run.plan.steps 与 steps 表必须一致
        table = [(s["idx"], s["kind"], s["status"]) for s in after["steps"]]
        plan_view = [(s["idx"], s["kind"], s["status"]) for s in (after["plan"] or {}).get("steps", [])]
        check(table == plan_view, "run.plan 与 steps 表一致", f"{len(table)} vs {len(plan_view)}")

        print("    重新规划后的步骤：")
        for s in after["steps"]:
            print(f"      {s['idx']}. [{s['kind']}] {s['title']}  ({s['status']})")

        # ── 3. 执行中切换模式 ──────────────────────────────────
        print("\n=== 3) 执行中切换模式 ===")
        client.post(f"/workflow/runs/{run['id']}/approve", headers=headers)
        patched = client.patch(
            f"/workflow/runs/{run['id']}", json={"mode": "manual"}, headers=headers
        )
        check(patched.status_code == 200, "改成人工审查")
        check(patched.json()["mode"] == "manual", "模式已生效", patched.json()["mode"])
        patched = client.patch(
            f"/workflow/runs/{run['id']}", json={"mode": "auto"}, headers=headers
        )
        check(patched.json()["mode"] == "auto", "切回自动")

        # ── 4. 终止任务 ────────────────────────────────────────
        print("\n=== 4) 终止任务 ===")
        cancelled = client.post(
            f"/workflow/runs/{run['id']}/cancel",
            json={"comment": "先到这里"},
            headers=headers,
        )
        check(cancelled.status_code == 200, "终止成功")
        final_state = cancelled.json()["run"]
        check(final_state["status"] == "cancelled", "状态为已终止", final_state["status"])
        kept = [s for s in final_state["steps"] if s["status"] == "done"]
        check(len(kept) == 2, "已完成的产出被保留", f"{len(kept)} 步")
        for s in kept:
            check(
                bool((s.get("output") or {}).get("summary")),
                f"步骤 {s['idx']} 产出仍在",
            )

        blocked = final_of(
            stream(client, f"/workflow/runs/{run['id']}/steps/3/execute", headers)
        )
        check(blocked.get("ok") is False, "终止后不能再执行")

        # ── 5. 编辑锁 ──────────────────────────────────────────
        print("\n=== 5) 任务书编辑锁 ===")
        locked = client.put(
            f"/workflow/runs/{run['id']}/plan",
            json={"goal": "偷改", "steps": [{"kind": "llm", "title": "x"}]},
            headers=headers,
        )
        check(locked.status_code == 409, "终止后拒绝整体替换任务书", str(locked.status_code))

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

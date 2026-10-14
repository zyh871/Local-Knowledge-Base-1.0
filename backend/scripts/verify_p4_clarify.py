"""P4 真机验证：澄清 → 回答 → 信息足够 → 生成任务书。

走真实 LLM。重点验证「接口测不出」的链路：

  * 每题是否真的带**可用的**推荐答案（推荐值必须落在选项里，否则前端点不到）
  * 轮次上限是否真的生效（不会无限追问）
  * 用户的回答是否被持久化并可回读（前端要展示「原话 → 澄清补充 → 任务书」）
  * 澄清结束后是否能顺利进入规划

「澄清结论进入计划提示词」由 tests/test_workflow_clarify.py 用假模型做了确定性断言，
这里只跑真实链路。

用法：
    venv/Scripts/python.exe scripts/verify_p4_clarify.py [baseUrl]
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
WS_NAME = "P4真机验证"
EXPECTED_MAX_ROUNDS = 3

failures: list[str] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  → {extra}" if extra else ""))
    if not ok:
        failures.append(label)


def stream(client: httpx.Client, path: str, headers: dict[str, str], json_body: Any = None):
    with client.stream("POST", path, headers=headers, json=json_body) as resp:
        events = []
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        return events


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
            print(f"清理历史工作区 {item['id']}")

    workspace = client.post(
        "/workflow/workspaces", json={"name": WS_NAME}, headers=headers
    ).json()

    try:
        run = client.post(
            "/workflow/runs",
            json={
                "workspace_id": workspace["id"],
                # 刻意给一个模糊的目标 —— 澄清才有事可做
                "raw_input": "帮我做一份调研",
            },
            headers=headers,
        ).json()
        print(f"任务 {run['id']} 已创建（目标刻意模糊：『帮我做一份调研』）\n")

        # ── 1. 逐轮澄清 ────────────────────────────────────────
        print("=== 1) 澄清轮次 ===")
        rounds_seen = 0
        for expected_round in range(1, EXPECTED_MAX_ROUNDS + 1):
            events = stream(client, f"/workflow/runs/{run['id']}/clarify/stream", headers)
            kinds = [e["type"] for e in events]
            final = final_of(events)

            if not final.get("ok"):
                check(False, f"第 {expected_round} 轮澄清生成成功", str(final.get("error")))
                break

            thinking = "".join(
                e["content"].get("text", "") for e in events if e["type"] == "thinking_delta"
            )
            check("clarify_questions" in kinds, f"第 {expected_round} 轮产出了问题事件")
            check("{" not in thinking, f"第 {expected_round} 轮推理流里没有 JSON")

            if final.get("sufficient") or not final.get("questions"):
                print(f"    模型在第 {expected_round} 轮判定「信息已足够」，结束澄清")
                check(
                    expected_round <= EXPECTED_MAX_ROUNDS,
                    "在轮次上限内结束",
                    str(expected_round),
                )
                break

            rounds_seen = expected_round
            questions = final["questions"]
            check(len(questions) <= 3, f"第 {expected_round} 轮问题数不超过 3", str(len(questions)))

            print(f"    第 {expected_round} 轮（{len(questions)} 题）：")
            answers = []
            for q in questions:
                values = {o["value"] for o in q.get("options") or []}
                # ★ 每题必须带落在选项里的推荐答案，否则前端「点一下就行」的交互就断了
                check(
                    bool(q.get("recommended")) and q["recommended"] in values,
                    f"    「{q['question'][:18]}…」带可用推荐答案",
                    str(q.get("recommended")),
                )
                recommended = q.get("recommended") or (list(values)[0] if values else "")
                print(
                    f"      · {q['question']}\n"
                    f"        选项 {[o['label'] for o in q.get('options') or []]} "
                    f"→ 采纳推荐「{recommended}」"
                )
                answers.append({"id": q["id"], "value": recommended})

            resp = client.post(
                f"/workflow/runs/{run['id']}/clarify/answer",
                json={"answers": answers},
                headers=headers,
            )
            check(resp.status_code == 200, f"第 {expected_round} 轮答案已提交", resp.text[:120])

        detail = client.get(f"/workflow/runs/{run['id']}", headers=headers).json()
        stored_rounds = detail["clarify"]["rounds"]
        check(
            len(stored_rounds) == rounds_seen,
            "澄清轮次已持久化",
            f"{len(stored_rounds)} 轮",
        )
        if stored_rounds:
            first = stored_rounds[0]
            check(bool(first.get("answered_at")), "已记录回答时间")
            check(
                all(bool(v.get("value") or v.get("text")) for v in first["answers"].values()),
                "答案内容已落库",
            )
        check(int(detail["clarify_round"]) <= EXPECTED_MAX_ROUNDS, "轮次未超上限", str(detail["clarify_round"]))

        # ── 2. 轮次上限 ────────────────────────────────────────
        print("\n=== 2) 轮次上限 ===")
        if int(detail["clarify_round"]) >= EXPECTED_MAX_ROUNDS:
            final = final_of(stream(client, f"/workflow/runs/{run['id']}/clarify/stream", headers))
            check(final.get("ok") is False, "达到上限后拒绝继续追问", str(final.get("error"))[:60])
        else:
            print(f"    仅用 {detail['clarify_round']} 轮就收敛，未触及上限（无需断言）")

        # ── 3. 澄清 → 任务书 ──────────────────────────────────
        print("\n=== 3) 生成任务书 ===")
        plan_final = final_of(stream(client, f"/workflow/runs/{run['id']}/plan/stream", headers))
        check(plan_final.get("ok") is True, "澄清之后能正常生成任务书", str(plan_final.get("error") or ""))
        plan = plan_final.get("plan") or {}
        steps = plan.get("steps") or []
        print(f"    目标：{plan.get('goal')}")
        print(f"    受众：{plan.get('audience')}")
        print(f"    约束：{plan.get('constraints')}")
        print(f"    假设：{plan.get('assumptions')}")
        print(f"    步骤：{len(steps)} 步")
        for step in steps[:6]:
            print(f"      {step['idx']}. [{step['kind']}] {step['title']}")
        check(bool(plan.get("goal")), "任务书有目标")
        check(1 <= len(steps) <= 12, "步骤数在限制内", str(len(steps)))

        # ── 4. 前端要的三段数据都在 ────────────────────────────
        print("\n=== 4) 三段对比数据 ===")
        detail = client.get(f"/workflow/runs/{run['id']}", headers=headers).json()
        check(bool(detail["raw_input"]), "① 原始输入可取", detail["raw_input"])
        check(bool(detail["clarify"]["rounds"]), "② 澄清补充可取")
        check(bool(detail["plan"]), "③ 任务书可取")

        # ── 5. 跳过澄清 ────────────────────────────────────────
        print("\n=== 5) 跳过澄清 ===")
        second = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "把这份需求整理成任务清单"},
            headers=headers,
        ).json()
        skipped = client.post(
            f"/workflow/runs/{second['id']}/clarify/skip",
            json={"reason": "需求已经很明确"},
            headers=headers,
        )
        check(skipped.status_code == 200, "跳过澄清成功")
        check(skipped.json()["run"]["clarify"]["skipped"] is True, "跳过状态已记录")
        check(
            skipped.json()["run"]["clarify"]["skip_reason"] == "需求已经很明确",
            "跳过原因已记录",
        )
        refused = final_of(
            stream(client, f"/workflow/runs/{second['id']}/clarify/stream", headers)
        )
        check(refused.get("ok") is False, "跳过后不再追问", str(refused.get("error"))[:60])

        skipped_plan = final_of(
            stream(client, f"/workflow/runs/{second['id']}/plan/stream", headers)
        )
        check(skipped_plan.get("ok") is True, "跳过后能直接生成任务书")

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

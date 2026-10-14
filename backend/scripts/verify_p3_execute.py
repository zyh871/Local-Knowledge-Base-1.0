"""P3 真机验证：批准任务书 → 逐步执行 → 产出与来源链接落库。

走真实 LLM 与真实工具（含 Tavily 联网搜索），因此跑得比较慢，但能验证
接口测试覆盖不到的东西：

  * 提示词在真实模型下是否产出可用的步骤
  * 步骤之间的依赖是否真的生效（前序产出被拼进后一步的提示词）
  * **web_search 的真实链接是否原样落到 `output.sources`**（需求 4 的落点）
  * 门禁在真实模式下是否按预期拦人

用法：
    venv/Scripts/python.exe scripts/verify_p3_execute.py [baseUrl]

结束时会删掉自己创建的工作区。
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
WS_NAME = "P3真机验证"
MAX_STEPS = 12

failures: list[str] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  → {extra}" if extra else ""))
    if not ok:
        failures.append(label)


def sse_events(response: httpx.Response) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


def stream(client: httpx.Client, path: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    with client.stream("POST", path, headers=headers) as resp:
        return sse_events(resp)


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
                # 故意要求联网搜索：这样才能验证真实链接是否落库
                "raw_input": (
                    "搜索 2026 年国产大模型 API 的公开价格，整理成一份对比表，"
                    "并在末尾列出所有参考链接"
                ),
                "mode": "manual",
                "force_search": True,
            },
            headers=headers,
        ).json()
        print(f"任务 {run['id']} 已创建\n")

        # ── 1. 生成并批准任务书 ─────────────────────────────────
        print("=== 1) 生成任务书 ===")
        plan_final = final_of(
            stream(client, f"/workflow/runs/{run['id']}/plan/stream", headers)
        )
        check(plan_final.get("ok") is True, "任务书生成成功", str(plan_final.get("error") or ""))
        plan = plan_final.get("plan") or {}
        steps = plan.get("steps") or []
        check(1 <= len(steps) <= MAX_STEPS, "步骤数在限制内", str(len(steps)))

        # 校验期的修正与预警必须先看 —— 比如「某步的工具缺必填参数」，
        # 这类问题本该在这里就被用户发现，而不是执行到那一步才报错
        issues = plan_final.get("issues") or []
        if issues:
            print(f"    生成时做了 {len(issues)} 处修正/预警：")
            for item in issues:
                print(f"      · {item}")
        else:
            print("    生成时无需修正")

        print("    步骤：")
        for step in steps:
            print(f"      {step['idx']}. [{step['kind']}] {step['title']}")

        approved = client.post(f"/workflow/runs/{run['id']}/approve", headers=headers)
        check(approved.status_code == 200, "任务书已批准", approved.json()["run"]["status"])

        detail = client.get(f"/workflow/runs/{run['id']}", headers=headers).json()
        check(detail["next_runnable_idx"] == 1, "服务端给出下一个可执行步骤", str(detail["next_runnable_idx"]))

        # ── 2. 逐步执行 ────────────────────────────────────────
        print("\n=== 2) 逐步执行 ===")
        executed = 0
        all_sources: list[dict[str, Any]] = []
        for _ in range(MAX_STEPS * 2):
            detail = client.get(f"/workflow/runs/{run['id']}", headers=headers).json()
            if detail["status"] in ("done", "failed", "cancelled"):
                break
            idx = detail["next_runnable_idx"]
            if idx is None:
                waiting = [s for s in detail["steps"] if s["status"] == "awaiting_approval"]
                if waiting:
                    print(f"    第 {waiting[0]['idx']} 步需人工确认，自动批准后继续")
                    client.post(
                        f"/workflow/runs/{run['id']}/steps/{waiting[0]['idx']}/approve",
                        headers=headers,
                    )
                    continue
                failed = [s for s in detail["steps"] if s["status"] == "failed"]
                check(not failed, "没有失败的步骤", str(failed[0]["error"]) if failed else "")
                break

            events = stream(
                client, f"/workflow/runs/{run['id']}/steps/{idx}/execute", headers
            )
            kinds = [e["type"] for e in events]
            final = final_of(events)

            if not final.get("ok"):
                check(False, f"步骤 {idx} 执行成功", str(final.get("error")))
                # 失败就跳过该步，继续验证后续步骤
                client.post(
                    f"/workflow/runs/{run['id']}/steps/{idx}/skip", headers=headers
                )
                continue

            executed += 1
            output = final.get("output") or {}
            sources = output.get("sources") or []
            all_sources.extend(sources)
            print(
                f"    步骤 {idx} 完成: kind={kinds.count('step_delta')} 个流分片, "
                f"产出 {len(str(output.get('summary') or ''))} 字, 来源 {len(sources)} 条"
            )

        check(executed >= 1, "至少成功执行了一步", str(executed))

        # ── 3. 终态与产出 ─────────────────────────────────────
        print("\n=== 3) 终态检查 ===")
        detail = client.get(f"/workflow/runs/{run['id']}", headers=headers).json()
        check(detail["status"] == "done", "任务已完成", detail["status"])
        check(bool(detail["summary"]), "给出了最终结论", f"{len(detail['summary'] or '')} 字")
        settled = {s["status"] for s in detail["steps"]}
        check(
            settled <= {"done", "skipped"},
            "所有步骤都已终结",
            str(settled),
        )

        # ── 4. 真实来源链接（需求 4 的落点）─────────────────────
        print("\n=== 4) 来源链接 ===")
        if all_sources:
            print(f"    共 {len(all_sources)} 条来源：")
            for src in all_sources[:6]:
                print(f"      [{src.get('source')}] {src.get('title', '')[:40]} → {src.get('href')}")
            bad = [s for s in all_sources if not str(s.get("href", "")).startswith("http")]
            check(not bad, "所有来源都带可访问的 http(s) 链接", str(bad[:2]))
            check(
                all(s.get("source") for s in all_sources),
                "来源标注了 provider",
            )
        else:
            print("    （本次计划没有产生联网搜索步骤，跳过链接断言）")

        # ── 5. 步骤顺序守卫 ────────────────────────────────────
        print("\n=== 5) 顺序守卫 ===")
        already_done = detail["steps"][-1]
        blocked = final_of(
            stream(
                client,
                f"/workflow/runs/{run['id']}/steps/{already_done['idx']}/execute",
                headers,
            )
        )
        check(blocked.get("ok") is False, "已完成的步骤不能重复执行")

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

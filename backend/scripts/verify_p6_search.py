"""P6 真机验证：真实 Tavily 检索 + 真实可达性探测 + 引用完整性。

需求 4 的实质是「必须返回**真实可访问**的搜索链接，而非仅生成内容」。
本脚本用一条**可直接证伪**的判据来验证：

    产出正文里出现的每一个 URL，都必须来自 sources（即 Tavily 返回过的地址）。

任何模型自编的链接都会让这条断言失败。这样比「看起来有链接」强得多。

用法：
    venv/Scripts/python.exe scripts/verify_p6_search.py [baseUrl]
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
WS_NAME = "P6真机验证"
MAX_STEPS = 12

URL_RE = re.compile(r"https?://[^\s<>\"'）)】\]，。；、]+", re.IGNORECASE)

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
        run = client.post(
            "/workflow/runs",
            json={
                "workspace_id": workspace["id"],
                "raw_input": (
                    "搜索 2026 年国产大模型 API 的公开定价，整理成对比结论，"
                    "并在每条结论后标注来源编号"
                ),
                "mode": "auto",
                "force_search": True,
            },
            headers=headers,
        ).json()

        plan_final = final_of(
            stream(client, f"/workflow/runs/{run['id']}/plan/stream", headers)
        )
        check(plan_final.get("ok") is True, "任务书生成成功", str(plan_final.get("error") or ""))
        steps = (plan_final.get("plan") or {}).get("steps") or []
        search_steps = [
            s for s in steps if (s.get("action") or {}).get("tool") == "web_search"
        ]
        check(bool(search_steps), "计划里包含联网搜索步骤", f"{len(search_steps)} 个")
        print(f"    共 {len(steps)} 步，其中搜索 {len(search_steps)} 步")

        client.post(f"/workflow/runs/{run['id']}/approve", headers=headers)

        # ── 逐步执行 ───────────────────────────────────────────
        print("\n=== 逐步执行 ===")
        for _ in range(MAX_STEPS * 2):
            state = detail(run["id"])
            if state["status"] in ("done", "failed", "cancelled"):
                break
            idx = state["next_runnable_idx"]
            if idx is None:
                waiting = [s for s in state["steps"] if s["status"] == "awaiting_approval"]
                if waiting:
                    client.post(
                        f"/workflow/runs/{run['id']}/steps/{waiting[0]['idx']}/approve",
                        headers=headers,
                    )
                    continue
                break

            events = stream(
                client, f"/workflow/runs/{run['id']}/steps/{idx}/execute", headers
            )
            result = final_of(events)
            kinds = [e["type"] for e in events]
            if not result.get("ok"):
                check(False, f"步骤 {idx} 执行成功", str(result.get("error")))
                client.post(
                    f"/workflow/runs/{run['id']}/steps/{idx}/skip", headers=headers
                )
                continue
            output = result.get("output") or {}
            extra = ""
            if output.get("sources"):
                extra = f"，来源 {len(output['sources'])} 条"
            if output.get("stripped_links"):
                extra += f"，剔除假链接 {len(output['stripped_links'])} 个"
            print(f"    步骤 {idx} 完成 ({len(kinds)} 个流事件{extra})")

        state = detail(run["id"])
        check(state["status"] in ("done", "paused"), "任务跑到终态或暂停", state["status"])

        # ── 1. 可达性标记 ─────────────────────────────────────
        print("\n=== 1) 可达性标记 ===")
        pooled: list[dict[str, Any]] = []
        for step in state["steps"]:
            for src in (step.get("output") or {}).get("sources") or []:
                if src.get("href") and src["href"] not in {s["href"] for s in pooled}:
                    pooled.append(src)

        check(bool(pooled), "落库了真实来源链接", f"{len(pooled)} 条")
        if pooled:
            judged = [s for s in pooled if s.get("reachable") is not None]
            check(bool(judged), "至少有一部分链接被判定过可达性", f"{len(judged)}/{len(pooled)}")
            print("    来源与探测结果：")
            for src in pooled[:8]:
                flag = {True: "可访问", False: "失效", None: "未校验"}[src.get("reachable")]
                print(f"      [{flag}] {src.get('title', '')[:34]} → {src['href']}")
            # 探测失败降级为 None 而不是 False —— 否则会把好链接误标成坏的
            check(
                any(s.get("reachable") is True for s in pooled) or len(judged) == 0,
                "存在被判定为可访问的链接（说明探测真的发出了请求）",
            )
            bad = [s for s in pooled if not str(s.get("href", "")).startswith("http")]
            check(not bad, "所有来源都是 http(s) 链接")

        # ── 2. 引用完整性（需求 4 的实质）─────────────────────
        print("\n=== 2) 引用完整性 ===")
        allowed = {s["href"] for s in pooled}
        violations: list[tuple[int, str]] = []
        for step in state["steps"]:
            output = step.get("output") or {}
            summary = str(output.get("summary") or "")
            for url in URL_RE.findall(summary):
                cleaned_url = url.rstrip(".,;:!?、，。；：！？")
                if cleaned_url not in allowed:
                    violations.append((step["idx"], cleaned_url))

        check(
            not violations,
            "★ 产出正文中的每个 URL 都来自真实来源集合（无自编链接）",
            str(violations[:3]),
        )

        stripped = [
            link
            for step in state["steps"]
            for link in ((step.get("output") or {}).get("stripped_links") or [])
        ]
        if stripped:
            print(f"    （执行期剔除了 {len(stripped)} 个自编链接：{stripped[:3]}）")
            check(
                all(link not in allowed for link in stripped),
                "被剔除的链接确实不在来源集合里",
            )

        # ── 3. 编号引用 ───────────────────────────────────────
        print("\n=== 3) 编号引用 ===")
        bodies = [
            str((s.get("output") or {}).get("summary") or "") for s in state["steps"]
        ]
        joined = "\n".join(bodies)
        refs = re.findall(r"\[(\d{1,2})\]", joined)
        if pooled:
            check(bool(refs), "正文使用了 [n] 编号引用", f"{len(refs)} 处")
            if refs:
                numbers = {int(n) for n in refs}
                check(
                    max(numbers) <= len(pooled),
                    "引用编号不超过来源数量",
                    f"最大 {max(numbers)} / 共 {len(pooled)} 条",
                )
        else:
            print("    （本次没有检索结果，跳过编号断言）")

        # ── 4. 搜索配额 ───────────────────────────────────────
        print("\n=== 4) 搜索配额 ===")
        caps = client.get("/workflow/capabilities", headers=headers).json()
        used = len(
            [
                s
                for s in state["steps"]
                if (s.get("action") or {}).get("tool") == "web_search"
                and s["status"] in ("done", "running", "approved")
            ]
        )
        print(f"    已用搜索 {used} 次（上限见 WORKFLOW_SEARCH_MAX_CALLS_PER_RUN）")
        check(used <= 5, "搜索次数未超过默认上限", str(used))
        check("tools" in caps, "capabilities 仍下发工具清单")

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

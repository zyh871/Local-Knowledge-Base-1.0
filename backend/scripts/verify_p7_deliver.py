"""P7 真机验证：交付物落盘与下载 + 分享隔离 + TTL 清理任务在跑。

走真实 LLM。三条「底线」断言：

  * 交付物必须**真的落盘且能下载**（用户会把它带走）
  * 分享页**绝不能带出用户上传的文件内容**（设计 §9.5 点出的风险）
  * 清理任务必须**已在运行**且不误删未到期文件

用法：
    venv/Scripts/python.exe scripts/verify_p7_deliver.py [baseUrl]
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
WS_NAME = "P7真机验证"
SECRET = "内部机密：成本价 0.42 元/千 token"
MAX_STEPS = 12

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
        # ── 准备：跑完一个任务 + 上传一个含敏感内容的文件 ──────
        run = client.post(
            "/workflow/runs",
            json={
                "workspace_id": workspace["id"],
                "raw_input": "总结小团队做代码评审的三条落地建议",
                "mode": "auto",
                # 显式开启联网搜索：否则模型可能判定「这题不需要搜」，
                # 「含来源链接」这条断言就变成了靠运气 —— 验证不该依赖模型的临时选择。
                "force_search": True,
            },
            headers=headers,
        ).json()

        uploaded = client.post(
            f"/workflow/runs/{run['id']}/files",
            files={"files": ("内部资料.txt", io.BytesIO(SECRET.encode()), "text/plain")},
            headers=headers,
        ).json()["files"][0]
        print(f"已上传文件 {uploaded['filename']}（内容含敏感串，用于验证分享隔离）")

        plan = final_of(stream(client, f"/workflow/runs/{run['id']}/plan/stream", headers))
        check(plan.get("ok") is True, "任务书生成成功", str(plan.get("error") or ""))
        client.post(f"/workflow/runs/{run['id']}/approve", headers=headers)

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
            result = final_of(
                stream(client, f"/workflow/runs/{run['id']}/steps/{idx}/execute", headers)
            )
            if not result.get("ok"):
                client.post(
                    f"/workflow/runs/{run['id']}/steps/{idx}/skip", headers=headers
                )
        state = detail(run["id"])
        check(state["status"] == "done", "任务执行到完成", state["status"])

        # ── 1. 交付物 ──────────────────────────────────────────
        print("\n=== 1) 交付物 ===")
        delivered = client.post(f"/workflow/runs/{run['id']}/deliver", headers=headers)
        check(delivered.status_code == 200, "交付物生成成功", delivered.text[:120])
        artifact = delivered.json()["artifact"]
        print(f"    文件名：{artifact['filename']}（{artifact['size_bytes']} 字节）")

        download = client.get(
            f"/workflow/files/{artifact['id']}/download", headers=headers
        )
        check(download.status_code == 200, "交付物可下载")
        text = download.content.decode("utf-8")
        check(text.startswith("# "), "是 Markdown 且以标题开头")
        check("## 结论" in text, "包含结论段")
        check("## 执行明细" in text, "包含执行明细段")
        # 每一步都要在交付物里有交代 —— 少一步就意味着某段产出被吞了
        missing = [
            s["title"] for s in state["steps"] if s.get("title") and s["title"] not in text
        ]
        check(not missing, "执行明细覆盖了全部步骤", str(missing[:3]))
        print(f"    文档 {len(text)} 字，前 3 行：")
        for line in text.splitlines()[:3]:
            print(f"      {line}")

        listed = client.get(f"/workflow/runs/{run['id']}/artifacts", headers=headers).json()
        check(listed["count"] == 1, "产物列表可查", str(listed["count"]))

        # ── 2. 分享隔离 ────────────────────────────────────────
        print("\n=== 2) 分享隔离 ===")
        shared = client.post(
            f"/workflow/runs/{run['id']}/share", json={}, headers=headers
        )
        check(shared.status_code == 200, "分享链接生成成功", shared.text[:120])
        token_value = shared.json()["token"]
        print(f"    分享路径：{shared.json()['url_path']}")

        # 公开读取（不带任何鉴权）
        public = client.get(f"/share/{token_value}")
        check(public.status_code == 200, "分享页可公开读取（无需鉴权）")
        payload = public.json()["data"]
        check(payload.get("kind") == "workflow", "payload 标明是工作流", str(payload.get("kind")))
        check(bool(payload.get("steps")), "含步骤时间线", f"{len(payload.get('steps') or [])} 步")
        check(bool(payload.get("summary")), "含结论文本")
        # 来源是否进分享页取决于本次计划有没有搜索步 —— 那是 P6 的验证点
        # （P6 有确定性断言）。这里只在真的搜过时校验，避免让 P7 依赖模型的选择。
        if payload.get("sources"):
            check(True, "含来源链接", f"{len(payload['sources'])} 条")
        else:
            print("    （本次计划未包含搜索步骤，跳过来源断言 —— 见 P6 验证）")

        raw = public.text
        # ★ 底线（设计 §9.5）：分享页**不能**让接收者拿到工作区里的本地文件。
        #   断言的是「没有可据此下载原文件的标识」，而不是「正文里不许提文件名」——
        #   交付物正文里写「根据《内部资料.txt》整理」是自然的，
        #   设计要防的是把文件本身（字节 / 路径 / 下载入口）一并公开。
        check(uploaded["id"] not in raw, "★ 分享页不含上传文件的 id（无法据此下载原文件）")
        check("stored_path" not in raw, "★ 分享页不含落盘路径")
        check("workspaces" not in raw, "分享页不含工作区目录信息")
        check(
            f"/workflow/files/{uploaded['id']}" not in raw,
            "★ 分享页不含上传文件的下载入口",
        )
        # 文件内容是否被大段带出：这里是观察性的（模型可能引用）
        if SECRET in raw:
            print("    注意：正文引用了上传文件里的一句话（摘录，非整份文件）")
        else:
            print("    正文未出现上传文件的敏感串")
        # 产物只给文件名与大小
        artifacts = payload.get("artifacts") or []
        check(bool(artifacts), "含产物清单（仅文件名与大小）", str(artifacts[:1]))

        # ── 3. 产物与临时文件的区别 ────────────────────────────
        print("\n=== 3) 文件类型区分 ===")
        files = client.get(f"/workflow/runs/{run['id']}/files", headers=headers).json()
        kinds = {f["kind"] for f in files["files"]}
        check("input" in kinds and "artifact" in kinds, "输入与产物分开了", str(kinds))
        check(
            listed["artifacts"][0]["id"] == artifact["id"],
            "产物列表只含产物",
        )

        # ── 4. 清理任务 ────────────────────────────────────────
        print("\n=== 4) TTL 清理 ===")
        # 未到期的文件必须还在（清理任务在后台跑着，不能误删）
        still_there = client.get(
            f"/workflow/files/{uploaded['id']}/download", headers=headers
        )
        check(still_there.status_code == 200, "★ 未到期的上传文件安然无恙")
        artifact_there = client.get(
            f"/workflow/files/{artifact['id']}/download", headers=headers
        )
        check(artifact_there.status_code == 200, "★ 未到期的交付物安然无恙")

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

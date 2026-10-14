"""诊断：工作流模式下「让 AI 生成代码 / 写文件」到底会发生什么。

刻意走真实链路（澄清 → 任务书 → 批准 → 执行），因为要回答的是
「用户提了这种需求，系统实际给了什么」，而不是「某个函数单看对不对」。

用法：
  venv/Scripts/python.exe scripts/diagnose_codegen.py [baseUrl] ["需求"]
"""
import json
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ASK = sys.argv[2] if len(sys.argv) > 2 else (
    "写一个 Python 脚本计算斐波那契数列前 20 项，保存为 fib.py，并给出运行说明"
)


def sse_events(lines):
    out = []
    for line in lines:
        if not line:
            continue
        if line.startswith("data:"):
            raw = line[5:].strip()
            if raw in ("", "[DONE]"):
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                out.append({"type": "raw", "content": raw})
    return out


def stream(c, method, url, **kw):
    with c.stream(method, url, **kw) as r:
        return sse_events(r.iter_lines())


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=600.0, trust_env=False) as c:
        token = c.post("/token", data={"username": "admin", "password": "admin123"}).json()[
            "access_token"
        ]
        h = {"Authorization": f"Bearer {token}"}

        caps = c.get("/workflow/capabilities", headers=h).json()
        print("=== 能力 ===")
        print("可用工具        :", caps.get("tools"))
        print("强制审批工具    :", caps.get("require_approval_tools"))

        ws = c.post(
            "/workflow/workspaces",
            json={"name": f"代码生成诊断-{int(time.time()) % 100000}"},
            headers=h,
        ).json()
        print(f"\n=== 需求 ===\n{ASK}\n")
        run = c.post(
            "/workflow/runs",
            json={"workspace_id": ws["id"], "raw_input": ASK, "mode": "auto"},
            headers=h,
        ).json()
        run_id = run["id"]

        events = stream(c, "POST", f"/workflow/runs/{run_id}/plan/stream", json={}, headers=h)
        plan, issues = None, []
        for ev in events:
            if ev.get("type") == "final":
                content = ev.get("content") or {}
                plan = content.get("plan") or content
                issues = content.get("issues") or []
        print("=== 计划校验问题 ===")
        print("\n".join(f"  ⚠️ {i}" for i in issues) or "  （无）")

        print("\n=== 任务书步骤 ===")
        code_steps = []
        for s in (plan or {}).get("steps", []):
            action = s.get("action") or {}
            args = action.get("args") if isinstance(action.get("args"), dict) else {}
            print(f"  [{s.get('idx')}] kind={s.get('kind')} tool={action.get('tool')} "
                  f"title={s.get('title')}")
            if action.get("tool") == "code_executor":
                code_steps.append(s.get("idx"))
                print(f"      args 的键 = {list(args.keys())}")
                for k, v in args.items():
                    print(f"      {k}: {len(str(v))} 字符")
                if "code" in args:
                    print(f"      ⚠️ 带 code_executor 的步骤共 {len(code_steps)} 个")
        if not code_steps:
            print("  （模型没有计划任何 code_executor 步骤）")

        c.post(f"/workflow/runs/{run_id}/approve", headers=h)

        print("\n=== 逐步执行 ===")
        detail = c.get(f"/workflow/runs/{run_id}", headers=h).json()
        for s in detail.get("steps", []):
            idx = s["idx"]
            evs = stream(
                c, "POST", f"/workflow/runs/{run_id}/steps/{idx}/execute", headers=h
            )
            types = [e.get("type") for e in evs]
            print(f"  [{idx}] kind={s.get('kind')} title={s.get('title')}")
            print(f"      事件 = {sorted(set(types))}")

            fin = [e for e in evs if e.get("type") == "final"]
            if fin and isinstance(fin[0].get("content"), dict):
                content = fin[0]["content"]
                if content.get("code"):
                    print(f"      ⛔ 未能执行：code={content.get('code')} "
                          f"error={content.get('error')}")
                    # 按提示批准后重试
                    ap = c.post(
                        f"/workflow/runs/{run_id}/steps/{idx}/approve", headers=h
                    )
                    print(f"      批准该步 -> HTTP {ap.status_code}")
                    evs = stream(
                        c, "POST", f"/workflow/runs/{run_id}/steps/{idx}/execute", headers=h
                    )
                    types = [e.get("type") for e in evs]
                    print(f"      批准后事件 = {sorted(set(types))}")

            for e in evs:
                if e.get("type") == "step_failed":
                    print(f"      ❌ 失败：{e.get('content')}")
                if e.get("type") == "step_done":
                    out = (e.get("content") or {}).get("output") or {}
                    body = out.get("summary") or ""
                    print(f"      ✅ 产出 {len(body)} 字符  truncated={out.get('truncated')}")
                    print(f"      尾部 60 字: {body[-60:]!r}")

        detail = c.get(f"/workflow/runs/{run_id}", headers=h).json()
        print("\n=== 最终状态 ===", detail.get("status"))
        for s in detail.get("steps", []):
            out = s.get("output") or {}
            print(f"  [{s['idx']}] {s['status']:18s} truncated={out.get('truncated')} "
                  f"err={s.get('error')}")

        print("\n=== 服务端能否创建文件（交付物）===")
        deliver = c.post(f"/workflow/runs/{run_id}/deliver", headers=h)
        print("  deliver ->", deliver.status_code, str(deliver.json())[:220])

        print("\n=== 产物列表 ===")
        print(" ", c.get(f"/workflow/runs/{run_id}/artifacts", headers=h).json())

        c.delete(f"/workflow/workspaces/{ws['id']}", headers=h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

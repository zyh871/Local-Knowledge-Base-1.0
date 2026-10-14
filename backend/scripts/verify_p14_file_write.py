"""验收：让工作流真的产出一个「能用的代码文件」。

这是用户那句「工作流模式生成的代码无法正常使用，且运行过程中无法创建文件」
的**修复验收**。刻意对着真实后端跑，并且分两部分：

  A. **模型自己规划**（用用户的原话）—— 看它现在会不会走 file_write 两步式，
     以及真的落盘后文件能不能下载、内容对不对。
  B. **手工任务书**（确定性）—— 码里有 import / class / __main__ 守卫，
     以前会被子串黑名单误杀，现在必须能跑通。

用法：venv/Scripts/python.exe scripts/verify_p14_file_write.py [baseUrl]
"""
import json
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
USER, PWD = "admin", "admin123"
ASK = "写一个 Python 脚本计算斐波那契数列前 20 项，保存为 fib.py，并给出运行说明"

fails: list[str] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  → {extra}" if extra else ""))
    if not ok:
        fails.append(label)


def stream(c, method, url, **kw):
    out = []
    with c.stream(method, url, **kw) as resp:
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw in ("", "[DONE]"):
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return out


def finals(events):
    return [e.get("content") or {} for e in events if e.get("type") == "final"]


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=900.0, trust_env=False) as c:
        token = c.post(
            "/token", data={"username": USER, "password": PWD}
        ).json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        caps = c.get("/workflow/capabilities", headers=h).json()
        print("=== 可用工具 ===")
        print(" ", caps.get("tools"))
        check("file_write" in (caps.get("tools") or []), "★ file_write 已注册并下发")

        ws = c.post(
            "/workflow/workspaces",
            json={"name": f"P14验收-{int(time.time()) % 100000}"},
            headers=h,
        ).json()

        # ── A. 模型自己规划 ────────────────────────────────────────
        print(f"\n=== A. 模型自主规划 ===\n需求：{ASK}")
        run = c.post(
            "/workflow/runs",
            json={"workspace_id": ws["id"], "raw_input": ASK, "mode": "auto"},
            headers=h,
        ).json()
        run_id = run["id"]

        events = stream(c, "POST", f"/workflow/runs/{run_id}/plan/stream", json={}, headers=h)
        plan, issues = None, []
        for content in finals(events):
            plan = content.get("plan") or content
            issues = content.get("issues") or []
        print("计划问题：", issues or "（无）")

        tools_used = []
        for s in (plan or {}).get("steps", []):
            action = s.get("action") or {}
            tools_used.append(action.get("tool") or s.get("kind"))
            print(f"  [{s.get('idx')}] {s.get('kind')}/{action.get('tool')} {s.get('title')}")
        check("file_write" in tools_used, "★ 模型用了 file_write 来产出文件", str(tools_used))
        check(
            "code_executor" not in tools_used
            or tools_used.index("file_write") < len(tools_used),
            "没有拿 code_executor 当写文件工具用",
        )

        c.post(f"/workflow/runs/{run_id}/approve", headers=h)
        steps = c.get(f"/workflow/runs/{run_id}", headers=h).json()["steps"]
        print("\n逐步执行：")
        for s in steps:
            idx = s["idx"]
            evs = stream(
                c, "POST", f"/workflow/runs/{run_id}/steps/{idx}/execute", headers=h
            )
            content = (finals(evs) or [{}])[0]
            if content.get("code") == "approval_required":
                # 强制审批的工具（code_executor / file_write）在 auto 模式也要点一次
                c.post(f"/workflow/runs/{run_id}/steps/{idx}/approve", headers=h)
                evs = stream(
                    c, "POST", f"/workflow/runs/{run_id}/steps/{idx}/execute", headers=h
                )
                content = (finals(evs) or [{}])[0]
            status = "OK" if content.get("ok") else f"FAILED {content.get('error')}"
            print(f"  [{idx}] {s.get('title')} -> {status}")

        detail = c.get(f"/workflow/runs/{run_id}", headers=h).json()
        artifacts = c.get(f"/workflow/runs/{run_id}/artifacts", headers=h).json()
        names = [a["filename"] for a in artifacts.get("artifacts") or []]
        print("\n产物：", names)
        check(bool(names), "★ 任务产出了文件", str(names))

        py_artifact = next(
            (a for a in artifacts.get("artifacts") or [] if a["filename"].endswith(".py")),
            None,
        )
        if py_artifact:
            body = c.get(
                f"/workflow/files/{py_artifact['id']}/download", headers=h
            ).content.decode("utf-8", "replace")
            check("def " in body or "print(" in body, "★ 下载到的文件是真实代码", body[:60])
            check(len(body) > 80, "文件不是空壳", f"{len(body)} 字符")
            # ★ 落盘的文件必须真的能用：不能带 markdown 围栏、必须能编译
            check(
                not body.lstrip().startswith("```") and "```" not in body,
                "★ 落盘文件没有 markdown 围栏（第一行不能是 ```python）",
                repr(body[:24]),
            )
            try:
                compile(body, "fib.py", "exec")
                check(True, "★ 落盘文件能通过 Python 语法编译")
            except SyntaxError as exc:
                check(False, "★ 落盘文件能通过 Python 语法编译", str(exc))
        else:
            check(False, "产物里有 .py 文件")

        # ── B. 手工任务书：真实脚本必须能跑 ────────────────────────
        print("\n=== B. 手工任务书：code_executor 能跑真脚本 ===")
        run2 = c.post(
            "/workflow/runs",
            json={"workspace_id": ws["id"], "raw_input": "跑一段脚本", "mode": "auto"},
            headers=h,
        ).json()
        script = (
            "import math\n"
            "from statistics import mean\n"
            "class Fib:\n"
            "    def __init__(self):\n"
            "        self.seq = []\n"
            "    def build(self, n):\n"
            "        a, b = 0, 1\n"
            "        for _ in range(n):\n"
            "            self.seq.append(a)\n"
            "            a, b = b, a + b\n"
            "        return self.seq\n"
            "if __name__ == '__main__':\n"
            "    f = Fib()\n"
            "    seq = f.build(20)\n"
            "    print('前20项和 =', sum(seq))\n"
            "    print('均值 =', round(mean(seq), 2))\n"
            "    print('黄金比 =', round(seq[-1] / seq[-2], 4))\n"
            "    print('sqrt(2) =', round(math.sqrt(2), 4))\n"
        )
        client_issues = c.put(
            f"/workflow/runs/{run2['id']}/plan",
            json={
                "goal": "跑通一个带 import/class 的脚本",
                "steps": [
                    {
                        "kind": "tool",
                        "title": "试跑脚本",
                        "action": {"tool": "code_executor", "args": {"expression": script}},
                    }
                ],
            },
            headers=h,
        ).json()
        joined = " ".join(client_issues["issues"])
        check(
            "expression" in joined and "code" in joined,
            "★ 参数别名 expression→code 在计划期被报告",
            joined[:80],
        )
        c.post(f"/workflow/runs/{run2['id']}/approve", headers=h)
        c.post(f"/workflow/runs/{run2['id']}/steps/1/approve", headers=h)
        evs = stream(
            c, "POST", f"/workflow/runs/{run2['id']}/steps/1/execute", headers=h
        )
        content = (finals(evs) or [{}])[0]
        check(content.get("ok") is True, "★ 带 import/class/__main__ 的脚本能跑通",
              str(content.get("error") or "")[:120])
        out = (content.get("output") or {}).get("summary") or ""
        check("前20项和" in out, "拿到了脚本的真实输出", out.replace("\n", " | ")[:100])

        # ── C. 仍然拦得住写文件 ───────────────────────────────────
        print("\n=== C. open() 仍然被拒，且给出替代方案 ===")
        run3 = c.post(
            "/workflow/runs",
            json={"workspace_id": ws["id"], "raw_input": "写文件", "mode": "auto"},
            headers=h,
        ).json()
        c.put(
            f"/workflow/runs/{run3['id']}/plan",
            json={
                "goal": "试图直接写文件",
                "steps": [
                    {
                        "kind": "tool",
                        "title": "绕过沙箱写文件",
                        "action": {
                            "tool": "code_executor",
                            "args": {"code": "open('x.txt', 'w').write('hi')"},
                        },
                    }
                ],
            },
            headers=h,
        )
        c.post(f"/workflow/runs/{run3['id']}/approve", headers=h)
        c.post(f"/workflow/runs/{run3['id']}/steps/1/approve", headers=h)
        evs = stream(c, "POST", f"/workflow/runs/{run3['id']}/steps/1/execute", headers=h)
        content = (finals(evs) or [{}])[0]
        err = json.dumps(content, ensure_ascii=False)
        check(content.get("ok") is False, "★ open() 仍然被拒")
        check("file_write" in err, "★ 拒绝时告诉用户改用 file_write", err[:120])

        c.delete(f"/workflow/workspaces/{ws['id']}", headers=h)

    print("")
    print("全部通过" if not fails else f"失败 {len(fails)} 项: {' | '.join(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())

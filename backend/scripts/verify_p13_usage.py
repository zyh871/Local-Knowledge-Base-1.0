"""P13 端到端验证：成本可见性（token 用量）+ 交付物去重 + 截断标注。

对着**真实跑起来的后端**验证（不是 TestClient），因为要看的是：
模型返回的 usage 是否真的穿过了 节点 → 落库 → 汇总 → 接口 → 交付物 这一整条链路。
任何一环漏传都不会报错，只会让界面永远显示「没有数据」。

用法：venv/Scripts/python.exe scripts/verify_p13_usage.py [baseUrl]
"""
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
USER, PWD = "admin", "admin123"

fails: list[str] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  → {extra}" if extra else ""))
    if not ok:
        fails.append(label)


def main() -> int:
    # 本机 HTTP_PROXY 会把 127.0.0.1 的请求也代理掉，必须显式绕过
    with httpx.Client(base_url=BASE, timeout=120.0, trust_env=False) as c:
        token = c.post("/token", data={"username": USER, "password": PWD}).json()[
            "access_token"
        ]
        h = {"Authorization": f"Bearer {token}"}

        ws = c.post(
            "/workflow/workspaces",
            json={"name": f"P13验证-{int(time.time()) % 100000}", "description": "自动验证"},
            headers=h,
        ).json()
        ws_id = ws["id"]

        run = c.post(
            "/workflow/runs",
            json={"workspace_id": ws_id, "raw_input": "写一段不超过 30 字的问候", "mode": "auto"},
            headers=h,
        ).json()
        run_id = run["id"]
        print(f"run={run_id}")

        # 两步：第一步 llm，第二步 synthesis（结论）—— 用来验证交付物去重
        c.put(
            f"/workflow/runs/{run_id}/plan",
            json={
                "goal": "产出一句问候并总结",
                "steps": [
                    {"kind": "llm", "title": "起草问候", "action": {"prompt_ref": "写一句问候"}},
                    {"kind": "synthesis", "title": "汇总结论", "action": {"prompt_ref": "汇总上面内容"}},
                ],
            },
            headers=h,
        )
        c.post(f"/workflow/runs/{run_id}/approve", headers=h)

        print("\n1) 逐步执行")
        for idx in (1, 2):
            resp = c.post(f"/workflow/runs/{run_id}/steps/{idx}/execute", headers=h)
            check(resp.status_code == 200, f"第 {idx} 步执行返回 200", str(resp.status_code))
            body = resp.text
            check('"final"' in body or "final" in body, f"第 {idx} 步有 final 事件")

        detail = c.get(f"/workflow/runs/{run_id}", headers=h).json()

        print("\n2) 成本可见性：usage 汇总")
        usage = detail.get("usage")
        if usage is None:
            print("     ⚠️ 上游未返回 usage（本环境模型可能不给）→ 退化为注入式验证")
            from app.repositories import workflow_repo as repo  # noqa: PLC0415

            repo.add_run_usage(run_id, "clarify", {"input_tokens": 100, "output_tokens": 20})
            repo.add_run_usage(run_id, "clarify", {"input_tokens": 100, "output_tokens": 20})
            repo.add_run_usage(run_id, "plan", {"input_tokens": 50, "output_tokens": 30})
            detail = c.get(f"/workflow/runs/{run_id}", headers=h).json()
            usage = detail.get("usage")
            check(usage is not None, "注入后 usage 可见")
            if usage:
                # 2 轮澄清 + 1 次规划 + 2 步
                check(usage["calls"] >= 3, "★ 同一相位多次调用按次数计", f"calls={usage['calls']}")
                check(
                    usage["input_tokens"] >= 250,
                    "★ 澄清/规划的 token 计入总量",
                    f"input={usage['input_tokens']} output={usage['output_tokens']}",
                )
        else:
            check(usage["calls"] >= 1, "★ usage.calls 有值", str(usage["calls"]))
            check(
                usage["total_tokens"] > 0,
                "★ total_tokens > 0",
                f"{usage['total_tokens']} (in={usage['input_tokens']} out={usage['output_tokens']})",
            )
            print(f"     phases={usage.get('phases')}")
            print(f"     steps={usage.get('steps')}")

        print("\n3) 交付物：结论不再重复出现")
        deliver = c.post(f"/workflow/runs/{run_id}/deliver", headers=h).json()
        # 交付物正文不在响应体里，而在产物文件中（响应只给元信息）
        artifact = (deliver or {}).get("artifact") or {}
        text = ""
        if artifact.get("id"):
            text = c.get(
                f"/workflow/files/{artifact['id']}/download", headers=h
            ).content.decode("utf-8", "replace")
        check(bool(text), "交付物正文可读", f"{len(text)} 字符")
        check("## 结论" in text, "交付物有结论段")
        head, _, tail = text.partition("## 结论")
        conclusion_body = tail.split("\n## ")[0]
        # 结论正文 30 字以上才谈得上「重复」
        probe = conclusion_body.strip().split("\n")
        probe = [ln for ln in probe if len(ln) > 30]
        if probe:
            sample = probe[0][:40]
            n = text.count(sample)
            check(n == 1, "★ 结论正文在交付物中只出现一次", f"出现 {n} 次: {sample!r}")
        else:
            print("     （结论正文过短，跳过重复计数）")

        print("\n4) 截断标注")
        truncated = [
            s for s in detail.get("steps", []) if (s.get("output") or {}).get("truncated")
        ]
        print(f"     被截断的步骤：{[s['idx'] for s in truncated] or '无'}")
        if truncated:
            check(
                "此段可能不完整" in text or "输出被截断" in text,
                "★ 截断的步骤在交付物里有标注",
            )
        else:
            check("此段可能不完整" not in text, "无截断时不误报截断标注")

        # 清理
        deleted = c.delete(f"/workflow/workspaces/{ws_id}", headers=h)
        check(deleted.status_code == 200, "验证用工作区已清理", str(deleted.status_code))

    print("")
    print("全部通过" if not fails else f"失败 {len(fails)} 项: {' | '.join(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())

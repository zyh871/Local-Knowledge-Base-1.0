"""P11 真机验证：工作区文件夹清单进入提示词（改动 3）。

**必须真调一次模型**才看得出来：改动 3 的效果就体现在「模型问的问题变了」——
从「你说的『这个文件夹』是指哪个文件夹？」变成能说出具体文件名的追问。

用**用户实际的那个目录**（`专高6\\AI教程`，6 个 md）验证，全程只读：
`summarize_dir` 只做一次 scandir，拿到的是名称与大小，不读内容、不改文件。

用法：
    venv/Scripts/python.exe scripts/verify_p11_folder_context.py [baseUrl]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

# 本脚本落在 scripts/ 下，Python 只把脚本所在目录加进 sys.path，
# 因此要显式把后端根目录加进来才能 import app.*（用于确定性地构建提示词）
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
USER_FOLDER = r"C:\Users\a1731\Desktop\专高6\AI教程"
WS_NAME = "P11验证-文件夹清单"

failures: list[str] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  → {extra}" if extra else ""))
    if not ok:
        failures.append(label)


def stream_events(client: httpx.Client, path: str, headers: dict[str, str]):
    with client.stream("POST", path, headers=headers) as resp:
        return [
            json.loads(line[6:])
            for line in resp.iter_lines()
            if line.startswith("data: ")
        ]


def final_of(events: list[dict[str, Any]]) -> dict[str, Any]:
    finals = [e for e in events if e["type"] == "final"]
    return finals[-1]["content"] if finals else {}


#: 澄清/任务书节点在带上文件夹清单时打的标记
_LISTING_MARKER = "已带上工作区文件夹清单"


def _count_marker(log_path: str) -> int:
    """数日志里出现了几次「带上清单」。

    用「调用前后计数是否增加」来判断链路通没通 —— 比按时间戳匹配稳。
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            return fh.read().count(_LISTING_MARKER)
    except OSError:
        return 0


def main() -> int:
    if not os.path.isdir(USER_FOLDER):
        print(f"跳过：{USER_FOLDER} 不存在")
        return 0

    client = httpx.Client(base_url=BASE, timeout=300.0, trust_env=False)
    token = client.post(
        "/token",
        data={"username": "admin", "password": "admin123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print(f"已登录（{BASE}）\n")

    workspace_id = ""
    try:
        for item in client.get("/workflow/workspaces", headers=headers).json()["workspaces"]:
            if item["name"] == WS_NAME:
                client.delete(f"/workflow/workspaces/{item['id']}", headers=headers)
        workspace = client.post(
            "/workflow/workspaces",
            json={"name": WS_NAME, "local_dir": USER_FOLDER},
            headers=headers,
        ).json()
        workspace_id = workspace["id"]

        # ── 1. 任务详情下发目录清单 ─────────────────────────────
        print("=== 1) 任务详情里的目录清单 ===")
        run = client.post(
            "/workflow/runs",
            json={
                "workspace_id": workspace_id,
                "raw_input": "整理这个文件夹里的文件",
                "mode": "manual",
            },
            headers=headers,
        ).json()
        detail = client.get(f"/workflow/runs/{run['id']}", headers=headers).json()
        summary = detail.get("folder")
        check(summary is not None, "详情带出目录清单")
        if summary:
            names = [f["name"] for f in summary["files"]]
            print(f"    {summary['total_files']} 个文件，合计 {summary['total_bytes']} 字节")
            for n in names[:6]:
                print(f"      - {n}")
            check(summary["total_files"] >= 1, "文件数正确", str(summary["total_files"]))
            # ★ 只有名称/大小/路径，绝不能有内容
            check(
                all(set(f.keys()) == {"name", "size", "path"} for f in summary["files"]),
                "★ 清单只有名称/大小/路径（无内容字段）",
            )
            # 提示词渲染里不能出现磁盘路径
            from app.workflow.file_context import render_folder_listing as _render

            check(
                USER_FOLDER not in _render(summary),
                "★ 提示词里不含磁盘路径",
            )
        check(detail.get("file_context") is None, "0 个引用文件时不显示注入量预览")

        # ── 2. 提示词里确实带上了清单 ───────────────────────────
        print("\n=== 2) 清单是否真的进了提示词 ===")
        # 2a) 确定性检查：用**真实的目录**按服务端同一套函数生成提示词
        from app.workflow.clarifier import build_clarify_prompt
        from app.workflow.file_context import render_folder_listing
        from app.workflow.local_fs import summarize_dir

        block = render_folder_listing(summarize_dir(USER_FOLDER))
        prompt = build_clarify_prompt(
            raw_input="整理这个文件夹里的文件", folder_listing=block
        )
        missing = [
            f["name"] for f in (summary or {}).get("files", []) if f["name"] not in prompt
        ]
        check(not missing, "★ 提示词里含全部真实文件名", f"{len(prompt)} 字符")
        check("内容尚未加入" in prompt, "★ 带免责声明（防止模型编造内容）")
        check(
            "完全相同的目录在无绑定时不进提示词"
            and "工作区文件夹" not in build_clarify_prompt(raw_input="整理这个文件夹里的文件"),
            "无绑定时不出现该块",
        )

        # 2b) 端到端检查：节点是否真把这行日志打出来了（说明 state → 提示词是通的）
        log_path = str(_BACKEND_ROOT / "logs" / "app.log")
        before = _count_marker(log_path)
        run2 = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace_id, "raw_input": "整理这个文件夹里的文件"},
            headers=headers,
        ).json()
        events = stream_events(
            client, f"/workflow/runs/{run2['id']}/clarify/stream", headers
        )
        content = final_of(events)
        questions = content.get("questions") or []
        check(bool(questions), "生成了澄清问题", f"{len(questions)} 个")
        check(
            _count_marker(log_path) > before,
            "★ 运行中的澄清节点确认带上了清单（state → 提示词链路通）",
        )

        blob = json.dumps(questions, ensure_ascii=False)
        for q in questions:
            print(f"    Q: {str(q.get('question', ''))[:90]}")
            for opt in (q.get("options") or [])[:3]:
                print(f"       · {opt.get('label', '')[:60]}")
        # 用户可见的改善：不再反问「你说的那个文件夹是哪个」
        vague = "哪个文件夹" in blob or "是指哪个文件夹" in blob
        check(not vague, "★ 不再反问「你说的『这个文件夹』是指哪个文件夹？」")
        named = any(f["name"] in blob for f in (summary or {}).get("files", []))
        print(f"    （模型主动点出文件名：{'是' if named else '否 —— 属模型选择，不作断言'}）")

        run = run2

        # ── 3. 批量引用与注入量预览 ─────────────────────────────
        print("\n=== 3) 批量引用 → 注入量预览 ===")
        paths = [os.path.join(USER_FOLDER, f["name"]) for f in (summary or {}).get("files", [])][:3]
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import-batch",
            json={"paths": paths},
            headers=headers,
        )
        check(resp.status_code == 200, f"一次引用 {len(paths)} 个文件", resp.text[:100])
        if resp.status_code == 200:
            body = resp.json()
            check(len(body["files"]) == len(paths), "全部引用成功", f"{len(body['files'])} 个")
            check(body.get("failed") == [], "没有失败项")

        detail = client.get(f"/workflow/runs/{run['id']}", headers=headers).json()
        ctx = detail.get("file_context")
        check(ctx is not None, "详情带出注入量预览")
        if ctx:
            print(f"    预算 {ctx['budget_chars']} 字 / 解析出 {ctx['total_chars']} 字"
                  f" → 实际注入 {ctx['injected_chars']} 字")
            for note in ctx["notes"]:
                pct = round(note["injected_chars"] / note["chars"] * 100) if note["chars"] else 0
                print(f"      - {note['filename']}：{note['chars']} → {note['injected_chars']} 字"
                      f"（{pct}%，{note['mode']}）")
            check(
                ctx["total_chars"] > ctx["budget_chars"],
                "确实超出预算（正是要如实告知的场景）",
            )
            check(
                0 < ctx["injected_chars"] < ctx["total_chars"],
                "★ 注入量小于总字数（说明「全部加入 ≠ 全部进上下文」可见）",
            )

    finally:
        if workspace_id:
            client.delete(f"/workflow/workspaces/{workspace_id}", headers=headers)
        print("\n已清理验证用工作区（未触碰用户文件）")

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{' | '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

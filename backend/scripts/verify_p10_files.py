"""P10 真机验证：类型白名单下发 + `.html` 可被引用 + 超时配置生效。

用**用户实际的那份文件**验证（`C:\\Users\\a1731\\Desktop\\BWL\\mome.html`）——
只读地导入一份副本到临时任务里，验证完即删除，不触碰用户数据。

对应诊断结论：「选择器里看得见 .html、一点就被拒」是用户怀疑
「程序访问不了我的本地文件」的真正来源。

用法：
    venv/Scripts/python.exe scripts/verify_p10_files.py [baseUrl]
"""

from __future__ import annotations

import os
import sys
from typing import Any

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
USER_FOLDER = r"C:\Users\a1731\Desktop\BWL"
WS_NAME = "P10验证-类型白名单"

failures: list[str] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  → {extra}" if extra else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=120.0, trust_env=False)
    token = client.post(
        "/token",
        data={"username": "admin", "password": "admin123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print(f"已登录（{BASE}）\n")

    workspace_id = ""
    try:
        # ── 1. capabilities 下发允许类型 ───────────────────────
        print("=== 1) 允许类型下发给前端 ===")
        caps = client.get("/workflow/capabilities", headers=headers).json()
        exts = caps.get("allowed_extensions") or []
        check(bool(exts), "capabilities 含 allowed_extensions", f"{len(exts)} 种")
        check(".html" in exts, "★ .html 在白名单里")
        check(".js" in exts and ".css" in exts, "前端三件套齐全", 
              str([e for e in exts if e in (".js", ".css", ".json")]))
        check(all(e.startswith(".") for e in exts), "格式统一（以 . 开头）")

        # ── 2. 绑定用户的真实文件夹 ────────────────────────────
        print("\n=== 2) 绑定用户的实际文件夹 ===")
        if not os.path.isdir(USER_FOLDER):
            print(f"    跳过：{USER_FOLDER} 不存在（用户可能已删除）")
            return 0
        for item in client.get("/workflow/workspaces", headers=headers).json()["workspaces"]:
            if item["name"] == WS_NAME:
                client.delete(f"/workflow/workspaces/{item['id']}", headers=headers)
        workspace = client.post(
            "/workflow/workspaces",
            json={"name": WS_NAME, "local_dir": USER_FOLDER},
            headers=headers,
        ).json()
        workspace_id = workspace["id"]
        check(workspace["local_dir"] == USER_FOLDER, "绑定成功", workspace["local_dir"])

        # ── 3. 目录浏览能看到、且能判断可否引用 ────────────────
        print("\n=== 3) 目录浏览 ===")
        listing = client.get(
            "/workflow/fs/list", params={"path": USER_FOLDER}, headers=headers
        )
        check(listing.status_code == 200, "目录可访问", str(listing.status_code))
        entries = listing.json()["entries"]
        names = [e["name"] for e in entries if not e["is_dir"]]
        print(f"    文件：{names}")
        check(
            not [n for n in names if n.endswith(".html") and ".html" not in exts],
            "★ 目录里的 .html 现在属于「可引用」范围",
            names[0] if names else "(空)",
        )

        # ── 4. 引用那个 .html ──────────────────────────────────
        print("\n=== 4) 引用用户的 .html ===")
        html_files = [e for e in entries if not e["is_dir"] and e["name"].lower().endswith(".html")]
        if not html_files:
            print("    跳过：目录里没有 .html")
            return 0
        target = html_files[0]
        run = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace_id, "raw_input": "改一下这个页面"},
            headers=headers,
        ).json()
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import",
            json={"path": target["path"]},
            headers=headers,
        )
        check(resp.status_code == 200, f"★ {target['name']} 引用成功（此前会被拒）", resp.text[:120])
        if resp.status_code == 200:
            record = resp.json()["files"][0]
            check(record["filename"] == target["name"], "文件名正确", record["filename"])
            check(
                bool(record.get("parsed_chars")),
                "已被解析为文本（能进模型上下文）",
                f"{record.get('parsed_chars')} 字符",
            )
            download = client.get(
                f"/workflow/files/{record['id']}/download", headers=headers
            )
            raw = download.content
            body = raw.decode("utf-8", errors="replace")
            # 内容确实是 HTML 原文（不剥标签）
            check("<" in body and ">" in body, "内容是原文（保留标签）", f"{len(raw)} 字节")
            # ⚠️ 必须用**字节**比字节：用 len(str) 会比出「少了 800」，
            # 那是中文按 UTF-8 占 3 字节造成的，不是截断
            check(
                len(raw) == record["size_bytes"],
                "复制完整，未被截断",
                f"{len(raw)} / {record['size_bytes']} 字节",
            )

        # ── 5. 仍然拒绝不该放行的类型 ──────────────────────────
        print("\n=== 5) 边界仍然收得住 ===")
        bad = os.path.join(USER_FOLDER, "bad.exe")
        resp = client.post(
            f"/workflow/runs/{run['id']}/files/import",
            json={"path": bad},
            headers=headers,
        )
        check(resp.status_code == 400, "不存在的文件仍被拒", str(resp.status_code))

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

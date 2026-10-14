"""P9 真机验证：接入第三方 OpenAI 兼容端点，并在问答链路上真正用起来。

用**真实的**智谱 OpenAI 兼容端点做验证 —— 不需要额外的第三方密钥，
却完整走通了「自定义接口地址 + 自定义密钥 + 自定义模型名」这条路径。

三条可直接证伪的断言：

  1. 配好的自定义模型能被 `/ask` 用起来
  2. **模型名真的发到了上游** —— 故意配一个不存在的模型名，请求必须失败；
     只看「请求成功」是证明不了的（可能悄悄回落到了默认模型）
  3. 删除后立刻失效，且 `/ask` 仍是 400 + `answer` 字段（硬约束 2）

用法：
    venv/Scripts/python.exe scripts/verify_p9_models.py [baseUrl]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
NAME_OK = "P9验证-可用模型"
NAME_BAD = "P9验证-错误模型名"

failures: list[str] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  → {extra}" if extra else ""))
    if not ok:
        failures.append(label)


def read_env_key() -> tuple[str, str]:
    """从 backend/.env 读智谱密钥与 Base URL（仅用于本次验证）。"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    values: dict[str, str] = {}
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    key = values.get("ZHIPU_API_KEY", "")
    base = values.get("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    return key, base


def stream_events(client: httpx.Client, path: str, headers: dict[str, str], body: Any):
    with client.stream("POST", path, headers=headers, json=body) as resp:
        return [
            json.loads(line[6:])
            for line in resp.iter_lines()
            if line.startswith("data: ")
        ]


def final_of(events: list[dict[str, Any]]) -> dict[str, Any]:
    finals = [e for e in events if e["type"] == "final"]
    return finals[-1]["content"] if finals else {}


def main() -> int:
    api_key, base_url = read_env_key()
    if not api_key:
        print("跳过：backend/.env 里没有 ZHIPU_API_KEY，无法做真机验证")
        return 0

    client = httpx.Client(base_url=BASE, timeout=300.0, trust_env=False)
    token = client.post(
        "/token",
        data={"username": "admin", "password": "admin123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print(f"已登录（验证目标 {BASE}）\n")

    created: list[str] = []
    try:
        # ── 清理历史残留 ──────────────────────────────────────
        for item in client.get("/model-endpoints", headers=headers).json()["endpoints"]:
            if item["name"].startswith("P9验证"):
                client.delete(f"/model-endpoints/{item['id']}", headers=headers)

        # ── 0. 基线 ────────────────────────────────────────────
        print("=== 0) 基线 ===")
        before = client.get("/models", headers=headers).json()["models"]
        check(
            not [m for m in before if m["custom"]],
            "初始没有自定义模型",
            f"共 {len(before)} 个模型",
        )
        default_model = client.get("/models", headers=headers).json()["default_model"]
        print(f"    默认模型：{default_model}")

        # ── 1. 添加一个可用的自定义端点 ────────────────────────
        print("\n=== 1) 添加自定义端点 ===")
        resp = client.post(
            "/model-endpoints",
            json={
                "name": NAME_OK,
                # 用智谱的 OpenAI 兼容端点作为「第三方地址」，走的是完全相同的代码路径
                "base_url": base_url,
                "api_key": api_key,
                "model": "glm-4-plus",
            },
            headers=headers,
        )
        check(resp.status_code == 200, "创建成功", resp.text[:150])
        endpoint = resp.json()
        created.append(endpoint["id"])
        check(endpoint["model_id"] == f"custom:{endpoint['id']}", "分配了模型 id", endpoint["model_id"])

        # ★ 密钥只进不出
        check(api_key not in resp.text, "★ 响应里没有完整密钥")
        check(endpoint["has_api_key"] is True, "标记了「已配置密钥」")
        check(
            endpoint["api_key_masked"].endswith(api_key[-4:]) and len(endpoint["api_key_masked"]) <= 6,
            "只回掩码（末 4 位）",
            endpoint["api_key_masked"],
        )
        listed = client.get("/model-endpoints", headers=headers)
        check(api_key not in listed.text, "★ 列表接口里也没有完整密钥")

        # ── 2. 连通性测试 ──────────────────────────────────────
        print("\n=== 2) 连通性测试 ===")
        tested = client.post(f"/model-endpoints/{endpoint['id']}/test", headers=headers).json()
        check(tested["last_test_ok"] is True, "测试通过", str(tested.get("last_test_error") or ""))

        # ── 3. 出现在模型清单里 ────────────────────────────────
        print("\n=== 3) 模型清单 ===")
        models = client.get("/models", headers=headers).json()["models"]
        custom = [m for m in models if m["custom"]]
        check(len(custom) == 1, "清单里出现自定义模型", f"{len(custom)} 个")
        if custom:
            check(custom[0]["id"] == endpoint["model_id"], "id 一致")
            check("glm-4-plus" in custom[0]["label"], "标签里带上上游模型名", custom[0]["label"])
            check(custom[0]["provider"] == NAME_OK, "带出端点显示名", custom[0]["provider"])
        check(len(models) > len(before), "内置模型没有被挤掉", f"{len(before)} → {len(models)}")

        # ── 4. 在问答链路上真正用起来 ──────────────────────────
        print("\n=== 4) /ask 使用自定义模型 ===")
        ask = client.post(
            "/ask",
            json={"question": "用一句话说明什么是代码评审", "model": endpoint["model_id"]},
            headers=headers,
        )
        check(ask.status_code == 200, "请求成功", str(ask.status_code))
        payload = ask.json()
        check(payload.get("model") == endpoint["model_id"], "响应回显了自定义模型", str(payload.get("model")))
        answer = (payload.get("answer") or "").strip()
        check(len(answer) > 10, "拿到了真实回答", answer[:60].replace("\n", " "))

        # ── 5. ★ 证明模型名真的发到了上游 ──────────────────────
        print("\n=== 5) 证明模型名真的发给了上游 ===")
        bad = client.post(
            "/model-endpoints",
            json={
                "name": NAME_BAD,
                "base_url": base_url,
                "api_key": api_key,
                "model": "glm-4-does-not-exist-xyz",
            },
            headers=headers,
        ).json()
        created.append(bad["id"])
        bad_test = client.post(f"/model-endpoints/{bad['id']}/test", headers=headers).json()
        # 只看「成功的请求」说明不了任何事 —— 必须让一个错误模型名暴露出上游确实收到了它
        check(bad_test["last_test_ok"] is False, "★ 错误模型名被上游拒绝（说明模型名确实发出去了）",
              str(bad_test.get("last_test_error"))[:90])

        # ── 6. 停用即失效 ──────────────────────────────────────
        print("\n=== 6) 停用 / 删除后立刻失效 ===")
        client.patch(
            f"/model-endpoints/{endpoint['id']}", json={"enabled": False}, headers=headers
        )
        after_disable = client.get("/models", headers=headers).json()["models"]
        check(
            not [m for m in after_disable if m["id"] == endpoint["model_id"]],
            "停用后从模型清单消失",
        )
        rejected = client.post(
            "/ask",
            json={"question": "你好", "model": endpoint["model_id"]},
            headers=headers,
        )
        check(rejected.status_code == 400, "停用后用它被拒", str(rejected.status_code))
        check("answer" in rejected.json(), "★ 仍是 400 + answer 字段（契约不变）")

        client.patch(
            f"/model-endpoints/{endpoint['id']}", json={"enabled": True}, headers=headers
        )
        back = client.get("/models", headers=headers).json()["models"]
        check(
            bool([m for m in back if m["id"] == endpoint["model_id"]]),
            "重新启用后恢复可用",
        )

        # ── 7. 编辑 ────────────────────────────────────────────
        print("\n=== 7) 编辑 ===")
        renamed = client.patch(
            f"/model-endpoints/{endpoint['id']}",
            json={"name": NAME_OK + "（改名）"},
            headers=headers,
        ).json()
        check(renamed["name"].endswith("（改名）"), "改名成功")
        check(renamed["has_api_key"] is True, "★ 未重填密钥时原密钥仍在")
        # 改名**不影响连通性**，所以不该清掉上一次的测试结论 ——
        # 清了反而会让用户以为「改了名字就要重测」
        check(
            renamed["last_test_ok"] is True,
            "改名保留上一次的测试结论",
            f"last_test_ok={renamed['last_test_ok']!r}",
        )

        # 但改「接口地址」就必须清 —— 旧的「测试通过」属于旧地址，会误导用户
        client.patch(
            f"/model-endpoints/{endpoint['id']}",
            json={"base_url": base_url.rstrip("/") + "/"},
            headers=headers,
        )
        reverted = client.patch(
            f"/model-endpoints/{endpoint['id']}",
            json={"base_url": base_url},
            headers=headers,
        ).json()
        check(
            reverted["last_test_ok"] is False
            and "重新测试" in (reverted["last_test_error"] or ""),
            "★ 改动接口地址后清掉旧的测试结论",
            str(reverted.get("last_test_error"))[:40],
        )

        # 重新测一次应恢复可用
        retested = client.post(
            f"/model-endpoints/{endpoint['id']}/test", headers=headers
        ).json()
        check(retested["last_test_ok"] is True, "重测后恢复通过")

        # ── 8. 删除后回落 ──────────────────────────────────────
        print("\n=== 8) 删除 ===")
        for eid in list(created):
            assert client.delete(f"/model-endpoints/{eid}", headers=headers).status_code == 200
        created.clear()
        final = client.get("/models", headers=headers).json()["models"]
        check(not [m for m in final if m["custom"]], "删除后自定义模型清空")
        gone = client.post(
            "/ask", json={"question": "你好", "model": endpoint["model_id"]}, headers=headers
        )
        check(gone.status_code == 400, "删除后引用它被拒", str(gone.status_code))
        check("answer" in gone.json(), "仍是 400 + answer 字段")

        # 内置模型照常可用
        fallback = client.post(
            "/ask", json={"question": "用一句话说明什么是代码评审"}, headers=headers
        )
        check(fallback.status_code == 200, "不指定模型时走默认模型，仍然正常")

        # ── 9. 嵌入模型未受影响 ────────────────────────────────
        print("\n=== 9) 嵌入模型未受影响 ===")
        health = client.get("/health").json()
        check(health.get("chroma", {}).get("ok") is True, "向量库仍健康",
              str(health.get("chroma")))

    finally:
        for eid in created:
            try:
                client.delete(f"/model-endpoints/{eid}", headers=headers)
            except Exception:  # noqa: BLE001
                pass
        print("\n已清理验证用的模型端点")

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{' | '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

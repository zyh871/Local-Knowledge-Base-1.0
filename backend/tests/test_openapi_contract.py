"""
接口契约回归测试。

这是重构期间最重要的自动化护栏：把 v3.0.0 的 OpenAPI 路径集合固化为基线，
任何**路径或方法的增删**都会立刻失败。

为什么需要它：本次重构把 31 个端点从单个 main.py 拆到 9 个模块，
人工核对极易漏项；而"前端零改动"是硬约束，接口集合必须逐条一致。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

BASELINE = Path(__file__).resolve().parent / "data" / "openapi_v3_baseline.json"


def _collect(app) -> dict[str, list[str]]:
    spec = app.openapi()
    return {
        path: sorted(ops.keys())
        for path, ops in spec.get("paths", {}).items()
    }


@pytest.fixture(scope="module")
def baseline() -> dict[str, list[str]]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def current() -> dict[str, list[str]]:
    from app.main import app

    return _collect(app)


class TestOpenAPIContract:
    def test_no_missing_paths(self, baseline, current):
        """基线里的每个路径都必须仍然存在（防止拆分时漏端点）。"""
        missing = sorted(set(baseline) - set(current))
        assert not missing, f"重构导致端点丢失: {missing}"

    def test_no_added_paths(self, baseline, current):
        """不得凭空新增路径（新增需同步更新基线并评估前端影响）。"""
        added = sorted(set(current) - set(baseline))
        assert not added, f"出现未登记的新端点: {added}"

    def test_http_methods_unchanged(self, baseline, current):
        """同一路径上的 HTTP 方法集合不得变化。"""
        changed = {
            path: {"baseline": baseline[path], "current": current[path]}
            for path in baseline
            if path in current and baseline[path] != current[path]
        }
        assert not changed, f"端点方法发生变化: {changed}"

    def test_operation_count(self, baseline, current):
        assert sum(len(v) for v in current.values()) == sum(
            len(v) for v in baseline.values()
        )

    def test_openapi_schema_generates(self):
        """openapi() 本身能生成（模型定义无歧义、无重复 operationId）。"""
        from app.main import app

        spec = app.openapi()
        assert spec["info"]["title"]
        assert "paths" in spec


class TestErrorEnvelope:
    """统一错误响应需同时保留旧 `detail` 与新 `error` 结构。"""

    def test_401_has_both_shapes(self, client):
        response = client.get(
            "/users/me/", headers={"Authorization": "Bearer not-a-token"}
        )
        assert response.status_code == 401
        body = response.json()
        assert "detail" in body, "顶层 detail 缺失会破坏前端旧读取方式"
        # invalid_token 是 unauthorized 的细分 code（更精确），两者都表示 401
        assert body["error"]["code"] in ("unauthorized", "invalid_token")
        assert body["error"]["trace_id"]
        assert response.headers.get("WWW-Authenticate") == "Bearer"

    def test_404_has_both_shapes(self, client):
        response = client.get("/definitely-not-a-route")
        assert response.status_code == 404
        body = response.json()
        assert "detail" in body and body["error"]["code"] == "not_found"

    def test_trace_id_header_present(self, client):
        response = client.get("/health")
        assert response.headers.get("X-Trace-ID")

    def test_validation_error_envelope(self, client, auth_headers):
        response = client.post("/ask", json={}, headers=auth_headers)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

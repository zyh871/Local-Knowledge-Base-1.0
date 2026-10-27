"""模型目录（治理视图）接口测试。

重点：
  * 新端点可用且结构完整
  * 既有 `/models` 的响应**不被污染**（新增字段一律只进新端点）
"""

from __future__ import annotations


def test_catalog_returns_governance_fields(client, auth_headers):
    response = client.get("/models/catalog", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["backend"] == "mock"
    assert body["offline_capable"] is True
    assert body["default_model"]
    assert isinstance(body["note"], str) and body["note"]
    assert body["models"], "至少应返回一个内置模型"

    kinds = {item["kind"] for item in body["models"]}
    assert "llm" in kinds
    for item in body["models"]:
        assert item["cost_tier"] in ("low", "medium", "high")
        assert isinstance(item["offline_capable"], bool)


def test_catalog_requires_auth(client):
    assert client.get("/models/catalog").status_code == 401


def test_existing_models_contract_untouched(client, auth_headers):
    """/models 的结构必须保持原样，catalog 字段不得混入。"""
    response = client.get("/models", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "models" in body and "default_model" in body
    assert "backend" in body
    assert "catalog" not in body
    assert "note" not in body

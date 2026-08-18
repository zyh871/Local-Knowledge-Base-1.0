"""
集成测试 — 验证 FastAPI 端点的请求/响应契约和状态码。
使用 TestClient (无需启动真实服务器)。
运行: python -m pytest tests/test_api.py -v
"""

import os
import sys
import json
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

os.environ.setdefault("MODEL_BACKEND", "mock")

from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def auth_token(client):
    resp = client.post(
        "/token",
        data={"username": os.getenv("ADMIN_USERNAME", "admin"),
              "password": os.getenv("ADMIN_PASSWORD", "admin123")},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


@pytest.fixture
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "degraded")
        assert "service" in data
        assert "version" in data

    def test_health_includes_chroma(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "chroma" in data
        assert isinstance(data["chroma"], dict)

    def test_health_includes_sqlite(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "sqlite_conversations" in data


# ---------------------------------------------------------------------------
# /agent/intents
# ---------------------------------------------------------------------------

class TestAgentIntents:
    def test_list_intents(self, client, auth_headers):
        resp = client.get("/agent/intents", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "intents" in data
        assert isinstance(data["intents"], list)
        assert len(data["intents"]) >= 7
        assert "data_sources" in data

    def test_intents_require_auth(self, client):
        resp = client.get("/agent/intents")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /agent/classify
# ---------------------------------------------------------------------------

class TestAgentClassify:
    def test_classify_question(self, client, auth_headers):
        resp = client.post(
            "/agent/classify",
            json={"question": "今天北京天气怎么样"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "intent" in data
        assert "confidence" in data
        assert isinstance(data["confidence"], (int, float))

    def test_classify_empty_question(self, client, auth_headers):
        resp = client.post(
            "/agent/classify",
            json={"question": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_classify_require_auth(self, client):
        resp = client.post("/agent/classify", json={"question": "test"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /agent/capabilities
# ---------------------------------------------------------------------------

class TestAgentCapabilities:
    def test_get_capabilities(self, client, auth_headers):
        resp = client.get("/agent/capabilities", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "capabilities" in data
        assert isinstance(data["capabilities"], list)
        assert len(data["capabilities"]) >= 5
        assert "model_backend" in data
        assert "llm_model" in data


# ---------------------------------------------------------------------------
# /conversations (pagination)
# ---------------------------------------------------------------------------

class TestConversationPagination:
    def test_list_conversations_pagination(self, client, auth_headers):
        resp = client.get(
            "/conversations?skip=0&limit=5",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "conversations" in data
        assert "total" in data
        assert "skip" in data
        assert "limit" in data
        assert data["skip"] == 0
        assert data["limit"] == 5

    def test_list_conversations_with_search(self, client, auth_headers):
        resp = client.get(
            "/conversations?search=test&skip=0&limit=10",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "conversations" in data


# ---------------------------------------------------------------------------
# /documents (pagination)
# ---------------------------------------------------------------------------

class TestDocumentsPagination:
    def test_list_documents_pagination(self, client, auth_headers):
        resp = client.get(
            "/documents?skip=0&limit=10",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "documents" in data
        assert "total" in data
        assert "skip" in data
        assert "limit" in data

    def test_list_documents_require_auth(self, client):
        resp = client.get("/documents")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /feedback/stats
# ---------------------------------------------------------------------------

class TestFeedbackStats:
    def test_get_feedback_stats(self, client, auth_headers):
        resp = client.get("/feedback/stats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "ratings" in data
        assert "badcase_count" in data
        assert "recent" in data

    def test_feedback_stats_require_auth(self, client):
        resp = client.get("/feedback/stats")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /feedback/list
# ---------------------------------------------------------------------------

class TestFeedbackList:
    def test_list_feedback(self, client, auth_headers):
        resp = client.get(
            "/feedback/list?skip=0&limit=10",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "skip" in data
        assert "limit" in data

    def test_list_feedback_with_rating_filter(self, client, auth_headers):
        resp = client.get(
            "/feedback/list?rating_filter=5&skip=0&limit=10",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item.get("rating") == 5


# ---------------------------------------------------------------------------
# /profile
# ---------------------------------------------------------------------------

class TestProfile:
    def test_get_profile(self, client, auth_headers):
        resp = client.get("/profile", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "username" in data or "facts" in data

    def test_update_profile(self, client, auth_headers):
        resp = client.put(
            "/profile",
            json={"display_name": "Test User", "language": "zh"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "profile" in data


# ---------------------------------------------------------------------------
# /share/{token} (rate limited, no auth)
# ---------------------------------------------------------------------------

class TestShareEndpoint:
    def test_invalid_token_returns_404(self, client):
        resp = client.get("/share/nonexistent_token_12345678")
        assert resp.status_code == 404

    def test_short_token_returns_400(self, client):
        resp = client.get("/share/short")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /token (auth)
# ---------------------------------------------------------------------------

class TestAuth:
    def test_login_success(self, client):
        resp = client.post(
            "/token",
            data={"username": os.getenv("ADMIN_USERNAME", "admin"),
                  "password": os.getenv("ADMIN_PASSWORD", "admin123")},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["token_type"] == "bearer"
        assert len(data["access_token"]) > 10

    def test_login_wrong_password(self, client):
        resp = client.post(
            "/token",
            data={"username": "admin", "password": "wrong_password"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 401


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

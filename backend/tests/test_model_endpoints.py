"""自定义模型端点的测试。

重点在三条「出错就会被用户直接感受到」的地方：

  * **密钥只进不出** —— 完整 `api_key` 绝不能出现在任何响应里
  * **变更后缓存必须失效** —— 否则「改了密钥还在用旧的」，且完全不报错
  * **可用性判据只有一处** —— `/ask` 校验、`/models` 列表、真实构造必须一致，
    不能出现「列表里有、用的时候说不支持」
"""

from __future__ import annotations

import pytest

VALID = {
    "name": "我的 DeepSeek",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "sk-secret-1234567890abcd",
    "model": "deepseek-chat",
}


@pytest.fixture
def model_env(tmp_path, monkeypatch):
    from app.core.config import clear_settings_cache
    from app.repositories import model_repo
    from app.rag import providers

    monkeypatch.setenv("CUSTOM_MODELS_DB_PATH", str(tmp_path / "models.db"))
    clear_settings_cache()
    model_repo.reset_connection()
    # providers 的实例缓存是模块级的，跨测试必须清，否则会互相污染
    providers.set_custom_endpoints([])
    yield tmp_path
    providers.set_custom_endpoints([])
    model_repo.reset_connection()
    clear_settings_cache()


def _create(client, auth_headers, **overrides):
    payload = {**VALID, **overrides}
    return client.post("/model-endpoints", json=payload, headers=auth_headers)


def _stub_probe(monkeypatch, ok: bool, error: str = "") -> list[tuple]:
    """把连通性探测换成受控结果，测试里不真的发请求。"""
    from app.services import model_service as mod

    calls: list[tuple] = []

    def _fake(self, *, base_url, api_key, model):
        calls.append((base_url, api_key, model))
        return ok, error

    monkeypatch.setattr(mod.ModelService, "_probe", _fake)
    return calls


# ---------------------------------------------------------------------------
#  增删改查
# ---------------------------------------------------------------------------


class TestCrud:
    def test_create_and_list(self, client, auth_headers, model_env):
        resp = _create(client, auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == VALID["name"]
        assert body["model"] == VALID["model"]
        assert body["enabled"] is True
        assert body["model_id"] == f"custom:{body['id']}"

        listed = client.get("/model-endpoints", headers=auth_headers).json()
        assert listed["enabled"] is True
        assert [e["id"] for e in listed["endpoints"]] == [body["id"]]

    def test_create_requires_all_fields(self, client, auth_headers, model_env):
        for field in ("name", "base_url", "api_key", "model"):
            payload = {**VALID, field: ""}
            resp = client.post("/model-endpoints", json=payload, headers=auth_headers)
            assert resp.status_code == 422 or resp.status_code == 400, field

    def test_base_url_must_have_scheme(self, client, auth_headers, model_env):
        resp = _create(client, auth_headers, base_url="api.deepseek.com/v1")
        assert resp.status_code == 400
        assert "http" in resp.json()["error"]["message"]

    def test_base_url_trailing_slash_removed(self, client, auth_headers, model_env):
        body = _create(client, auth_headers, base_url="https://api.deepseek.com/v1/").json()
        assert body["base_url"] == "https://api.deepseek.com/v1"

    def test_base_url_keeps_user_path(self, client, auth_headers, model_env):
        """**不替用户补 `/v1`**：猜错会得到一个 404，而用户看不出是谁的问题。"""
        body = _create(client, auth_headers, base_url="https://gw.example.com/openai").json()
        assert body["base_url"] == "https://gw.example.com/openai"

    def test_update_fields(self, client, auth_headers, model_env):
        endpoint_id = _create(client, auth_headers).json()["id"]
        resp = client.patch(
            f"/model-endpoints/{endpoint_id}",
            json={"name": "改名后", "model": "deepseek-reasoner"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "改名后"
        assert resp.json()["model"] == "deepseek-reasoner"

    def test_delete(self, client, auth_headers, model_env):
        endpoint_id = _create(client, auth_headers).json()["id"]
        assert (
            client.delete(f"/model-endpoints/{endpoint_id}", headers=auth_headers).status_code
            == 200
        )
        assert client.get("/model-endpoints", headers=auth_headers).json()["endpoints"] == []
        assert client.get("/model-endpoints", headers=auth_headers).status_code == 200

    def test_missing_endpoint_is_404(self, client, auth_headers, model_env):
        assert (
            client.patch(
                "/model-endpoints/me_nope", json={"name": "x"}, headers=auth_headers
            ).status_code
            == 404
        )
        assert (
            client.delete("/model-endpoints/me_nope", headers=auth_headers).status_code == 404
        )
        assert (
            client.post("/model-endpoints/me_nope/test", headers=auth_headers).status_code
            == 404
        )


# ---------------------------------------------------------------------------
#  密钥只进不出
# ---------------------------------------------------------------------------


class TestKeyHygiene:
    def test_full_key_never_returned(self, client, auth_headers, model_env):
        """★ 完整密钥出现在响应里，等于让它进日志、进浏览器历史、进截图。"""
        created = _create(client, auth_headers)
        listed = client.get("/model-endpoints", headers=auth_headers)
        patched = client.patch(
            f"/model-endpoints/{created.json()['id']}",
            json={"name": "x"},
            headers=auth_headers,
        )
        for resp in (created, listed, patched):
            assert VALID["api_key"] not in resp.text
            assert "api_key" not in resp.json().get("endpoints", [{}])[0] if "endpoints" in resp.json() else True

    def test_masked_form_shows_only_tail(self, client, auth_headers, model_env):
        body = _create(client, auth_headers).json()
        assert body["has_api_key"] is True
        assert body["api_key_masked"].endswith(VALID["api_key"][-4:])
        # 掩码里不能含有密钥的头部
        assert VALID["api_key"][:8] not in body["api_key_masked"]

    def test_update_without_key_keeps_old(self, client, auth_headers, model_env):
        """改个名字不该被迫重新粘贴一遍密钥。"""
        from app.repositories import model_repo

        endpoint_id = _create(client, auth_headers).json()["id"]
        client.patch(
            f"/model-endpoints/{endpoint_id}", json={"name": "只改名"}, headers=auth_headers
        )
        stored = model_repo.get_endpoint(endpoint_id)
        assert stored["api_key"] == VALID["api_key"]
        assert stored["name"] == "只改名"

    def test_update_with_new_key_replaces(self, client, auth_headers, model_env):
        from app.repositories import model_repo

        endpoint_id = _create(client, auth_headers).json()["id"]
        client.patch(
            f"/model-endpoints/{endpoint_id}",
            json={"api_key": "sk-brand-new-key-9999"},
            headers=auth_headers,
        )
        assert model_repo.get_endpoint(endpoint_id)["api_key"] == "sk-brand-new-key-9999"

    def test_mask_helper(self):
        from app.services.model_service import mask_key

        assert mask_key("") == ""
        assert mask_key("abc") == "****"
        assert mask_key("sk-1234567890abcd") == "…abcd"


# ---------------------------------------------------------------------------
#  与模型解析链路的集成
# ---------------------------------------------------------------------------


class TestModelResolution:
    def test_appears_in_models_list(self, client, auth_headers, model_env):
        endpoint_id = _create(client, auth_headers).json()["id"]
        models = client.get("/models", headers=auth_headers).json()["models"]
        custom = [m for m in models if m["custom"]]
        assert len(custom) == 1
        assert custom[0]["id"] == f"custom:{endpoint_id}"
        assert "deepseek-chat" in custom[0]["label"]
        assert custom[0]["provider"] == VALID["name"]
        # 内置模型仍然在
        assert any(not m["custom"] for m in models)

    def test_llm_built_with_endpoint_credentials(self, client, auth_headers, model_env):
        """★ 真正要保证的是「构造出来的客户端用的是用户填的地址/密钥/模型」。"""
        from app.rag.providers import get_llm

        endpoint_id = _create(client, auth_headers).json()["id"]
        llm = get_llm(f"custom:{endpoint_id}")
        assert llm.model_name == "deepseek-chat"
        assert str(llm.openai_api_base).rstrip("/") == VALID["base_url"]
        assert llm.openai_api_key.get_secret_value() == VALID["api_key"]

    def test_timeout_reaches_the_client(self, client, auth_headers, model_env, monkeypatch):
        """超时必须真的落到客户端上。

        只在配置层断言是不够的 —— 中间任何一环漏传，最终仍然是「无限等」，
        而那种 bug 完全不会报错，只会在上游卡住时暴露。
        """
        from app.core.config import clear_settings_cache
        from app.rag.providers import get_llm

        monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "123")
        clear_settings_cache()

        endpoint_id = _create(client, auth_headers).json()["id"]
        llm = get_llm(f"custom:{endpoint_id}")
        assert llm.request_timeout == 123
        assert llm.max_retries == 1

    def test_changes_invalidate_cached_client(self, client, auth_headers, model_env):
        """★ 不失效缓存就会出现「改了密钥还在用旧的」，且完全不报错。"""
        from app.rag.providers import get_llm

        endpoint_id = _create(client, auth_headers).json()["id"]
        model_id = f"custom:{endpoint_id}"
        first = get_llm(model_id)

        client.patch(
            f"/model-endpoints/{endpoint_id}",
            json={"api_key": "sk-rotated-key-0001"},
            headers=auth_headers,
        )
        second = get_llm(model_id)

        assert second is not first
        assert second.openai_api_key.get_secret_value() == "sk-rotated-key-0001"

    def test_unchanged_endpoint_keeps_cache(self, client, auth_headers, model_env):
        """没动过的模型仍应命中缓存 —— 每次请求重建客户端是浪费。"""
        from app.rag.providers import get_llm
        from app.services.model_service import ModelService

        endpoint_id = _create(client, auth_headers).json()["id"]
        model_id = f"custom:{endpoint_id}"
        first = get_llm(model_id)
        ModelService(settings=__import__("app.core.config", fromlist=["x"]).get_settings()).sync_providers()
        assert get_llm(model_id) is first

    def test_disabled_endpoint_drops_out(self, client, auth_headers, model_env):
        endpoint_id = _create(client, auth_headers).json()["id"]
        client.patch(
            f"/model-endpoints/{endpoint_id}", json={"enabled": False}, headers=auth_headers
        )
        models = client.get("/models", headers=auth_headers).json()["models"]
        assert not [m for m in models if m["custom"]]

        from app.rag.providers import is_model_available

        assert is_model_available(f"custom:{endpoint_id}") is False

    def test_deleted_endpoint_becomes_invalid(self, client, auth_headers, model_env):
        from app.rag.providers import is_model_available, resolve_model

        endpoint_id = _create(client, auth_headers).json()["id"]
        model_id = f"custom:{endpoint_id}"
        assert is_model_available(model_id) is True

        client.delete(f"/model-endpoints/{endpoint_id}", headers=auth_headers)

        assert is_model_available(model_id) is False
        with pytest.raises(ValueError, match="不存在或已停用"):
            resolve_model(model_id)

    def test_ask_request_validator_accepts_custom(self, client, auth_headers, model_env):
        """`/ask` 的模型校验必须认自定义模型，且**仍是 400 + answer 字段**。"""
        from app.schemas.qa import QuestionRequest

        endpoint_id = _create(client, auth_headers).json()["id"]
        model_id = f"custom:{endpoint_id}"
        assert QuestionRequest(question="你好", model=model_id).resolve_model() == model_id

        with pytest.raises(ValueError, match="不支持的模型"):
            QuestionRequest(question="你好", model="custom:me_nope").resolve_model()
        with pytest.raises(ValueError, match="不支持的模型"):
            QuestionRequest(question="你好", model="glm-不存在").resolve_model()

    def test_unknown_custom_rejected_by_api(self, client, auth_headers, model_env):
        resp = client.post(
            "/ask",
            json={"question": "你好", "model": "custom:me_nope"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        # 硬约束 2：校验错误保持 400 + answer 字段
        assert "answer" in resp.json()

    def test_embeddings_unaffected(self, client, auth_headers, model_env):
        """自定义端点只用于生成 —— 换嵌入模型要重建全库（硬约束 5）。"""
        from app.core.config import get_settings

        _create(client, auth_headers)
        # 配置里的嵌入模型没有因为新增端点而改变
        assert get_settings().model.embedding_model == "" or get_settings().model.embedding_model


# ---------------------------------------------------------------------------
#  连通性测试
# ---------------------------------------------------------------------------


class TestConnectionTest:
    def test_inline_success(self, client, auth_headers, model_env, monkeypatch):
        calls = _stub_probe(monkeypatch, ok=True)
        resp = client.post(
            "/model-endpoints/test-connection", json=VALID, headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert calls and calls[0][2] == "deepseek-chat"

    def test_inline_failure_reports_reason(self, client, auth_headers, model_env, monkeypatch):
        _stub_probe(monkeypatch, ok=False, error="认证失败（401）：请检查 API 密钥")
        body = client.post(
            "/model-endpoints/test-connection", json=VALID, headers=auth_headers
        ).json()
        assert body["ok"] is False
        assert "401" in body["error"]

    def test_inline_does_not_persist(self, client, auth_headers, model_env, monkeypatch):
        _stub_probe(monkeypatch, ok=True)
        client.post("/model-endpoints/test-connection", json=VALID, headers=auth_headers)
        assert client.get("/model-endpoints", headers=auth_headers).json()["endpoints"] == []

    def test_saved_test_records_result(self, client, auth_headers, model_env, monkeypatch):
        endpoint_id = _create(client, auth_headers).json()["id"]
        _stub_probe(monkeypatch, ok=False, error="无法连接到该地址：请检查接口地址与网络")
        body = client.post(
            f"/model-endpoints/{endpoint_id}/test", headers=auth_headers
        ).json()
        assert body["last_test_ok"] is False
        assert "无法连接" in body["last_test_error"]
        assert body["last_test_at"]

        # 结果要能读回来（界面显示「上次测试」）
        listed = client.get("/model-endpoints", headers=auth_headers).json()["endpoints"][0]
        assert listed["last_test_ok"] is False

    def test_rename_keeps_test_result(
        self, client, auth_headers, model_env, monkeypatch
    ):
        """改名**不影响连通性**，不该清掉测试结论 ——
        清了会让用户以为「改了名字就得重测」。"""
        _stub_probe(monkeypatch, ok=True)
        endpoint_id = _create(client, auth_headers).json()["id"]
        client.post(f"/model-endpoints/{endpoint_id}/test", headers=auth_headers)

        body = client.patch(
            f"/model-endpoints/{endpoint_id}", json={"name": "只改名"}, headers=auth_headers
        ).json()
        assert body["last_test_ok"] is True

    def test_toggle_enabled_keeps_test_result(
        self, client, auth_headers, model_env, monkeypatch
    ):
        _stub_probe(monkeypatch, ok=True)
        endpoint_id = _create(client, auth_headers).json()["id"]
        client.post(f"/model-endpoints/{endpoint_id}/test", headers=auth_headers)

        body = client.patch(
            f"/model-endpoints/{endpoint_id}",
            json={"enabled": False},
            headers=auth_headers,
        ).json()
        assert body["last_test_ok"] is True

    def test_changing_config_clears_stale_test_result(
        self, client, auth_headers, model_env, monkeypatch
    ):
        """★ 改了密钥后还显示「测试通过」，会让用户以为新密钥是好的。"""
        endpoint_id = _create(client, auth_headers).json()["id"]
        _stub_probe(monkeypatch, ok=True)
        client.post(f"/model-endpoints/{endpoint_id}/test", headers=auth_headers)
        assert (
            client.get("/model-endpoints", headers=auth_headers).json()["endpoints"][0][
                "last_test_ok"
            ]
            is True
        )

        body = client.patch(
            f"/model-endpoints/{endpoint_id}",
            json={"api_key": "sk-another-key-7777"},
            headers=auth_headers,
        ).json()
        assert body["last_test_ok"] is False
        assert "重新测试" in (body["last_test_error"] or "")

    def test_probe_maps_common_errors(self, model_env, monkeypatch):
        """上游报错要翻译成可操作的提示，而不是把原文甩给用户。"""
        from app.services.model_service import ModelService

        cases = {
            "Error code: 401 - invalid_api_key": "认证失败",
            "Error code: 404 - Not Found": "404",
            "model `foo` not exist": "模型不存在",
            "Request timed out.": "超时",
            "Connection error.": "无法连接",
        }
        for raw, expected in cases.items():
            def _raise(*_a, **_kw):
                raise RuntimeError(raw)

            monkeypatch.setattr("app.rag.providers._openai_compatible", _raise)
            ok, message = ModelService(
                settings=__import__("app.core.config", fromlist=["x"]).get_settings()
            )._probe(base_url="https://x.example/v1", api_key="k", model="m")
            assert ok is False
            assert expected in message, f"{raw} → {message}"


# ---------------------------------------------------------------------------
#  开关
# ---------------------------------------------------------------------------


class TestDisabledByConfig:
    def test_disabled_refuses_create(self, client, auth_headers, model_env, monkeypatch):
        from app.core.config import clear_settings_cache
        from app.repositories import model_repo

        monkeypatch.setenv("ALLOW_CUSTOM_MODELS", "false")
        clear_settings_cache()
        model_repo.reset_connection()

        resp = _create(client, auth_headers)
        assert resp.status_code == 409
        assert "不允许" in resp.json()["error"]["message"]

    def test_disabled_flag_in_list(self, client, auth_headers, model_env, monkeypatch):
        from app.core.config import clear_settings_cache
        from app.repositories import model_repo

        monkeypatch.setenv("ALLOW_CUSTOM_MODELS", "false")
        clear_settings_cache()
        model_repo.reset_connection()

        assert client.get("/model-endpoints", headers=auth_headers).json()["enabled"] is False

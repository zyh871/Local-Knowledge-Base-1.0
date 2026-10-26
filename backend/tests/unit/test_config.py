"""
配置层回归测试。

**这个文件的存在本身就是一次事故的产物**：
分组 Settings 若缺少 `env_prefix`，pydantic-settings 不会报错，
而是静默使用默认值 —— `RedisSettings.url` 会去读 `URL` 而非 `REDIS_URL`。
由于默认值恰好与 .env 相同，问题在手工验证时完全看不出来。

因此这里对**每个分组**都用"显式设置环境变量 → 断言读到该值"的方式做绑定校验。
新增配置分组时请同步在这里加一条。
"""

from __future__ import annotations

import pytest

from app.core.config import clear_settings_cache, get_settings


@pytest.fixture
def fresh_settings():
    """每个用例前后都清空配置缓存，避免 lru_cache 串味。"""
    clear_settings_cache()
    yield
    clear_settings_cache()


class TestGroupEnvBinding:
    """每个分组的字段必须真正绑定到 .env 里写的环境变量名。"""

    def test_redis_group_uses_redis_prefix(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("REDIS_URL", "redis://example-host:6380/3")
        monkeypatch.setenv("REDIS_KEY_PREFIX", "myprefix")
        monkeypatch.setenv("REDIS_TTL_SESSION", "1234")
        monkeypatch.setenv("REDIS_FALLBACK_TO_MEMORY", "false")
        settings = get_settings()
        assert settings.redis.url == "redis://example-host:6380/3"
        assert settings.redis.key_prefix == "myprefix"
        assert settings.redis.ttl_session == 1234
        assert settings.redis.fallback_to_memory is False

    def test_bailian_group_uses_bailian_prefix(self, monkeypatch, fresh_settings):
        """百炼（DashScope）密钥与模型必须绑定到 BAILIAN_ 前缀。

        它是独立供应商，不能借用 ZHIPU_API_KEY：一旦借用，
        「换聊天模型」会顺带换掉出图账号，而报错信息还指向无关的地方。
        """
        monkeypatch.setenv("BAILIAN_API_KEY", "sk-test")
        monkeypatch.setenv("BAILIAN_IMAGE_MODEL", "wanx-test-image")
        monkeypatch.setenv("BAILIAN_VIDEO_MODEL", "wanx-test-video")
        monkeypatch.setenv("BAILIAN_VIDEO_TIMEOUT_SECONDS", "600")
        settings = get_settings()
        assert settings.bailian.api_key == "sk-test"
        assert settings.bailian.image_model == "wanx-test-image"
        assert settings.bailian.video_model == "wanx-test-video"
        assert settings.bailian.video_timeout_seconds == 600
        assert settings.bailian.enabled is True

    def test_thinking_settings_bind(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("THINKING_MODEL", "glm-test")
        monkeypatch.setenv("THINKING_MODELS", "glm-test,other")
        monkeypatch.setenv("ZHIPU_API_KEY", "k")
        monkeypatch.setenv("MODEL_BACKEND", "zhipu")
        settings = get_settings()
        assert settings.model.thinking_model == "glm-test"
        assert settings.model.is_thinking_capable("other") is True
        # 思考模型必须自动出现在可选清单里，否则请求无法切过去
        assert "glm-test" in settings.model.available_llm_models

    def test_mock_backend_does_not_advertise_thinking_models(self, fresh_settings):
        """mock / local 没有推理通道，不该往模型清单里塞一个永远没有思考过程的模型。"""
        from app.core.config import ModelSettings

        settings = ModelSettings(_env_file=None, backend="mock", llm_choices="mock")
        assert settings.available_llm_models == ["mock"]

    def test_creation_provider_switch(self, monkeypatch, fresh_settings):
        """创作供应商是可切换的开关，不是写死的常量。

        切到 ModelScope 后百炼的模型不应再出现；视频能力则由
        MODELSCOPE_VIDEO_ENABLED 单独控制（平台未开放时保持关闭）。
        """
        monkeypatch.setenv("CREATION_PROVIDERS", "modelscope,bailian")
        monkeypatch.setenv("MODELSCOPE_VIDEO_ENABLED", "true")
        model = get_settings().model
        assert model.creation_providers == "modelscope,bailian"
        assert model.modelscope_video_enabled is True

    def test_generation_timeout_and_retries(self, monkeypatch, fresh_settings):
        """生成请求的超时与重试必须可配置。

        超时的价值不只是「防死等」：**它让「慢」和「死」变得可区分** ——
        没有超时时，卡住的上游与正常但缓慢的生成长得一模一样。
        """
        monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "42.5")
        monkeypatch.setenv("LLM_MAX_RETRIES", "3")
        settings = get_settings()
        assert settings.model.llm_timeout_seconds == 42.5
        assert settings.model.llm_max_retries == 3

    def test_generation_timeout_defaults_are_generous(self, fresh_settings):
        """默认值必须足够宽松：观测到的单步最长约 2 分钟，
        默认 600s 不会误杀正常的长生成。"""
        settings = get_settings()
        assert settings.model.llm_timeout_seconds >= 300
        # 重试次数刻意低于 langchain 的默认 2 —— 最坏耗时 ≈ 超时 × (重试+1)
        assert settings.model.llm_max_retries <= 1

    def test_timeout_zero_means_unlimited(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0")
        settings = get_settings()
        from app.rag.providers import _openai_options

        options = _openai_options()
        assert settings.model.llm_timeout_seconds == 0
        assert "timeout" not in options  # 0 = 交给 provider 默认（不限）

    def test_openai_options_carry_timeout(self, fresh_settings):
        from app.rag.providers import _openai_options

        options = _openai_options()
        assert options["timeout"] == get_settings().model.llm_timeout_seconds
        assert options["max_retries"] == get_settings().model.llm_max_retries

    def test_graph_group_uses_graph_prefix(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("GRAPH_CHECKPOINTER", "redis")
        monkeypatch.setenv("GRAPH_RECURSION_LIMIT", "42")
        monkeypatch.setenv("GRAPH_MAX_AGENT_ITERATIONS", "7")
        settings = get_settings()
        assert settings.graph.checkpointer == "redis"
        assert settings.graph.recursion_limit == 42
        assert settings.graph.max_agent_iterations == 7

    def test_ocr_group_uses_ocr_prefix(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("OCR_ENABLED", "false")
        monkeypatch.setenv("OCR_TESSERACT_CMD", r"C:\custom\tesseract.exe")
        monkeypatch.setenv("OCR_TESSDATA_PREFIX", r"C:\custom\tessdata")
        monkeypatch.setenv("OCR_LANGUAGES", "chi_sim+eng+jpn")
        monkeypatch.setenv("OCR_TIMEOUT_SECONDS", "90")
        settings = get_settings()
        assert settings.ocr.enabled is False
        assert settings.ocr.tesseract_cmd == r"C:\custom\tesseract.exe"
        assert settings.ocr.tessdata_prefix == r"C:\custom\tessdata"
        assert settings.ocr.languages == "chi_sim+eng+jpn"
        assert settings.ocr.timeout_seconds == 90
        # languages 是 "+" 连接的，需能被拆成列表
        assert settings.ocr.language_list == ["chi_sim", "eng", "jpn"]

    def test_workflow_group_uses_workflow_prefix(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("WORKFLOW_ENABLED", "false")
        monkeypatch.setenv("WORKFLOW_DB_PATH", r"D:\tmp\wf.db")
        monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", r"D:\tmp\ws")
        monkeypatch.setenv("WORKFLOW_DEFAULT_MODE", "auto")
        monkeypatch.setenv("WORKFLOW_MAX_STEPS", "7")
        monkeypatch.setenv("WORKFLOW_MAX_CLARIFY_ROUNDS", "2")
        monkeypatch.setenv("WORKFLOW_FILE_TTL_DAYS", "3")
        monkeypatch.setenv("WORKFLOW_REQUIRE_APPROVAL_TOOLS", "code_executor,file_write")
        settings = get_settings()
        assert settings.workflow.enabled is False
        assert settings.workflow.db_path == r"D:\tmp\wf.db"
        assert settings.workflow.workspace_root == r"D:\tmp\ws"
        assert settings.workflow.default_mode == "auto"
        assert settings.workflow.max_steps == 7
        assert settings.workflow.max_clarify_rounds == 2
        assert settings.workflow.file_ttl_days == 3
        assert settings.workflow.approval_tool_list == ["code_executor", "file_write"]

    def test_workflow_paths_fall_back_to_data_dir(self, monkeypatch, fresh_settings):
        """留空时应推导到 data/ 下，而不是变成相对路径或空串。"""
        monkeypatch.setenv("WORKFLOW_DB_PATH", "")
        monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", "")
        settings = get_settings()
        assert settings.workflow.resolved_db_path.endswith("workflow.db")
        assert settings.workflow.resolved_workspace_root.endswith("workspaces")

    def test_workflow_write_group_uses_workflow_prefix(self, monkeypatch, fresh_settings):
        """★ 新增配置字段必须真的绑到 `WORKFLOW_` 前缀上。

        pydantic-settings 不按类名推导前缀；漏了 env_prefix 时
        环境变量会被静默忽略 —— 表现为"我明明配了却不生效"。
        """
        monkeypatch.setenv("WORKFLOW_WRITE_MAX_KB", "64")
        monkeypatch.setenv("WORKFLOW_WRITE_TO_BOUND_FOLDER", "false")
        monkeypatch.setenv(
            "WORKFLOW_WRITE_ALLOWED_EXTENSIONS", "py, .md , .json"
        )
        settings = get_settings()
        assert settings.workflow.write_max_kb == 64
        assert settings.workflow.write_to_bound_folder is False
        # 前缀点号可有可无，且要排序去重 —— 否则白名单比对永远对不上
        assert settings.workflow.write_extension_list == [".json", ".md", ".py"]

    def test_workflow_write_extensions_default_is_code_friendly(
        self, monkeypatch, fresh_settings
    ):
        """"留下空"要用内置默认，而不是空集 —— 空集会让工具静默无法写任何文件。"""
        monkeypatch.setenv("WORKFLOW_WRITE_ALLOWED_EXTENSIONS", "")
        settings = get_settings()
        exts = settings.workflow.write_extension_list
        assert ".py" in exts and ".md" in exts and len(exts) > 10

    def test_workflow_write_to_bound_folder_defaults_on(self, fresh_settings):
        """本机单用户场景默认允许写进绑定目录；对外部署靠环境变量关掉。"""
        assert get_settings().workflow.write_to_bound_folder is True

    def test_log_group_uses_log_prefix(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("LOG_MAX_BACKUP_COUNT", "7")
        monkeypatch.setenv("LOG_FILE_ENABLED", "false")
        settings = get_settings()
        assert settings.log.level == "DEBUG"
        assert settings.log.level_int == 10
        assert settings.log.max_backup_count == 7
        assert settings.log.file_enabled is False

    def test_rate_limit_group_uses_rate_limit_prefix(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
        monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "11")
        monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "30")
        settings = get_settings()
        assert settings.rate_limit.enabled is False
        assert settings.rate_limit.max_requests == 11
        assert settings.rate_limit.window_seconds == 30

    def test_model_backend_uses_legacy_var_name(self, monkeypatch, fresh_settings):
        """`backend` 字段名与旧变量 `MODEL_BACKEND` 不一致，依赖别名映射。"""
        monkeypatch.setenv("MODEL_BACKEND", "mock")
        monkeypatch.setenv("MODELSCOPE_API_KEY", "")  # mock 后端不需要密钥
        settings = get_settings()
        assert settings.model.backend == "mock"
        assert settings.model.llm_model == "mock"

    def test_app_env_uses_legacy_var_name(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("APP_ENV", "test")
        assert get_settings().env == "test"

    def test_security_and_retrieval_bind_by_field_name(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("ADMIN_USERNAME", "root")
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("JWT_ALGORITHM", "HS512")
        monkeypatch.setenv("RETRIEVER_K", "9")
        monkeypatch.setenv("RRF_TOP_N", "8")
        settings = get_settings()
        assert settings.security.admin_username == "root"
        assert settings.security.jwt_algorithm == "HS512"
        assert settings.retrieval.retriever_k == 9
        assert settings.retrieval.rrf_top_n == 8


class TestValidation:
    def test_chunk_overlap_must_be_less_than_chunk_size(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("CHUNK_SIZE", "200")
        monkeypatch.setenv("CHUNK_OVERLAP", "300")
        with pytest.raises(Exception) as exc:
            get_settings()
        assert "CHUNK_OVERLAP" in str(exc.value) or "chunk_overlap" in str(exc.value)

    def test_missing_admin_credentials_rejected(self):
        """直接对分组做单测（`_env_file=None` 关掉 .env 读取，避免它提供凭据）。

        注意：不能靠 `monkeypatch.setenv("ADMIN_USERNAME", "")` 来测 ——
        `env_ignore_empty=True` 会让空环境变量不覆盖 .env 文件里的值，
        这是有意的行为（空值视为"未设置"而不是"清空配置"）。
        """
        from app.core.config import SecuritySettings

        with pytest.raises(Exception) as exc:
            SecuritySettings(_env_file=None, admin_username="", admin_password="")
        assert "ADMIN_USERNAME" in str(exc.value)

    def test_modelscope_requires_api_key(self):
        from app.core.config import ModelSettings

        with pytest.raises(Exception) as exc:
            ModelSettings(_env_file=None, backend="modelscope", modelscope_api_key="")
        assert "MODELSCOPE_API_KEY" in str(exc.value)

    def test_openai_requires_api_key(self):
        from app.core.config import ModelSettings

        with pytest.raises(Exception) as exc:
            ModelSettings(_env_file=None, backend="openai", openai_api_key="")
        assert "OPENAI_API_KEY" in str(exc.value)

    def test_empty_env_var_does_not_clear_dotenv_value(self, monkeypatch, fresh_settings):
        """空环境变量不覆盖 .env —— 回归保护（env_ignore_empty 的语义）。"""
        monkeypatch.setenv("ADMIN_USERNAME", "")
        monkeypatch.setenv("ADMIN_PASSWORD", "")
        monkeypatch.setenv("MODEL_BACKEND", "mock")
        settings = get_settings()
        assert settings.security.admin_username  # 来自 .env，未被空值清空
        assert settings.security.admin_password

    def test_invalid_backend_rejected(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("MODEL_BACKEND", "not-a-real-backend")
        with pytest.raises(Exception):
            get_settings()


class TestCrossFieldDefaults:
    def test_model_names_default_by_backend(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("MODEL_BACKEND", "ollama")
        monkeypatch.setenv("EMBEDDING_MODEL", "")
        monkeypatch.setenv("LLM_MODEL", "")
        settings = get_settings()
        assert settings.model.embedding_model == "nomic-embed-text"
        assert settings.model.llm_model == "llama3.2:3b"

    def test_explicit_model_overrides_backend_default(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("MODEL_BACKEND", "ollama")
        monkeypatch.setenv("LLM_MODEL", "custom-model")
        assert get_settings().model.llm_model == "custom-model"

    def test_rate_limit_scope_falls_back_to_global(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "77")
        monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "30")
        settings = get_settings()
        assert settings.rate_limit.limit_for("unknown_scope") == (77, 30)
        # 已登记的 scope 用独立配额
        assert settings.rate_limit.limit_for("token")[0] == 5

    def test_supports_native_tool_calling_flag(self, monkeypatch, fresh_settings):
        monkeypatch.setenv("MODEL_BACKEND", "mock")
        assert get_settings().model.supports_native_tool_calling is False

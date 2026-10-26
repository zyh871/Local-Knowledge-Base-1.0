"""
统一配置中心 —— 基于 pydantic-settings。

相较旧 `app/config.py` 的 dataclass 版本，改进点：
  * 类型校验由 pydantic 承担，配置错误在**启动时**暴露而非运行时报错
  * 每个分组是独立 Settings 类，通过缓存属性访问（复用旧的分组语义）
  * 新增 graph / redis 两个分组
  * `.env` 由 pydantic-settings 原生加载，不再依赖调用方先执行 load_dotenv()

配置加载优先级（后者覆盖前者）：
    字段默认值 < .env 文件 < 真实环境变量

注意：分组采用「独立 BaseSettings + cached property」而非嵌套模型。
原因是 pydantic-settings 对嵌套的 BaseSettings 字段有已知的环境变量读取歧义，
独立实例化语义更明确、可预测。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# ---------------------------------------------------------------------------
#  路径基准
# ---------------------------------------------------------------------------

CORE_DIR = Path(__file__).resolve().parent          # backend/app/core
APP_DIR = CORE_DIR.parent                           # backend/app
BASE_DIR = APP_DIR.parent                           # backend/
PROJECT_ROOT = BASE_DIR.parent                      # 仓库根

# .env 查找顺序：仓库根 -> backend/（后者优先，与旧行为一致）
_ENV_FILES = (str(PROJECT_ROOT / ".env"), str(BASE_DIR / ".env"))

_COMMON_CFG = SettingsConfigDict(
    env_file=_ENV_FILES,
    env_file_encoding="utf-8",
    extra="ignore",
    case_sensitive=False,
    # 空字符串视为「未设置」，交由各分组的默认值/校验接管
    env_ignore_empty=True,
    # 允许用字段名直接构造（如 Settings(env="test")），便于测试注入
    populate_by_name=True,
)


class _BaseGroup(BaseSettings):
    """所有配置分组的基类，统一 env 装载行为。

    ⚠️ 重要：pydantic-settings **不会**根据类名自动推导环境变量前缀。
    `RedisSettings.url` 默认读的是 `URL` 而不是 `REDIS_URL`。
    需要统一前缀的分组必须显式声明 `env_prefix`，
    否则会静默使用默认值 —— 这类错误不会报错，只会让配置永远不生效。
    """

    model_config = _COMMON_CFG


def _parse_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y", "on")


# ---------------------------------------------------------------------------
#  路径分组
# ---------------------------------------------------------------------------


class PathSettings(_BaseGroup):
    data_path: str = str(BASE_DIR / "data")
    chroma_persist_dir: str = str(BASE_DIR / "chroma_db")
    log_dir: str = str(BASE_DIR / "logs")

    def ensure_dirs(self) -> None:
        for p in (self.data_path, self.chroma_persist_dir, self.log_dir):
            Path(p).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
#  检索分组
# ---------------------------------------------------------------------------


class RetrievalSettings(_BaseGroup):
    retriever_k: int = Field(3, ge=1)
    chunk_size: int = Field(500, ge=100)
    chunk_overlap: int = Field(50, ge=0)
    conversation_window_size: int = Field(5, ge=1)
    locked_window_size: int = Field(10, ge=1, description="会话锁定后的窗口大小")
    rrf_k: int = Field(60, ge=1)
    rrf_top_n: int = Field(5, ge=1)
    max_context_tokens: int = Field(3200, ge=256)

    @model_validator(mode="after")
    def _check_overlap(self) -> "RetrievalSettings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP({self.chunk_overlap}) 必须小于 CHUNK_SIZE({self.chunk_size})"
            )
        return self


# ---------------------------------------------------------------------------
#  模型分组
# ---------------------------------------------------------------------------

ModelBackend = Literal[
    "local", "ollama", "openai", "baidu_qianfan", "modelscope", "zhipu", "mock"
]

_BACKEND_MODELS: dict[str, tuple[str, str, str]] = {
    # backend: (embedding, llm, vision)
    "openai": ("text-embedding-ada-002", "gpt-3.5-turbo", "gpt-4o"),
    "baidu_qianfan": ("Embedding-V1", "ERNIE-3.5-8K", "ERNIE-4.0-8K-Lite"),
    "modelscope": ("local-tfidf", "Qwen/Qwen3-Coder-30B-A3B-Instruct", "Qwen/Qwen3.5-35B-A3B"),
    # 智谱 AI（OpenAI 兼容协议）。embedding-3 输出 2048 维，
    # 与 TF-IDF 的 768 维不兼容 —— 切换后必须重建向量库。
    "zhipu": ("embedding-3", "glm-4-flash", "glm-4v-flash"),
    "ollama": ("nomic-embed-text", "llama3.2:3b", "llama3.2-vision"),
    "local": ("all-MiniLM-L6-v2", "mock", "mock"),
    "mock": ("mock", "mock", "mock"),
}

# 各后端可选的 LLM 候选（供前端「模型切换」控件使用）。
# 顺序即展示顺序，第一个为默认。可用环境变量 `LLM_CHOICES` 覆盖（逗号分隔）。
_BACKEND_LLM_CHOICES: dict[str, tuple[str, ...]] = {
    "zhipu": ("glm-4-flash", "glm-4-air", "glm-4-plus", "glm-4-long"),
    "modelscope": ("Qwen/Qwen3-Coder-30B-A3B-Instruct",),
    "openai": ("gpt-4o-mini", "gpt-4o", "gpt-4-turbo"),
    "baidu_qianfan": ("ERNIE-3.5-8K", "ERNIE-4.0-8K-Lite"),
    "ollama": ("llama3.2:3b", "qwen2.5:7b"),
    "local": ("mock",),
    "mock": ("mock",),
}

# 模型中文说明（仅用于前端展示，缺失则回落到模型名）
_MODEL_LABELS: dict[str, str] = {
    "glm-4-flash": "快速版 · 响应最快，适合日常问答",
    "glm-4-air": "均衡版 · 速度与质量兼顾",
    "glm-4-plus": "增强版 · 复杂推理与长回答更稳",
    "glm-4-long": "长文本版 · 超长上下文",
    "glm-4.5": "深度思考 · 先推理再作答",
    "glm-4.5-air": "深度思考 · 轻量快速",
    "gpt-4o-mini": "轻量 · 成本低",
    "gpt-4o": "旗舰 · 综合能力最强",
    "gpt-4-turbo": "高吞吐 · 长上下文",
}

# 各后端的视觉能力回退顺序（重试时可逐个降级）
_ZHIPU_VISION_FALLBACKS = ("glm-4v-flash", "glm-4v-plus", "glm-4v")

#: 具备 `images/generations` 能力的后端（与 rag/image_gen.py 的判定保持一致）。
_IMAGE_CAPABLE_BACKENDS = ("zhipu", "openai", "modelscope")

#: 会把 `reasoning_content` 流出来的后端。mock/local 没有这条通道 ——
#: 给它们追加思考模型只会让用户选中一个永远没有思考过程的选项。
_REASONING_BACKENDS = ("zhipu", "openai", "modelscope")

# 各后端可选的图像模型候选。
# ⚠️ 智谱的 CogView-3-Flash 免费且出图快，适合做默认值；
# CogView-4 质量更好但按张计费，因此排在后面而不是默认。
_BACKEND_IMAGE_CHOICES: dict[str, tuple[str, ...]] = {
    "zhipu": ("cogview-3-flash", "cogview-4-250304"),
    "openai": ("gpt-image-1", "dall-e-3"),
    "modelscope": ("Qwen/Qwen-Image",),
}

_IMAGE_MODEL_LABELS: dict[str, str] = {
    "cogview-3-flash": "CogView 3 Flash",
    "cogview-4-250304": "CogView 4",
    "gpt-image-1": "GPT Image 1",
    "dall-e-3": "DALL·E 3",
    "Qwen/Qwen-Image": "Qwen Image",
}


class ModelSettings(_BaseGroup):
    # 字段名 `backend` 与旧环境变量 `MODEL_BACKEND` 不一致，需显式别名
    backend: ModelBackend = Field(
        "zhipu",
        validation_alias=AliasChoices("MODEL_BACKEND", "BACKEND"),
    )

    # 显式覆盖项（留空则按 backend 取默认）
    embedding_model: str = ""
    llm_model: str = ""
    vision_model: str = ""
    query_model: str = Field("", description="用于分类/抽取的轻量模型，留空则复用 llm_model")

    # ── 图像生成（AI 创作页）──────────────────────────────────────
    #: 默认图像模型。CogView-3-Flash 免费且出图快，作为开箱默认值。
    image_model: str = "cogview-3-flash"
    #: 可选图像模型清单（逗号分隔），留空用后端内置候选。
    #: 与 `LLM_CHOICES` 同一思路：只允许切换到这里列出的模型，
    #: 防止任意模型名透传到上游（既可能触发计费，也可能根本不存在）。
    image_models: str = ""
    #: 单张图片的生成超时（秒）。图像比文本慢一个数量级，
    #: 但也不能无限等 —— 用户需要知道「还在跑」与「已经废了」的区别。
    image_timeout_seconds: float = Field(180.0, ge=10, le=600)

    # ── 创作（文生图 / 图生图 / 文生视频 / 图生视频）──────────────
    #: 允许使用的生成供应商（逗号分隔）：modelscope / bailian / zhipu。
    #: 只有列在这里、且密钥可用的供应商，其模型才会出现在创作页 ——
    #: 这比"全列出来让用户自己试"更省事：试错成本是我们的，不是用户的。
    creation_providers: str = "modelscope"
    #: 视频供应商白名单（留空 = 沿用 creation_providers）。
    #: 图像与视频的可用供应商常常不一致：例如 ModelScope 只开放了出图，
    #: 而智谱 CogVideoX-3 正好相反 —— 一个列表盖不住两种情况。
    creation_video_providers: str = ""
    #: 是否开放 ModelScope 的视频模型。
    #: ⚠️ 默认 false：实测该账号调用 Wan/CogVideoX 全部返回 40212
    #: （模型存在，但 API-Inference 未对账号开放）。平台开放后把这里改成 true 即可，
    #: 调用链已经写好，不需要改代码。
    modelscope_video_enabled: bool = False
    #: 智谱视频模型（CogVideoX 系列）。实测 cogvideox-3 可用。
    zhipu_video_model: str = "cogvideox-3"
    #: quality（画质优先）/ speed（速度优先）
    zhipu_video_quality: str = "quality"
    #: CogVideoX-3 可带音效；打开后体积更大，关闭可省一点带宽
    zhipu_video_with_audio: bool = True

    # ── 深度思考（推理过程可视化）────────────────────────────────
    #: 总开关。关掉后即使前端请求 `thinking=true` 也不启用 ——
    #: 这是「不想为此付费」时的唯一开关，不必去改前端。
    thinking_enabled: bool = True
    #: 启用深度思考时使用的模型。当前只有 GLM-4.5 系列会在流里返回
    #: `reasoning_content`；`glm-4-flash` 不会，选了也看不到思考过程。
    thinking_model: str = "glm-4.5"
    #: 具备推理输出的模型白名单（逗号分隔）。不在这个名单里的模型
    #: 即使用 `thinking=true` 请求也不会退化成「假装在思考」。
    thinking_models: str = "glm-4.5,glm-4.5-air,glm-4.6,glm-z1-air,glm-z1-flash"

    # 前端「模型切换」控件的候选清单；逗号分隔，留空则用后端内置候选。
    # 只允许切换到该清单内的模型 —— 防止任意模型名透传到上游。
    llm_choices: str = ""

    # ── 自定义模型端点（用户自行接入的 OpenAI 兼容服务）──────────────
    #: 是否允许用户配置自己的模型端点。
    #: ⚠️ 允许意味着服务器会携用户填写的密钥向**任意地址**发请求。
    #: 本机单用户部署没问题；多人或对外部署应当设为 false。
    allow_custom_models: bool = Field(
        True, description="是否允许用户配置自定义模型端点"
    )
    #: 自定义端点的 SQLite 路径（留空 = data/custom_models.db）
    custom_models_db_path: str = ""

    # 单次生成的最大输出 token 数。
    # 留空 = 透传给 provider 用其默认值（智谱 glm-4 系列默认约 1024）。
    # 显式设置的好处：截断成为「可预期的配置」而不是「神秘变短」。
    max_tokens: Optional[int] = Field(None, ge=1)

    # ── 生成请求的超时与重试 ─────────────────────────────────────
    #: 单次生成请求的超时（秒）。0 = 不设超时（旧行为：上游挂起就无限等）。
    #: 有超时的意义不只是"防死等"：**它让「慢」和「死」变得可区分** ——
    #: 没有超时时，一个卡住的上游与一个正常但缓慢的生成在界面上长得一模一样。
    llm_timeout_seconds: float = Field(600.0, ge=0)
    #: 失败重试次数（langchain 默认是 2）。默认降到 1：最坏耗时 ≈ 超时 × (重试+1)，
    #: 重试越多，"还要等多久"越不可预期。
    llm_max_retries: int = Field(1, ge=0, le=5)

    modelscope_api_key: str = ""
    modelscope_base_url: str = "https://api-inference.modelscope.cn/v1"
    # 智谱 AI：兼容 OpenAI 协议，直接复用 ChatOpenAI / OpenAIEmbeddings
    zhipu_api_key: str = ""
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    openai_api_key: str = ""
    openai_base_url: str = ""
    qianfan_ak: str = ""
    qianfan_sk: str = ""
    ollama_base_url: str = "http://localhost:11434"
    tavily_api_key: str = ""

    @model_validator(mode="after")
    def _fill_defaults_and_require_keys(self) -> "ModelSettings":
        emb, llm, vis = _BACKEND_MODELS[self.backend]
        self.embedding_model = self.embedding_model or emb
        self.llm_model = self.llm_model or llm
        self.vision_model = self.vision_model or vis
        self.query_model = self.query_model or self.llm_model

        # 与旧 _require_env() 行为一致：缺少必需密钥时给出明确错误
        if self.backend == "openai" and not self.openai_api_key:
            raise ValueError(
                "环境变量 OPENAI_API_KEY 未设置。使用 openai 后端时必须配置此变量。"
            )
        if self.backend == "baidu_qianfan" and not (self.qianfan_ak and self.qianfan_sk):
            raise ValueError(
                "环境变量 QIANFAN_AK / QIANFAN_SK 未设置。使用 baidu_qianfan 后端时必须配置。"
            )
        if self.backend == "modelscope" and not self.modelscope_api_key:
            raise ValueError(
                "环境变量 MODELSCOPE_API_KEY 未设置。使用 modelscope 后端时必须配置此变量。"
            )
        if self.backend == "zhipu" and not self.zhipu_api_key:
            raise ValueError(
                "环境变量 ZHIPU_API_KEY 未设置。使用 zhipu 后端时必须配置此变量。\n"
                "获取地址：https://open.bigmodel.cn/  →  API Keys"
            )
        return self

    @property
    def supports_native_tool_calling(self) -> bool:
        return self.backend in ("modelscope", "openai", "zhipu")

    @property
    def supports_vision(self) -> bool:
        return self.backend in ("modelscope", "openai", "baidu_qianfan", "ollama", "zhipu")

    # -- 模型切换（前端控件用） ------------------------------------------

    @property
    def available_llm_models(self) -> list[str]:
        """可选 LLM 清单。

        优先用环境变量 `LLM_CHOICES`（逗号分隔）；否则取后端内置候选。
        当前 `llm_model` 一定包含在内且**排在最前**，保证默认项可见。
        """
        raw = [m.strip() for m in (self.llm_choices or "").split(",") if m.strip()]
        choices = raw or list(_BACKEND_LLM_CHOICES.get(self.backend, ()))

        if self.llm_model and self.llm_model not in choices:
            choices = [self.llm_model, *choices]
        elif self.llm_model in choices:
            # 把当前默认模型提到首位
            choices = [self.llm_model] + [m for m in choices if m != self.llm_model]

        # 深度思考模型必须可选 —— 否则 `thinking=true` 时无法把请求切过去，
        # 用户会看到「开关打开了但仍然没有思考过程」。
        # 只在支持推理通道的后端追加：mock/local 没有这条通道，
        # 塞一个永远不会返回 reasoning 的模型名只会误导。
        if (
            self.thinking_enabled
            and self.backend in _REASONING_BACKENDS
            and self.thinking_model
            and self.thinking_model not in choices
        ):
            choices.append(self.thinking_model)
        return choices

    # -- 深度思考 ---------------------------------------------------------

    @property
    def thinking_model_ids(self) -> list[str]:
        raw = [m.strip() for m in (self.thinking_models or "").split(",") if m.strip()]
        if self.thinking_model and self.thinking_model not in raw:
            raw.insert(0, self.thinking_model)
        return raw

    def is_thinking_capable(self, model: str) -> bool:
        return bool(model) and model in self.thinking_model_ids

    def is_llm_allowed(self, model: str) -> bool:
        """校验模型名白名单。

        必须校验 —— 否则客户端可以把任意模型名透传到上游 API，
        既可能触发未预期计费，也可能访问到不该访问的模型。
        """
        return bool(model) and model in self.available_llm_models

    def model_label(self, model: str) -> str:
        return _MODEL_LABELS.get(model, "")

    # -- 图像模型（AI 创作页）--------------------------------------------

    @property
    def available_image_models(self) -> list[dict[str, str]]:
        """可选图像模型清单。

        不支持图像生成的后端返回空清单 —— 界面据此显示「当前后端不支持」，
        而不是给出一个点了必然失败的模型下拉。
        """
        if self.backend not in _IMAGE_CAPABLE_BACKENDS:
            return []

        raw = [m.strip() for m in (self.image_models or "").split(",") if m.strip()]
        choices = raw or list(_BACKEND_IMAGE_CHOICES.get(self.backend, ()))
        if self.image_model and self.image_model not in choices:
            choices = [self.image_model, *choices]
        elif self.image_model in choices:
            choices = [self.image_model] + [m for m in choices if m != self.image_model]
        return [
            {"id": name, "label": _IMAGE_MODEL_LABELS.get(name, name)}
            for name in choices
        ]

    def is_image_model_allowed(self, model: str) -> bool:
        return bool(model) and any(
            item["id"] == model for item in self.available_image_models
        )

    @property
    def resolved_llm_choice(self) -> str:
        """切换控件的默认选中项。"""
        choices = self.available_llm_models
        return choices[0] if choices else self.llm_model


# ---------------------------------------------------------------------------
#  安全分组
# ---------------------------------------------------------------------------


class SecuritySettings(_BaseGroup):
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(1440, ge=1)

    admin_username: str = ""
    admin_password: str = ""

    @model_validator(mode="after")
    def _require_admin(self) -> "SecuritySettings":
        if not self.admin_username or not self.admin_password:
            raise ValueError(
                "ADMIN_USERNAME 和 ADMIN_PASSWORD 环境变量必须设置！"
                "（旧实现会在启动时抛 RuntimeError，此处改为配置校验期报错）"
            )
        return self

    @property
    def is_default_secret(self) -> bool:
        return self.jwt_secret_key == "change-me-in-production"


# ---------------------------------------------------------------------------
#  限流分组
# ---------------------------------------------------------------------------


class RateLimitSettings(_BaseGroup):
    model_config = SettingsConfigDict(**_COMMON_CFG, env_prefix="RATE_LIMIT_")

    enabled: bool = True
    max_requests: int = Field(60, ge=1)
    window_seconds: int = Field(60, ge=10)
    # 关键接口的独立配额：{scope: (limit, window_seconds)}
    scope_limits: dict[str, tuple[int, int]] = Field(
        default_factory=lambda: {
            "token": (5, 60),
            "ask": (30, 60),
            "ask_stream": (30, 60),
            "ask_image": (20, 60),
            "agent_classify": (60, 60),
            "upload": (10, 60),
            "share_read": (30, 60),
            "long_text": (10, 60),
            # 出图比文本贵得多（单张按张计费），配额默认收紧到 6 次/分钟
            "creation": (6, 60),
        }
    )

    @field_validator("enabled", mode="before")
    @classmethod
    def _coerce_bool(cls, v: object) -> bool:
        return _parse_bool(v, True)

    def limit_for(self, scope: str) -> tuple[int, int]:
        """返回 (limit, window)；未配置的 scope 用全局默认值。"""
        return self.scope_limits.get(scope, (self.max_requests, self.window_seconds))


# ---------------------------------------------------------------------------
#  日志分组
# ---------------------------------------------------------------------------


class LogSettings(_BaseGroup):
    model_config = SettingsConfigDict(**_COMMON_CFG, env_prefix="LOG_")

    level: str = "INFO"
    format: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(trace_id)s | %(message)s"
    datefmt: str = "%Y-%m-%d %H:%M:%S"
    file_enabled: bool = True
    max_file_size_mb: int = Field(10, ge=1)
    max_backup_count: int = Field(30, ge=1)

    @field_validator("file_enabled", mode="before")
    @classmethod
    def _coerce_bool(cls, v: object) -> bool:
        return _parse_bool(v, True)

    @property
    def level_int(self) -> int:
        return getattr(logging, str(self.level).upper(), logging.INFO)


# ---------------------------------------------------------------------------
#  Redis 分组
# ---------------------------------------------------------------------------


class RedisSettings(_BaseGroup):
    """Redis 连接与键空间策略。

    fallback: 当 Redis 不可用时是否降级为进程内实现。
              开发环境（无 Docker/Redis）应保持 true；生产环境建议 false，
              以便故障时快速暴露而不是静默退化为单机语义。
    """

    model_config = SettingsConfigDict(**_COMMON_CFG, env_prefix="REDIS_")

    url: str = "redis://localhost:6379/0"
    max_connections: int = Field(50, ge=1)
    socket_timeout: float = Field(5.0, gt=0)
    key_prefix: str = "kbqa"
    fallback_to_memory: bool = True
    connect_retries: int = Field(2, ge=0)

    # ── TTL（秒）────────────────────────────────
    ttl_session: int = Field(7 * 24 * 3600, ge=60)
    ttl_profile_cache: int = Field(3600, ge=60)
    ttl_retrieval_cache: int = Field(300, ge=1)
    ttl_embedding_cache: int = Field(24 * 3600, ge=60)
    ttl_share_default: int = Field(24 * 3600, ge=60)
    ttl_share_max: int = Field(14 * 24 * 3600, ge=60)
    ttl_lock_default: int = Field(30, ge=1)
    ttl_lock_rebuild: int = Field(600, ge=1)
    ttl_job_status: int = Field(24 * 3600, ge=60)
    checkpoint_ttl: int = Field(14 * 24 * 3600, ge=3600)

    @field_validator("fallback_to_memory", mode="before")
    @classmethod
    def _coerce_bool(cls, v: object) -> bool:
        return _parse_bool(v, True)


# ---------------------------------------------------------------------------
#  LangGraph 分组
# ---------------------------------------------------------------------------


class GraphSettings(_BaseGroup):
    """LangGraph 图执行参数。

    注意：图已是**唯一**的业务编排实现（原过程式 router 已删除），
    因此没有"开关"配置项 —— 图构建失败即启动失败（fail fast）。
    """

    model_config = SettingsConfigDict(**_COMMON_CFG, env_prefix="GRAPH_")

    checkpointer: Literal["redis", "memory", "none"] = "memory"
    max_agent_iterations: int = Field(5, ge=1)
    recursion_limit: int = Field(25, ge=5)


# ---------------------------------------------------------------------------
#  OCR 分组
# ---------------------------------------------------------------------------


class OcrSettings(_BaseGroup):
    """Tesseract OCR 参数（图片类文档解析）。

    `tesseract_cmd` / `tessdata_prefix` 留空时由 `app/document/ocr.py`
    按「配置 → 常见安装路径」顺序自动发现。

    ⚠️ 语言包目录常与可执行文件目录**不在一起**：Windows 下 Tesseract 默认
    装在 `C:\\Program Files\\Tesseract-OCR`（写它需要管理员权限），
    因此追加语言包时通常放到用户可写目录，再用 `OCR_TESSDATA_PREFIX` 指过去。
    """

    model_config = SettingsConfigDict(**_COMMON_CFG, env_prefix="OCR_")

    enabled: bool = True
    tesseract_cmd: str = ""
    tessdata_prefix: str = ""
    languages: str = "chi_sim+eng"
    timeout_seconds: int = Field(60, ge=5)

    @field_validator("enabled", mode="before")
    @classmethod
    def _coerce_enabled(cls, v: object) -> bool:
        return _parse_bool(v, True)

    @property
    def language_list(self) -> list[str]:
        return [x.strip() for x in self.languages.split("+") if x.strip()]


# ---------------------------------------------------------------------------
#  媒体生成分组（阿里云百炼 / DashScope）
# ---------------------------------------------------------------------------


class BailianSettings(_BaseGroup):
    """阿里云百炼的图像 / 视频生成配置。

    ⚠️ 密钥刻意**与 `MODEL_BACKEND` 的密钥分开**：百炼是独立的供应商，
    借用 `ZHIPU_API_KEY` / `OPENAI_API_KEY` 会把「换聊天模型」变成
    「顺便换掉出图账号」，而且报错信息会指向完全无关的地方。

    ## 为什么这些接口都是「异步任务」

    DashScope 的 `text2image` / `video-synthesis` 都只返回 `task_id`，
    真正的产物要轮询 `/api/v1/tasks/{id}` 才拿得到（视频 1-3 分钟是常态）。
    直接把 HTTP 请求挂到用户请求上会因为网关超时而「图出了但接口 504」，
    所以上层用「提交 → 轮询 → 落盘」三步，图片同步等（几十秒可接受），
    视频走任务行 + 前端轮询。
    """

    model_config = SettingsConfigDict(**_COMMON_CFG, env_prefix="BAILIAN_")

    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com"
    #: 默认图像模型（已实测可用）
    image_model: str = "wanx2.1-t2i-turbo"
    #: 默认视频模型（已实测可用；尺寸必须是官方白名单之一）
    video_model: str = "wan2.2-t2v-plus"
    #: 任务轮询间隔。太密会被上游限流，太疏则前端看着像卡住
    poll_interval_seconds: float = Field(3.0, ge=0.5, le=30)
    image_timeout_seconds: float = Field(180.0, ge=20, le=600)
    video_timeout_seconds: float = Field(900.0, ge=60, le=1800)

    @property
    def enabled(self) -> bool:
        return bool((self.api_key or "").strip())


# ---------------------------------------------------------------------------
#  AI 工作流分组
# ---------------------------------------------------------------------------


class WorkflowSettings(_BaseGroup):
    """AI 工作流模式参数（设计见 docs/WORKFLOW_MODE_DESIGN.md）。

    工作流数据**独立于对话**，存 `data/workflow.db`：`/conversations` 的返回
    结构是对外契约，加 `kind` 字段会直接撞契约测试；而且步骤状态是状态机数据，
    塞进消息 JSON 会让「查所有等待审批的 run」退化成全表扫 + 反序列化。

    `db_path` / `workspace_root` 留空时按 `paths.data_path` 推导，
    避免在 .env 里重复写一遍 data 目录。
    """

    model_config = SettingsConfigDict(**_COMMON_CFG, env_prefix="WORKFLOW_")

    enabled: bool = True
    db_path: str = ""
    workspace_root: str = ""

    # manual = 人工逐步审查；auto = AI 自动执行
    default_mode: Literal["manual", "auto"] = "manual"

    max_steps: int = Field(12, ge=1, description="单个 run 的步骤上限，超出要求用户裁剪")
    max_clarify_rounds: int = Field(3, ge=1, description="澄清追问轮次上限")
    file_ttl_days: int = Field(7, ge=1)
    file_inline_max_chars: int = Field(8000, ge=100, description="小于此长度直接全文进 prompt")
    max_file_mb: int = Field(20, ge=1)
    step_timeout_seconds: int = Field(180, ge=5)
    search_max_calls_per_run: int = Field(5, ge=0, description="单 run 联网搜索调用上限")
    #: 对搜索来源发 HEAD 做可达性标记（设计 §10.2 第 3 条）。
    #: 测试可关掉，避免在无网络/CI 环境里产生真实请求与等待。
    verify_links: bool = Field(True, description="是否探测来源链接可达性")
    #: 允许浏览服务器本地文件系统以绑定工作区文件夹。
    #: ⚠️ 这是**有意开放**的本地读能力，前提是「本机单用户」部署。
    #: 暴露到局域网/公网前必须设为 false —— 否则任何登录用户都能枚举服务器磁盘。
    fs_browse: bool = Field(True, description="是否允许浏览本地文件夹以设置工作区")
    # 无论什么模式都强制单独审批的高风险工具（逗号分隔）
    require_approval_tools: str = "code_executor,file_write"

    # ── 文件产出（file_write 工具）─────────────────────────────
    #: 允许**写出**的文件后缀（逗号分隔，空 = 用工具内置默认）。
    #: 这是"写"白名单：与"允许引用"的读白名单不是一回事 ——
    #: 读白名单里有 .pdf/.png 这类不可能生成的二进制格式，
    #: 而写白名单必须有 .py/.js 这类代码交付物。
    write_allowed_extensions: str = ""
    #: 单个产出文件的大小上限（KB）。产物是给人用的文件，不是数据集；
    #: 真要搬运大文件应当走引用文件 / 知识库。
    write_max_kb: int = Field(512, ge=1)
    #: 是否允许 file_write 写到工作区**已绑定**的本地文件夹。
    #: 关掉后只能写进任务产物目录（仍可下载）。多用户/对外部署建议 false。
    write_to_bound_folder: bool = Field(
        True, description="是否允许把产出文件写进工作区已绑定的本地文件夹"
    )
    cleanup_interval_seconds: int = Field(3600, ge=60)

    @property
    def write_extension_list(self) -> list[str]:
        from app.tools.file_write import DEFAULT_WRITE_EXTENSIONS

        raw = (self.write_allowed_extensions or "").strip()
        if not raw:
            return sorted(DEFAULT_WRITE_EXTENSIONS)
        items = [item.strip().lower() for item in raw.split(",")]
        return sorted({item if item.startswith(".") else f".{item}" for item in items if item})

    @field_validator("enabled", mode="before")
    @classmethod
    def _coerce_enabled(cls, v: object) -> bool:
        return _parse_bool(v, True)

    @staticmethod
    def _data_path() -> str:
        return get_path_settings().data_path

    @property
    def resolved_db_path(self) -> str:
        return self.db_path or str(Path(self._data_path()) / "workflow.db")

    @property
    def resolved_workspace_root(self) -> str:
        return self.workspace_root or str(Path(self._data_path()) / "workspaces")

    @property
    def approval_tool_list(self) -> list[str]:
        return [x.strip() for x in self.require_approval_tools.split(",") if x.strip()]


# ---------------------------------------------------------------------------
#  顶层 Settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """应用配置总入口。分组通过缓存属性暴露，避免每次访问重读 .env。"""

    model_config = _COMMON_CFG

    # 字段名 `env` 与旧环境变量 `APP_ENV` 不一致，需显式别名
    env: Literal["dev", "test", "prod"] = Field(
        "dev", validation_alias=AliasChoices("APP_ENV", "ENV")
    )
    app_name: str = "本地知识库问答系统"
    app_version: str = "4.0.0"
    api_prefix: str = ""  # 保持为空 → 接口路径与 v3 完全一致（前端零改动）

    # NoDecode：禁止 pydantic-settings 把 CORS_ORIGINS 当 JSON 解析，
    # 交由下面的 before-validator 按逗号切分（兼容旧 .env 写法）
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost",
            "http://localhost:80",
            "http://localhost:8081",
            "http://localhost:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:5174",
        ]
    )

    # 上传限制
    max_upload_size_mb: int = Field(50, ge=1)
    max_image_size_mb: int = Field(20, ge=1)

    # /health 是否做 LLM 探活。
    # 默认关闭：旧实现每次健康检查都请求模型，既产生开销，
    # 又会因部分服务商拒绝裸 "ping" 提示词而误报 degraded。
    health_check_llm: bool = False

    @field_validator("health_check_llm", mode="before")
    @classmethod
    def _coerce_health_llm(cls, v: object) -> bool:
        return _parse_bool(v, False)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("app_version", mode="before")
    @classmethod
    def _str_version(cls, v: object) -> str:
        return str(v)

    # ── 分组访问器（进程内缓存，只实例化一次）──────────
    @property
    def paths(self) -> PathSettings:
        return get_path_settings()

    @property
    def retrieval(self) -> RetrievalSettings:
        return get_retrieval_settings()

    @property
    def model(self) -> ModelSettings:
        return get_model_settings()

    @property
    def security(self) -> SecuritySettings:
        return get_security_settings()

    @property
    def rate_limit(self) -> RateLimitSettings:
        return get_rate_limit_settings()

    @property
    def log(self) -> LogSettings:
        return get_log_settings()

    @property
    def redis(self) -> RedisSettings:
        return get_redis_settings()

    @property
    def bailian(self) -> BailianSettings:
        return get_bailian_settings()

    @property
    def graph(self) -> GraphSettings:
        return get_graph_settings()

    @property
    def ocr(self) -> OcrSettings:
        return get_ocr_settings()

    @property
    def workflow(self) -> WorkflowSettings:
        return get_workflow_settings()

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"


# ---------------------------------------------------------------------------
#  单例访问器
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """应用配置单例。

    **立即实例化所有分组** —— 分组的校验错误（如 MODELSCOPE_API_KEY 缺失、
    chunk_overlap >= chunk_size）必须在**启动时**抛出，而不是等到第一次访问
    该分组属性才暴露。旧 `AppConfig.from_env()` 是即时构建全部子配置的，
    此处保持同样的 fail-fast 语义。
    """
    settings = Settings()
    # 触发各分组的构建与校验（属性访问是惰性的，必须显式触碰）
    _ = (
        settings.paths,
        settings.retrieval,
        settings.model,
        settings.security,
        settings.rate_limit,
        settings.log,
        settings.redis,
        settings.graph,
        settings.ocr,
        settings.workflow,
        settings.bailian,
    )
    return settings


@lru_cache(maxsize=1)
def get_path_settings() -> PathSettings:
    return PathSettings()


@lru_cache(maxsize=1)
def get_retrieval_settings() -> RetrievalSettings:
    return RetrievalSettings()


@lru_cache(maxsize=1)
def get_model_settings() -> ModelSettings:
    return ModelSettings()


@lru_cache(maxsize=1)
def get_security_settings() -> SecuritySettings:
    return SecuritySettings()


@lru_cache(maxsize=1)
def get_rate_limit_settings() -> RateLimitSettings:
    return RateLimitSettings()


@lru_cache(maxsize=1)
def get_log_settings() -> LogSettings:
    return LogSettings()


@lru_cache(maxsize=1)
def get_redis_settings() -> RedisSettings:
    return RedisSettings()


@lru_cache(maxsize=1)
def get_graph_settings() -> GraphSettings:
    return GraphSettings()


@lru_cache(maxsize=1)
def get_ocr_settings() -> OcrSettings:
    return OcrSettings()


@lru_cache(maxsize=1)
def get_workflow_settings() -> WorkflowSettings:
    return WorkflowSettings()


@lru_cache(maxsize=1)
def get_bailian_settings() -> BailianSettings:
    return BailianSettings()


def clear_settings_cache() -> None:
    """测试用：清空配置缓存（配合 monkeypatch.setenv）。"""
    for fn in (
        get_settings,
        get_path_settings,
        get_retrieval_settings,
        get_model_settings,
        get_security_settings,
        get_rate_limit_settings,
        get_log_settings,
        get_redis_settings,
        get_graph_settings,
        get_ocr_settings,
        get_workflow_settings,
        get_bailian_settings,
    ):
        fn.cache_clear()

"""
统一配置管理 - 所有配置集中于此，支持环境变量覆盖和校验。

配置加载顺序（后者覆盖前者）：
1. 默认值
2. .env 文件（通过 python-dotenv）
3. 环境变量

配置项分类：
- 路径配置：数据目录、向量库目录
- 检索配置：召回数量、分块大小、重叠率
- 模型配置：后端类型、模型名称、API密钥
- 安全配置：JWT密钥、令牌过期时间
- 限流配置：请求频率限制
- 日志配置：日志级别、文件路径
"""

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent


def _get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get environment variable, supporting .env files."""
    return os.getenv(key, default)


def _require_env(key: str, backend: str) -> str:
    """Get an env var or raise a clear error."""
    val = _get_env(key)
    if not val:
        raise ValueError(
            f"环境变量 {key} 未设置。使用 {backend} 后端时必须配置此变量。\n"
            f"请创建 .env 文件或在环境中设置:\n  {key}=your-value-here"
        )
    return val


def _parse_int(value: Optional[str], default: int, min_val: int = None, max_val: int = None) -> int:
    """Parse integer with validation."""
    try:
        result = int(value) if value is not None else default
    except (ValueError, TypeError):
        result = default
    if min_val is not None and result < min_val:
        raise ValueError(f"值 {result} 小于最小值 {min_val}")
    if max_val is not None and result > max_val:
        raise ValueError(f"值 {result} 大于最大值 {max_val}")
    return result


def _parse_bool(value: Optional[str], default: bool) -> bool:
    """Parse boolean from environment variable."""
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "y")


@dataclass
class PathConfig:
    data_path: str = str(BASE_DIR / "data")
    chroma_persist_dir: str = str(BASE_DIR / "chroma_db")
    log_dir: str = str(BASE_DIR / "logs")

    def __post_init__(self):
        os.makedirs(self.data_path, exist_ok=True)
        os.makedirs(self.chroma_persist_dir, exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)


@dataclass
class RetrievalConfig:
    retriever_k: int = 3
    chunk_size: int = 500
    chunk_overlap: int = 50
    conversation_window_size: int = 5
    rrf_k: int = 60
    rrf_top_n: int = 5

    def __post_init__(self):
        if self.chunk_size < 100:
            raise ValueError(f"chunk_size 不能小于 100，当前值: {self.chunk_size}")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(f"chunk_overlap ({self.chunk_overlap}) 不能大于等于 chunk_size ({self.chunk_size})")
        if self.retriever_k < 1:
            raise ValueError(f"retriever_k 不能小于 1，当前值: {self.retriever_k}")


@dataclass
class ModelConfig:
    backend: str = "modelscope"
    embedding_model: str = ""
    llm_model: str = ""
    vision_model: str = ""
    openai_api_key: str = ""
    qianfan_ak: str = ""
    qianfan_sk: str = ""
    modelscope_api_key: str = ""
    modelscope_base_url: str = "https://api-inference.modelscope.cn/v1"
    tavily_api_key: str = ""

    def __post_init__(self):
        if self.backend == "openai":
            self.openai_api_key = _require_env("OPENAI_API_KEY", "openai")
            self.embedding_model = self.embedding_model or "text-embedding-ada-002"
            self.llm_model = self.llm_model or "gpt-3.5-turbo"
            self.vision_model = self.vision_model or "gpt-4o"
        elif self.backend == "baidu_qianfan":
            self.qianfan_ak = _require_env("QIANFAN_AK", "baidu_qianfan")
            self.qianfan_sk = _require_env("QIANFAN_SK", "baidu_qianfan")
            self.embedding_model = self.embedding_model or "Embedding-V1"
            self.llm_model = self.llm_model or "ERNIE-3.5-8K"
            self.vision_model = self.vision_model or "ERNIE-4.0-8K-Lite"
        elif self.backend == "modelscope":
            self.modelscope_api_key = _require_env("MODELSCOPE_API_KEY", "modelscope")
            self.embedding_model = self.embedding_model or "local-tfidf"
            self.llm_model = self.llm_model or "Qwen/Qwen3-Coder-30B-A3B-Instruct"
            self.vision_model = self.vision_model or "Qwen/Qwen3.5-35B-A3B"
            self.tavily_api_key = _get_env("TAVILY_API_KEY", "")
        elif self.backend == "ollama":
            self.embedding_model = self.embedding_model or "nomic-embed-text"
            self.llm_model = self.llm_model or "llama3.2:3b"
            self.vision_model = self.vision_model or "llama3.2-vision"
        elif self.backend == "local":
            self.embedding_model = self.embedding_model or "all-MiniLM-L6-v2"
            self.llm_model = self.llm_model or "mock"
        elif self.backend == "mock":
            self.embedding_model = self.embedding_model or "mock"
            self.llm_model = self.llm_model or "mock"
        else:
            raise ValueError(f"不支持的模型后端: {self.backend}，可选值: ollama/openai/baidu_qianfan/modelscope/local/mock")


@dataclass
class SecurityConfig:
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    def __post_init__(self):
        if not self.jwt_secret_key:
            import warnings
            warnings.warn(
                "⚠️  JWT_SECRET_KEY 环境变量未设置，使用不安全的内置默认密钥。\n"
                "生产环境必须设置此变量！"
            )
            self.jwt_secret_key = "change-me-in-production"


@dataclass
class RateLimitConfig:
    enabled: bool = True
    max_requests: int = 60
    window_seconds: int = 60


@dataclass
class LogConfig:
    level: int = logging.INFO
    format: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(trace_id)s | %(message)s"
    datefmt: str = "%Y-%m-%d %H:%M:%S"
    file_enabled: bool = True
    max_file_size_mb: int = 10
    max_backup_count: int = 30


@dataclass
class AppConfig:
    paths: PathConfig = field(default_factory=PathConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    log: LogConfig = field(default_factory=LogConfig)

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Create config from environment variables."""
        return cls(
            paths=PathConfig(
                data_path=_get_env("DATA_PATH", str(BASE_DIR / "data")),
                chroma_persist_dir=_get_env("CHROMA_PERSIST_DIR", str(BASE_DIR / "chroma_db")),
                log_dir=_get_env("LOG_DIR", str(BASE_DIR / "logs")),
            ),
            retrieval=RetrievalConfig(
                retriever_k=_parse_int(_get_env("RETRIEVER_K"), 3, min_val=1),
                chunk_size=_parse_int(_get_env("CHUNK_SIZE"), 500, min_val=100),
                chunk_overlap=_parse_int(_get_env("CHUNK_OVERLAP"), 50),
                conversation_window_size=_parse_int(_get_env("CONVERSATION_WINDOW_SIZE"), 5, min_val=1),
                rrf_k=_parse_int(_get_env("RRF_K"), 60, min_val=1),
                rrf_top_n=_parse_int(_get_env("RRF_TOP_N"), 5, min_val=1),
            ),
            model=ModelConfig(
                backend=_get_env("MODEL_BACKEND", "modelscope"),
                embedding_model=_get_env("EMBEDDING_MODEL", ""),
                llm_model=_get_env("LLM_MODEL", ""),
                vision_model=_get_env("VISION_MODEL", ""),
                openai_api_key=_get_env("OPENAI_API_KEY", ""),
                qianfan_ak=_get_env("QIANFAN_AK", ""),
                qianfan_sk=_get_env("QIANFAN_SK", ""),
                modelscope_api_key=_get_env("MODELSCOPE_API_KEY", ""),
                modelscope_base_url=_get_env("MODELSCOPE_BASE_URL", "https://api-inference.modelscope.cn/v1"),
                tavily_api_key=_get_env("TAVILY_API_KEY", ""),
            ),
            security=SecurityConfig(
                jwt_secret_key=_get_env("JWT_SECRET_KEY", ""),
                jwt_algorithm=_get_env("JWT_ALGORITHM", "HS256"),
                access_token_expire_minutes=_parse_int(_get_env("ACCESS_TOKEN_EXPIRE_MINUTES"), 1440),
            ),
            rate_limit=RateLimitConfig(
                enabled=_parse_bool(_get_env("RATE_LIMIT_ENABLED"), True),
                max_requests=_parse_int(_get_env("RATE_LIMIT_MAX_REQUESTS"), 60, min_val=1),
                window_seconds=_parse_int(_get_env("RATE_LIMIT_WINDOW_SECONDS"), 60, min_val=10),
            ),
            log=LogConfig(
                level=getattr(logging, _get_env("LOG_LEVEL", "INFO").upper(), logging.INFO),
                format=_get_env("LOG_FORMAT", "%(asctime)s | %(levelname)-8s | %(name)s | %(trace_id)s | %(message)s"),
                datefmt=_get_env("LOG_DATEFMT", "%Y-%m-%d %H:%M:%S"),
                file_enabled=_parse_bool(_get_env("LOG_FILE_ENABLED"), True),
                max_file_size_mb=_parse_int(_get_env("LOG_MAX_FILE_SIZE_MB"), 10, min_val=1),
                max_backup_count=_parse_int(_get_env("LOG_MAX_BACKUP_COUNT"), 30, min_val=1),
            ),
        )


config = AppConfig.from_env()

# Backwards-compatible aliases for existing code
DATA_PATH = config.paths.data_path
CHROMA_PERSIST_DIR = config.paths.chroma_persist_dir
RETRIEVER_K = config.retrieval.retriever_k
CHUNK_SIZE = config.retrieval.chunk_size
CHUNK_OVERLAP = config.retrieval.chunk_overlap
CONVERSATION_WINDOW_SIZE = config.retrieval.conversation_window_size
LOG_LEVEL = config.log.level
MODEL_BACKEND = config.model.backend
EMBEDDING_MODEL = config.model.embedding_model
LLM_MODEL = config.model.llm_model
VISION_MODEL = config.model.vision_model
OPENAI_API_KEY = config.model.openai_api_key
QIANFAN_AK = config.model.qianfan_ak
QIANFAN_SK = config.model.qianfan_sk
MODELSCOPE_API_KEY = config.model.modelscope_api_key
MODELSCOPE_BASE_URL = config.model.modelscope_base_url
TAVILY_API_KEY = config.model.tavily_api_key
JWT_SECRET_KEY = config.security.jwt_secret_key
JWT_ALGORITHM = config.security.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = config.security.access_token_expire_minutes

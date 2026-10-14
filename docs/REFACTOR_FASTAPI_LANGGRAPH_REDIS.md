# 后端重构方案：FastAPI + LangGraph + Redis

> 目标项目：本地知识库问答系统（`backend/`，v3.0.0）  
> 文档定位：架构设计 + 迁移执行手册  
> 前置结论：**现有实现不是"推倒重来"，而是一次"分层化 + 编排图化 + 状态外置"的重构**。

---

## 0. 现状评估与复用可行性

### 0.1 现有后端盘点

| 维度    | 现状                                                                                 |
| ----- | ---------------------------------------------------------------------------------- |
| 代码规模  | `app/` 下 33 个 `.py`，合计 **7366 行**；`main.py` 单文件 **1483 行**                         |
| 入口    | `app.main:app`，一个 `FastAPI` 实例，**30+ 端点全部内联在 main.py**                             |
| 配置    | `app/config.py`：6 个 dataclass 聚合 → 模块级别名（`MODEL_BACKEND` 等）                        |
| 业务流程  | `app/agents/router.py`：**手写 `if/elif` 分发**（`route()` / `route_stream()` 两份几乎重复的实现） |
| 检索    | `rag_chain.py`：`hybrid_search()` = 向量 + BM25 → RRF 融合                              |
| 记忆    | `memory/conversation_memory.py`：**SQLite + 进程内 dict `_memory_instances`**          |
| 工具    | `tools/base.py`：`BaseTool` 抽象 + `ToolRegistry` 自动注册                                |
| Agent | `agents/function_calling.py`：手写 5 轮工具调用循环                                          |
| 限流    | `slowapi`（**进程内计数**）                                                               |
| 分享链接  | 磁盘 JSON 文件 + `_purge_expired_shares()` 手动清理                                        |
| 流式    | 手写 async generator 产出 `data: {...}\n\n`                                            |

### 0.2 关键发现（决定方案走向）

1. **LangGraph 已经装好了，但一行没用。**  
   `venv/Lib/site-packages` 中已存在 `langgraph==1.2.10`、`langgraph_checkpoint==4.1.1`、`langgraph_prebuilt==1.1.0`、`langgraph_sdk==0.4.2`。  
   → 引入 LangGraph **零新增依赖成本**，纯属把已有能力用起来。
2. **Redis 未安装。** 这是唯一需要真正新增的基础设施依赖。
3. **`router.py` 就是一张"伪状态图"。**  
   `route()` 里的 `if intent == ...` 分支、`route_stream()` 里近乎逐行重复的第二份分支——这是典型的"本该由状态图 + 条件边表达的编排逻辑，被写成了过程式代码"。两份实现并存意味着**任何业务改动都要同步改两处**，是当前最大的维护债。
4. **&#x38;*&#x20;个检索器里有 6 个是死代码*。**
   | 模块                                                                                                              | 外部引用数                  | 结论                                   |
   | --------------------------------------------------------------------------------------------------------------- | ---------------------- | ------------------------------------ |
   | `rrf_fusion.py`                                                                                                 | 1（被 `hybrid_search` 用） | ✅ 保留                                 |
   | `vector_retriever.py` / `bm25_retriever.py`                                                                     | 0                      | ⚠️ 被 `rag_chain` 内部同名类引用（非模块引用），实为在用 |
   | `graph_rag.py`                                                                                                  | 0                      | ❌ 死代码，未被任何路径调用                       |
   | `metadata_filter.py`                                                                                            | 0                      | ❌ 死代码                                |
   | `multimodal_retriever.py`                                                                                       | 0                      | ❌ 死代码                                |
   | `query_rewriter.py`                                                                                             | 0                      | ❌ 死代码                                |
   | `reranker.py`                                                                                                   | 0                      | ❌ 死代码                                |
   | → 重构时应**先把这些挂到 LangGraph 节点上**（`query_rewriter` / `reranker` 接在 `retrieve` 前后），否则就明确删除，不要继续留着"看起来有、实际不生效"的幻觉代码。 |                        |                                      |
5. **`_TOKEN_CACHE` 和 `_memory_instances` 是进程内全局态。**  
   当前以单 worker 运行掩盖了这个问题的严重性：一旦 `uvicorn --workers 4`，会话记忆分片、BM25 索引不一致、限流计数各自为政。**这正是引入 Redis 的第一动机**，不是"为了用 Redis 而用 Redis"。

### 0.3 复用矩阵（结论先行）

| 现有资产                                                                                        | 处置                        | 说明                                                                                                                                            |
| ------------------------------------------------------------------------------------------- | ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `rag_chain.py` 的 `hybrid_search` / `format_docs` / `get_sources` / `add_documents_to_store` | **原样复用**                  | 是稳定的领域能力，直接抽成 `app/rag/` 包，被图节点调用                                                                                                             |
| `rag_chain.py` 的 `get_llm` / `get_embeddings` / `get_vision_llm`                            | **复用 + 包一层 provider**     | 改为由 `core/deps.py` 提供，便于测试注入                                                                                                                  |
| `rag_chain.py` 的 `RAGChain` 类                                                               | **降级为薄适配器**               | `route_invoke` / `route_stream` 保留签名，内部改为 `graph.ainvoke()` / `graph.astream()`，**给前端和旧调用方留平滑过渡**                                             |
| `agents/intent_classifier.py`                                                               | **原样复用**                  | 规则优先 + LLM 回退的分类器逻辑质量足够，直接作为 `classify_intent` 节点                                                                                             |
| `agents/router.py` 的 6 个 `_handle_*` 函数                                                     | **搬进节点，逻辑留存**             | `_handle_kb_qa` → `retrieve` + `generate` 节点；`_handle_agent` → agent 子图；`_format_history` / `_extract_text` / `_lang_instruction` 抽到 `utils/` |
| `agents/router.py` 的 `route()` / `route_stream()`                                           | **废弃**                    | 由图的 `add_conditional_edges` + `stream_mode="custom"` 取代                                                                                       |
| `agents/function_calling.py` + `react_agent.py`                                             | **改造为子图**                 | 手写循环 → `ToolNode` + `tools_condition` 条件边                                                                                                     |
| `tools/` (`BaseTool` / `ToolRegistry`)                                                      | **原样复用**                  | `ToolRegistry.to_function_schemas()` 可直接喂 `llm.bind_tools()`；LangGraph `ToolNode` 接 `registry.execute`                                        |
| `memory/conversation_memory.py` 的 **SQLite 表结构**                                            | **复用为真相源**                | `conversations` + `conversation_messages` 表设计合理，保留                                                                                            |
| `memory/conversation_memory.py` 的 **进程内 dict + 手动窗口裁剪**                                     | **替换为 Redis**             | 窗口裁剪交给 LangGraph checkpointer 的 `trim_messages`，不再手写                                                                                          |
| `memory/long_term.py` / `user_profile.py`                                                   | **复用，加 Redis 缓存层**        | 抽取逻辑保留，读路径走 Redis 缓存                                                                                                                          |
| `config.py` 的 dataclass 分组思路                                                                | **升级为 pydantic-settings** | 分组保留，校验能力换成 pydantic（`pydantic-settings 2.14.2` 已安装）                                                                                          |
| `main.py` 的 auth / 限流 / trace_id / SSE 写法                                                   | **搬运 + 修改**               | 逻辑保留，位置从 main.py 挪到 `core/` 与 `api/v1/`；限流换成 Redis 后端                                                                                         |
| 分享链接的磁盘 JSON                                                                                | **替换为 Redis**             | TTL 原生支持，`_purge_expired_shares()` 这种手动清理代码直接删掉                                                                                               |
| 7 个死代码检索器                                                                                   | **删除或接入**                 | 见 0.2 第 4 点                                                                                                                                   |

---

## 1. 目标架构总览

### 1.1 分层视图

```
┌──────────────────────────────────────────────────────────────┐
│  API 层  (app/api/v1/*)                                      │
│  FastAPI Router · Pydantic Schemas · Depends 注入 · SSE 输出  │
│  职责：HTTP 语义、鉴权、参数校验、异常 → HTTP 状态码映射       │
└───────────────────────────┬──────────────────────────────────┘
                            │ 只依赖 Service 接口，不碰 graph/rag
┌───────────────────────────▼──────────────────────────────────┐
│  Service 层  (app/services/*)                                │
│  QAService · DocumentService · ConversationService · Share... │
│  职责：编排用例；调 Graph 或 Repository；事务边界              │
└──────────┬─────────────────────────────────┬─────────────────┘
           │                                 │
┌──────────▼──────────────┐   ┌──────────────▼─────────────────┐
│  Graph 层 (app/graph/)  │   │  Repository 层 (app/repositories)│
│  LangGraph StateGraph   │   │  SQLite(真相源) · Redis(缓存/态) │
│  State / Node / Edge    │   └──────────────┬─────────────────┘
│  Checkpointer           │                  │
└──────────┬──────────────┘                  │
           │                                 │
┌──────────▼─────────────────────────────────▼─────────────────┐
│  领域能力层 (app/rag/ · app/tools/ · app/agents/)             │
│  hybrid_search · embeddings · loaders · ToolRegistry          │
└──────────┬───────────────────────────────────────────────────┘
           │
┌──────────▼───────────────────────────────────────────────────┐
│  基础设施层 (app/core/)                                       │
│  config(pydantic-settings) · redis pool · exceptions          │
│  security(JWT) · logging(trace_id) · deps(provider)           │
└──────────────────────────────────────────────────────────────┘
```


**依赖方向铁律：只能向下依赖，同层之间通过接口调用，禁止反向 import。**  
判据：`app/core/` 里不允许出现 `from app.services import ...`；`app/graph/` 里不允许出现 `from fastapi import ...`。

### 1.2 目标目录结构

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                     # 只剩 create_app() + lifespan，≈80 行
│   │
│   ├── core/                       # 基础设施（无业务）
│   │   ├── config.py               # pydantic-settings BaseSettings，分组 Settings
│   │   ├── logging.py              # TraceIDFilter / setup_logging
│   │   ├── exceptions.py           # AppError 域异常族 + 全局 handler 注册
│   │   ├── security.py             # JWT 签发/校验、bcrypt、token 黑名单
│   │   ├── redis.py                # 连接池、RedisKeys、TTL 常量、lifespan 钩子
│   │   └── deps.py                 # Depends provider：get_settings/get_redis/get_graph/…
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py           # APIRouter 汇总，统一 prefix="/api/v1"
│   │       ├── auth.py             # /token  /users/me
│   │       ├── qa.py               # /ask  /ask/stream  /ask/image  /agent/*
│   │       ├── documents.py        # /upload  /upload-multiple  /documents  /rebuild-vectorstore
│   │       ├── conversations.py    # /conversations*
│   │       ├── profile.py          # /profile  /remember-turn
│   │       ├── feedback.py         # /feedback*
│   │       ├── share.py            # /share/{token}  /conversations/{id}/share
│   │       └── system.py           # /health  /stats  /example-questions
│   │
│   ├── schemas/                    # Pydantic 请求/响应模型（唯一的对外契约）
│   │   ├── common.py               # ErrorResponse / PageMeta / TraceMixin
│   │   ├── auth.py                 # Token / User / UserInDB
│   │   ├── qa.py                   # QuestionRequest / QuestionResponse / SSEEvent
│   │   ├── document.py             # DocumentItem / UploadResult / BatchUploadResult
│   │   ├── conversation.py         # ConversationMeta / ConversationUpdate / BatchDeleteRequest
│   │   └── profile.py              # UserProfile / UserProfileUpdate / FeedbackRequest
│   │
│   ├── services/                   # 用例编排
│   │   ├── qa_service.py           # 调用 graph，把 State → QuestionResponse，SSE 事件流
│   │   ├── document_service.py     # 上传落盘 → 入队 → 触发图/worker 重建索引
│   │   ├── conversation_service.py # 会话 CRUD + 记忆读写（走 Repository）
│   │   ├── share_service.py        # 创建/读取分享链接（Redis）
│   │   ├── profile_service.py      # 画像 + 长期记忆
│   │   └── ingest_worker.py        # Redis Stream 消费：解析 + 向量化
│   │
│   ├── graph/                      # LangGraph —— 核心业务编排
│   │   ├── state.py                # RAGState / AgentState TypedDict + reducer
│   │   ├── build.py                # build_qa_graph() / 子图装配
│   │   ├── checkpointer.py         # RedisSaver / MemorySaver 工厂 + 降级
│   │   ├── edges.py                # 条件边路由函数（route_by_intent 等）
│   │   ├── nodes/
│   │   │   ├── ingest.py           # 输入规范化、history/profile/语言指令装载
│   │   │   ├── classify.py         # 意图分类（包 intent_classifier）
│   │   │   ├── retrieve.py         # hybrid_search（含 query_rewriter / reranker 挂点）
│   │   │   ├── web_search.py       # Tavily 联网检索
│   │   │   ├── file_qa.py          # 按文件名过滤检索
│   │   │   ├── long_text.py        # 长文本摘要/翻译/问答
│   │   │   ├── build_prompt.py     # 组装最终 prompt
│   │   │   ├── generate.py         # LLM 生成（流式 writer）
│   │   │   ├── clarify.py          # 反问
│   │   │   └── persist.py          # 记忆写回 + 埋点
│   │   └── subgraphs/
│   │       └── agent.py            # 工具调用子图（ToolNode + tools_condition）
│   │
│   ├── rag/                        # 从 rag_chain.py 拆出的领域能力
│   │   ├── embeddings.py           # SimpleTFIDFEmbeddings / ModelScopeHTTPEmbeddings
│   │   ├── providers.py            # get_llm / get_query_llm / get_vision_llm / get_embeddings
│   │   ├── vectorstore.py          # get_vectorstore / add / remove / rebuild
│   │   ├── hybrid.py               # hybrid_search / format_docs / get_sources
│   │   ├── loaders.py              # load_documents / load_single_document / split_documents
│   │   └── prompts.py              # RAG_TEMPLATE / PROMPT
│   │
│   ├── agents/                     # 保留：分类器
│   │   ├── intent_classifier.py    # 原样保留
│   │   └── __init__.py
│   │
│   ├── tools/                      # 保留：工具抽象与注册中心
│   │   ├── base.py                 # BaseTool / ToolRegistry（原样）
│   │   ├── calculator.py · code_executor.py · knowledge_query.py · web_search.py
│   │   └── __init__.py
│   │
│   ├── repositories/
│   │   ├── sqlite.py               # SQLite 连接 + 建表（从 conversation_memory 拆出）
│   │   ├── conversation_repo.py    # conversations / conversation_messages
│   │   ├── profile_repo.py         # user_profiles
│   │   └── redis_repo.py           # 会话缓存 / 分享 / 限流 / 锁 / 检索缓存 的读写封装
│   │
│   ├── utils/
│   │   ├── text.py                 # _extract_text / _format_history / _lang_instruction
│   │   └── files.py                # 上传落盘、文件名校验、分块
│   │
│   ├── data/ · chroma_db/ · logs/  # 运行时目录（不变）
│   └── rag_chain.py                # 【过渡期保留】兼容层，内部转发到 graph
│
├── .env / .env.example
├── requirements.txt
├── Dockerfile
└── tests/
    ├── unit/                       # 节点级：纯函数、无网络
    ├── integration/                # 图级：MemorySaver + fake LLM
    └── conftest.py                 # redis/llm 夹具
```

---

## 2. FastAPI 层设计

### 2.1 配置管理：dataclass → pydantic-settings

保留现有 `config.py` 的**分组思路**，换上 pydantic 的**校验与类型能力**，并让配置成为可注入对象（而不是 import 时就固化的模块级常量）。

```python
# app/core/config.py
from functools import lru_cache
from typing import Literal
from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RedisSettings(BaseSettings):
    url: str = "redis://localhost:6379/0"
    max_connections: int = 50
    socket_timeout: float = 5.0
    key_prefix: str = "kbqa"

    # 各域 TTL（秒）——集中声明，见第 4 节
    ttl_session: int = 7 * 24 * 3600
    ttl_share_default: int = 24 * 3600
    ttl_retrieval_cache: int = 300
    ttl_embedding_cache: int = 24 * 3600
    ttl_profile_cache: int = 3600
    ttl_lock_default: int = 30
    ttl_ratelimit_default: int = 60


class ModelSettings(BaseSettings):
    backend: Literal["local", "ollama", "openai", "baidu_qianfan", "modelscope", "mock"] = "modelscope"
    embedding_model: str = ""
    llm_model: str = ""
    vision_model: str = ""
    modelscope_api_key: str = ""
    modelscope_base_url: str = "https://api-inference.modelscope.cn/v1"
    openai_api_key: str = ""
    qianfan_ak: str = ""
    qianfan_sk: str = ""
    tavily_api_key: str = ""

    @field_validator("modelscope_api_key")
    @classmethod
    def _require_ms_key(cls, v, info):
        if info.data.get("backend") == "modelscope" and not v:
            raise ValueError("MODEL_BACKEND=modelscope 时必须提供 MODELSCOPE_API_KEY")
        return v


class RetrievalSettings(BaseSettings):
    retriever_k: int = Field(3, ge=1)
    chunk_size: int = Field(500, ge=100)
    chunk_overlap: int = Field(50, ge=0)
    conversation_window_size: int = Field(5, ge=1)
    rrf_k: int = Field(60, ge=1)
    rrf_top_n: int = Field(5, ge=1)
    max_context_tokens: int = 3200

    @field_validator("chunk_overlap")
    @classmethod
    def _lt_chunk(cls, v, info):
        if v >= info.data.get("chunk_size", 500):
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        return v


class SecuritySettings(BaseSettings):
    jwt_secret_key: str = Field("change-me-in-production", min_length=16)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    admin_username: str
    admin_password: str


class PathSettings(BaseSettings): ...
class RateLimitSettings(BaseSettings): ...
class LogSettings(BaseSettings): ...
class GraphSettings(BaseSettings):
    """LangGraph 编排开关——灰度切换的关键"""
    use_langgraph: bool = True          # False 时回退到旧 router
    checkpointer: Literal["redis", "memory", "none"] = "redis"
    checkpoint_ttl_seconds: int = 14 * 24 * 3600
    max_agent_iterations: int = 5
    recursion_limit: int = 25


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8",
        extra="ignore", case_sensitive=False,
    )
    env: Literal["dev", "test", "prod"] = "dev"
    redis: RedisSettings = Field(default_factory=RedisSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    log: LogSettings = Field(default_factory=LogSettings)
    graph: GraphSettings = Field(default_factory=GraphSettings)
    cors_origins: list[str] = ["http://localhost:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v):
        return [o.strip() for o in v.split(",") if o.strip()] if isinstance(v, str) else v


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

**迁移期兼容**：在 `app/config.py` 里保留薄壳，把旧的模块级别名指向新 Settings，让尚未迁移的代码继续跑：

```python
# app/config.py  （过渡期保留，Phase 6 删除）
from app.core.config import get_settings
_s = get_settings()
MODEL_BACKEND = _s.model.backend
RETRIEVER_K = _s.retrieval.retriever_k
JWT_SECRET_KEY = _s.security.jwt_secret_key
# ... 其余别名保持签名不变
```

### 2.2 依赖注入

用 `Depends` + `app.state` 存放长生命周期对象（连接池、编译后的图）。**不要用模块级全局变量**——那是当前架构里 http 客户端、向量库单例、记忆字典散落各处、测试时无法替换的根因。

```python
# app/core/deps.py
from typing import Annotated, AsyncIterator
from fastapi import Depends, Request
from redis.asyncio import Redis
from app.core.config import Settings, get_settings
from app.core.security import get_current_active_user
from app.schemas.auth import User

SettingsDep = Annotated[Settings, Depends(get_settings)]

async def get_redis(request: Request) -> Redis:
    """连接池在 lifespan 中创建，挂载到 app.state。"""
    return request.app.state.redis

RedisDep = Annotated[Redis, Depends(get_redis)]

async def get_qa_graph(request: Request):
    """编译好的 StateGraph（含 checkpointer），在 lifespan 中构建一次。"""
    return request.app.state.qa_graph

QAGraphDep = Annotated[object, Depends(get_qa_graph)]

async def get_qa_service(graph: QAGraphDep, redis: RedisDep, settings: SettingsDep):
    from app.services.qa_service import QAService
    return QAService(graph=graph, redis=redis, settings=settings)

QAServiceDep = Annotated["QAService", Depends(get_qa_service)]
CurrentUser = Annotated[User, Depends(get_current_active_user)]
```


```python
# app/main.py —— 只剩组装，≈80 行
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.graph.build import build_qa_graph
from app.middleware.trace import TraceIDMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings)

    app.state.redis = Redis.from_url(
        settings.redis.url, max_connections=settings.redis.max_connections,
        decode_responses=True, socket_timeout=settings.redis.socket_timeout,
    )
    app.state.qa_graph = await build_qa_graph(settings, app.state.redis)

    # 后台预热（沿用现有 warmup 思路，但不再依赖模块级 _rag_chain）
    ...
    yield
    await app.state.redis.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="本地知识库问答系统", version="4.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(TraceIDMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
```

### 2.3 路由组织

```python
# app/api/v1/router.py
from fastapi import APIRouter
from app.api.v1 import auth, conversations, documents, feedback, profile, qa, share, system

api_router = APIRouter()
api_router.include_router(auth.router,        tags=["认证"])
api_router.include_router(qa.router,          tags=["问答"])
api_router.include_router(documents.router,   tags=["文档"])
api_router.include_router(conversations.router, tags=["对话"])
api_router.include_router(share.router,       tags=["分享"])
api_router.include_router(profile.router,     tags=["用户画像"])
api_router.include_router(feedback.router,    tags=["反馈"])
api_router.include_router(system.router,      tags=["系统"])
```

路由函数**只做三件事**：声明契约 → 调 Service → 返回 Schema。业务逻辑一律不在路由里。

```python
# app/api/v1/qa.py
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.core.deps import CurrentUser, QAServiceDep
from app.schemas.qa import QuestionRequest, QuestionResponse

router = APIRouter()

@router.post("/ask", response_model=QuestionResponse, summary="普通问答")
async def ask(req: QuestionRequest, user: CurrentUser, svc: QAServiceDep) -> QuestionResponse:
    return await svc.answer(question=req.question, conversation_id=req.conversation_id,
                            username=user.username, force_search=req.force_search)

@router.post("/ask/stream", summary="流式问答（SSE）")
async def ask_stream(req: QuestionRequest, user: CurrentUser, svc: QAServiceDep):
    return StreamingResponse(
        svc.stream_answer(question=req.question, conversation_id=req.conversation_id,
                          username=user.username, force_search=req.force_search),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
```

### 2.4 Pydantic 模型（请求/响应契约）

集中到 `app/schemas/`，**与 API 层解耦、与内部 State 解耦**。要点：

- `QuestionRequest.validate_question()` 里那种"在模型方法里抛 ValueError"的写法，改为 `field_validator` + `HTTPException` 映射，让 422 由 FastAPI 原生处理；
- SSE 事件用**联合类型 + discriminator** 显式建模，契约可被 OpenAPI 描述、可被前端生成 TS 类型。

```python
# app/schemas/qa.py
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field, field_validator
from app.schemas.common import SourceItem

FORBIDDEN = ("<script", "javascript:", "data:", "vbscript:")

class QuestionRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="问题内容")
    conversation_id: str | None = Field(None, description="对话 ID，用于记忆上下文")
    force_search: bool = Field(False, description="强制联网检索")

    @field_validator("question")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("问题不能为空")
        if any(p in v.lower() for p in FORBIDDEN):
            raise ValueError("问题包含非法内容")
        return v


class QuestionResponse(BaseModel):
    answer: str
    sources: list[SourceItem] = []
    data_source: str = "rag"
    trace_id: str = ""
    intent: str = ""
    intent_confidence: float = 0.0
    detected_language: str = "auto"
    needs_clarification: bool = False


# ---- SSE 事件契约（discriminated union）----
class SSEIntent(BaseModel):
    type: Literal["intent"] = "intent"
    content: dict
    trace_id: str = ""

class SSESources(BaseModel):
    type: Literal["sources"] = "sources"
    content: list[SourceItem]
    trace_id: str = ""

class SSEChunk(BaseModel):
    type: Literal["chunk"] = "chunk"
    content: str
    trace_id: str = ""

class SSEFinal(BaseModel):
    type: Literal["final"] = "final"
    content: QuestionResponse
    trace_id: str = ""

SSEEvent = Annotated[
    Union[SSEIntent, SSESources, SSEChunk, SSEFinal],
    Field(discriminator="type"),
]
```

> **契约稳定性**：`QuestionResponse` 字段名与 SSE 事件 `type`/`content` 结构**必须与 v3.0.0 完全一致**，前端零改动。这是硬约束，方案里所有触及输出的改动都以此为前提。

### 2.5 统一异常处理

定义**领域异常族**，路由与 Service 只抛领域异常，由处理器统一翻译成 HTTP 响应。

```python
# app/core/exceptions.py
from typing import Any

class AppError(Exception):
    """所有业务异常的基类。"""
    status_code: int = 500
    code: str = "internal_error"
    message: str = "服务内部错误"

    def __init__(self, message: str | None = None, *, detail: Any = None):
        self.message = message or self.__class__.message
        self.detail = detail
        super().__init__(self.message)


class ValidationError(AppError):        status_code, code = 400, "validation_error"
class NotFoundError(AppError):          status_code, code = 404, "not_found"
class PermissionDeniedError(AppError):  status_code, code = 403, "permission_denied"
class UnauthorizedError(AppError):      status_code, code = 401, "unauthorized"
class ConflictError(AppError):          status_code, code = 409, "conflict"
class RateLimitError(AppError):         status_code, code = 429, "rate_limited"
class UnsupportedFileTypeError(ValidationError): code = "unsupported_file_type"
class FileTooLargeError(ValidationError):        code = "file_too_large"
class DocumentParseError(AppError):     status_code, code = 422, "document_parse_failed"
class UpstreamLLMError(AppError):       status_code, code = 502, "upstream_llm_error"
class VectorStoreError(AppError):       status_code, code = 503, "vectorstore_unavailable"


def register_exception_handlers(app):
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from slowapi.errors import RateLimitExceeded
    from app.core.logging import get_trace_id

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        trace_id = get_trace_id(request)
        logger.warning(f"[{trace_id}] AppError {exc.code}: {exc.message}")
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"code": exc.code, "message": exc.message,
                                               "detail": exc.detail, "trace_id": trace_id}})

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {
            "code": "validation_error", "message": "请求参数校验失败",
            "detail": exc.errors(), "trace_id": get_trace_id(request)}})

    @app.exception_handler(RateLimitExceeded)
    async def _ratelimit(request: Request, exc: RateLimitExceeded):
        return JSONResponse(status_code=429, content={"error": {
            "code": "rate_limited", "message": "请求过于频繁，请稍后再试",
            "retry_after": getattr(exc, "retry_after", 60), "trace_id": get_trace_id(request)}})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": {
            "code": "http_error", "message": str(exc.detail), "trace_id": get_trace_id(request)}})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        trace_id = get_trace_id(request)
        logger.error(f"[{trace_id}] 未捕获异常: {exc}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": {
            "code": "internal_error", "message": "服务内部错误", "trace_id": trace_id}})
```

**SSE 特例**：流式响应一旦开始，HTTP 状态码已经发出（200），无法再改。因此 `stream_answer` 内部必须**把异常翻译成 `final` 事件**（沿用现有做法），而不是抛出去。这一点在 `QAService` 里显式处理。

---

## 3. LangGraph 状态图设计

### 3.1 为什么值得改（而非为用而用）

| 现有痛点                                  | LangGraph 如何解决                                |
| ------------------------------------- | --------------------------------------------- |
| `route()` 与 `route_stream()` 两份重复分发逻辑 | 单一图定义 + 两处调用（`invoke` / `astream`），**编排只写一次** |
| `_handle_*` 之间靠函数参数和返回值手工传递中间数据       | State 集中承载，节点按需读写，新增字段不动签名                    |
| 手写 5 轮工具循环（`function_calling.py`）     | `ToolNode` + `tools_condition` 条件边，标准循环       |
| 无中断/恢复能力（图片问答中途断线只能重来）                | Checkpointer 支持 `interrupt` / 断点续跑 / 时间旅行     |
| 无执行轨迹（排查"为什么走了 general 没走 kb_qa"要翻日志） | `astream(stream_mode="updates")` 天然给出逐节点轨迹    |

### 3.2 State 结构

**关键设计约束：State 必须可 JSON 序列化**（否则 checkpointer 存不进去）。现有代码里流动的是 LangChain `Document` 对象——**不能直接放进 State**，必须转成 `dict`。

```python
# app/graph/state.py
import operator
from typing import Annotated, Any, TypedDict


def merge_sources(left: list[dict], right: list[dict]) -> list[dict]:
    """sources 的去重合并 reducer：按 (file, content_preview) 去重。"""
    seen, out = set(), []
    for item in [*(left or []), *(right or [])]:
        key = (item.get("file"), item.get("content_preview"))
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


class RAGState(TypedDict, total=False):
    # ── 输入（ingest 节点写入，之后只读） ─────────────────────
    question: str
    conversation_id: str
    username: str
    force_search: bool
    image_data_url: str | None          # 图片问答时由 API 层注入

    # ── 装载的上下文（ingest 节点） ──────────────────────────
    history: list[dict]                 # [{role, content}]  取窗口后
    profile_ctx: str                    # 用户画像文本块
    lang_instruction: str               # 语言指令
    locked: bool                        # 会话是否锁定（影响窗口裁剪）

    # ── 分类结果（classify 节点） ────────────────────────────
    intent: str
    intent_confidence: float
    needs_clarification: bool
    detected_language: str

    # ── 检索产物（retrieve / web_search / file_qa 节点） ──────
    docs: list[dict]                    # ★ Document → dict，可序列化
    sources: Annotated[list[dict], merge_sources]
    search_context: str
    context: str                        # 融合后的最终参考资料

    # ── 生成产物（build_prompt / generate 节点） ─────────────
    prompt: str
    answer: str
    data_source: str

    # ── Agent 子图产物 ───────────────────────────────────────
    messages: Annotated[list[Any], operator.add]   # 消息累加（子图用）
    tool_calls: list[dict]
    iterations: int

    # ── 可观测性 ─────────────────────────────────────────────
    trace_id: str
    timings: dict[str, float]           # 节点名 → 耗时 ms
    error: str | None
    degraded: bool                      # 是否走了降级路径
```


**reducer 说明**（LangGraph 的并发合并语义，必须显式声明）：

- `sources` 用自定义 `merge_sources`——因为 `retrieve` 与 `web_search` 可能并行执行，两个分支都要往 sources 里写；
- `messages` 用 `operator.add` 做累加——这是 ReAct 循环的标准写法；
- `timings` 需要在节点里 merge（`{**state.get("timings", {}), "node": ms}`），或同样用 `operator.or_`；
- **其余字段默认"后写覆盖"**——这是 LangGraph 的默认行为，符合本流程的线性语义。

### 3.3 节点划分

每个节点是**纯函数 `(state) -> partial_update`**，不直接产出 SSE、不直接写数据库（除了 `persist`）。这样节点可单测、可组合。

| #  | 节点                 | 输入（读 State）                                                             | 输出（写 State）                                                       | 复用现有实现                                                                                     |
| -- | ------------------ | ----------------------------------------------------------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| 1  | `ingest`           | question, conversation_id, username, force_search                       | history, profile_ctx, lang_instruction, locked, trace_id          | `load_history` / `is_locked` / `get_profile` / `get_profile_context` / `_lang_instruction` |
| 2  | `classify_intent`  | question, history                                                       | intent, intent_confidence, needs_clarification, detected_language | `agents.intent_classifier.classify()` **原样**                                               |
| 3  | `clarify`          | intent_confidence, detected_language                                    | answer, data_source="clarification"                               | `build_clarification_message()`                                                            |
| 4  | `retrieve`         | question                                                                | docs, sources                                                     | `hybrid_search()` + `get_sources()`；预留 `query_rewriter` / `reranker` 挂点                    |
| 5  | `web_search`       | question, force_search                                                  | search_context, sources                                           | `tools/web_search.py` 的 `search()` / `build_search_context()` / `should_search()`          |
| 6  | `file_qa_retrieve` | question                                                                | docs, sources                                                     | `_extract_filename()` + `vs.similarity_search` 过滤逻辑（搬进 `nodes/file_qa.py`）                 |
| 7  | `long_text`        | question, lang_instruction                                              | answer, data_source                                               | `_handle_long_text()` 逻辑 + `document/long_text.py`                                         |
| 8  | `agent`（**子图**）    | question, history, lang_instruction                                     | answer, tool_calls, iterations                                    | `tools/` + `function_calling.py` 改造为 `ToolNode` 循环                                         |
| 9  | `build_prompt`     | intent, context, history, profile_ctx, lang_instruction, question       | prompt                                                            | `router.py` 里各 handler 的 prompt 拼接逻辑，**统一收敛到一处**                                           |
| 10 | `generate`         | prompt                                                                  | answer（流式产出 chunk）                                                | `chain._llm.stream()` / `_extract_text` 兜底                                                 |
| 11 | `persist`          | conversation_id, question, answer, locked, needs_clarification, timings | （外部副作用）                                                           | `save_context()` + `remember_turn()`                                                       |

> **节点粒度原则**：一个节点 = 一个可独立观测、可独立失败的步骤。不要把 `retrieve + web_search + build_prompt` 塞进一个节点——那样会退回 `_handle_kb_qa` 的黑盒状态。

### 3.4 边与条件边

```python
# app/graph/build.py（装配部分）
from langgraph.graph import StateGraph, START, END
from app.graph.state import RAGState
from app.graph.edges import route_by_intent, route_after_retrieve, should_use_agent_subgraph
from app.graph.nodes import (ingest, classify, clarify, retrieve, web, file_qa,
                             long_text, build_prompt, generate, persist)


def build_qa_graph(checkpointer=None, settings=None, deps=None):
    g = StateGraph(RAGState)

    g.add_node("ingest", ingest.make(deps))
    g.add_node("classify_intent", classify.make(deps))
    g.add_node("clarify", clarify.make(deps))
    g.add_node("retrieve", retrieve.make(deps))
    g.add_node("web_search", web.make(deps))
    g.add_node("file_qa_retrieve", file_qa.make(deps))
    g.add_node("long_text", long_text.make(deps))
    g.add_node("agent", build_agent_subgraph(deps))     # 子图
    g.add_node("build_prompt", build_prompt.make(deps))
    g.add_node("generate", generate.make(deps))
    g.add_node("persist", persist.make(deps))

    # ── 主干 ──
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "classify_intent")

    # ── 意图分发（唯一的编排决策点） ──
    g.add_conditional_edges("classify_intent", route_by_intent, {
        "clarify": "clarify",
        "long_text": "long_text",
        "file_qa_retrieve": "file_qa_retrieve",
        "agent": "agent",
        "retrieve": "retrieve",
        "build_prompt": "build_prompt",     # general / task 直达
    })

    # ── 检索后：视情况联网 ──
    g.add_conditional_edges("retrieve", route_after_retrieve, {
        "web_search": "web_search",
        "build_prompt": "build_prompt",
    })
    g.add_edge("web_search", "build_prompt")

    # ── 终端节点 ──
    g.add_edge("build_prompt", "generate")
    g.add_edge("generate", "persist")
    g.add_edge("clarify", "persist")        # persist 内部据 needs_clarification 跳过写回
    g.add_edge("long_text", "persist")
    g.add_edge("file_qa_retrieve", "build_prompt")
    g.add_edge("agent", "persist")
    g.add_edge("persist", END)

    return g.compile(checkpointer=checkpointer)
```

```python
# app/graph/edges.py
from app.agents.intent_classifier import (INTENT_AGENT, INTENT_CLARIFY, INTENT_FILE_QA,
                                          INTENT_GENERAL, INTENT_KB_QA, INTENT_LONG_TEXT,
                                          INTENT_TASK)
from app.tools.web_search import should_search

_TERMINAL_INTENTS = {INTENT_CLARIFY, INTENT_LONG_TEXT, INTENT_FILE_QA, INTENT_AGENT}


def route_by_intent(state: RAGState) -> str:
    """唯一的编排决策点：意图 → 下一个节点。"""
    intent = state.get("intent", INTENT_KB_QA)
    if intent == INTENT_CLARIFY:
        return "clarify"
    if intent == INTENT_LONG_TEXT:
        return "long_text"
    if intent == INTENT_FILE_QA:
        return "file_qa_retrieve"
    if intent == INTENT_AGENT:
        return "agent"
    if intent in (INTENT_GENERAL, INTENT_TASK):
        # 不需要知识库检索，但可能仍需联网（general + 实时类问题）
        return "retrieve" if state.get("force_search") or should_search(state["question"]) else "build_prompt"
    return "retrieve"   # kb_qa（默认）


def route_after_retrieve(state: RAGState) -> str:
    """检索完成：知识库为空但需要联网时，补一次 web 检索。"""
    needs_web = state.get("force_search") or should_search(state["question"])
    if needs_web and not state.get("search_context"):
        return "web_search"
    return "build_prompt"
```

**条件边的价值**：原来 `route_stream()` 里 `if not docs and not search_context: intent_res.intent = INTENT_GENERAL` 这种**运行时改写意图**的隐式跳转，在新设计里变成 `route_after_retrieve` 一个显式函数——可读、可测、可观测。

### 3.5 Agent 子图（工具调用循环）

`function_calling.py` 里手写的 for 循环 → 标准 ReAct 子图：

```python
# app/graph/subgraphs/agent.py
import json
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from app.graph.state import AgentState
from app.tools.base import get_registry


def build_agent_subgraph(deps):
    registry = get_registry()

    def call_model(state: AgentState):
        llm = deps.llm.bind_tools(registry.to_function_schemas())
        resp = llm.invoke([*state["messages"], ("human", "请基于以上工具调用结果给出最终回答。")])
        return {"messages": [resp]}

    def run_tool(state: AgentState):
        """把 LangGraph 的 ToolMessage 协议桥接到现有 ToolRegistry。"""
        last = state["messages"][-1]
        outputs = []
        for tc in (getattr(last, "tool_calls", None) or []):
            result = registry.execute(tc["name"], **(tc["args"] or {}))
            outputs.append({"tool": tc["name"], "arguments": tc["args"],
                            "result_preview": str(result)[:200],
                            "result": result})
        return {"tool_calls": state.get("tool_calls", []) + outputs,
                "iterations": state.get("iterations", 0) + 1,
                "messages": [ToolNode(_tool_fn(registry)).invoke(state)["messages"][-1]]}

    g = StateGraph(AgentState)
    g.add_node("model", call_model)
    g.add_node("tools", run_tool)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", tools_condition, {"tools": "tools", END: END})
    g.add_edge("tools", "model")
    return g.compile()
```

`AgentState` 复用 `RAGState` 的 `messages: Annotated[list, operator.add]` 字段，并以 `iterations` + `recursion_limit` 替代原来硬编码的 `_MAX_ITERATIONS = 5`。

**ReAct 回退保留**：`_supports_native_function_calling()` 为 False 时（local/mock/ollama 后端），仍走 `react_agent.py` 的文本解析模式——把它也做成子图内的条件分支，或保留为独立节点。这一层不要删，`modelscope` 之外的部署场景依赖它。

### 3.6 流式输出（保持 SSE 契约）

用 `stream_mode="custom"` + `get_stream_writer()`，让 `generate` 节点直接把 token 推给调用方，**事件结构完全对齐现有前端**：

```python
# app/graph/nodes/generate.py
from langgraph.config import get_stream_writer
from app.utils.text import extract_text


def make(deps):
    def generate(state: RAGState) -> dict:
        writer = get_stream_writer()
        full = ""
        try:
            for chunk in deps.llm.stream(state["prompt"]):
                text = extract_text(chunk)
                if not text:
                    continue
                full += text
                writer({"type": "chunk", "content": text})      # ← 与 v3 事件结构一致
        except Exception as e:
            # 沿用现有降级：stream 失败 → invoke
            ans = deps.llm.invoke(state["prompt"])
            full = extract_text(ans) or "（生成失败）"
            writer({"type": "chunk", "content": full})
        return {"answer": full}
    return generate
```


```python
# app/services/qa_service.py
async def stream_answer(self, *, question, conversation_id, username, force_search):
    cid = conversation_id or "default"
    state_in = {"question": question, "conversation_id": cid, "username": username,
                "force_search": force_search, "trace_id": current_trace_id()}
    config = {"configurable": {"thread_id": cid, "checkpoint_ns": username or "anon"},
              "recursion_limit": self.settings.graph.recursion_limit}
    try:
        # 逐节点轨迹 + 自定义流 双流合并
        async for mode, payload in self.graph.astream(
            state_in, config, stream_mode=["updates", "custom"]
        ):
            if mode == "custom":
                yield self._sse(payload)                      # chunk
            elif mode == "updates":
                node, upd = next(iter(payload.items()))
                for ev in self._events_for_node(node, upd):    # intent / sources / tool_calls
                    yield self._sse(ev)
        final_state = await self.graph.aget_state(config)
        yield self._sse({"type": "final", "content": self._to_response(final_state.values)})
    except Exception as e:
        yield self._sse({"type": "final", "content": self._error_response(e)})
```

关键点：`updates` 流给出**每个节点完成后的增量**，因此 `intent` / `sources` 事件可以在对应节点完成时精确发出，而不需要在代码里手工编排顺序——这正是把顺序正确性从"人肉保证"变成"图结构保证"。

### 3.7 Checkpointer：需不需要？怎么用？

**结论：需要，但要明确它管什么、不管什么。**

| 用途                                             | 用什么               | 说明                                                                                                      |
| ---------------------------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------- |
| 多轮对话的**消息历史**（前端要列表/重命名/归档/导出）                 | ❌ 不用 checkpointer | 这是**业务数据**，走 `conversation_messages` 表 + Redis 缓存。前端需要按 `conversation_id` 查询、排序、改标题，这些不是 checkpoint 的能力 |
| 图执行的**中间状态**（中断恢复、human-in-the-loop、时间旅行、断点续跑） | ✅ checkpointer    | 图片问答/Agent 长任务断线后可 `resume`；未来要做"工具调用前人工确认"必须靠它                                                         |
| 单次请求内的状态传递                                     | ❌ 不需要             | 内存中传递即可                                                                                                 |

**为什么最终还是开启 checkpointer：**

1. 图里的 `interrupt()` 依赖 checkpointer 才有意义——没有它，未来做"Agent 执行危险工具前需用户确认"就无从下手；
2. LangGraph 的 `aget_state(config)` 可在**请求结束后**读取状态，用于审计与埋点（比翻日志可靠）；
3. 长任务断线续跑（SSE 断开但图仍在跑）是知识库 + Agent 场景的真实需求。

**Redis 实现方式（两个方案，推荐 A）：**

**方案 A（推荐）：官方 `langgraph-checkpoint-redis`**

```bash
pip install langgraph-checkpoint-redis
```

```python
# app/graph/checkpointer.py
from langgraph.checkpoint.memory import InMemorySaver


async def make_checkpointer(settings, redis_client):
    mode = settings.graph.checkpointer
    if mode == "none":
        return None
    if mode == "memory":
        return InMemorySaver()
    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
        saver = AsyncRedisSaver(redis_client)
        await saver.asetup()          # 建 RediSearch 索引，必须显式调用
        return saver
    except Exception as e:
        # 降级：Redis 不可用时不阻塞服务启动
        import logging
        logging.getLogger(__name__).warning(f"RedisSaver 初始化失败，降级为内存模式: {e}")
        return InMemorySaver()
```

> **注意**：`AsyncRedisSaver` 依赖 **Redis Stack（RediSearch 模块）**。若使用官方 `redis:7-alpine` 镜像会缺少模块而初始化失败——因此 `docker-compose.yml` 里应使用 **`redis/redis-stack-server`** 镜像，且**必须调用 `asetup()`** 创建索引，否则首次写入会报索引不存在。降级到 `InMemorySaver` 是必要的安全垫。

**方案 B（备选，零额外模块依赖）：不接 checkpointer，状态自行持久化**

若不想引入 Redis Stack，可设 `GRAPH_CHECKPOINTER=none`，然后在 `persist` 节点里把关键状态写入普通 Redis 键（见第 4 节 `ckpt:` 命名）用于审计，放弃 `interrupt` 能力。**这是低配部署的合理选择，不是妥协——只要明确放弃了哪些能力。**

**thread_id 设计**：`thread_id = conversation_id`，`checkpoint_ns = username`。这样同一 `conversation_id` 天然形成一条可恢复的执行线，且不同用户的同名 ID 不互相污染。

**checkpoint 的 TTL**：checkpointer 自身不设 TTL（会破坏索引），因此在 `persist` 节点记录 `ckpt:last:{thread_id}` 时间戳，由定时任务清理超过 `checkpoint_ttl_seconds` 的线程——避免 Redis 无限增长。**这是必须补的一环，官方 saver 不会自动帮你清理。**

---

## 4. Redis 设计

### 4.1 职责边界（以及明确不做什么）

| 职责                   | 用 Redis？ | 说明                                                           |
| -------------------- | -------- | ------------------------------------------------------------ |
| 会话消息热缓存              | ✅        | 最近 N 轮消息，读多写多；真相源仍是 SQLite                                   |
| LangGraph checkpoint | ✅        | 图执行的中间态                                                      |
| 分布式限流                | ✅        | 替换 slowapi 的进程内计数，多 worker 下才准                               |
| 分享链接                 | ✅        | **完美替代磁盘 JSON + 手动 purge**，TTL 原生                            |
| 检索结果缓存               | ✅        | 相同 question + 参数 → 命中即省一次 hybrid_search                      |
| 向量/嵌入缓存              | ✅        | `embed(text)` 幂等，缓存收益高                                       |
| 用户画像缓存               | ✅        | 降低 SQLite 读压力                                                |
| 分布式锁                 | ✅        | 向量库重建、索引重建必须互斥                                               |
| 任务队列                 | ✅        | Redis Stream：文档解析/向量化异步化                                     |
| JWT 黑名单              | ✅        | 支持登出/强制失效                                                    |
| **向量数据本身**           | ❌        | 继续用 Chroma（Redis 做向量检索需 Redis Stack + 额外洗数据，不划算）             |
| **原始文档文件**           | ❌        | 继续用磁盘（或未来对象存储）                                               |
| **业务持久化真相源**         | ❌        | SQLite 保留为 truth；Redis 是缓存与运行时态。**Redis 挂了服务应能降级运行，而不是数据丢失** |

### 4.2 键命名规范

统一格式：**`{prefix}:{domain}:{entity}[:{id}][:{sub}]`**

- `prefix` 默认 `kbqa`（来自 `RedisSettings.key_prefix`），便于多环境共用实例时隔离；
- 全小写、冒号分隔、**禁止在键名里出现用户输入原文**（用 hash 或 id 替代）；
- 每个域有且仅有一处常量声明（`core/redis.py` 的 `RedisKeys`），**禁止在业务代码里手拼字符串**。

```python
# app/core/redis.py
import hashlib
from dataclasses import dataclass


def _h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class RedisKeys:
    prefix: str = "kbqa"

    def _k(self, *parts: str) -> str:
        return ":".join((self.prefix, *parts))

    # ── 会话运行时 ──
    def session_msgs(self, cid: str) -> str:       return self._k("session", cid, "msgs")        # List
    def session_meta(self, cid: str) -> str:       return self._k("session", cid, "meta")        # Hash
    def session_lock(self, cid: str) -> str:       return self._k("lock", "session", cid)        # String(NX)

    # ── 用户画像 ──
    def profile(self, user: str) -> str:           return self._k("profile", user)               # Hash

    # ── LangGraph checkpoint 辅助 ──
    def ckpt_last(self, thread_id: str) -> str:    return self._k("ckpt", "last", thread_id)     # String
    def ckpt_index(self) -> str:                   return self._k("ckpt", "index")               # ZSet(score=时间戳)

    # ── 缓存 ──
    def cache_retrieval(self, question: str, k: int) -> str:
        return self._k("cache", "retrieval", f"{_h(question)}:{k}")                              # String(JSON)
    def cache_embedding(self, text: str) -> str:   return self._k("cache", "embed", _h(text))    # String(bytes)
    def cache_prompt(self, key: str) -> str:       return self._k("cache", "prompt", _h(key))    # String

    # ── 分享链接 ──
    def share(self, token: str) -> str:            return self._k("share", token)                # Hash
    def share_user_index(self, user: str) -> str:  return self._k("share", "by_user", user)      # Set

    # ── 限流 ──
    def ratelimit(self, scope: str, identity: str, window: int) -> str:
        return self._k("rl", scope, identity, str(window))                                       # String(计数)

    # ── 分布式锁 ──
    def lock(self, resource: str) -> str:          return self._k("lock", resource)              # String(NX+PX)

    # ── 任务队列 ──
    def stream_ingest(self) -> str:                return self._k("stream", "ingest")            # Stream
    def stream_ingest_group(self) -> str:          return self._k("stream", "ingest", "workers") # ConsumerGroup

    # ── 认证 ──
    def jwt_blacklist(self, jti: str) -> str:      return self._k("jwt", "blacklist", jti)       # String


KEYS = RedisKeys()
```

**规范要点**：

- 会话 ID、token 本身就是不可猜的随机串，可直接入键；**用户提问原文绝不入键**（用 `sha1[:16]`）；
- 需要列举某个用户的分享链接 → 用 `share:by_user:{user}` Set 做二级索引（Redis 没有按模式前缀扫描的廉价方式，`SCAN` 在生产环境要避免）；
- `ckpt:index` 用 ZSet 记录活跃 thread，为 TTL 清理任务提供扫描入口。

### 4.3 TTL 策略

| 键模式                        | TTL                                | 理由                               |
| -------------------------- | ---------------------------------- | -------------------------------- |
| `session:{cid}:msgs`       | **7 天**（滑动续期）                      | 活跃会话热数据；冷数据从 SQLite 回源，回源后可丢掉缓存  |
| `session:{cid}:meta`       | 7 天                                | 同上                               |
| `profile:{user}`           | **1 小时**                           | 变更低频，写时主动失效（`DEL`）而非等过期          |
| `cache:retrieval:*`        | **300 秒**                          | 知识库可能刚更新；宁短勿脏。文档增删时**主动清空**该前缀   |
| `cache:embed:*`            | **24 小时**                          | 文本→向量是纯函数，长 TTL 安全               |
| `share:{token}`            | **= ttl_hours（1~336 小时）**          | 语义即"链接有效期"，与业务 TTL 一一对应          |
| `rl:{scope}:{id}:{window}` | **= window 秒**（固定窗口）               | 计数器生命周期即窗口                       |
| `lock:*`                   | **默认 30 秒 + 看门狗续期**                | 必须短于最长任务；重建向量库作业需 `PX` 放宽到 10 分钟 |
| `ckpt:last:{thread_id}`    | **14 天**                           | 与 `checkpoint_ttl_seconds` 一致    |
| `jwt:blacklist:{jti}`      | **= token 剩余有效期**                  | 过期即无意义，自动回收                      |
| Redis checkpoint 数据本体      | **无原生 TTL，由定时任务按 `ckpt:index` 清理** | 见 3.7 说明                         |

**策略原则**：

1. **所有键必须有 TTL**（唯一例外是 `share:by_user` 二级索引——它需要与 share 记录一致，在 share 记录过期时由清理任务同步移除）。
2. **缓存失效优先主动失效**，TTL 只作兜底。文档上传/删除后必须清 `cache:retrieval:*`，否则用户上传新文档后问问题拿到旧答案——这是最容易踩的坑。
3. TTL 常量集中在 `RedisSettings`，**不在业务代码里写魔法数字**。

### 4.4 与 LangGraph 状态持久化的衔接

两条链路**并行、各司其职**，在 `persist` 节点汇合：

```
┌─────────────── 请求期内 ───────────────┐      ┌──────── 请求期后 ────────┐
│  LangGraph 执行                          │      │                         │
│  State 在内存中流转                      │      │  checkpoint 落 Redis    │
│    ↓ 每个超级步（superstep）结束         │      │  （供 resume/审计）      │
│  checkpointer.put()  ←── 序列化 State ───┼──────┘                         │
│    ↓                                     │                                │
│  persist 节点：                          │      ┌──────────────────────┐  │
│    save_context(cid, q, a)  ─────────────┼──────▶  session:{cid}:msgs  │  │
│      → Redis List RPUSH + LTRIM          │      │  (业务消息，前端用)    │  │
│      → SQLite INSERT（真相源）            │      └──────────────────────┘  │
│    remember_turn(user, q) ───────────────┼──────▶  profile:{user} 缓存    │
└──────────────────────────────────────────┘                                │
```


**衔接的三个明确约定：**

1. **职责分离**：checkpoint 存"图执行到什么位置"，`session:{cid}:msgs` 存"用户看到了什么对话"。**两者不可互相替代**，也不要试图把 checkpoint 当消息表用——它的内部结构不是稳定契约。
2. **一致性顺序**：先写 Redis（快、可能失败但不致命）→ 再写 SQLite（真相源）。若 SQLite 写失败，Redis 中的消息会在 7 天后自然过期，不会造成永久不一致。反过来（先 SQLite 后 Redis）会导致缓存缺失但数据正确，也接受——**两种顺序都可接受，关键是不要在同一事务里假设强一致**。
3. **多 worker 安全**：`get_memory()` / `save_context()` 中所有对 `_memory_instances` 的访问在新架构中**全部删除**。窗口裁剪交给 `trim_messages`（按 `conversation_window_size`）在 `ingest` 节点完成：

```python
# app/graph/nodes/ingest.py（片段）
from langchain_core.messages import trim_messages
from app.repositories.redis_repo import RedisConversationRepo

async def make(deps):
    async def ingest(state: RAGState) -> dict:
        cid = state["conversation_id"]
        locked = await deps.conv_repo.is_locked(cid)
        msgs = await deps.redis_repo.get_messages(cid)          # 先 Redis
        if not msgs:
            msgs = await deps.conv_repo.load_messages(cid)      # 回源 SQLite
            await deps.redis_repo.set_messages(cid, msgs)       # 回填缓存
        window = 10 if locked else deps.settings.retrieval.conversation_window_size
        trimmed = trim_messages(msgs, max_tokens=None, token_counter=len,
                                strategy="last", include_system=False) if not locked else msgs
        return {
            "history": [{"role": m.type, "content": m.content} for m in trimmed],
            "locked": locked,
            "trace_id": state.get("trace_id") or new_trace_id(),
            **_profile_and_lang(deps, state.get("username")),
        }
    return ingest
```

> **`trim_messages` 的边界**：本项目窗口以"消息条数"（`k=5`）而非 token 数计。`trim_messages` 更适合按 token 裁剪；若要保持 `k` 条语义，直接切片 `msgs[-2*k:]` 更直接、语义更清晰。**不要为了用框架 API 而扭曲业务语义**——这里选切片。

### 4.5 Redis Stream 任务队列（文档摄取异步化）

现状：`/upload` 在请求线程里同步跑 `load_single_document` + `add_documents_to_store`，大文件会把请求挂住（已有 `_executor` 线程池，但仍在同一进程内）。

改造：入队 → worker 消费 → 回写状态。

```python
# 入队（DocumentService）
async def upload(self, file, username) -> UploadResult:
    path = await save_upload(file, self.settings.paths.data_path)   # 落盘
    job_id = uuid4().hex
    await self.redis.xadd(KEYS.stream_ingest(), {
        "job_id": job_id, "path": path, "filename": file.filename, "user": username,
    }, maxlen=10000)
    await self.redis.set(self._job_key(job_id), "queued", ex=86400)
    return UploadResult(job_id=job_id, filename=file.filename, status="queued")
```

```python
# app/services/ingest_worker.py（独立进程：python -m app.services.ingest_worker）
async def run_worker(consumer: str):
    redis = Redis.from_url(settings.redis.url, decode_responses=True)
    try:
        await redis.xgroup_create(KEYS.stream_ingest(), KEYS.stream_ingest_group(), id="0", mkstream=True)
    except ResponseError:      # BUSYGROUP
        pass
    while True:
        entries = await redis.xreadgroup(KEYS.stream_ingest_group(), consumer,
                                        {KEYS.stream_ingest(): ">"}, count=5, block=5000)
        for _stream, items in entries or []:
            for msg_id, fields in items:
                try:
                    await _ingest_one(fields)                       # 解析 + 分块 + 向量化
                    await redis.set(_job_key(fields["job_id"]), "done", ex=86400)
                    await redis.xack(KEYS.stream_ingest(), KEYS.stream_ingest_group(), msg_id)
                except Exception as e:
                    await redis.set(_job_key(fields["job_id"]), f"failed: {e}", ex=86400)
                    # 不 ack → 留在 pending，由 reclaim 逻辑重试
```

**但要保留同步语义的兼容**：前端当前是"上传成功 = 立即可检索"。因此 `DocumentService.upload` 应支持 `wait=True`（默认），内部入队后轮询 job 状态（带超时）——**接口语义不变，只是把耗时工作移出请求线程**。真正做到异步需要前端配合，属于可选优化，不是本次重构的必做项。

### 4.6 Redis 限流（替换 slowapi）

```python
# app/core/redis.py（片段）——固定窗口计数，Lua 保证原子
_RL_LUA = """
local cur = redis.call('INCR', KEYS[1])
if cur == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
local ttl = redis.call('TTL', KEYS[1])
return {cur, ttl}
"""

async def check_rate_limit(redis, *, scope: str, identity: str, limit: int, window: int):
    key = KEYS.ratelimit(scope, identity, window)
    cur, ttl = await redis.eval(_RL_LUA, 1, key, window)
    if int(cur) > limit:
        raise RateLimitError(f"请求过于频繁，请 {ttl} 秒后重试", detail={"retry_after": ttl})
```

作为依赖使用（比装饰器更符合 FastAPI 的 DI 风格，且能按 `username` 而非仅 IP 限流）：

```python
# app/api/v1/qa.py
from app.core.ratelimit import rate_limit
from functools import partial

@router.post("/ask")
async def ask(req: QuestionRequest, user: CurrentUser, svc: QAServiceDep,
              _=Depends(rate_limit("ask", limit=50, window=60))):
    ...
```

**收益**：`slowapi` 的计数在进程内存里，`--workers 4` 时实际放行 4 倍请求。换成 Redis 后限流才是真的。同时可把 `identity` 从 IP 升级为 `username`，避免同一 NAT 出口的用户互相挤占配额。

---

## 5. 关键模块职责与依赖关系

### 5.1 依赖矩阵（`→` 表示允许依赖）

| 模块                           | 允许依赖                                                    | 禁止依赖                                            |
| ---------------------------- | ------------------------------------------------------- | ----------------------------------------------- |
| `api/v1/*`                   | `services`, `schemas`, `core/deps`                      | `graph`, `rag`, `repositories`（**必须经 Service**） |
| `services/*`                 | `graph`, `repositories`, `rag`, `core/*`                | `fastapi`（**除 SSE 生成器类型外**）、`api`               |
| `graph/*`                    | `rag`, `tools`, `agents`, `repositories`, `core/config` | `fastapi`, `services`, `api`                    |
| `rag/*` `tools/*` `agents/*` | `core/config`                                           | 上层任何模块                                          |
| `repositories/*`             | `core/*`, `utils`                                       | `services`, `graph`, `api`                      |
| `core/*`                     | 只依赖三方库与 `utils`                                         | 一切业务模块                                          |

**验证手段**：加一条 CI 检查（或用 `import-linter`）：

```python
# tests/unit/test_layering.py
def test_no_upward_imports():
    """core / repositories 不得 import services 或 graph。"""
```

> 分层规则若无自动化检查，三个月后必然被破坏。**这条不能省。**

### 5.2 核心模块职责一句话版

- `core/config.py` — 唯一配置入口，pydantic 校验，可注入。
- `core/redis.py` — 连接池 + 键规范 + TTL 常量 + 限流/锁原语。**唯一允许拼 Redis 键的地方。**
- `core/exceptions.py` — 领域异常族 + 全局处理器，业务代码只抛领域异常。
- `core/deps.py` — 所有 `Depends` provider，测试时替换点。
- `api/v1/*` — 只做契约声明与调用转发，**零业务逻辑**。
- `schemas/*` — 对外契约的唯一真相源，与内部 State 解耦。
- `services/*` — 用例编排与事务边界，HTTP 无关。
- `graph/*` — 业务流程编排（LangGraph），`state.py` 是数据契约、`edges.py` 是决策点。
- `repositories/*` — 数据访问，SQLite 为真相源、Redis 为缓存层。
- `rag/*` — 检索/嵌入/加载等领域能力，**从 `rag_chain.py` 机械拆分，逻辑不变**。
- `tools/*` — 工具抽象（保持现状）。
- `agents/intent_classifier.py` — 分类器（保持现状）。

---

## 6. 迁移步骤（分 6 阶段，每阶段可运行、可回滚）

> **总原则：接口路径与响应结构在整个迁移过程中不变。** 前端零改动是硬约束，也是每阶段验收的依据。

### Phase 0 — 基础设施准备（半天）

1. `requirements.txt` 增加：
   ```
   redis>=5.2.0
   langgraph>=1.0.0            # 已装 1.2.10，显式声明
   langgraph-checkpoint>=4.0.0 # 已装 4.1.1
   langgraph-checkpoint-redis>=0.1.0   # 方案 A 需要
   pydantic-settings>=2.6.0    # 已装 2.14.2
   import-linter>=2.0          # 分层检查
   ```
2. `docker-compose.yml` 增加 Redis（**用 redis-stack 以支持 checkpoint 索引**）：
   ```yaml
   redis:
     image: redis/redis-stack-server:7.4.0-v0
     container_name: rag-redis
     ports: ["${REDIS_PORT:-6379}:6379"]
     volumes: [redis_data:/data]
     command: ["redis-stack-server", "--appendonly", "yes", "--maxmemory", "1gb",
               "--maxmemory-policy", "allkeys-lru"]
     healthcheck:
       test: ["CMD", "redis-cli", "ping"]
       interval: 10s
       timeout: 3s
       retries: 5
     networks: [rag-network]
   volumes:
     redis_data: { name: rag-redis-data }
   ```
   > `allkeys-lru` + `maxmemory` 是必须配置的：否则缓存会把 Redis 撑爆。但注意 **LRU 可能淘汰 checkpoint/锁**——若对 checkpoint 可靠性要求高，应改用 `noeviction` 并把缓存与状态拆到两个 DB（`/0` 缓存、`/1` 状态）。
3. `.env.example` 新增：
   ```env
   REDIS_URL=redis://localhost:6379/0
   REDIS_MAX_CONNECTIONS=50
   REDIS_KEY_PREFIX=kbqa
   GRAPH_USE_LANGGRAPH=true
   GRAPH_CHECKPOINTER=redis          # redis | memory | none
   GRAPH_CHECKPOINT_TTL_SECONDS=1209600
   GRAPH_MAX_AGENT_ITERATIONS=5
   GRAPH_RECURSION_LIMIT=25
   ```
4. 本地起 Redis：`docker run -d --name rag-redis -p 6379:6379 redis/redis-stack-server:7.4.0-v0`

**验收**：`redis-cli ping` → `PONG`；现有服务行为完全不变。

### Phase 1 — 基础设施层（`core/`，1 天）

1. 新建 `core/config.py`（pydantic-settings），保留 `app/config.py` 作为别名薄壳。
2. 新建 `core/logging.py`（搬 `TraceIDFilter` / `setup_logging`）、`core/exceptions.py`、`core/redis.py`、`core/security.py`（搬 JWT）、`core/deps.py`。
3. `main.py` 接入新 handler 与 lifespan 中的 Redis 连接池，**路由仍留在 main.py**。

**验收**：`/health` 返回中新增 `redis: {ok: true}`；既有接口全部回归通过（`pytest`）。

### Phase 2 — Schemas 与 Services 抽取（2 天）

1. 把 `main.py` 里的 Pydantic 模型搬到 `app/schemas/`（**字段名零改动**）。
2. 每个端点抽出 `QAService` / `DocumentService` / `ConversationService` / `ShareService` / `ProfileService`。此时 Service 内部仍调用旧的 `_get_chain()` / `memory.*`。
3. 把 `memory/conversation_memory.py` 拆为 `repositories/sqlite.py` + `repositories/conversation_repo.py`（SQL 语句原样搬运）。

**验收**：`main.py` 行数从 1483 降到 ~500；接口行为不变。

### Phase 3 — 路由拆分（1 天）

`main.py` 的端点搬进 `api/v1/*.py`，删除 `main.py` 中所有业务代码，只留 `create_app()`。

**验收**：`pytest tests/` 全绿；`/docs` 的 OpenAPI 路径集合与迁移前**逐条比对无差异**（用脚本抓 `/openapi.json` 做 diff）。这是最有价值的一次自动校验。

### Phase 4 — Redis 接入（2 天）

按收益排序，逐项替换，每项独立可回滚：

1. **分享链接**（最简单、收益明确）：磁盘 JSON → `share:{token}` Hash + TTL，删掉 `_purge_expired_shares()`。
2. **分布式限流**：slowapi → Redis Lua 计数，`identity` 从 IP 改为 `username`。
3. **会话消息缓存**：读写加缓存层，SQLite 仍是真相源。
4. **检索/嵌入缓存**：包在 `hybrid_search` 外；**文档增删时必须清 `cache:retrieval:*`**。
5. **分布式锁**：`rebuild_vectorstore` 加 `lock:vectorstore:rebuild`（NX + PX 10min），避免多 worker 同时重建。
6. **Redis Stream** 文档摄取（可选，第 4.5 节）。

**验收**：`--workers 4` 启动后，限流计数正确（并发压测验证）；分享链接在 TTL 后自动失效；会话在多 worker 间表现一致。

### Phase 5 — LangGraph 替换 hand-written router（3~4 天）

**这是核心阶段，务必灰度。**

1. 建 `graph/state.py` / `edges.py` / `nodes/*` / `build.py` / `checkpointer.py`。
2. 节点实现**优先搬运现有 `_handle_*` 函数体**，其次才重写——降低行为漂移风险。
3. 在 `RAGChain` 中保留 `route_invoke` / `route_stream` **签名不变**，内部按开关分流：

```python
def route_invoke(self, input_data, username="", conversation_id="default", force_search=False) -> dict:
    if not config.get_settings().graph.use_langgraph:
        return _legacy_route(self, ...)          # 旧路径，Phase 6 删除
    return run_graph_sync(self._graph, input_data, username, conversation_id, force_search)
```

1. **对照测试**：同一批问题分别跑旧 router 与新 graph，断言 `intent` / `data_source` / `sources` 结构一致、答案语义相近。建 `tests/integration/test_graph_parity.py`。
2. 灰度：`GRAPH_USE_LANGGRAPH=false` 一键回退。

**验收**：parity 测试通过；`astream(stream_mode="updates")` 能打印出完整节点轨迹；SSE 事件序列与 v3 一致（前端无需改动即可跑通）。

### Phase 6 — 清理与加固（2 天）

1. 删除 `rag_chain.py` 的 `RAGChain` 编排部分与 `agents/router.py` 的 `route` / `route_stream`；保留 `hybrid_search` 等能力（已迁至 `rag/`）。
2. 删除 `app/config.py` 别名薄壳。
3. 处置死代码检索器：`graph_rag` / `metadata_filter` / `multimodal_retriever` / `query_rewriter` / `reranker` —— **接入节点或删除，二选一，不要留**。（建议：`query_rewriter` 与 `reranker` 接入 `retrieve` 前后，其余删除。）
4. 加分层检查（`import-linter`）+ checkpoint 清理定时任务 + `/health` 增加 graph/checkpointer 探活。
5. 压测与文档更新（`README.md` / `CLAUDE.md` / `docs/API_REFERENCE.md`）。

**验收**：`main.py` < 150 行；无 `_memory_instances` / `_TOKEN_CACHE` 类进程内状态残留（`grep` 验证）；所有分层检查通过。

### 6.1 风险清单

| 风险                                           | 影响                    | 应对                                                               |
| -------------------------------------------- | --------------------- | ---------------------------------------------------------------- |
| **Document 对象进 State 导致序列化失败**               | checkpoint 写入报错，服务不可用 | State 里只放 `dict`（`page_content` + `metadata`），节点内按需重建 `Document` |
| **Redis Stack 未装 → `AsyncRedisSaver` 初始化失败** | 启动失败                  | 捕获异常降级 `InMemorySaver`（见 3.7），并打 warning                         |
| SSE 事件顺序变化                                   | 前端渲染错乱                | 用 `updates` 流按节点发事件；parity 测试断言事件序列                              |
| 缓存陈旧（上传新文档后仍返回旧检索结果）                         | 用户感知为"功能坏了"           | 文档增删时主动 `DEL cache:retrieval:*`（TTL 只是兜底）                        |
| 双实现并行期行为漂移                                   | 灰度期问题难复现              | 严格走服务层；`GRAPH_USE_LANGGRAPH` 是唯一开关，不做局部混合                        |
| `maxmemory-policy=allkeys-lru` 淘汰 checkpoint | 恢复失败                  | 状态与缓存分 DB；或 checkpoint 用 `noeviction` 实例                         |
| 迁移期间 `main.py` 与 `api/v1` 双份路由               | 改一处漏一处                | Phase 3 一次性搬完，不留半迁移状态                                            |
| 限流 identity 改为 username 后未登录接口无身份            | 限流失效                  | 分享等公开接口保留 IP 作为 identity                                         |

---

## 7. 本地开发环境启动与配置

### 7.1 依赖

```bash
cd backend
python -m venv venv
venv\Scripts\activate            # Windows
source venv/bin/activate         # Linux/macOS
pip install -r requirements.txt
```

> **Windows 中文路径注意**：启动务必用 `python -m uvicorn`，**不要**用 `venv\Scripts\uvicorn.exe`（命令行解析乱码）。沿用现有约定。

### 7.2 启动 Redis

```bash
# 方式一：Docker（推荐，带 RediSearch，支持 LangGraph checkpoint）
docker run -d --name rag-redis -p 6379:6379 \
  redis/redis-stack-server:7.4.0-v0 \
  redis-stack-server --appendonly yes --maxmemory 1gb --maxmemory-policy allkeys-lru

# 验证
docker exec rag-redis redis-cli ping          # → PONG
docker exec rag-redis redis-cli MODULE LIST   # → 应含 search（Redis Stack 才有）
```

```bash
# 方式二：docker compose（连后端一起）
docker compose up -d redis
```

> **关键**：若用普通 `redis:7-alpine`，`MODULE LIST` 无 `search`，`AsyncRedisSaver` 会初始化失败。此时应把 `.env` 中 `GRAPH_CHECKPOINTER` 设为 `none` 或 `memory`（降级可用，但失去中断恢复能力）。

### 7.3 环境变量

```bash
cp .env.example .env
```

在现有 `.env` 基础上**新增**（其余保持原样）：

```env
# ── Redis（新增） ──
REDIS_URL=redis://localhost:6379/0
REDIS_MAX_CONNECTIONS=50
REDIS_KEY_PREFIX=kbqa

# ── LangGraph（新增） ──
GRAPH_USE_LANGGRAPH=true
GRAPH_CHECKPOINTER=redis
GRAPH_CHECKPOINT_TTL_SECONDS=1209600
GRAPH_MAX_AGENT_ITERATIONS=5
GRAPH_RECURSION_LIMIT=25
```


````

**必须保留的现有变量**（`config.py` 中 `_require_env` 会强校验）：

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<你的密码>
MODEL_BACKEND=modelscope
MODELSCOPE_API_KEY=<你的 key>
MODELSCOPE_BASE_URL=https://api-inference.modelscope.cn/v1
VISION_MODEL=Qwen/Qwen3.5-35B-A3B
TAVILY_API_KEY=<可选>
JWT_SECRET_KEY=<生产务必替换，≥16 字符>
ACCESS_TOKEN_EXPIRE_MINUTES=1440
RATE_LIMIT_ENABLED=true
RATE_LIMIT_MAX_REQUESTS=60
RATE_LIMIT_WINDOW_SECONDS=60
CORS_ORIGINS=http://localhost,http://localhost:80,http://127.0.0.1,http://localhost:5173
RETRIEVER_K=3
CHUNK_SIZE=500
CHUNK_OVERLAP=50
CONVERSATION_WINDOW_SIZE=5
RRF_K=60
RRF_TOP_N=5

````

### 7.4 启动后端

```bash
cd backend
venv\Scripts\activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

多 worker 验证（Redis 改造的核心收益，Phase 4 后才有意义）：

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
```

**启动后自检清单**：

| 检查项          | 命令 / 地址                                         | 期望                                                        |
| ------------ | ----------------------------------------------- | --------------------------------------------------------- |
| API 文档       | <http://localhost:8000/docs>                    | 端点齐全                                                      |
| 健康检查         | <http://localhost:8000/health>                  | `status=ok`，且含 `redis.ok=true`、`graph.checkpointer=redis` |
| Redis 连通     | `docker exec rag-redis redis-cli KEYS "kbqa:*"` | 请求后能看到 `kbqa:session:*`                                   |
| LangGraph 轨迹 | 日志中搜 `node=`                                    | 出现 `ingest → classify_intent → retrieve → ...`            |
| 灰度开关         | 改 `GRAPH_USE_LANGGRAPH=false` 重启                | 行为退回旧 router，接口不变                                         |

### 7.5 启动文档摄取 worker（Phase 4 后，可选）

```bash
cd backend
python -m app.services.ingest_worker --consumer worker-1
```

### 7.6 启动前端

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173，Vite 代理 /api → 127.0.0.1:8000
```

> **注意 API 前缀变化**：新架构统一挂在 `/api/v1`。为保持前端零改动，有两种做法，**推荐第 1 种**：
>
> 1. **保持无版本前缀**：`app.include_router(api_router)` 不带 prefix，路径与 v3 完全一致（`/ask`、`/token`…）。版本化留给后续大版本。
> 2. 加 `/api/v1` 前缀，并在 Vite 代理与 nginx 中做 rewrite（`/api/*` → `/api/v1/*`），同时旧路径加 307 重定向过渡。
>
> 本方案采用做法 1，因为"契约不变、前端零改动"是本次重构的硬约束；`api/v1/` 只作为**代码组织目录**存在。

### 7.7 Docker 全量启动

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f backend
docker compose exec rag-redis redis-cli INFO memory | head
```

新增数据卷：`rag-redis-data`（AOF 持久化）。其余卷（`rag-backend-data` / `rag-backend-chroma` / `rag-backend-logs`）不变。

---

## 8. 落地顺序建议（一页速览）

| 阶段 | 内容                       | 产出                | 回滚方式                        |
| -- | ------------------------ | ----------------- | --------------------------- |
| P0 | 依赖 + Redis + 环境变量        | Redis 可用          | 停容器                         |
| P1 | `core/` 基础设施             | 配置/异常/日志/Redis/DI | 保留旧 `config.py`             |
| P2 | `schemas/` + `services/` | main.py 减半        | 逐端点回退                       |
| P3 | `api/v1/` 路由拆分           | main.py ≈80 行     | OpenAPI diff 校验             |
| P4 | Redis 六项接入               | 多 worker 可用       | 逐项开关                        |
| P5 | LangGraph 状态图            | 编排单一化             | `GRAPH_USE_LANGGRAPH=false` |
| P6 | 清理 + 分层检查 + 压测           | 无进程内状态残留          | tag 回退                      |

**最小可交付切片（若时间有限）**：P0 + P1 + P4 的"分享链接 + 限流"两项 —— 独立收益明确、风险最低、不触碰编排逻辑。  
**最高价值切片**：P5 —— 消除 `route()` / `route_stream()` 双份维护是本次重构最大的技术债偿还。

---

## 附录 A：新旧路径映射表

| v3 位置                                     | v4 位置                                                        | 处置                    |
| ----------------------------------------- | ------------------------------------------------------------ | --------------------- |
| `main.py` 全部端点                            | `api/v1/*.py`                                                | 搬运                    |
| `main.py` Pydantic 模型                     | `schemas/*.py`                                               | 搬运（字段不变）              |
| `main.py` auth / limiter / logging        | `core/security.py` / `core/ratelimit.py` / `core/logging.py` | 搬运+改造                 |
| `config.py`                               | `core/config.py`                                             | 重写为 pydantic-settings |
| `rag_chain.py::hybrid_search` 等           | `rag/hybrid.py`                                              | 原样搬运                  |
| `rag_chain.py::RAGChain.route_*`          | `graph/`                                                     | **替换**                |
| `agents/router.py::route/route_stream`    | `graph/edges.py` + `build.py`                                | **废弃**                |
| `agents/router.py::_handle_*`             | `graph/nodes/*.py`                                           | 搬运                    |
| `agents/intent_classifier.py`             | 原位保留                                                         | 不动                    |
| `agents/function_calling.py`              | `graph/subgraphs/agent.py`                                   | 改造为子图                 |
| `tools/*`                                 | 原位保留                                                         | 不动                    |
| `memory/conversation_memory.py`           | `repositories/conversation_repo.py` + `core/redis.py`        | 拆分                    |
| `memory/long_term.py` / `user_profile.py` | 原位保留 + Redis 缓存                                              | 增量                    |
| 分享链接磁盘 JSON                               | `services/share_service.py` + Redis                          | **替换**                |
| `retrievers/` 中 5 个死代码模块                  | 删除或接入节点                                                      | 二选一                   |

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

本地知识库问答系统 (Local Knowledge Base Q&A System) — 基于 RAG 的文档问答平台，Python/FastAPI 后端 + Vue 3 前端。支持文档上传、混合检索（BM25 + 向量检索 + RRF 融合）、意图分类路由、GraphRAG 多跳检索、Function Calling Agent、流式 SSE 响应、多轮对话记忆、图片问答、联网搜索、深色/浅色主题切换、分享链接等功能。

## 常用命令

### 后端 (Python 3.10+)

```bash
cd backend
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt

# 启动（务必使用 python -m uvicorn，中文路径下 uvicorn.exe 会乱码）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 多 worker（Redis 接入后才有意义：限流/锁/缓存跨进程共享）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4

# 测试（224 项：接口契约 / SSE 事件序列 / 分层依赖 / Redis 原语 / 状态图 / 真实 Redis）
python -m pytest tests/ -q
```

`tests/test_redis_live.py` 需要**真实 Redis**（无 Redis 时自动跳过 23 个用例）。
它覆盖进程内降级无法验证的语义：多 worker 共享限流/锁/会话缓存、Redis checkpoint 落库。

### Redis（v4 起为可选依赖）

```bash
docker compose up -d redis                      # 推荐：redis-stack（含 RediSearch）
# 或
docker run -d --name rag-redis -p 6379:6379 redis/redis-stack-server:7.4.0-v0

docker exec rag-redis redis-cli ping            # PONG
docker exec rag-redis redis-cli MODULE LIST     # 应含 search（Redis Stack 才有）
docker exec rag-redis redis-cli KEYS "kbqa:*"   # 观察键
```

> 未启动 Redis 时后端仍可运行：`RedisManager` 会降级为进程内 `fakeredis` 并打 warning。
> 开发环境方便，**生产环境必须设 `REDIS_FALLBACK_TO_MEMORY=false`**，否则故障时会静默退化为单机语义。

### 前端 (Node.js >= 22)

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173（Vite 代理 /api → localhost:8000）
npm run build      # 生产构建
npm run lint       # oxlint + eslint 全部检查
npm run format     # Prettier 格式化
npm run test:unit  # Vitest 单元测试
npm run test:e2e   # Playwright E2E 测试
```

### Docker

```bash
docker compose up -d --build   # 一键启动（前后端 + 命名卷）
docker compose down            # 停止
docker compose down -v         # 完全清理（含数据卷）
docker compose logs -f backend # 后端日志
docker compose logs -f frontend # 前端日志
```

## 架构概览

### 后端分层（v4：FastAPI + LangGraph + Redis）

```
api/v1/*  →  services/*  →  graph/*  ·  repositories/*
                                    ↓
                        rag/* · tools/* · agents/*  →  core/*
```

依赖方向只能向下，由 `tests/unit/test_layering.py` 静态检查强制。

| 层 | 目录 | 职责 |
|----|------|------|
| HTTP | `app/api/v1/` | 9 个路由模块，只做契约声明与转发 |
| 契约 | `app/schemas/` | 31 个 Pydantic 模型；**字段名与 v3 完全一致** |
| 用例 | `app/services/` | 10 个 Service，编排逻辑，不 import fastapi 请求对象 |
| 编排 | `app/graph/` | LangGraph 状态图：`state.py`(数据契约) `edges.py`(决策点) `nodes/` `subgraphs/` `checkpointer.py` `runner.py` |
| 能力 | `app/rag/` | 模型工厂(providers) / 嵌入(embeddings) / 加载(loaders) / 向量库(vectorstore) / 门面(chain) |
| 能力 | `app/tools/` `app/agents/` | 工具集；意图分类器 + 工具调用 Agent |
| 数据 | `app/repositories/` | 会话消息与画像的 Redis 热缓存（**不得依赖 services/graph/api**） |
| 记忆 | `app/memory/` | 对话记忆（SQLite 真相源）与用户画像 |
| 基建 | `app/core/` | `config.py`(pydantic-settings) `logging.py` `exceptions.py` `security.py` `redis.py` `deps.py` |
| 组装 | `app/main.py` | lifespan + 中间件 + 异常处理器 + 挂路由（215 行） |

**入口**：`app.main:app`。`app.state` 上挂载单例：`redis` / `redis_manager` / `rag_chain` / `qa_graph`。

**关键配置**：

| 变量 | 默认 | 说明 |
|------|------|------|
| `MODEL_BACKEND` | `zhipu` | 智谱 AI；可选 `local`/`ollama`/`openai`/`baidu_qianfan`/`modelscope`/`mock` |
| `ZHIPU_API_KEY` | 无 | 智谱 Key；`MODEL_BACKEND=zhipu` 时必填 |
| `LLM_CHOICES` | 内置候选 | 前端「模型切换」控件的候选清单（逗号分隔） |
| `MAX_TOKENS` | 空 | 单次回答最大输出 token；留空用 provider 默认（智谱约 1024） |
| `GRAPH_CHECKPOINTER` | `memory` | `redis` / `memory` / `none`；redis 需 Redis Stack |
| `GRAPH_MAX_AGENT_ITERATIONS` | `5` | Agent 工具调用轮次上限 |
| `GRAPH_RECURSION_LIMIT` | `25` | 图执行超级步上限 |
| `REDIS_FALLBACK_TO_MEMORY` | `true` | Redis 不可用时降级为进程内实现（**生产应设 false**） |
| `HEALTH_CHECK_LLM` | `false` | `/health` 是否做 LLM 探活（默认关闭，避免误报 degraded） |

> 没有 `GRAPH_USE_LANGGRAPH` 开关 —— 状态图是**唯一**编排实现，原过程式 router 已删除。

### 后端模块

**`app/rag/` —— RAG 领域能力（替代原 858 行的 `rag_chain.py`）：**

- `providers.py` — 模型工厂：`get_llm()` / `get_query_llm()` / `get_vision_llm()` / `get_embeddings()`，按 `MODEL_BACKEND` 分派，进程内单例
- `embeddings.py` — `OpenAICompatEmbeddings`（ModelScope/智谱通用）+ `CachedEmbeddings`（Redis 嵌入缓存）
- `loaders.py` — `load_documents()` / `load_single_document()` / `split_documents()`
- `vectorstore.py` — 向量库与 BM25 生命周期、`hybrid_search()`、`rebuild_vectorstore()`、`ensure_indexed()`、检索缓存
- `chain.py` — `RAGChain` 门面（**只做懒初始化 + 统计，不含编排**）
- `prompts.py` — 提示词模板

**`app/graph/` —— 业务流程编排：**

- `state.py` — `RAGState` / `AgentState` TypedDict + reducer；**State 必须可 JSON 序列化**
- `edges.py` — `route_by_intent` / `route_after_retrieve` / `route_after_file_qa`，唯一决策点
- `nodes/` — `prepare.py`(ingest, classify_intent) `retrieval.py`(retrieve, web_search, file_qa_retrieve) `respond.py`(long_text, clarify, build_prompt, generate) `persist.py` `agent.py`
- `subgraphs/` — `build_agent_subgraph()`：`model ⇄ tools` 标准 ReAct 循环
- `runner.py` — `run_graph()` / `stream_graph()`，把图结果翻译成 v3 响应契约

**其他：**

- **`agents/`** — `intent_classifier.py`（规则优先 + LLM 回退，长文本阈值 800 字符）、`function_calling.py` + `react_agent.py`（非原生工具调用后端的回退实现）

- **`tools/`** — Agent 工具集（统一 `BaseTool` 接口 → `ToolRegistry`）：
  - `web_search.py` — Tavily 联网搜索，`should_search()` 判断是否需要联网。
  - `calculator.py` — 数学计算。
  - `code_executor.py` — 代码执行。
  - `knowledge_query.py` — 知识库查询。

- **`memory/`** — 记忆系统：
  - `conversation_memory.py` — SQLite 持久化对话记忆（`conversations.db`）。支持窗口记忆（k=5）、锁定模式（阻止窗口裁剪）、元数据管理（标题/置顶/归档/所有者）。使用 `ConversationBufferWindowMemory`。
  - `long_term.py` — 长期记忆存储。
  - `user_profile.py` — 用户画像管理。

- **`retrievers/`** — 检索器集合：
  - `bm25_retriever.py` — BM25 关键词检索（单例，向量库重建时自动重建）。
  - `vector_retriever.py` — Chroma 向量相似度检索。
  - `rrf_fusion.py` — Reciprocal Rank Fusion（k=60，返回 top-5）。
  - `graph_rag.py` — GraphRAG：LLM 抽取实体/关系 → networkx 构建知识图谱 → N 跳邻居检索，与 hybrid_search 结果融合。
  - `metadata_filter.py` — 元数据过滤检索。
  - `multimodal_retriever.py` — 多模态检索。
  - `query_rewriter.py` — 查询改写。
  - `reranker.py` — 重排序。

- **`embeddings/`** — `SimpleTFIDFEmbeddings`（本地 TF-IDF，768 维）。
- **`document/`** — `parser.py`（多格式文档解析器）、`long_text.py`（长文本摘要/翻译/问答）。

### 前端 (`frontend/src/`)

单页应用，核心文件：

- **`App.vue`** — 根组件。仅包含 `<router-view>` + 深色/浅色主题切换（CSS 变量方案，`light`/`dark` class 切换，无外部 UI 库）。所有样式通过 Tailwind CSS v4 + `@theme` 自定义 design tokens 定义。
- **`views/Chat.vue`** — 主聊天页面（~3000+ 行单文件组件）。包含侧边栏（对话列表/搜索/批量管理/重命名/置顶/归档）、消息区（Markdown 渲染 marked + DOMPurify 防 XSS）、输入框（文字/图片上传预览/联网搜索开关/发送/停止）、SSE 流式消费（`fetch` + `ReadableStream` reader，非 EventSource，因为需要 POST + Authorization header）、对话持久化（localStorage `chatData` 键）、临时对话（`temp-*` ID 发送首条消息后转为 `conv-*`）。
- **`views/Login.vue`** — 登录页面。JWT 获取后存入 localStorage（`token` + `tokenExpire`）。
- **`views/SharedView.vue`** — 分享对话查看页面（公开路由 `/shared/:token`，无需登录）。
- **`router/index.js`** — 路由守卫：未登录重定向 `/login`，已登录访问 `/login` 重定向 `/chat`，`/shared/:token` 为公开路由。

### 数据流

```
用户上传 → POST /upload → 文件存 backend/data/ → 增量更新 Chroma + BM25
用户提问 → POST /ask/stream → 意图分类 → router 分发
  → kb_qa: hybrid_search(BM25 + 向量 → RRF 融合) + GraphRAG（可选）→ top-k → prompt → LLM 流式生成
  → agent: Function Calling / ReAct → 工具调用循环 → 最终回答
  → general/task: 直接 LLM 回答
  → long_text: 针对粘贴长文本的摘要/翻译/问答
  → SSE chunks → 前端打字机效果渲染 → Markdown 渲染
```

### 关键配置项（可在 `.env` 或环境变量中覆盖）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MODEL_BACKEND` | `modelscope` | 模型后端类型 |
| `CHUNK_SIZE` | 500 | 文档分块大小 |
| `CHUNK_OVERLAP` | 50 | 分块重叠量 |
| `RETRIEVER_K` | 3 | 检索召回数量 |
| `CONVERSATION_WINDOW_SIZE` | 5 | 对话记忆窗口大小 |
| `RRF_K` | 60 | RRF 融合常数 |
| `RRF_TOP_N` | 5 | RRF 最终返回数量 |
| `JWT_SECRET_KEY` | 无（会警告） | 生产环境必须设置 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 1440 | Token 有效期 |
| `RATE_LIMIT_MAX_REQUESTS` | 60 | 全局限流（次/窗口） |
| `TAVILY_API_KEY` | 无 | 联网搜索 API 密钥（缺失时功能不可用） |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接串 |
| `REDIS_KEY_PREFIX` | `kbqa` | 键前缀，多环境共用实例时隔离 |
| `REDIS_FALLBACK_TO_MEMORY` | `true` | 不可用时降级为进程内实现（生产设 false） |
| `REDIS_TTL_SESSION` | 604800 | 会话消息缓存 TTL（秒，7 天） |
| `REDIS_TTL_RETRIEVAL_CACHE` | 300 | 检索结果缓存 TTL（文档增删时主动清空） |
| `MODEL_BACKEND` | `zhipu` | 模型后端类型（智谱） |
| `LLM_MODEL` / `VISION_MODEL` / `EMBEDDING_MODEL` | 按后端默认 | 覆盖默认模型 |
| `GRAPH_CHECKPOINTER` | `memory` | `redis` / `memory` / `none` |
| `GRAPH_CHECKPOINTER` | `memory` | `redis` / `memory` / `none` |
| `GRAPH_MAX_AGENT_ITERATIONS` | 5 | Agent 工具调用最大轮次 |
| `GRAPH_RECURSION_LIMIT` | 25 | 图执行超级步上限 |

## 注意事项

- 默认管理员账号：`admin` / `admin123`
- 首次启动自动加载 `backend/data/` 目录文档并创建向量库
- 切换模型后端时建议删除 `chroma_db/` 目录重新构建向量库
- 图片 OCR 需要安装 Tesseract OCR（Docker 镜像已包含中英文语言包）
- 图片问答依赖 ModelScope 多模态模型（Qwen3.5-35B-A3B），local/mock 模式回退至文本 LLM
- 前端 Vite 开发代理：`/api/*` → `http://127.0.0.1:8000`（Vite 配置中自动去掉 `/api` 前缀）
- 生产环境修改 `JWT_SECRET_KEY` 为安全随机字符串
- Windows 中文路径下启动后端必须使用 `python -m uvicorn`，不要直接用 `uvicorn.exe`

## v4 实现踩坑记录（勿重犯）

1. **pydantic-settings 不会根据类名推导环境变量前缀**。`RedisSettings.url` 默认读 `URL`
   而非 `REDIS_URL` —— 缺少 `env_prefix` 时**不报错**，静默用默认值。
   新增配置分组必须显式声明 `env_prefix`，并在 `tests/unit/test_config.py` 补一条绑定测试。
2. **LangGraph 的 `checkpoint_ns` 是子图命名空间**，传用户名会抛
   `ValueError: Subgraph admin not found`。用户维度要并入 `thread_id`。
3. **State 不能存 LangChain `Document` 对象**，否则 checkpointer 序列化失败。
   统一转 `{page_content, metadata}` 字典（`graph/state.py` 提供转换函数）。
4. **Python 会删除 `except ... as exc` 的变量**。在 except 块里定义 async generator
   并捕获 `exc`，会在生成器被消费时抛 `NameError` —— 必须先取出为普通局部变量。
5. **`/ask` 的校验错误必须返回 400 + `answer` 字段**，不能改用 pydantic 校验（会变 422，
   破坏前端契约）。故 `QuestionRequest.validate_question()` 保留为方法。
6. **`env_ignore_empty=True`** 意味着空环境变量**不覆盖** `.env` 文件的值
   （空值视为"未设置"）。测「缺失必填项」要直接对分组类用 `_env_file=None` 构造。
7. **换嵌入模型必须重建向量库**。TF-IDF 是 768 维、智谱 `embedding-3` 是 2048 维，
   维度不匹配时 Chroma 会报 `Collection expecting embedding with dimension of 768, got 2048`。
   调用 `POST /rebuild-vectorstore`（会清库 + 重新索引 `data/`）。
8. **`app/rag` 不得 import `app/graph`**（分层规则）。
   `Document ↔ dict` 转换器因此放在 `app/utils/documents.py`，两边共用。
9. **同步代码路径要缓存必须用 `sync_client`**。检索/嵌入/会话缓存位于 LangGraph 的
   同步节点内执行，拿不到事件循环。`RedisManager` 同时维护异步与同步两个客户端；
   同步客户端通过 `bind_sync_client()` 在 lifespan 中做进程内绑定。
10. **`AsyncRedisSaver` 的第一个位置参数是 `redis_url: str`**，不是客户端对象。
    传客户端会抛 `AttributeError: 'Redis' object has no attribute 'startswith'`，
    并被 `try/except` 吞掉后**静默降级为内存模式** —— 表现为"配了 redis checkpoint 却没生效"。
    也**不要**用 `redis_client=` 复用我们的连接池：我们的是 `decode_responses=True`，
    而 checkpoint 存 msgpack 二进制，字符串解码会破坏数据。
11. **Redis checkpoint 的键不带 `kbqa:` 前缀**（由 langgraph 库自行管理，
    实测有 `checkpoint*` / `checkpoint_write*` / `write_keys_zset`）。
    `KEYS "kbqa:*"` 看不到它们，按前缀清理也删不掉；`allkeys-lru` 还可能淘汰它们。
    对可靠性要求高时应把缓存与状态分到不同 DB。
12. **测试若连真实 Redis，务必注意两点**（fakeredis 掩盖了这两点）：
    * **共用同一个事件循环**：`redis.asyncio` 客户端绑定创建它时的循环，
      每个用例 `asyncio.run()` 会让第二个用例报 `Event loop is closed`；
    * **键按运行随机命名**：真实 Redis 持久化，上一轮的限流计数会让这一轮断言莫名失败。
13. **模型切换是「按请求」的，服务端无状态**。`model` 随请求传入，经
    `QuestionRequest.resolve_model()` 白名单校验（**不静默回退** —— 静默回退会让用户
    以为切成功了），再由 `rag.providers.get_llm(model)` 按模型名缓存实例。
    新增端点 `/models` 时须同步更新 `tests/data/openapi_v3_baseline.json`。
14. **排查「回答不全」先看 `finish_reason`**，不要先怀疑前端。
    `generate` 节点会打 `[生成完成]` / `[截断]` 日志，详见
    `docs/DIAGNOSIS_INCOMPLETE_ANSWER.md`。流式分片**不能 trim**（会吃掉词间空格）。
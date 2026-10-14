# 重构执行报告（v4）

> 对应方案：[`REFACTOR_FASTAPI_LANGGRAPH_REDIS.md`](./REFACTOR_FASTAPI_LANGGRAPH_REDIS.md)
> 执行时间：2026-10-09
> 状态：**P0–P6 全部完成，并已用真实 Redis 实例完成端到端验证**

> 为什么单独成文：方案文档在编辑过程中被外部进程反复回退（详见文末「已知异常」），
> 为保证结论可追溯，执行结果独立记录于此。

---

## 1. 阶段完成情况

| 阶段 | 内容 | 状态 | 产物 |
|------|------|------|------|
| P0 | 依赖 + 编排配置 | ✅ | `requirements.txt`、`docker-compose.yml`（redis-stack）、`.env` / `.env.example` |
| P1 | `core/` 基础设施 | ✅ | `config.py` `logging.py` `exceptions.py` `security.py` `redis.py` `deps.py` |
| P2 | `schemas/` + `services/` | ✅ | 31 个 Pydantic 模型；10 个 Service；`utils/` 工具层 |
| P3 | `api/v1/` 路由拆分 | ✅ | 9 个路由模块；`main.py` **1483 → 218 行** |
| P4 | Redis 接入 | ✅ 6/6 | 分享链接、分布式限流、会话消息缓存、检索缓存、嵌入缓存、重建分布式锁 |
| P5 | LangGraph 状态图 | ✅ | `graph/` 全部模块，11 节点 + 工具调用子图 |
| P6 | 清理与加固 | ✅ | 删除 9 个文件、移除 slowapi、拆分 `rag/` 包、分层检查 |

---

## 2. 交付后的模块结构

```
app/
├── main.py        218 行，纯组装
├── core/          7 文件   ~1830 行   配置 / 日志 / 异常 / 安全 / Redis / DI
├── api/          13 文件    ~830 行   9 个路由模块 + service 依赖装配
├── schemas/       8 文件    ~470 行   对外契约
├── services/     10 文件   ~1430 行   用例编排
├── graph/        13 文件   ~1510 行   LangGraph 状态图
├── rag/           7 文件   ~1200 行   模型工厂 / 嵌入 / 加载 / 向量库 / 门面
├── repositories/  2 文件    ~200 行   会话消息与画像的 Redis 热缓存
├── memory/        4 文件    ~850 行   SQLite 真相源 + 用户画像
├── agents/        4 文件    ~670 行   意图分类器 + 工具调用 Agent
├── retrievers/    4 文件    ~270 行   BM25 / 向量 / RRF
└── tools/ utils/ document/ embeddings/
```

依赖方向只能向下，由 `tests/unit/test_layering.py` 静态 AST 检查强制：

```
api/v1/*  →  services/*  →  graph/* · repositories/*
                                  ↓
              rag/* · tools/* · agents/* · memory/*  →  core/*
```

---

## 3. 删除的文件（P6 清理）

| 文件 | 原因 |
|------|------|
| `app/rag_chain.py`（858 行） | 拆分为 `app/rag/` 包；编排逻辑迁入 `app/graph/` |
| `app/config.py` | 兼容外壳，引用已全部迁移到 `app/core/config.py` |
| `app/agents/router.py`（611 行） | 过程式 if/elif 分发，被状态图取代 |
| `app/agents/plan_execute.py` | 从未被接线的死代码 |
| `app/retrievers/graph_rag.py` | 死代码（外部引用 0） |
| `app/retrievers/metadata_filter.py` | 死代码 |
| `app/retrievers/multimodal_retriever.py` | 死代码 |
| `app/retrievers/query_rewriter.py` | 死代码 |
| `app/retrievers/reranker.py` | 死代码 |
| `test_write.txt` | 遗留空文件 |

同时移除 `slowapi` 依赖（限流完全由 Redis 承载），
并删除 `GRAPH_USE_LANGGRAPH` 灰度开关 —— 旧实现已删除，保留一个指向不存在代码的开关
只会误导人；现在状态图是唯一编排实现，构建失败即启动失败。

**检索器现状**：`__init__.py` + `bm25_retriever.py` + `vector_retriever.py` + `rrf_fusion.py`，
三者均被 `app/rag/vectorstore.py` 实际引用。

---

## 4. 模型后端：智谱 AI

`MODEL_BACKEND=zhipu`，走 OpenAI 兼容协议（复用 `ChatOpenAI`，只替换 `api_key` + `base_url`）：

| 用途 | 模型 | 说明 |
|------|------|------|
| LLM | `glm-4-flash` | 快、成本低；可换 `glm-4-air` / `glm-4-plus` |
| Vision | `glm-4v-flash` | 图片问答；更强可选 `glm-4v-plus` |
| Embedding | `embedding-3` | **2048 维** |

> ⚠️ **embedding-3 是 2048 维，与 TF-IDF 的 768 维不兼容** ——
> 切换嵌入模型后**必须重建向量库**，否则检索报维度错误。
> 本次已重建：7 文档 → 1318 分块。

---

## 5. 验证结果

| 验证项 | 结果 |
|--------|------|
| 接口契约（OpenAPI diff） | **29 路径 / 32 操作 零差异**（基线 `tests/data/openapi_v3_baseline.json`） |
| 测试套件 | **224 passed**（原 50 → 224） |
| 真实 uvicorn 启动 | `status=ok`，图 11 节点，Chroma 1318 分块 |
| 知识库问答 | `/ask` 真实检索 + 智谱生成，来源正确标注 |
| SSE 事件序列 | `intent → sources → chunk×305 → final`，与 v3 逐事件一致 |
| 图片问答 | `glm-4v-flash` 正确描述图片内容 |
| Redis 宕机降级 | 停掉容器后 `mode=memory`，问答完全可用 |
| 严格模式 | `REDIS_FALLBACK_TO_MEMORY=false` + 不可达 → 启动即失败（fail fast） |

### 5.1 Redis 真实实例验证（Redis Stack 7.4.0）

新增 `tests/test_redis_live.py`（23 个用例，无 Redis 时自动跳过），
并把 `tests/unit/test_core_redis.py` 改为真实环境优先。

| 项 | 实测结果 |
|----|---------|
| 连接 | `mode=redis`，版本 7.4.0，异步与同步客户端共用同一实例 |
| **多 worker 限流**（核心收益） | `--workers 2` 下 `/token`（5/分钟）**精确放行 5 次后 429**，Redis 计数 9 —— 进程内计数会放行最多 10 次 |
| 分布式锁 | 两个独立连接互斥；锁 TTL 正常；退出即释放 |
| 会话消息缓存 | 跨 worker 可见；缓存清空后可从 SQLite 完整恢复；TTL 604785s |
| 画像缓存 | 读写穿透；更新后不残留旧值 |
| 分享链接 | 名义 TTL + 1h 宽限（用于区分 404 与 410）；过期正确返回 410 |
| 检索/嵌入缓存 | 命中不再调模型；按前缀批量失效 |
| **Redis checkpoint** | `GRAPH_CHECKPOINTER=redis` 下 checkpoint 真实落库；thread 之间隔离 |

**运维注意**：LangGraph 的 checkpoint 键由库自行管理，**不带 `kbqa:` 前缀**
（实测 562 个 `checkpoint*` / `write_keys_zset` 键）。
因此 `KEYS "kbqa:*"` 看不到它们，按前缀清理也不会删掉它们 ——
而 `allkeys-lru` 淘汰策略有可能淘汰 checkpoint 数据。
对可靠性要求高时，应把缓存与状态分到不同 DB（`REDIS_URL` 的 `/0` 与 `/1`）。

---

## 6. 重构过程中发现并修复的真实缺陷

### 6.1 配置静默失效（最严重）

pydantic-settings **不按类名推导环境变量前缀**。
`RedisSettings.url` 读的是 `URL` 而非 `REDIS_URL`，缺 `env_prefix` 时**不报错、静默用默认值**。
`REDIS_*` / `GRAPH_*` / `LOG_*` / `RATE_LIMIT_*` 全部中招，因默认值恰好等于 `.env` 而长期未暴露。

修法：显式 `env_prefix` + `AliasChoices`，并为每个分组补环境变量绑定测试。

### 6.2 `load_documents()` 定义了却从无调用点

后果：`/rebuild-vectorstore` **只清空索引、从不重建** ——
用户点一下"重建"，知识库反而变空；而 README 宣称的"首次启动自动加载文档"从未实现。

修法：`rebuild_vectorstore(reindex=True)` 真正重建；新增 `ensure_indexed()` 启动时按需索引。

### 6.3 `AsyncRedisSaver` 构造参数错误（仅真实 Redis 可暴露）

`AsyncRedisSaver` 的第一个位置参数是 `redis_url: str`。
原先写成 `AsyncRedisSaver(redis_client)`，真实 Redis 下抛
`AttributeError: 'Redis' object has no attribute 'startswith'`，
被 `try/except` 捕获后**静默降级为内存模式** ——
即"配了 Redis checkpoint，实际根本没生效"。

修法：改用 `redis_url=settings.redis.url`。
同时**不**复用我们的连接池（我们的客户端是 `decode_responses=True`，
而 checkpoint 存的是 msgpack 二进制，字符串解码会破坏数据）。

### 6.4 Python 会删除 `except ... as exc` 的变量

`/ask/stream` 在 except 块里定义 async generator 并捕获 `exc`，
生成器延迟消费时抛 `NameError: cannot access free variable 'exc'`。
只在响应头已发出、真正读流时才炸 —— 由 SSE 契约测试抓到。

### 6.5 状态图 `checkpoint_ns` 不能传用户名

LangGraph 把 `checkpoint_ns` 解释为**子图命名空间**，
传 `"admin"` 直接报 `ValueError: Subgraph admin not found`。
用户维度必须并入 `thread_id`。

### 6.6 其余

| 缺陷 | 修复 |
|------|------|
| 会话记忆用进程内 dict，多 worker 下历史分裂 | 改为 Redis 缓存 + SQLite 真相源 |
| 分享链接创建不校验归属，可越权读他人对话 | 新增归属校验（403） |
| `/rebuild-vectorstore` 在请求线程内同步执行，阻塞事件循环 | 改线程池 + 分布式锁 |
| `VectorRetriever` 缺 `Optional` 导入（局部注解未求值而侥幸不报错） | 补齐导入 |
| 健康检查每次都请求 LLM 探活，且因裸 `"ping"` 被服务商拒绝而误报 `degraded` | 改为 `HEALTH_CHECK_LLM` 显式开启，默认关闭 |

---

## 7. 测试工程上的两个真实环境专属陷阱

这两个坑只在连**真实 Redis** 时出现，用 fakeredis 永远测不出来：

1. **事件循环绑定**：`redis.asyncio` 客户端绑定在创建它的事件循环上。
   若每个用例 `asyncio.run()`（每次新建并关闭循环），第二个用例即报
   `RuntimeError: Event loop is closed`。→ 整个测试模块共用同一个持久循环。

2. **真实 Redis 数据跨运行残留**：上一轮测试遗留的限流计数让这一轮断言莫名失败
   （`[True,True,True,True,False]` 而非 `[True,True,True,False,False]`）。
   fakeredis 进程结束即清空，掩盖了这个问题。→ 所有键按运行随机命名（uuid 后缀）。

---

## 8. 未完成项

| 项目 | 说明 |
|------|------|
| Redis Stream 文档摄取 | 方案 §4.5 的异步化优化，需前端配合"上传即返回 job"语义，不在 P4 六项之内 |
| Tesseract OCR | 未安装，图片类文档解析为 0 页（`data/微信图片_*.jpg`） |
| 缓存与状态分库 | 当前同处 `db 0`，且淘汰策略为 `allkeys-lru`，理论上可能淘汰 checkpoint |
| `repositories/` 仅覆盖缓存 | SQLite 读写仍在 `app/memory/`，未进一步拆到 repository 层 |

---

## 9. 已知异常

`docs/REFACTOR_FASTAPI_LANGGRAPH_REDIS.md` 在编辑过程中**被外部进程回退了两次**：
写入并校验成功后（1662 行，含「执行结果」章节），再次读取时又退回 1565 行的原始版本。
同一时间 `README.md` / `CLAUDE.md` 的改动均正常保留，故排除编辑器/格式化器的普遍性行为。

**建议**：若该文档正在编辑器中打开，请先关闭再重新应用改动 —— 可能是编辑器的陈旧缓冲区回写覆盖。

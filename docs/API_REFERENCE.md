# 本地知识库问答系统 — 技术文档

## 系统架构

```
backend/
├── app/
│   ├── main.py              # FastAPI 主入口，API 端点定义
│   ├── config.py             # 环境变量配置管理 (AppConfig)
│   ├── rag_chain.py          # RAG 核心链路 (嵌入/检索/生成)
│   ├── agents/
│   │   ├── intent_classifier.py  # 意图分类 (规则优先 + LLM 兜底)
│   │   └── router.py              # 路由分发 (kb_qa/general/file_qa/...)
│   ├── retrievers/
│   │   ├── rrf_fusion.py     # RRF 排序融合 (内容哈希去重)
│   │   ├── bm25_retriever.py # BM25 关键词检索 (增量索引)
│   │   └── vector_retriever.py # Chroma 向量检索
│   ├── embeddings/
│   │   └── tfidf_embeddings.py # TF-IDF 本地嵌入 (词表复用)
│   ├── memory/
│   │   ├── conversation_memory.py # 对话记忆 (SQLite 持久化 + 窗口缓存)
│   │   └── user_profile.py        # 用户画像 (事实衰减 + 去重)
│   ├── tools/
│   │   └── web_search.py     # Tavily 联网搜索 (异步 + 缓存 + 智能触发)
│   └── document/
│       └── parser.py         # 多格式文档加载 (PDF/DOCX/PPTX/Excel/CSV)
├── tests/
│   ├── test_modules.py       # 单元测试 (27 项)
│   └── test_api.py           # 集成测试 (API 端点契约验证)
└── requirements.txt
```

## API 端点清单

### 认证

| 方法 | 路径 | 认证 | 速率限制 | 说明 |
|------|------|------|----------|------|
| POST | `/token` | 无 | 5/min | OAuth2 密码模式获取 JWT |
| GET  | `/users/me/` | Bearer | - | 获取当前用户信息 |

### 问答

| 方法 | 路径 | 认证 | 速率限制 | 说明 |
|------|------|------|----------|------|
| POST | `/ask` | Bearer | 30/min | 同步问答 (意图路由 + RAG) |
| POST | `/ask/stream` | Bearer | 30/min | SSE 流式问答 |
| POST | `/ask/image` | Bearer | 20/min | 图片视觉问答 (多模态) |

### Agent 交互接口

| 方法 | 路径 | 认证 | 速率限制 | 说明 |
|------|------|------|----------|------|
| GET  | `/agent/intents` | Bearer | - | 获取系统支持的所有意图类型 |
| POST | `/agent/classify` | Bearer | 60/min | 对问题进行意图分类 (不执行回答) |
| GET  | `/agent/capabilities` | Bearer | - | 获取系统可用工具和能力列表 |

### 文档管理

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/upload` | Bearer | 上传单个文件 (去重入库) |
| POST | `/upload-multiple` | Bearer | 批量上传文件 |
| GET  | `/documents` | Bearer | 分页查询文档列表 (skip/limit) |
| DELETE | `/documents/{file_name}` | Bearer | 删除指定文档 |
| POST | `/rebuild-vectorstore` | Bearer | 全量重建向量库 |

### 对话管理

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET  | `/conversations` | Bearer | 分页查询对话列表 (skip/limit/search) |
| GET  | `/conversations/{id}` | Bearer | 获取对话详情 (含历史消息) |
| PUT  | `/conversations/{id}` | Bearer | 更新对话 (title/pinned/locked/archived) |
| DELETE | `/conversations/{id}` | Bearer | 删除对话 |
| POST | `/conversations/batch-delete` | Bearer | 批量删除对话 |
| POST | `/conversations/{id}/export` | Bearer | 导出对话 |
| POST | `/conversations/{id}/clear` | Bearer | 清空对话记忆 |
| POST | `/conversations/{id}/share` | Bearer | 创建分享链接 |

### 反馈

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/feedback` | Bearer | 提交反馈 |
| GET  | `/feedback/stats` | Bearer | 聚合统计反馈数据 (badcase 分析) |
| GET  | `/feedback/list` | Bearer | 分页查询反馈列表 (支持评分过滤) |

### 用户画像

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET  | `/profile` | Bearer | 获取当前用户画像 |
| PUT  | `/profile` | Bearer | 更新用户画像 (事实去重 + 衰减) |

### 系统状态

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET  | `/health` | 无 | 依赖健康检查 (Chroma/SQLite/LLM) |
| GET  | `/stats` | Bearer | 系统统计信息 |

### 分享 (公开)

| 方法 | 路径 | 认证 | 速率限制 | 说明 |
|------|------|------|----------|------|
| GET  | `/share/{token}` | 无 | 30/min | 公开访问分享的对话 |

## 核心模块说明

### RRF 融合 (`rrf_fusion.py`)

使用基于文档内容 MD5 哈希的签名 (`source|len(content)|content[:200]`) 替代 `id(doc)` 作为跨检索器去重标识。BM25 和向量检索的同一文档会被正确合并，得分累加。

### 对话持久化 (`conversation_memory.py`)

- **元数据**: 存储在 `conversations` 表 (SQLite)
- **消息历史**: 存储在 `conversation_messages` 表，服务重启后可从 DB 恢复
- **内存缓存**: `ConversationBufferWindowMemory` 作为一级缓存，DB 为二级持久化
- **连接复用**: 单例 `sqlite3.Connection` (check_same_thread=False, timeout=30) + RLock

### BM25 增量索引 (`bm25_retriever.py`)

`add_documents` 使用 `base_idx` 计算偏移量，只对新文档更新 `_doc_id_to_index` 映射，避免全量重建映射表。索引持久化到 `bm25_index.pkl`。

### TF-IDF 词表复用 (`tfidf_embeddings.py`)

`embed_documents` 在已就绪时通过集合判重，只对真正的新文本调用 `_build_vocab`，避免词表漂移导致存量向量失效。词表持久化到 `tfidf_vocab.pkl`。

### 智能搜索触发 (`web_search.py`)

`should_search` 基于四类触发词判断：
- **STRONG_TRIGGERS**: 搜索/搜一下/查一下/百度/谷歌
- **TIME_TRIGGERS**: 最新/最近/今天/新闻/2025
- **FACT_TRIGGERS**: 天气/股价/汇率/比分
- **QUESTION_STARTERS**: 短问题以"什么是/如何"开头

默认返回 `False`，仅命中触发词时返回 `True`。

### ModelScope 嵌入 (`rag_chain.py`)

`ModelScopeHTTPEmbeddings` 为模块级单例类：
- `requests.Session` 连接池复用
- `BATCH_SIZE=32` 批量嵌入请求
- 双重检查锁定 (DCL) 单例模式

### 文档去重 (`rag_chain.py`)

`add_documents_to_store` 在写入前按 `source` 文件名批量删除向量库和 BM25 中的旧 chunk，防止同名文件重复入库。

## 安全机制

1. **JWT 认证**: 所有端点 (除 `/token`、`/health`、`/share/{token}`) 均需 Bearer Token
2. **对话越权防护**: `verify_owner()` 检查对话所有者，非所有者返回 403
3. **速率限制**: `slowapi` 按 IP 限流，敏感端点独立配置限额
4. **分享链接限流**: `/share/{token}` 限制 30/min，防止暴力遍历
5. **文件上传校验**: 扩展名白名单 + 大小限制 (50MB) + 路径穿越防护
6. **输入验证**: `QuestionRequest.validate_question()` 过滤 XSS 和超长输入

## 测试

```bash
# 单元测试
cd backend
python -m pytest tests/test_modules.py -v

# 集成测试
python -m pytest tests/test_api.py -v

# 全部测试
python -m pytest tests/ -v --tb=short
```

### 测试覆盖

| 模块 | 测试类 | 测试数 | 覆盖点 |
|------|--------|--------|--------|
| rrf_fusion | TestRRFFusion | 5 | 签名稳定性、去重、排序融合、排名加权 |
| web_search | TestShouldSearch | 5 | 空输入、时间/事实/强触发词、正常问题不触发 |
| router | TestConfigTopK | 3 | max 返回、大值/小值边界 |
| conversation_memory | TestConversationMemory | 4 | 分页、消息持久化、权限验证、清空记忆 |
| user_profile | TestUserProfile | 3 | 不存在用户、增改查、事实去重 |
| tfidf_embeddings | TestTFIDFEmbeddings | 3 | 无限递归修复、_ready 设置、词表复用 |
| bm25_retriever | TestBM25Retriever | 2 | 增量保留、按 source 删除 |
| config | TestConfig | 2 | VISION_MODEL 定义、RETRIEVER_K 正数 |
| API 端点 | Test* (10 classes) | 20+ | 健康检查、Agent 接口、分页、反馈、鉴权 |

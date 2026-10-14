# 本地知识库问答系统

基于 FastAPI + LangGraph + Redis + RAG 技术栈构建的本地知识库问答系统，支持文档上传、混合检索、智能问答、流式输出、对话记忆、用户认证、AI 工作流、文档分享与容器化部署。

## 功能特性

- **流式输出**: 支持 Server-Sent Events (SSE) 实时通信，实现打字机效果展示
- **对话记忆**: 集成 ConversationBufferWindowMemory，支持上下文感知回答
- **混合检索**: BM25 关键词检索 + 向量检索 + RRF 算法融合，提升检索准确率
- **多格式支持**: 支持 txt、md、pdf、docx、pptx 文档及图片 OCR 文字识别
- **联网搜索**: 基于 Tavily API 的实时网络搜索，知识库检索与联网搜索结果融合，支持强制联网模式
- **图片问答**: 集成多模态视觉大模型（glm-4v-flash），支持上传图片直接进行看图问答、图表解读、内容描述
- **用户认证**: JWT 身份认证 + bcrypt 口令哈希，支持接口访问控制
- **接口限流**: 基于 Redis 的分布式固定窗口计数，防止恶意请求（多 worker 下精确共享）
- **容器化部署**: Docker + docker-compose 一键部署方案
- **意图分类**: 内置意图分类器，支持不同类型问题的智能路由
- **临时对话**: 支持临时对话创建，未发送消息时自动丢弃
- **对话中切换模型**: 前端模型选择器，按请求无状态切换到白名单内模型，立即生效（不保存会话偏好）
- **自定义模型端点**: 用户可自行接入任意 OpenAI 兼容服务（含密钥，需评估部署场景）
- **用户画像与长期记忆**: 自动从对话中学习用户偏好，持久化到 SQLite
- **长文本处理**: 支持长文本的摘要 / 翻译 / 问答
- **反馈收集**: 可对回答提交点赞 / 点踩与评论
- **文档分享**: 生成只读分享链接，无需登录即可查看对话结论
- **AI 工作流模式**: 多步骤任务规划与执行（manual 人工逐步审查 / auto 自动执行），内置代码沙箱、文件产出与本地文件系统绑定，高风险操作（执行代码、写文件）强制人工审批

## 技术栈

### 后端

- **框架**: FastAPI
- **业务流程编排**: LangGraph（状态图，替代过程式 if/elif 分发）
- **缓存 / 会话状态 / 限流 / 分布式锁 / Checkpoint**: Redis（缺席时自动降级为进程内实现）
- **配置**: pydantic-settings
- **RAG**: LangChain
- **向量数据库**: Chroma
- **嵌入模型**: 智谱 `embedding-3`（2048 维，默认）/ 本地 `all-MiniLM-L6-v2` / 本地 `TF-IDF`（768 维，modelscope 后端）
- **LLM**: 智谱 `glm-4-flash`（默认） / MockLLM (本地测试)
- **视觉模型**: 智谱 `glm-4v-flash`（图片问答多模态模型）
- **联网搜索**: Tavily Search API
- **混合检索**: rank-bm25 (BM25算法) + RRF 融合
- **认证**: JWT (python-jose) + bcrypt
- **容器化**: Docker

### 前端

- **框架**: Vue 3
- **构建工具**: Vite
- **状态管理**: Pinia
- **HTTP客户端**: Axios
- **流式处理**: EventSource API
- **路由**: Vue Router
- **样式**: Tailwind CSS
- **Markdown 渲染**: marked + DOMPurify（渲染前净化，防 XSS）

## 项目结构

```
本地知识库问答系统/
├── backend/                    # 后端服务（FastAPI）
│   ├── app/
│   │   ├── main.py             # FastAPI 入口（lifespan + 中间件 + 异常 + 路由装配）
│   │   ├── config.py           # 统一配置中心（pydantic-settings）
│   │   ├── api/v1/             # HTTP 层：14 个路由模块（契约声明 + 转发）
│   │   ├── schemas/            # Pydantic 请求/响应契约
│   │   ├── services/           # 用例编排（与 HTTP 解耦）
│   │   ├── graph/              # LangGraph 状态图（State / 节点 / 条件边 / Checkpointer）
│   │   ├── rag/ retrievers/    # 检索与向量库（BM25 / RRF / Chroma）
│   │   ├── tools/ agents/      # 工具（联网搜索 / 代码沙箱 / 文件写出）与 Agent
│   │   ├── memory/             # 对话记忆与用户画像
│   │   ├── repositories/       # 数据持久化（SQLite）
│   │   ├── utils/              # 文本与文件工具
│   │   └── core/               # 配置 / 日志 / 异常 / 安全 / Redis / 依赖注入
│   ├── data/                   # SQLite 业务数据 + 文档 + 工作流库（conversations / user_profile / workflow）
│   ├── chroma_db/              # Chroma 向量库持久化目录
│   ├── logs/                   # 结构化日志
│   ├── requirements.txt        # Python 依赖（开发用）
│   ├── requirements.lock.txt   # pip freeze 锁定版本（可复现构建，148 包）
│   ├── Dockerfile              # Docker 构建文件
│   └── .env                    # 环境变量配置（从 .env.example 复制）
├── frontend/                   # 前端界面（Vue 3 + Vite + Tailwind）
│   ├── src/
│   │   ├── views/              # 页面：Chat / Login / ModelSettings / SharedView / WorkView
│   │   ├── stores/             # Pinia 状态管理（chat 等）
│   │   ├── router/             # 路由配置（含守卫）
│   │   ├── components/         # 子组件
│   │   └── api/                # 前端 HTTP 封装
│   ├── public/                 # 静态资源
│   ├── nginx.conf              # 生产构建的 Nginx 反代配置
│   ├── package.json            # 前端依赖
│   └── Dockerfile              # Docker 构建文件
├── docker-compose.yml          # Docker Compose 配置（backend / frontend / redis）
├── .env.example                # 环境变量示例
├── docs/                       # 设计文档（重构、工作流、诊断等）
└── README.md                   # 项目文档
```

## 快速开始

### 环境要求

- Python >= 3.10
- Node.js >= 22.18（或 >= 24.12）
- Docker (可选，用于容器化部署)

### 安装依赖

**后端**

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

pip install -r requirements.txt
```

**前端**

```bash
cd frontend
npm install
```

### 环境变量配置

复制 `.env.example` 为 `.env`，填入实际值：

```bash
copy .env.example .env
```

核心配置项：

```env
# 模型后端（local / ollama / openai / baidu_qianfan / modelscope / zhipu / mock）
MODEL_BACKEND=zhipu
ZHIPU_API_KEY=your-api-key

# 可选：覆盖默认模型
LLM_MODEL=glm-4-flash
VISION_MODEL=glm-4v-flash
EMBEDDING_MODEL=embedding-3

# 管理员账号（缺失时服务拒绝启动，务必修改默认密码）
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me

# JWT 密钥（生产环境务必修改为强随机字符串）
JWT_SECRET_KEY=your-secret-key
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Tavily 联网搜索 API（可选，缺失时联网搜索功能不可用）
TAVILY_API_KEY=your-tavily-key

# CORS 白名单（Docker 部署需包含前端来源）
CORS_ORIGINS=http://localhost,http://localhost:80,http://localhost:5173,http://localhost:5174,http://127.0.0.1
```

> ⚠️ `ADMIN_USERNAME` 与 `ADMIN_PASSWORD` 为**必填项**：缺失时后端启动即报错（fail fast），不提供任何默认账号。首次部署请务必修改为非默认值。

***

## 运行方式

本项目支持三种运行模式，根据场景选择：

### 模式一：纯本地开发（推荐开发调试）

前后端均在本地运行，前端通过 Vite 代理直接访问后端。

**1. 启动后端**

```bash
cd backend
venv\Scripts\activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

> **Windows 中文路径注意**：若项目路径包含中文，**不要使用** `venv\Scripts\uvicorn.exe` 直接启动，命令行解析会乱码导致启动失败。务必使用 `python -m uvicorn`。

后端启动后访问：<http://localhost:8000>（API） / <http://localhost:8000/docs>（Swagger 文档）

**2. 启动前端**

```bash
cd frontend
npm run dev
```

前端启动后访问：<http://localhost:5173>

> Vite 开发服务器已配置代理：`/api/*` → `http://127.0.0.1:8000`，前端代码中请求 `/api/xxx` 会自动转发到后端。

***

### 模式二：Docker Compose 一键部署（推荐完整部署）

使用 `docker-compose.yml` 同时启动前后端容器，数据通过命名卷持久化。

**1. 配置环境变量**

确保项目根目录存在 `.env` 文件（参考 `.env.example`）。

**2. 构建并启动**

```bash
# 在项目根目录执行
docker compose up -d --build
```

**3. 查看状态**

```bash
docker compose ps
docker compose logs -f backend
docker compose logs -f frontend
```

**4. 访问服务**

- 前端：<http://localhost>（默认 80 端口，可通过 `.env` 中 `FRONTEND_PORT` 修改）
- 后端 API：<http://localhost:8000>（默认 8000 端口，可通过 `.env` 中 `BACKEND_PORT` 修改）
- API 文档：<http://localhost:8000/docs>

**5. 常用命令**

| 操作         | 命令                                           |
| ---------- | -------------------------------------------- |
| 启动         | `docker compose up -d`                       |
| 停止         | `docker compose down`                        |
| 重启         | `docker compose restart`                     |
| 查看日志       | `docker compose logs -f [backend\|frontend]` |
| 进入后端容器     | `docker compose exec backend sh`             |
| 重新构建       | `docker compose build --no-cache`            |
| 完全清理（含数据卷） | `docker compose down -v`                     |

**数据持久化卷**

- `rag-backend-data` — SQLite 业务数据（用户、对话、分享链接、工作流）
- `rag-backend-chroma` — Chroma 向量数据库
- `rag-backend-logs` — 结构化日志文件
- `rag-redis-data` — Redis 持久化数据

***

### 模式三：本地后端 + Docker 前端（混合部署）

适合在本地开发后端、同时用 Docker 验证前端生产构建的场景。

**1. 启动本地后端**

后端必须绑定 `0.0.0.0`，否则 Docker 容器无法访问宿主机服务。

```bash
cd backend
venv\Scripts\activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

同时确保 `.env` 中的 `CORS_ORIGINS` 包含 Docker 前端的访问来源，例如：

```env
CORS_ORIGINS=http://localhost,http://localhost:8081,http://127.0.0.1
```

**2. 获取宿主机真实 IP**

Docker 容器通过虚拟网卡访问宿主机，不能使用 `127.0.0.1`。在 Windows 上执行：

```powershell
ipconfig
# 查找类似 192.168.x.x 的 IPv4 地址
```

**3. 修改前端代理配置**

编辑 `frontend/nginx.conf`，将 `proxy_pass` 中的 IP 替换为上一步获取的宿主机真实 IP：

```nginx
location /api/ {
    proxy_pass http://192.168.x.x:8000/;   # <-- 替换为你的宿主机IP
    # ... 其余保持不变
}
```

**4. 构建并运行前端容器**

```bash
cd frontend

# 构建镜像
docker build -t rag-frontend:latest .

# 运行容器（映射到宿主机 8081 端口）
docker run -d --name rag-frontend -p 8081:80 rag-frontend:latest
```

**5. 访问**

打开浏览器访问：<http://localhost:8081>

**停止并删除前端容器**

```bash
docker stop rag-frontend
docker rm rag-frontend
```

***

### 模式四：单独 Docker 运行后端

```bash
cd backend
docker build -t rag-backend:latest .
docker run -d --name rag-backend -p 8000:8000 --env-file ../.env rag-backend:latest
```

## API 接口

所有受保护接口均需在请求头携带 `Authorization: Bearer <token>`。完整契约以 `/docs`（Swagger）为准，以下为常用接口概览。

### 主要接口一览

| 分组 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| 认证 | POST | `/token` | 获取访问令牌（form 表单） |
| 认证 | GET | `/users/me/` | 获取当前用户 |
| 问答 | POST | `/ask` | 普通问答（JSON 响应） |
| 问答 | POST | `/ask/stream` | 流式问答（SSE） |
| 问答 | POST | `/ask/image` | 图片问答（SSE，multipart） |
| 文档 | POST | `/upload` | 上传单个文档 |
| 文档 | POST | `/upload-multiple` | 批量上传 |
| 文档 | GET | `/documents` | 文档列表 |
| 文档 | DELETE | `/document/{filename}` | 删除文档 |
| 文档 | POST | `/rebuild-vectorstore` | 重建向量库（切换嵌入模型后调用） |
| 对话 | GET | `/conversations` | 对话列表 |
| 对话 | GET | `/conversations/{id}` | 对话详情 |
| 对话 | PUT | `/conversations/{id}` | 重命名对话 |
| 对话 | DELETE | `/conversations/{id}` | 删除对话 |
| 对话 | POST | `/conversations/batch-delete` | 批量删除 |
| 对话 | POST | `/conversations/{id}/clear` | 清空对话记忆 |
| 对话 | GET | `/conversations/{id}/export` | 导出对话 |
| 模型 | GET | `/models` | 可选模型清单（前端切换控件用） |
| 模型端点 | GET/POST | `/model-endpoints` | 自定义模型端点列表 / 新增 |
| 模型端点 | DELETE | `/model-endpoints/{id}` | 删除自定义端点 |
| 分享 | POST | `/share` | 创建分享链接 |
| 分享 | GET | `/share/{token}` | 读取分享内容（公开，无需鉴权） |
| 用户画像 | GET/PUT | `/profile` | 获取 / 更新用户画像 |
| 用户画像 | POST | `/remember-turn` | 从一轮对话学习用户信息 |
| 用户画像 | POST | `/long-text` | 长文本摘要 / 翻译 / 问答 |
| 反馈 | POST | `/feedback` | 提交反馈 |
| 反馈 | GET | `/feedback/list` | 反馈列表 |
| Agent | GET | `/agent/intents` | 系统支持的意图类型 |
| Agent | GET | `/agent/capabilities` | 系统可用能力与当前模型 |
| 系统 | GET | `/health` | 健康检查（含 Redis / LangGraph 状态） |
| 系统 | GET | `/stats` | 系统统计 |
| 系统 | GET | `/example-questions` | 示例问题 |
| 工作流 | — | `/workflow/*` | AI 工作流（详见下文） |

### 认证接口

#### 获取 Token

**POST** `/token`

请求: `application/x-www-form-urlencoded`

| 参数       | 类型     | 说明                  |
| -------- | ------ | ------------------- |
| username | string | 用户名（默认: admin）     |
| password | string | 密码（由 `.env` 的 `ADMIN_PASSWORD` 决定，无硬编码默认） |

响应:

```json
{
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer"
}
```

#### 获取当前用户

**GET** `/users/me/`

请求头: `Authorization: Bearer <token>`

响应:

```json
{
    "username": "admin",
    "disabled": false
}
```

### 问答接口

#### 普通问答

**POST** `/ask`

请求头: `Authorization: Bearer <token>`

请求体:

```json
{
    "question": "什么是 Agent？",
    "conversation_id": "conv-abc123",
    "model": "glm-4-flash"
}
```

| 参数               | 类型     | 说明             |
| ---------------- | ------ | -------------- |
| question         | string | 问题内容           |
| conversation\_id | string | 可选，对话ID用于记忆上下文 |
| model            | string | 可选，指定白名单内模型   |

响应:

```json
{
    "answer": "回答内容...",
    "sources": [
        {
            "file": "大模型面试题合集.md",
            "content_preview": "相关文档片段..."
        }
    ],
    "data_source": "rag",
    "trace_id": "abc12345"
}
```

#### 流式问答

**POST** `/ask/stream`

请求头: `Authorization: Bearer <token>`

请求体:

```json
{
    "question": "什么是 RAG？",
    "conversation_id": "conv-abc123",
    "model": "glm-4-flash"
}
```

响应: SSE 流 (`text/event-stream`)

```
data: {"type": "intent", "content": {"intent": "knowledge_qa", "confidence": 0.98}}
data: {"type": "sources", "content": [...]}
data: {"type": "chunk", "content": "根据上下文信息，"}
data: {"type": "chunk", "content": "**RAG** 全称是"}
data: {"type": "final", "content": {"answer": "...", "sources": [], "trace_id": "abc12345", "model": "glm-4-flash", "truncated": false}}
```

> SSE 事件固定序列为 `intent → sources → (tool_calls) → chunk* → final`，每个请求恰好一次 `intent` 事件。

#### 图片问答

**POST** `/ask/image`

上传图片并以多模态视觉大模型进行看图问答，支持图片内容描述、图表解读、识图问答。回答以 SSE 流式返回，事件格式与 `/ask/stream` 一致。

请求头: `Authorization: Bearer <token>`

请求: `multipart/form-data`

| 参数               | 类型     | 说明                                           |
| ---------------- | ------ | -------------------------------------------- |
| file             | File   | 图片文件（jpg/jpeg/png/bmp/tiff/webp/gif，最大 20MB） |
| question         | string | 可选，针对图片的提问，为空时默认"请描述这张图片的内容"                 |
| conversation\_id | string | 可选，对话ID，用于记忆上下文                              |

响应: SSE 流 (`text/event-stream`)

```
data: {"type": "intent", "content": {"intent": "image_qa", "confidence": 1.0}}
data: {"type": "sources", "content": []}
data: {"type": "chunk", "content": "这张图片展示了..."}
data: {"type": "final", "content": {"answer": "...", "data_source": "vision", "intent": "image_qa"}}
```

### 文件上传

#### 单个上传

**POST** `/upload`

请求头: `Authorization: Bearer <token>`

请求: `multipart/form-data`

| 参数   | 类型   | 说明   |
| ---- | ---- | ---- |
| file | File | 文档文件 |

响应:

```json
{
    "message": "文件 xxx.md 上传成功，知识库已更新！",
    "trace_id": "abc12345"
}
```

#### 批量上传

**POST** `/upload-multiple`

请求头: `Authorization: Bearer <token>`

请求: `multipart/form-data`

| 参数    | 类型      | 说明     |
| ----- | ------- | ------ |
| files | File\[] | 文档文件数组 |

响应:

```json
{
    "message": "批量上传完成！成功 3 个文件，知识库已更新。",
    "success_count": 3,
    "failed_files": [],
    "trace_id": "abc12345"
}
```

### 文档管理

#### 文档列表

**GET** `/documents`

请求头: `Authorization: Bearer <token>`

响应:

```json
{
    "documents": [
        {
            "filename": "大模型面试题合集.md",
            "size": 102400,
            "extension": ".md"
        }
    ],
    "count": 1
}
```

#### 删除文档

**DELETE** `/document/{filename}`

请求头: `Authorization: Bearer <token>`

响应:

```json
{
    "message": "文件 xxx.md 删除成功，知识库已更新！",
    "trace_id": "abc12345"
}
```

#### 重建向量库

**POST** `/rebuild-vectorstore`

切换嵌入模型（如从本地 TF-IDF 切到智谱 `embedding-3`）后，原有向量维度不兼容，必须调用本接口清空并重新索引 `data/` 下的全部文档。

请求头: `Authorization: Bearer <token>`

响应:

```json
{
    "message": "向量库重建完成",
    "document_count": 12,
    "vector_count": 1034
}
```

### 对话管理

**GET** `/conversations` — 对话列表（含 `id` / `title` / `updated_at`）。

**GET** `/conversations/{conversation_id}` — 对话详情（消息历史）。

**PUT** `/conversations/{conversation_id}` — 重命名（`{"title": "新标题"}`）。

**DELETE** `/conversations/{conversation_id}` — 删除对话。

**POST** `/conversations/batch-delete` — 批量删除（`{"ids": [...]`}）。

**POST** `/conversations/{conversation_id}/clear` — 清空对话记忆（保留会话外壳）。

**GET** `/conversations/{conversation_id}/export` — 导出对话（Markdown 等）。

### 模型与文档分享

#### 可选模型清单

**GET** `/models`

返回当前后端允许切换的模型清单（受 `LLM_CHOICES` 与后端内置候选约束）。传入未登记模型名的请求会被拒绝（400）。

响应:

```json
{
    "models": [
        {"id": "glm-4-flash", "label": "快速版 · 响应最快，适合日常问答", "is_default": true},
        {"id": "glm-4-plus", "label": "增强版 · 复杂推理与长回答更稳", "is_default": false}
    ],
    "default_model": "glm-4-flash",
    "backend": "zhipu"
}
```

#### 创建分享链接

**POST** `/share`

将某段对话结论固化为只读分享。请求体含 `conversation_id` 与可分享内容；返回带 `token` 的访问地址。

请求头: `Authorization: Bearer <token>`

响应:

```json
{
    "token": "a1b2c3d4",
    "url": "/share/a1b2c3d4"
}
```

#### 读取分享内容

**GET** `/share/{token}`

公开接口，无需鉴权。返回该分享的结论正文与来源。

### 用户画像与反馈

**GET / PUT** `/profile` — 获取 / 更新用户画像（长期偏好）。

**POST** `/remember-turn` — 从一轮对话中自动抽取并持久化用户信息。

**POST** `/long-text` — 长文本处理：摘要 / 翻译 / 针对长文本提问。

**POST** `/feedback` — 提交反馈（`{"conversation_id": "...", "rating": "up", "comment": "..."}`）。

**GET** `/feedback/list` — 反馈列表（管理员）。

### AI 工作流接口

工作流是一套独立的多步骤任务系统（路由前缀 `/workflow`），与对话数据隔离（存 `data/workflow.db`）。生命周期为 `intake → clarify → plan → gate → execute → deliver`。内置工具能力：`calculator / code_executor / file_write / knowledge_query / web_search`。

常用端点：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/workflow/runs` | 任务列表 |
| POST | `/workflow/runs` | 创建任务 |
| GET | `/workflow/runs/{run_id}` | 任务详情 |
| DELETE | `/workflow/runs/{run_id}` | 删除任务 |
| POST | `/workflow/runs/{run_id}/clarify/stream` | 产出一轮澄清问题（SSE） |
| POST | `/workflow/runs/{run_id}/plan/stream` | 生成任务书（SSE） |
| POST | `/workflow/runs/{run_id}/steps/{step_id}/approve` | 审批单步（高风险工具强制） |
| POST | `/workflow/runs/{run_id}/steps/{step_id}/execute` | 执行单步 |
| GET | `/workflow/fs/list` | 浏览本地文件夹（需 `WORKFLOW_FS_BROWSE=true`） |
| POST | `/workflow/fs/bind` | 绑定工作区文件夹 |
| GET | `/workflow/files/{file_id}/download` | 下载引用 / 产出文件 |

> 代码执行在受限 AST 白名单沙箱中进行，超时会被真正终止；`code_executor` 与 `file_write` 无论 manual/auto 模式都需人工单独审批。

### 系统接口

#### 健康检查

**GET** `/health`

响应:

```json
{
    "status": "ok",
    "service": "本地知识库问答系统"
}
```

#### 系统统计

**GET** `/stats`

请求头: `Authorization: Bearer <token>`

响应:

```json
{
    "document_count": 1,
    "vector_count": 86,
    "chroma_exists": true,
    "model_backend": "zhipu",
    "embedding_model": "embedding-3",
    "llm_model": "glm-4-flash"
}
```

## 支持的文件格式

| 类型 | 格式                | 说明             |
| -- | ----------------- | -------------- |
| 文本 | .txt              | 纯文本文件          |
| 文档 | .md               | Markdown文档     |
| 文档 | .pdf              | PDF文档          |
| 文档 | .docx             | Word文档         |
| 演示 | .pptx             | PowerPoint演示文稿 |
| 表格 | .xlsx             | Excel 表格        |
| 图像 | .png, .jpg, .jpeg | 图片OCR文字识别      |

## 配置说明

配置文件位于 `backend/app/core/config.py`（pydantic-settings），环境变量通过 `.env` 文件配置。配置按「分组」组织，除检索分组外多数分组显式声明了 `env_prefix`（如 `REDIS_`、`RATE_LIMIT_`、`GRAPH_`、`OCR_`、`WORKFLOW_`），否则会静默使用默认值。

### 模型后端配置

```python
MODEL_BACKEND = "zhipu"   # local / ollama / openai / baidu_qianfan / modelscope / zhipu / mock
```

| 后端             | 嵌入模型                       | LLM模型                             | 视觉模型                 | 说明                    |
| -------------- | -------------------------- | --------------------------------- | -------------------- | --------------------- |
| **zhipu**      | **embedding-3 (2048维)**       | **glm-4-flash**                   | **glm-4v-flash**     | **默认。智谱 AI，OpenAI 兼容** |
| local          | all-MiniLM-L6-v2           | mock                              | mock                 | 纯本地运行，无需外部依赖          |
| ollama         | nomic-embed-text          | llama3.2:3b                       | llama3.2-vision      | 需要安装Ollama            |
| openai         | text-embedding-ada-002    | gpt-3.5-turbo                     | gpt-4o               | 需要OpenAI API密钥        |
| baidu\_qianfan | Embedding-V1              | ERNIE-3.5-8K                      | ERNIE-4.0-8K-Lite    | 需要百度千帆API密钥           |
| modelscope     | local-tfidf (768维)         | Qwen/Qwen3-Coder-30B-A3B-Instruct | Qwen/Qwen3.5-35B-A3B | 阿里ModelScope          |

> 切换**嵌入模型**后向量维度会变（本地 TF-IDF 768 维 vs 智谱 embedding-3 2048 维），
> 必须调用 `POST /rebuild-vectorstore` 重建向量库，否则检索报维度错误。

### 文档配置

| 环境变量                      | 说明        | 默认值  |
| ------------------------- | --------- | ---- |
| RETRIEVER\_K              | 召回片段数     | 3    |
| CHUNK\_SIZE               | 切分大小（字符）  | 500  |
| CHUNK\_OVERLAP            | 切分重叠（字符）  | 50   |
| CONVERSATION\_WINDOW\_SIZE | 对话窗口大小    | 5    |
| LOCKED\_WINDOW\_SIZE      | 锁定后窗口大小   | 10   |
| RRF\_K                    | RRF 融合常数  | 60   |
| RRF\_TOP\_N               | RRF 取前 N  | 5    |
| MAX\_CONTEXT\_TOKENS      | 上下文 token 上限 | 3200 |

### 认证配置

| 环境变量                    | 说明        | 默认值                   |
| ----------------------- | --------- | --------------------- |
| ADMIN\_USERNAME         | 管理员用户名    | **无默认值（必填）**           |
| ADMIN\_PASSWORD         | 管理员密码     | **无默认值（必填）**           |
| JWT\_SECRET\_KEY        | JWT 签名密钥  | change-me-in-production |
| JWT\_ALGORITHM          | JWT 算法    | HS256                 |
| ACCESS\_TOKEN\_EXPIRE\_MINUTES | Token 有效期（分钟） | 1440             |

> `ADMIN_USERNAME` / `ADMIN_PASSWORD` 缺失时服务**拒绝启动**。口令使用 bcrypt 哈希存储，不在配置中明文落库。

### 限流配置

限流分组前缀 `RATE_LIMIT_`。默认全局 `60 次/分钟`，关键接口独立配额（`scope_limits`）：

| 接口 / scope        | 限流规则    |
| ----------------- | ------- |
| token             | 5次/分钟  |
| ask / ask\_stream | 30次/分钟 |
| ask\_image        | 20次/分钟 |
| upload            | 10次/分钟 |
| share\_read       | 30次/分钟 |
| long\_text        | 10次/分钟 |

### Redis 配置

分组前缀 `REDIS_`：

| 环境变量                       | 说明              | 默认值                       |
| -------------------------- | --------------- | ------------------------- |
| REDIS\_URL                 | Redis 连接地址       | redis://localhost:6379/0   |
| REDIS\_FALLBACK\_TO\_MEMORY | 不可用时降级为进程内实现    | true（生产建议 false）           |
| REDIS\_KEY\_PREFIX         | 键前缀             | kbqa                      |
| REDIS\_TTL\_SESSION        | 会话缓存 TTL（秒）     | 604800（7天）                |
| REDIS\_TTL\_SHARE\_DEFAULT | 分享链接默认 TTL（秒）   | 86400（1天）                 |
| REDIS\_TTL\_LOCK\_DEFAULT  | 分布式锁默认 TTL（秒）   | 30                        |

### LangGraph 配置

分组前缀 `GRAPH_`。状态图是**唯一**的业务编排实现（原过程式 router 已删除），构建失败即启动失败。

| 环境变量                       | 说明              | 默认值    |
| -------------------------- | --------------- | ------ |
| GRAPH\_CHECKPOINTER        | checkpoint 后端   | memory |
| GRAPH\_MAX\_AGENT\_ITERATIONS | 单轮最大 Agent 迭代 | 5      |
| GRAPH\_RECURSION\_LIMIT    | 递归上限            | 25     |

### OCR 配置

分组前缀 `OCR_`：

| 环境变量              | 说明         | 默认值           |
| ----------------- | ---------- | ------------- |
| OCR\_ENABLED      | 是否启用 OCR   | true          |
| OCR\_LANGUAGES    | 识别语言（+ 连接） | chi\_sim+eng  |
| OCR\_TESSERACT\_CMD | Tesseract 路径 | 自动发现          |
| OCR\_TESSDATA\_PREFIX | 语言包目录     | 自动发现          |

### AI 工作流配置

分组前缀 `WORKFLOW_`：

| 环境变量                             | 说明                  | 默认值      |
| -------------------------------- | ------------------- | -------- |
| WORKFLOW\_DEFAULT\_MODE          | 默认模式（manual / auto） | manual   |
| WORKFLOW\_MAX\_STEPS             | 单任务步骤上限             | 12       |
| WORKFLOW\_MAX\_CLARIFY\_ROUNDS   | 澄清追问轮次上限            | 3        |
| WORKFLOW\_FILE\_TTL\_DAYS        | 临时文件 TTL（天）         | 7        |
| WORKFLOW\_REQUIRE\_APPROVAL\_TOOLS | 强制单独审批的工具          | code\_executor,file\_write |
| WORKFLOW\_FS\_BROWSE             | 允许浏览本地文件夹绑定工作区       | true（多用户部署建议 false） |
| WORKFLOW\_WRITE\_TO\_BOUND\_FOLDER | 允许写进已绑定本地文件夹        | true（多用户部署建议 false） |

## RAG 流程

```
用户提问 → 对话记忆检索 → 问题向量化 → BM25检索 + 向量检索 → RRF融合 → 提取相关文档 → 生成回答
```

1. **文档加载**: 支持多种格式文档（txt、md、pdf、docx、pptx、xlsx、图片OCR）
2. **文档切分**: 将文档切分为多个片段，便于检索
3. **向量化**: 使用嵌入模型将文本转换为向量（维度取决于后端：智谱 embedding-3 为 2048 维，本地 TF-IDF 为 768 维）
4. **向量存储**: 将向量存入Chroma向量数据库
5. **混合检索**:
   - BM25: 关键词匹配检索
   - 向量检索: 语义相似度检索
   - RRF融合: 综合两种检索结果排序
6. **生成**: 将检索到的文档作为上下文，结合对话历史生成回答
7. **流式输出**: 通过SSE实时推送回答内容

## 使用说明

1. **启动服务**: 同时启动后端和前端服务，或使用Docker一键部署
2. **登录**: 使用 `.env` 中配置的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 登录系统
3. **上传文档**: 通过前端界面或API上传知识库文档
4. **提问**: 在前端界面输入问题，获取基于知识库的回答
5. **切换模型**: 输入框上方右侧的模型选择器可切换到白名单内模型，立即对后续请求生效（选择存于 localStorage，刷新保留）
6. **联网搜索**: 点击输入框工具栏的搜索图标开启联网模式，系统会同时检索本地知识库与实时网络结果并融合回答；对天气、新闻等实时性问题，意图分类器会自动判定需要联网
7. **图片问答**: 点击输入框工具栏的图片图标选择图片（jpg/png/bmp/tiff/webp/gif，≤20MB），输入框上方会显示预览缩略图，输入问题（如"描述这张图"、"图中的表格数据是多少"）后按 Enter 发送，系统调用视觉大模型看图回答；不输入问题时默认"请描述这张图片的内容"
8. **查看来源**: 回答会显示引用的文档来源
9. **多轮对话**: 系统会自动记忆对话历史，支持上下文相关的追问
10. **新对话**: 点击"新对话"创建临时对话，发送消息后自动保存到历史列表
11. **分享结论**: 在对话中将结论生成只读分享链接，无需登录即可对外查看
12. **AI 工作流**: 进入工作流页面，描述任务目标；系统先澄清、再生成任务书，逐步（manual 模式需逐步骤审批）执行，最终交付结论与产物文件

## 注意事项

- 管理员账号由 `.env` 的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 决定，**没有硬编码默认密码**，缺失会导致服务拒绝启动
- 首次启动会自动加载 `data/` 目录下的文档并创建向量库
- 修改配置后需要重启服务生效
- 切换模型后端（尤其嵌入模型）时，务必调用 `POST /rebuild-vectorstore` 重建向量库
- 本地（local）模式下，LLM 使用 MockLLM 直接返回检索到的文档内容，embedding 用 `all-MiniLM-L6-v2`
- 图片OCR需要本机安装 Tesseract OCR 引擎（含中文语言包）
- 图片问答默认依赖智谱多模态模型（glm-4v-flash）；无视觉能力的后端会回退至文本 LLM（无看图能力）
- 生产环境请修改 `JWT_SECRET_KEY` 为安全的随机字符串，并将 `REDIS_FALLBACK_TO_MEMORY` 设为 `false`、`GRAPH_CHECKPOINTER` 设为 `redis`，以便故障时快速暴露
- `WORKFLOW_FS_BROWSE` / `WORKFLOW_WRITE_TO_BOUND_FOLDER` 默认开启是**有意面向本机单用户**的本地读/写能力；暴露到局域网或公网前必须设为 `false`，否则登录用户可枚举服务器磁盘或落文件到用户目录
- `ALLOW_CUSTOM_MODELS`（模型分组）允许服务器携用户填写的密钥向任意地址发请求，多人/对外部署应设为 `false`
- ModelScope API 密钥无效时，上传功能将使用本地 TF-IDF 嵌入模型

## v4 架构（FastAPI + LangGraph + Redis）

> 完整设计与迁移手册见 [`docs/REFACTOR_FASTAPI_LANGGRAPH_REDIS.md`](docs/REFACTOR_FASTAPI_LANGGRAPH_REDIS.md)。
> **接口路径与 SSE 事件结构与 v3 完全一致，前端无需改动。**

### 分层结构

```
app/
├── main.py         仅组装：lifespan + 中间件 + 异常处理器 + 挂路由（约 250 行）
├── api/v1/         HTTP 层：契约声明 + 转发（14 个路由模块）
├── schemas/        Pydantic 请求/响应契约
├── services/       用例编排（与 HTTP 解耦）
├── graph/          LangGraph 状态图：State / 节点 / 条件边 / Checkpointer
├── rag/ tools/ agents/   领域能力
├── memory/         对话记忆与用户画像
├── repositories/   数据持久化（SQLite）
├── utils/          文本与文件工具
└── core/           配置 / 日志 / 异常 / 安全 / Redis / 依赖注入
```

依赖方向只能向下：`api → services → graph/repositories → rag/tools → core`。
该规则由 `tests/unit/test_layering.py` 用静态 AST 分析强制检查。

### 关键设计点

- **业务流程是一张状态图**：`classify_intent` 是唯一分发点，条件边负责编排；
  同步与流式两条路径共用同一张图，消除了 v3 中 `route()` / `route_stream()` 的双份维护。
- **State 必须可 JSON 序列化**：检索结果以 `{page_content, metadata}` 字典形式入 State，
  而非 LangChain `Document` 对象（否则 checkpointer 写入失败）。
- **Redis 是增强项而非硬依赖**：连不上时自动降级为进程内实现并打 warning，
  服务照常启动；生产环境应设 `REDIS_FALLBACK_TO_MEMORY=false` 以便故障时快速暴露。
- **统一错误响应**：同时包含顶层 `detail`（兼容前端旧读取方式）与结构化
  `error: {code, message, detail, trace_id}`。

### 启动 Redis

```bash
# 推荐：Redis Stack（含 RediSearch，LangGraph Redis checkpoint 需要）
docker compose up -d redis

# 或独立启动
docker run -d --name rag-redis -p 6379:6379 \
  redis/redis-stack-server:7.4.0-v0 \
  redis-stack-server --appendonly yes --maxmemory 1gb --maxmemory-policy allkeys-lru
```

未启动 Redis 时后端仍可运行（自动降级），但多 worker 下 限流 / 分布式锁 / 缓存 不共享。

### 模型配置（智谱 AI）

```env
MODEL_BACKEND=zhipu
ZHIPU_API_KEY=<你的 Key>
ZHIPU_BASE_URL=https://open.bigmodel.cn/api/paas/v4
```

默认模型：LLM `glm-4-flash` / Vision `glm-4v-flash` / Embedding `embedding-3`（2048 维）。
可用 `LLM_MODEL` / `VISION_MODEL` / `EMBEDDING_MODEL` 单独覆盖。

> ⚠️ **切换嵌入模型必须重建向量库**：`embedding-3` 是 2048 维，与本地 TF-IDF 的 768 维不兼容。
> 调用 `POST /rebuild-vectorstore` 即可（会清空并重新索引 `data/` 下的全部文档）。

### 对话中切换模型

输入框上方右侧有**模型选择控件**，列出当前后端允许使用的模型，切换后**立即对后续请求生效**
（选择会存到 `localStorage`，刷新后保留）。

实现上是**无状态**的：`model` 随每个请求发送，服务端不保存会话偏好，也不影响其他用户。

```bash
# 列出可选模型
GET /models
→ { "models": [{"id":"glm-4-flash","label":"快速版…","is_default":true}, …],
    "default_model": "glm-4-flash", "backend": "zhipu" }

# 按请求指定模型（留空则用服务端默认）
POST /ask        {"question":"…", "model":"glm-4-plus"}
POST /ask/stream {"question":"…", "model":"glm-4-plus"}
```

- 候选清单由 `LLM_CHOICES` 配置（逗号分隔），留空则用后端内置候选
- **只允许切换到白名单内的模型** —— 传入未登记的模型名返回 400，避免任意模型名透传到上游
- 响应体会回显实际使用的模型与是否被截断：`{"model":"glm-4-plus","truncated":false}`

### 回答不完整怎么排查

见 [`docs/DIAGNOSIS_INCOMPLETE_ANSWER.md`](docs/DIAGNOSIS_INCOMPLETE_ANSWER.md)
—— 区分「模型能力 / `max_tokens` / 流式中断 / 前端渲染」的判据与验证命令。

一句话版：先看 `final.answer` 与页面是否一致（排除前端），
再看日志里的 `finish_reason` —— `length` 是被 `max_tokens` 掐断，`stop` 是模型自己写完，
**缺失**则说明流在中途断了（网络或上游内容风控）。

```bash
cd backend
python scripts/diagnose_truncation.py     # 直接压模型，打印 finish_reason 与 token 用量
```

### 图执行参数

```env
GRAPH_CHECKPOINTER=memory   # redis | memory | none（redis 需 Redis Stack）
GRAPH_MAX_AGENT_ITERATIONS=5
GRAPH_RECURSION_LIMIT=25
```

状态图是**唯一**的业务编排实现（原过程式 router 已删除），构建失败即启动失败。

### 测试

```bash
cd backend
python -m pytest tests/ -q
```

测试覆盖：接口契约（OpenAPI 与 v3 基线逐条 diff）、SSE 事件序列、
分层依赖、Redis 键规范与限流/锁原语、**会话消息与画像缓存的多 worker 一致性**、
配置环境变量绑定、状态图路由与执行、代码沙箱逃逸防护、审批门禁与工作流状态机单调性。

---

## 依赖列表

### 后端（backend/requirements.txt）

按用途分组（完整可复现版本见 `requirements.lock.txt`，基于 pip freeze，共 148 个包）：

- **Web 框架**：`fastapi`、`uvicorn[standard]`、`pydantic`、`pydantic-settings`、`python-dotenv`
- **RAG / LLM**：`langchain` 系列（`langchain`、`langchain-chroma`、`langchain-community`、`langchain-openai`、`langchain-text-splitters`、`langchain-classic`）、`rank-bm25`、`chromadb`
- **编排（v4）**：`langgraph`、`langgraph-checkpoint`、`langgraph-checkpoint-redis`
- **缓存 / 状态**：`redis`、`fakeredis`（Redis 缺席时的进程内降级）
- **认证**：`python-jose[cryptography]`、`bcrypt`、`python-multipart`
- **文档解析**：`python-docx`、`python-pptx`、`PyPDF2`、`pdfplumber`、`pytesseract`、`Pillow`、`openpyxl`
- **工具**：`joblib`、`requests`
- **开发 / 测试**：`pytest`、`pytest-asyncio`、`httpx`、`import-linter`、`zstandard`

### 前端（frontend/package.json）

- **运行时依赖**：`vue@^3.5`、`vue-router@^5`、`pinia@^3`、`axios`、`marked`、`dompurify`、`tailwindcss@^4`
- **开发依赖**：`vite@^8`、`@vitejs/plugin-vue`、`vitest@^4`、`@playwright/test`、`eslint` / `oxlint`、`prettier`
- **运行环境**：Node.js `^22.18.0 || >=24.12.0`

---

## License

MIT License

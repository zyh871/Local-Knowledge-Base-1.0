# 本地知识库问答系统

基于 Python + LangChain + RAG 技术栈构建的本地知识库问答系统，支持文档上传、混合检索、智能问答、流式输出、对话记忆、用户认证和容器化部署。

## 功能特性

- **流式输出**: 支持 Server-Sent Events (SSE) 实时通信，实现打字机效果展示
- **对话记忆**: 集成 ConversationBufferWindowMemory，支持上下文感知回答
- **混合检索**: BM25 关键词检索 + 向量检索 + RRF 算法融合，提升检索准确率
- **多格式支持**: 支持 txt、md、pdf、docx、pptx 文档及图片 OCR 文字识别
- **联网搜索**: 基于 Tavily API 的实时网络搜索，知识库检索与联网搜索结果融合，支持强制联网模式
- **图片问答**: 集成多模态视觉大模型（Qwen3.5-35B-A3B），支持上传图片直接进行看图问答、图表解读、内容描述
- **用户认证**: JWT 身份认证系统，支持接口访问控制
- **接口限流**: 基于 slowapi 的请求限流机制，防止恶意请求
- **容器化部署**: Docker + docker-compose 一键部署方案
- **意图分类**: 内置意图分类器，支持不同类型问题的智能路由
- **临时对话**: 支持临时对话创建，未发送消息时自动丢弃

## 技术栈

### 后端

- **框架**: FastAPI
- **RAG**: LangChain
- **向量数据库**: Chroma
- **嵌入模型**: TF-IDF (本地) / ModelScope Embeddings
- **LLM**: MockLLM (本地) / ModelScope (Qwen系列)
- **视觉模型**: Qwen/Qwen3.5-35B-A3B (图片问答多模态模型)
- **联网搜索**: Tavily Search API
- **混合检索**: rank-bm25 (BM25算法) + RRF 融合
- **认证**: JWT (python-jose)
- **限流**: slowapi + limits
- **容器化**: Docker

### 前端

- **框架**: Vue 3
- **构建工具**: Vite
- **状态管理**: Pinia
- **HTTP客户端**: Axios
- **流式处理**: EventSource API
- **路由**: Vue Router

## 项目结构

```
本地知识库问答系统/
├── backend/                    # 后端服务
│   ├── app/                    # 应用代码
│   │   ├── __init__.py         # 包初始化
│   │   ├── config.py           # 配置文件
│   │   ├── main.py             # FastAPI入口（含JWT认证、限流）
│   │   ├── rag_chain.py        # RAG链实现（混合检索、对话记忆）
│   │   ├── agents/             # 智能代理模块
│   │   │   ├── __init__.py
│   │   │   ├── intent_classifier.py  # 意图分类器
│   │   │   └── router.py       # 代理路由
│   │   ├── document/           # 文档处理模块
│   │   │   ├── __init__.py
│   │   │   ├── long_text.py    # 长文本处理
│   │   │   └── parser.py       # 文档解析器
│   │   ├── embeddings/         # 嵌入模型模块
│   │   │   ├── __init__.py
│   │   │   └── tfidf_embeddings.py  # TF-IDF嵌入模型
│   │   ├── memory/             # 记忆模块
│   │   │   ├── __init__.py
│   │   │   ├── conversation_memory.py  # 对话记忆
│   │   │   ├── long_term.py    # 长期记忆
│   │   │   └── user_profile.py # 用户画像
│   │   ├── retrievers/         # 检索器模块
│   │   │   ├── __init__.py
│   │   │   ├── bm25_retriever.py     # BM25检索器
│   │   │   ├── rrf_fusion.py         # RRF融合算法
│   │   │   └── vector_retriever.py   # 向量检索器
│   │   └── tools/              # 工具模块
│   │       ├── __init__.py
│   │       └── web_search.py   # 网络搜索工具
│   ├── data/                   # 文档存储目录
│   ├── chroma_db/              # Chroma向量库持久化目录
│   ├── venv/                   # Python虚拟环境
│   ├── .env                    # 环境变量配置
│   ├── Dockerfile              # Docker构建文件
│   └── requirements.txt        # Python依赖
├── frontend/                   # 前端界面
│   ├── src/
│   │   ├── App.vue             # 主应用组件（路由容器）
│   │   ├── main.js             # 入口文件
│   │   ├── router/             # 路由配置（含守卫）
│   │   │   └── index.js
│   │   ├── stores/             # Pinia状态管理
│   │   │   ├── chat.js         # 聊天状态管理
│   │   │   └── counter.js
│   │   ├── views/              # 页面组件
│   │   │   ├── Chat.vue        # 聊天页面
│   │   │   └── Login.vue       # 登录页面
│   │   └── components/         # 子组件
│   │       ├── ChatSidebar.vue # 聊天侧边栏
│   │       ├── LoginForm.vue   # 登录表单
│   │       └── MessageBubble.vue # 消息气泡
│   ├── public/                 # 静态资源
│   ├── package.json            # 前端依赖
│   └── Dockerfile              # Docker构建文件
├── docker-compose.yml          # Docker Compose配置
└── README.md                   # 项目文档
```

## 快速开始

### 环境要求

- Python >= 3.10
- Node.js >= 22.0.0
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
# 模型后端（modelscope / openai / baidu_qianfan / ollama / local）
MODEL_BACKEND=modelscope
MODELSCOPE_API_KEY=your-api-key

# 可选：覆盖默认模型
LLM_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct
VISION_MODEL=Qwen/Qwen3.5-35B-A3B

# JWT 密钥（生产环境务必修改为强随机字符串）
JWT_SECRET_KEY=your-secret-key
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Tavily 联网搜索 API（可选，缺失时联网搜索功能不可用）
TAVILY_API_KEY=your-tavily-key

# CORS 白名单（Docker 部署需包含前端来源）
CORS_ORIGINS=http://localhost,http://localhost:80,http://localhost:5173,http://localhost:5174,http://127.0.0.1
```

---

## 运行方式

本项目支持三种运行模式，根据场景选择：

### 模式一：纯本地开发（推荐开发调试）

前后端均在本地运行，前端通过 Vite 代理直接访问后端。

**1. 启动后端**

```bash
cd backend
venv\Scripts\activate

# 绑定 127.0.0.1，仅本地访问
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

---

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

| 操作 | 命令 |
|------|------|
| 启动 | `docker compose up -d` |
| 停止 | `docker compose down` |
| 重启 | `docker compose restart` |
| 查看日志 | `docker compose logs -f [backend\|frontend]` |
| 进入后端容器 | `docker compose exec backend sh` |
| 重新构建 | `docker compose build --no-cache` |
| 完全清理（含数据卷） | `docker compose down -v` |

**数据持久化卷**

- `rag-backend-data` — SQLite 业务数据（用户、对话、分享链接）
- `rag-backend-chroma` — Chroma 向量数据库
- `rag-backend-logs` — 结构化日志文件

---

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

---

### 模式四：单独 Docker 运行后端

```bash
cd backend
docker build -t rag-backend:latest .
docker run -d --name rag-backend -p 8000:8000 --env-file ../.env rag-backend:latest
```

## API接口

### 认证接口

#### 获取Token

**POST** `/token`

请求: `application/x-www-form-urlencoded`

| 参数       | 类型     | 说明                |
| -------- | ------ | ----------------- |
| username | string | 用户名 (默认: admin)   |
| password | string | 密码 (默认: admin123) |

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
    "conversation_id": "conv-abc123"
}
```

| 参数               | 类型     | 说明             |
| ---------------- | ------ | -------------- |
| question         | string | 问题内容           |
| conversation\_id | string | 可选，对话ID用于记忆上下文 |

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
    "conversation_id": "conv-abc123"
}
```

响应: SSE 流 (`text/event-stream`)

```
data: {"type": "chunk", "content": "根据上下文信息，"}
data: {"type": "chunk", "content": "**RAG** 全称是"}
data: {"type": "final", "content": {"answer": "...", "sources": [], "trace_id": "abc12345"}}
```

#### 图片问答

**POST** `/ask/image`

上传图片并以多模态视觉大模型进行看图问答，支持图片内容描述、图表解读、识图问答。回答以 SSE 流式返回，事件格式与 `/ask/stream` 一致。

请求头: `Authorization: Bearer <token>`

请求: `multipart/form-data`

| 参数               | 类型   | 说明                                |
| ---------------- | ---- | --------------------------------- |
| file             | File | 图片文件（jpg/jpeg/png/bmp/tiff/webp/gif，最大 20MB） |
| question         | string | 可选，针对图片的提问，为空时默认"请描述这张图片的内容"     |
| conversation\_id | string | 可选，对话ID，用于记忆上下文                  |

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
    "model_backend": "modelscope",
    "embedding_model": "local-tfidf",
    "llm_model": "Qwen/Qwen3-Coder-30B-A3B-Instruct"
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
| 图像 | .png, .jpg, .jpeg | 图片OCR文字识别      |

## 配置说明

配置文件位于 `backend/app/config.py`，环境变量通过 `.env` 文件配置。

### 模型后端配置

```python
MODEL_BACKEND = "modelscope"
```

| 后端             | 嵌入模型                   | LLM模型                             | 视觉模型                  | 说明             |
| -------------- | ---------------------- | --------------------------------- | --------------------- | -------------- |
| local          | TF-IDF (本地)            | MockLLM                           | 回退至文本LLM             | 无需外部依赖，纯本地运行   |
| ollama         | nomic-embed-text       | llama3.2:3b                       | llama3.2-vision       | 需要安装Ollama     |
| openai         | text-embedding-ada-002 | gpt-3.5-turbo                     | gpt-4o                | 需要OpenAI API密钥 |
| baidu\_qianfan | Embedding-V1           | ERNIE-3.5-8K                      | ERNIE-4.0-8K-Lite     | 需要百度千帆API密钥    |
| modelscope     | TF-IDF (本地)            | Qwen/Qwen3-Coder-30B-A3B-Instruct | Qwen/Qwen3.5-35B-A3B | 阿里ModelScope   |

### 文档配置

```python
DOCUMENT_EXTENSIONS = [".txt", ".pdf", ".md", ".docx", ".pptx", ".png", ".jpg", ".jpeg"]
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
RETRIEVER_K = 3
```

### 认证配置

通过环境变量配置：

| 环境变量                           | 说明           | 默认值    |
| ------------------------------ | ------------ | ------ |
| JWT\_SECRET\_KEY               | JWT密钥        | 123456 |
| JWT\_ALGORITHM                 | JWT算法        | HS256  |
| ACCESS\_TOKEN\_EXPIRE\_MINUTES | Token有效期（分钟） | 30     |

### 限流配置

| 接口               | 限流规则   |
| ---------------- | ------ |
| /ask             | 50次/分钟 |
| /ask/stream      | 30次/分钟 |
| /ask/image       | 20次/分钟 |
| /upload          | 10次/分钟 |
| /upload-multiple | 10次/分钟 |

## RAG流程

```
用户提问 → 对话记忆检索 → 问题向量化 → BM25检索 + 向量检索 → RRF融合 → 提取相关文档 → 生成回答
```

1. **文档加载**: 支持多种格式文档（txt、md、pdf、docx、pptx、图片OCR）
2. **文档切分**: 将文档切分为多个片段，便于检索
3. **向量化**: 使用TF-IDF或嵌入模型将文本转换为向量（固定768维）
4. **向量存储**: 将向量存入Chroma向量数据库
5. **混合检索**:
   - BM25: 关键词匹配检索
   - 向量检索: 语义相似度检索
   - RRF融合: 综合两种检索结果排序
6. **生成**: 将检索到的文档作为上下文，结合对话历史生成回答
7. **流式输出**: 通过SSE实时推送回答内容

## 使用说明

1. **启动服务**: 同时启动后端和前端服务，或使用Docker一键部署
2. **登录**: 使用默认账号（admin/admin123）登录系统
3. **上传文档**: 通过前端界面或API上传知识库文档
4. **提问**: 在前端界面输入问题，获取基于知识库的回答
5. **联网搜索**: 点击输入框工具栏的搜索图标开启联网模式，系统会同时检索本地知识库与实时网络结果并融合回答；对天气、新闻等实时性问题，意图分类器会自动判定需要联网
6. **图片问答**: 点击输入框工具栏的图片图标选择图片（jpg/png/bmp/tiff/webp/gif，≤20MB），输入框上方会显示预览缩略图，输入问题（如"描述这张图"、"图中的表格数据是多少"）后按 Enter 发送，系统调用视觉大模型看图回答；不输入问题时默认"请描述这张图片的内容"
7. **查看来源**: 回答会显示引用的文档来源
8. **多轮对话**: 系统会自动记忆对话历史，支持上下文相关的追问
9. **新对话**: 点击"新对话"创建临时对话，发送消息后自动保存到历史列表

## 注意事项

- 默认管理员账号: admin / admin123
- 首次启动会自动加载 `data/` 目录下的文档并创建向量库
- 修改配置后需要重启服务生效
- 切换模型后端时，建议删除 `chroma_db/` 目录重新构建向量库
- 本地模式下，LLM使用MockLLM直接返回检索到的文档内容
- 图片OCR需要安装Tesseract OCR引擎
- 图片问答依赖 ModelScope 多模态模型（Qwen3.5-35B-A3B），local/mock 模式下回退至文本 LLM（无视觉能力）
- 生产环境请修改JWT\_SECRET\_KEY为安全的随机字符串
- ModelScope API密钥无效时，上传功能将使用本地TF-IDF嵌入模型

## License

MIT License

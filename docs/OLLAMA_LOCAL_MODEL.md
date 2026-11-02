# 本地模型（Ollama）检测与接入

`tools/ollama_bootstrap.py` —— 纯标准库单文件，回答一个问题：**这台机器能不能用本地模型对话？**
能就直接接上去聊，不能就说清楚差哪一步。

```bash
python tools/ollama_bootstrap.py                 # 自检 + 报告
python tools/ollama_bootstrap.py --chat          # 自检通过后多轮对话
python tools/ollama_bootstrap.py --once "你好"    # 单轮提问
python tools/ollama_bootstrap.py --json          # 机器可读
python tools/ollama_bootstrap.py --write-env     # 写 backend/.env，让智鲸 AI 用本地模型
python tools/ollama_bootstrap.py --serve         # 直接用本地模型把智鲸 AI 跑起来
```

自带退出码，可嵌脚本：`0` 可用 / `3` 未安装 / `4` 有服务但无可对话模型 / `5` 装了但服务没起。

---

## 一、检测逻辑：三段，逐段排除

不判「有没有 ollama 命令」就下结论，因为三者可以互相独立：

| 段 | 判据 | 为什么会单独失效 |
|---|---|---|
| **安装态** | `shutil.which("ollama")`，再退到各平台默认路径 | 装了但没进 PATH（Windows 装完要重开终端） |
| **服务态** | `GET {base}/api/version`，2.5 s 超时、重试 1 次 | 软件装了但服务没起（关掉了开机自启）；也可能服务在别的机器上 |
| **模型态** | `GET {base}/api/tags`，逐个分类 | 服务通但一个模型都没拉；或拉的全是向量模型 |

### 两个容易踩的点

**1. 必须绕过系统代理。**
用的是 `urllib.request.build_opener(ProxyHandler({}))` 而不是默认 opener。
本机实测过：环境里配了不可达的 `HTTP_PROXY` 时，urllib 会把对 `127.0.0.1:11434`
的请求也送进代理，结果「Ollama 明明在跑却连不上」。回环请求绝不能过代理。

**2. 向量模型不能当对话模型。**
`/api/tags` 不区分用途。`nomic-embed-text` / `bge-*` / `mxbai-*` / `all-minilm` /
`gte` / `e5-` / `jina-embed` 这些是纯 embedding 模型，选它们去 `/api/chat` 会 400 或空回复。
按关键词判出来单独归类，只在「有对话模型」时才判可用。

### 模型怎么挑

- 先按中文场景偏好打分：`qwen 100 > glm 90 > deepseek 85 > yi 80 > llama3 60 > gemma 50 > mistral 45`
- 同分时挑体积小的（更可能塞进内存）
- 体积取 `/api/tags` 的 `size` 字节数；拿不到就按名字里的参数规模估算

---

## 二、判断流程

```
                    ┌─────────────────────┐
                    │ 启动：detect()      │
                    └──────────┬──────────┘
                               │
                  find_binary() 有结果？
                     ┌─────────┴─────────┐
                    否                    是
                     │                     │
        ┌────────────▼───────────┐   ┌─────▼──────────────────┐
        │ /api/version 通？       │   │ /api/version 通？       │
        └────┬──────────────┬────┘   └────┬──────────────┬────┘
            通               不通          通              不通
             │                │            │               │
        READY(远程)    NOT_INSTALLED   READY        SERVICE_DOWN
                                                          │
                                              「装了但服务没起」
                                              给出 ollama serve / 打开 App
```

服务通之后进入模型态：

```
/api/tags 里有「对话」模型？ ── 有 ──> READY，chosen = 得分最高的那个
                            └─ 无 ──> NO_MODEL
                                       「服务通的，只是没模型」
                                       给出 ollama pull <按内存推荐的型号>
```

### 四种状态各自给什么

| 状态 | 报告 | 引导 |
|---|---|---|
| `READY` | 列出所有模型、标出将用哪个 | 直接可用，提示 `--chat` / `--write-env` / `--serve` |
| `NO_MODEL` | 指出只有向量模型，且它们不能对话 | `ollama pull qwen2.5:7b`（>12 GB 内存）/ `3b`（≥7 GB）/ `1.5b` |
| `SERVICE_DOWN` | 打印找到的 ollama 路径 | `"<路径>" serve`，或从开始菜单启动 Ollama |
| `NOT_INSTALLED` | 说明没检测到 | 路线 A 本地（下载地址 + pull 命令）/ 路线 B 云端（`MODEL_BACKEND=zhipu` + `ZHIPU_API_KEY`） |

程序**不会替你下载模型**——几个 GB 的流量应该由用户自己决定。

---

## 三、对话接入

### 1) 命令行直连（最快验证链路）

走 Ollama 原生 `POST /api/chat`，`stream: true`，按行读 NDJSON：

```json
{"model":"qwen3:4b","messages":[...],"stream":true,"options":{"temperature":0.7}}
```

每行一个 JSON，正文在 `message.content`，**思考型模型的推理过程单独放在
`message.thinking`**（qwen3、deepseek-r1 都是）。两者混着取会把思维链当答案显示出来，
所以默认只打 `content`，要看推理加 `--thinking`。

多轮靠本地维护 `messages` 数组，`/clear` 清空上下文。

### 2) 让智鲸 AI 本体的后端用它

后端的 `MODEL_BACKEND` 早就支持 `ollama`（`app/rag/providers.py` 里走 `langchain_ollama`），
只差两件事：`langchain-ollama` 这个依赖，以及一份能启动的 `.env`。

```bash
python tools/ollama_bootstrap.py --write-env
```

写入 `backend/.env`：

```ini
MODEL_BACKEND=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
LLM_MODEL=qwen3:4b
EMBEDDING_MODEL=<检测到的向量模型，没有就不写>
```

只改这几个键，其它原样保留（改前自动备份成 `.env.bak`）。**同时补齐首次启动的硬性前提**：
`SecuritySettings` 在校验期就要求 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 非空，缺了直接
`ValidationError`；另外生成一个随机 `JWT_SECRET_KEY`，避免所有人共用默认密钥。
初始密码只打印一次。

### 3) 不改配置文件，直接起服务

```bash
python tools/ollama_bootstrap.py --serve --port 8000
```

用**环境变量**覆盖：pydantic-settings 里环境变量优先级高于 `.env`，所以退出后
不留下任何改装。它会设 `MODEL_BACKEND` / `OLLAMA_BASE_URL` / `LLM_MODEL` /
`SERVE_FRONTEND_DIR`，然后 `uvicorn app.main:app` 起一个同时提供前端与 API 的进程。

### 4) 客户端里的入口

客户端安装包里有 `检测本地模型.cmd`（同 `python tools\ollama_bootstrap.py`）。
首次启动缺 `.env` 时，`start.ps1` 会直接引导用户去双击它。

---

## 四、向量模型（上传/检索走的是另一条线）

对话用 `LLM_MODEL`，**文档检索用的是 `EMBEDDING_MODEL`**，两者独立。装完客户端后
「上传文档一直转圈」不是上传接口的问题，而是这一步出了问题：

1. 没配 `EMBEDDING_MODEL` 时默认 `nomic-embed-text`，多数机器根本没拉过；
2. 嵌入不可用时，**Chroma 会静默回落到它内置的 all-MiniLM-L6-v2 ONNX 模型**，
   首次使用要联网从 `chroma-onnx-models.s3.amazonaws.com` 下 **79 MB**；
3. 而这一步跑在同步路径上，会把事件循环一起卡死——连 `/token` 都不返回，
   页面表现就是「全站点不动」。

已经修掉的两处：

- `CachedEmbeddings` / `SimpleTFIDFEmbeddings` 都补了 `__call__`。
  chromadb 只认「可调用对象」当 embedding_function，否则一律回落默认模型。
- 安装器与 `--write-env` 现在一定会写 `EMBEDDING_MODEL`：
  本机有向量模型就用它，没有就写 `local-tfidf`（纯 Python，零下载）。

想用向量检索（效果更好）就先 `ollama pull nomic-embed-text`，再重建向量库：

```bash
# 维度变了必须重建，否则上传会报
# "Collection expecting embedding with dimension of 2048, got 768"
curl -X POST http://127.0.0.1:8000/rebuild-vectorstore -H "Authorization: Bearer <token>"
```

## 五、已知限制

- **向量模型是另一条线**：`MODEL_BACKEND=ollama` 的默认 embedding 是 `nomic-embed-text`。
  只有对话模型、没有向量模型时，聊天能用，但知识库检索会报错。想用 RAG 就
  `ollama pull nomic-embed-text`，程序检测到会自动写进 `.env`。
- **换 embedding 必须重建向量库**（维度不兼容），即 `POST /rebuild-vectorstore`。
- 不改 `backend/.env` 的话，网页端仍按原 `MODEL_BACKEND` 走，本程序只影响它自己的
  `--chat` / `--write-env` / `--serve` 三条路径。

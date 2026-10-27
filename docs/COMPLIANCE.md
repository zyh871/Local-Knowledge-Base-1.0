# 合规「零出域」部署清单

> 适用场景：政企、涉密、金融、医疗等要求「数据不出本机/本内网」的环境。
> 目标：把「我们默认不联网」从一句承诺，变成一条**可执行、可复核**的检查。

## 1. 一键自检

```bash
cd backend
python scripts/compliance_check.py            # 人类可读
python scripts/compliance_check.py --json     # 给 CI / 交付报告用
python scripts/compliance_check.py --strict   # 警告也算失败（交付闸门）
```

退出码：`0` 通过；`1` 存在阻断项。脚本**只读**，仅在数据目录写一个临时探针文件验证可写性。

## 2. 检查项与处置

| ID | 检查 | 通过标准 | 不通过怎么办 |
| --- | --- | --- | --- |
| `jwt_secret` | JWT 签名密钥 | 非默认值且 ≥24 位 | 设置 `JWT_SECRET_KEY` 为随机 48 字节 |
| `admin_credentials` | 管理员凭据 | 账号与口令均已设置 | 配置 `ADMIN_USERNAME` / `ADMIN_PASSWORD` |
| `prod_env` | 运行环境 | `APP_ENV=prod` | 交付环境改为 `prod`（否则错误体含调试信息） |
| `redis_fallback` | Redis 降级 | `REDIS_FALLBACK_TO_MEMORY=false` | 生产必须关闭，避免故障时静默变单机 |
| `fs_browse` | 本地文件浏览 | `WORKFLOW_FS_BROWSE=false` | 多用户/对外部署必须关闭 |
| `custom_models` | 自定义模型端点 | `ALLOW_CUSTOM_MODELS=false` | 多用户部署必须关闭 |
| `model_egress` | 模型数据出域 | 后端为 `ollama` / `local` / `mock` | 零出域场景切换为本地 Ollama |
| `cors` | 跨域白名单 | 显式域名列表，不含 `*` | 改为实际访问域名 |
| `data_writable` | 数据目录 | 存在且可写 | 修正权限或改 `DATA_PATH` |

## 3. 标准落地步骤

```bash
# 1) 准备环境模板（务必替换所有占位值）
cp deploy/offline/.env.offline.example .env

# 2) 本地拉起 Ollama 并准备模型（在宿主机执行）
ollama pull llama3.2:3b
ollama pull nomic-embed-text

# 3) 用离线覆盖配置启动
docker compose -f docker-compose.yml -f deploy/offline/docker-compose.offline.yml up -d

# 4) 自检并留存报告
cd backend && python scripts/compliance_check.py --json > ../deliverables/compliance-report.json
```

> ⚠️ 切换嵌入模型（`EMBEDDING_MODEL`）后必须重建向量库：`POST /rebuild-vectorstore`。
> 否则会因维度不一致报 `Collection expecting embedding with dimension of ...`。

## 4. 交付时应当说明的边界

- **模型推理是否出域**，取决于 `MODEL_BACKEND`。设为云端后端（智谱/OpenAI/ModelScope）
  时，问答内容会发送到对应上游；只有 `ollama` / `local` / `mock` 才是真正零出域。
- **AI 创作（文生图/视频）默认依赖云端供应商**，离线环境应在 `.env` 中留空
  `CREATION_PROVIDERS` / 相关 Key，该入口会自动置灰。
- **联网搜索**（Tavily）在离线环境不可用，属于预期行为；关闭后检索只走本地知识库。
- 本清单覆盖**配置层**风险；主机加固、网络隔离、审计日志归集需配合运维侧基线执行。

## 5. 与代码的对应关系

- 自检实现：`backend/scripts/compliance_check.py`（`run_checks()` 可被 CI 直接调用）
- 离线覆盖：`deploy/offline/docker-compose.offline.yml`
- 环境模板：`deploy/offline/.env.offline.example`
- 相关配置项定义：`backend/app/core/config.py`（`SecuritySettings` / `RedisSettings` / `WorkflowSettings` / `ModelSettings`）

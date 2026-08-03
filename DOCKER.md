# 容器化部署指南

## 前置条件
- Docker Engine 20.10+
- Docker Compose v2.0+

## 快速开始

### 1. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env，填入真实的 API 密钥
```

### 2. 构建并启动
```bash
docker compose up -d --build
```

### 3. 查看状态
```bash
docker compose ps
docker compose logs -f backend
docker compose logs -f frontend
```

### 4. 访问
- 前端：http://localhost
- 后端 API：http://localhost:8000
- API 文档：http://localhost:8000/docs

## 常用命令

| 操作 | 命令 |
|------|------|
| 启动 | `docker compose up -d` |
| 停止 | `docker compose down` |
| 重启 | `docker compose restart` |
| 查看日志 | `docker compose logs -f [service]` |
| 进入容器 | `docker compose exec backend bash` |
| 重新构建 | `docker compose build --no-cache` |
| 清理 | `docker compose down -v`（同时删除数据卷） |

## 数据持久化
- `rag-backend-data` - 业务数据（用户、对话、分享链接）
- `rag-backend-chroma` - 向量数据库
- `rag-backend-logs` - 日志文件

## 网络架构
- `rag-network`：bridge 网络
- 前端通过 nginx 反向代理 `/api/*` 到后端 `backend:8000`
- 共享同一网络避免暴露后端端口到主机（可选）

## 端口修改
修改 `.env` 文件：
```
BACKEND_PORT=8000
FRONTEND_PORT=80
```

## 故障排查

### 1. 容器启动失败
```bash
docker compose logs backend
```

### 2. 模型加载慢
首次启动会下载模型文件，耐心等待。可查看日志：
```bash
docker compose logs -f backend
```

### 3. 向量库权限问题
```bash
docker compose exec backend ls -la /app/chroma_db
```

### 4. 重新初始化向量库
```bash
docker compose down
docker volume rm rag-backend-chroma
docker compose up -d
```

## 生产环境建议
1. 务必修改 `JWT_SECRET_KEY` 为强随机字符串
2. 配置 HTTPS（推荐使用反向代理如 Caddy/Nginx + Let's Encrypt）
3. 限制 `CORS_ORIGINS` 为实际域名
4. 启用日志收集（如 ELK、Loki）
5. 定期备份 `rag-backend-data` 和 `rag-backend-chroma` 卷
6. 监控容器资源使用情况

# 阶段 1：构建依赖
FROM python:3.11-slim AS builder

WORKDIR /app

# 系统依赖（用于编译 pdfplumber、pypdf 等 C 扩展 + tesseract OCR）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    build-essential \
    libssl-dev \
    libffi-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 先复制 requirements 利用 Docker 缓存
COPY requirements.txt .

# 使用国内 pip 镜像加速
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# 阶段 2：运行时镜像
FROM python:3.11-slim AS runtime

WORKDIR /app

# 运行时系统依赖（tesseract OCR + PDF + 图像处理）
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    tesseract-ocr-chi-tra \
    tesseract-ocr-eng \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 阶段复制已安装的 Python 包
COPY --from=builder /install /usr/local

# 创建非 root 用户
RUN groupadd -r appuser && useradd -r -g appuser -m -d /home/appuser appuser

# 复制应用代码
COPY --chown=appuser:appuser . .

# 创建数据/日志/向量库目录
RUN mkdir -p /app/data /app/chroma_db /app/logs && \
    chown -R appuser:appuser /app

# 切换到非 root 用户
USER appuser

# 环境变量
ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_PATH=/app/data \
    CHROMA_PERSIST_DIR=/app/chroma_db \
    LOG_DIR=/app/logs

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

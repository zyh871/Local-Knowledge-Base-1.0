# 阶段 1：构建 Vue 应用
FROM node:20-slim AS builder

WORKDIR /app

# 国内 npm 镜像加速
RUN npm config set registry https://registry.npmmirror.com

# 复制 package.json
COPY package.json package-lock.json* ./

# 安装依赖
RUN npm install --no-audit --no-fund

# 复制源码并构建
COPY . .

# 构建参数：API 地址（运行时通过 nginx 代理，无需在构建时指定）
ARG VITE_API_URL=/api
ENV VITE_API_URL=${VITE_API_URL}

RUN npm run build

# 阶段 2：使用 nginx 服务静态文件
FROM nginx:1.27-alpine AS runtime

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q --spider http://localhost/ || exit 1

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]

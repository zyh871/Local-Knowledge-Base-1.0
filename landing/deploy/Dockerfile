# 智鲸 AI 产品介绍页 · 容器镜像
#
# 构建：docker build -t zhijing-landing .        （在本文件所在目录执行）
#     但 index.html 在上一级，所以请用：
#     docker build -t zhijing-landing -f deploy/Dockerfile .
#
# 运行：docker run --rm -p 8080:80 zhijing-landing
# 访问：http://localhost:8080

FROM nginx:1.27-alpine

# 只复制页面本体与站点配置，镜像因此只有几 MB
COPY index.html /usr/share/nginx/html/index.html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf

# 健康检查：镜像自身能返回 200 才算就绪
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1

EXPOSE 80

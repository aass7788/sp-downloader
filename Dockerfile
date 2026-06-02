# Node.js + yt-dlp 下载服务
FROM node:22-alpine

# 安装 Python 和 yt-dlp
RUN apk add --no-cache python3 py3-pip ffmpeg tini && \
    pip3 install --no-cache-dir --break-system-packages yt-dlp && \
    addgroup -g 1000 appuser && \
    adduser -u 1000 -G appuser -D -h /app appuser

WORKDIR /app

# 依赖层
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && chown -R appuser:appuser /app

# 应用代码
COPY server.js ./
COPY public/ ./public/
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

# 下载目录
RUN mkdir -p /app/downloads && chown -R appuser:appuser /app

USER appuser
EXPOSE 3456

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://localhost:3456/api/health || exit 1

ENTRYPOINT ["/sbin/tini", "--", "./entrypoint.sh"]

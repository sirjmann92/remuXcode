# Stage 1: Build frontend
# Pin to linux/amd64 so this stage always runs natively on the CI runner.
# Node.js 22 uses CPU instructions that QEMU cannot emulate, causing SIGILL
# (exit 132) when building for arm64 under QEMU. The output is pure static
# files (JS/CSS/HTML), so the build architecture does not matter.
FROM --platform=linux/amd64 node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --silent && npm cache clean --force
COPY frontend/ ./
RUN npm run build \
    && rm -rf node_modules src package*.json

# Stage 2: Backend (FastAPI)
FROM python:3.13-alpine AS backend
WORKDIR /app

COPY requirements.txt /app/requirements.txt

# Install system dependencies, create users/groups, and install Python deps
RUN apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev \
    && apk add --no-cache ffmpeg su-exec shadow \
    && addgroup -g 1000 appgroup \
    && adduser -u 1000 -G appgroup -D -s /bin/sh appuser \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && apk del .build-deps \
    && rm -rf /root/.cache/pip /tmp/* /var/cache/apk/*

ARG APP_VERSION=dev
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PUID=1000 \
    PGID=1000 \
    APP_VERSION=${APP_VERSION} \
    REMUXCODE_CONFIG_PATH=/app/config/config.yaml \
    REMUXCODE_DB_PATH=/app/config/jobs.db

# Copy backend code
COPY backend/ /app/backend/

# Copy frontend build output and startup script
COPY --from=frontend-build /frontend/build /app/frontend/build
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh \
    && mkdir -p /app/logs /app/config

EXPOSE 7889

USER root
ENTRYPOINT ["/app/start.sh"]

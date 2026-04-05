# check=skip=FromPlatformFlagConstDisallowed

# ── Stage 1: Build frontend ──────────────────────────────────────────
# Pin to linux/amd64 so this stage always runs natively on the CI runner.
# Node.js uses CPU instructions that QEMU cannot emulate, causing SIGILL
# (exit 132) when building for arm64 under QEMU. The output is pure static
# files (JS/CSS/HTML), so the build architecture does not matter.
FROM --platform=linux/amd64 node:25-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --silent && npm cache clean --force
COPY frontend/ ./
RUN npm run build \
    && rm -rf node_modules src package*.json

# ── Stage 2: Runtime (FastAPI + FFmpeg) ──────────────────────────────
# Debian Trixie ships FFmpeg 7.1 with QSV (libvpl), VAAPI, and NVENC
# support built in.  Intel GPU drivers + oneVPL runtime are installed
# from the same repos — apt handles all transitive dependencies.
FROM python:3.14-slim AS backend
WORKDIR /app

COPY requirements.txt /app/requirements.txt

ARG APP_VERSION=dev
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    PUID=1000 \
    PGID=1000 \
    APP_VERSION=${APP_VERSION} \
    REMUXCODE_CONFIG_PATH=/app/config/config.yaml \
    REMUXCODE_DB_PATH=/app/config/jobs.db

RUN set -eux; \
    # Enable non-free-firmware for Intel GPU encoding (HEVC/AV1 via QSV)
    sed -i 's/Components: main/Components: main non-free non-free-firmware/' \
        /etc/apt/sources.list.d/debian.sources; \
    apt-get update; \
    # Intel GPU packages (QSV/VAAPI encode) are x86-only
    INTEL_PKGS=""; \
    if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
        INTEL_PKGS="intel-media-va-driver-non-free libvpl2 libmfx-gen1.2"; \
    fi; \
    apt-get install -y --no-install-recommends gosu ffmpeg $INTEL_PKGS; \
    # ── Trim ~192 MB of Mesa/LLVM deps that are dlopen'd, never linked ──
    # SDL→GBM→Mesa→gallium→LLVM→z3 chain: only for OpenGL display output.
    # Verified NOT in ldd chain of ffmpeg/ffprobe.
    LIBDIR="/usr/lib/$(dpkg --print-architecture | sed 's/amd64/x86_64-linux-gnu/;s/arm64/aarch64-linux-gnu/')"; \
    rm -f "$LIBDIR"/libLLVM*.so* \
          "$LIBDIR"/libgallium*.so* \
          "$LIBDIR"/libz3*.so*; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 appgroup \
    && useradd -u 1000 -g appgroup -s /bin/sh -M appuser

RUN pip install --no-cache-dir -r /app/requirements.txt \
    && rm -rf /root/.cache /tmp/*

COPY backend/ /app/backend/
COPY --from=frontend-build /frontend/build /app/frontend/build
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh && mkdir -p /app/logs /app/config

EXPOSE 7889
USER root
ENTRYPOINT ["/app/start.sh"]

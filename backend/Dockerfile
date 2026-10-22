# =========================
# 0) 可配置镜像仓库前缀（国内加速）
#    默认空串 = Docker Hub 官方; 需要镜像加速时可传自有 registry 前缀
# =========================
ARG REGISTRY=""
FROM ${REGISTRY}python:3.13-slim

# =========================
# 1) 可配置的软件源（默认官方源）
# =========================
ARG APT_MIRROR=deb.debian.org
ARG PIP_INDEX_URL=https://pypi.org/simple/
ARG PIP_TRUSTED_HOST=""

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# =========================
# 2) 换 apt 源 + 安装系统依赖
#    你是 Debian slim，sources 在 /etc/apt/sources.list.d/ 里更常见
# =========================
RUN set -eux; \
    # 可通过 build args 替换 deb.debian.org / security.debian.org
    sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    sed -i "s|security.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true; \
    sed -i "s|security.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true; \
    \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        libffi-dev \
        curl \
        fonts-wqy-microhei \
    ; \
    rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 先复制依赖文件（利用Docker缓存）
COPY requirements.txt .

# =========================
# 3) 安装 PyTorch (CUDA 12.4, 兼容 GRID 535 驱动 / L20 vGPU)
#    必须在 requirements.txt 之前安装，否则 sentence-transformers
#    会从 PyPI 拉取默认 torch，版本可能不匹配
#    cu124 通过 CUDA 前向兼容在 535 驱动上运行
#    TORCH_FIND_LINKS 非空时走扁平 wheels 目录，不把该 URL 当作 PEP 503 index。
#    钉死 +cu124，避免第三方 PyPI 镜像解析到 CPU 轮。
# =========================
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124
ARG TORCH_FIND_LINKS=""
ARG TORCH_PINNED=torch==2.6.0+cu124
RUN --mount=type=cache,target=/root/.cache/pip \
    set -eux; \
    trusted_host_args=""; \
    if [ -n "${PIP_TRUSTED_HOST}" ]; then trusted_host_args="--trusted-host ${PIP_TRUSTED_HOST}"; fi; \
    pip install --upgrade pip; \
    if [ -n "${TORCH_FIND_LINKS}" ]; then \
      pip install "${TORCH_PINNED}" \
        --find-links "${TORCH_FIND_LINKS}" \
        -i "${PIP_INDEX_URL}" \
        ${trusted_host_args} \
        --retries 5 --timeout 600; \
    else \
      pip install torch --index-url "${TORCH_INDEX_URL}" \
        --retries 5 --timeout 600; \
    fi

# =========================
# 4) pip 安装其余依赖（复用已安装的 torch）
# =========================
RUN --mount=type=cache,target=/root/.cache/pip \
    set -eux; \
    trusted_host_args=""; \
    if [ -n "${PIP_TRUSTED_HOST}" ]; then trusted_host_args="--trusted-host ${PIP_TRUSTED_HOST}"; fi; \
    pip install -r requirements.txt -i "${PIP_INDEX_URL}" ${trusted_host_args}

# 复制应用代码
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY migrations_alembic/ ./migrations_alembic/
COPY alembic.ini ./alembic.ini

# 单机镜像传 STRIP_PY=1：业务代码只留字节码。默认 0，云端构建仍是明文 .py。
# 迁移目录保留 .py，现有启动命令要靠 alembic 读取它们。
ARG STRIP_PY=0
RUN if [ "$STRIP_PY" = "1" ]; then \
      python -m compileall -b -q app scripts && \
      find app scripts -name '*.py' -delete; \
    fi

# 预创建 storage 子目录（Volume 挂载时保留权限）
RUN mkdir -p /app/storage/uploads /app/storage/chunks

# 创建非root用户并设置权限
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令: 先执行数据库迁移，再启动应用
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

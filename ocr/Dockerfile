# OCR Sidecar 服务 -- 独立容器运行 PaddleOCR GPU
# 与 backend 容器物理隔离，避免 paddlepaddle-gpu 与 torch 的 cuDNN 冲突
ARG REGISTRY=""
FROM ${REGISTRY}python:3.13-slim

ARG APT_MIRROR=deb.debian.org
ARG PIP_INDEX_URL=https://pypi.org/simple/
ARG PIP_TRUSTED_HOST=""
ARG PADDLE_INDEX_URL=https://www.paddlepaddle.org.cn/packages/stable/cu118/
ARG PADDLE_PACKAGE=paddlepaddle-gpu==3.2.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    PADDLE_PDX_MODEL_SOURCE=BOS

RUN set -eux; \
    sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    sed -i "s|security.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        libffi-dev \
        curl \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    ; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安装 PaddlePaddle (GPU/CPU 由 PADDLE_PACKAGE build arg 控制)
RUN pip install --upgrade pip && \
    pip install ${PADDLE_PACKAGE} --index-url "${PADDLE_INDEX_URL}"

# 安装其余依赖
COPY requirements.txt .
RUN set -eux; \
    trusted_host_args=""; \
    if [ -n "${PIP_TRUSTED_HOST}" ]; then trusted_host_args="--trusted-host ${PIP_TRUSTED_HOST}"; fi; \
    pip install -r requirements.txt -i "${PIP_INDEX_URL}" ${trusted_host_args}

COPY main.py structure_engine.py structure_blocks.py cpu_quota.py ./

# 与后端相同：默认不剥离，单机镜像传 STRIP_PY=1 后只留字节码。
ARG STRIP_PY=0
RUN if [ "$STRIP_PY" = "1" ]; then \
      python -m compileall -b -q main.py structure_engine.py structure_blocks.py cpu_quota.py && \
      rm -f main.py structure_engine.py structure_blocks.py cpu_quota.py; \
    fi

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

# BUG-823: 显式拉大服务端闲置 keep-alive 窗口(默认仅 5s), 必须远大于后端
# ocr_engine 客户端的 keepalive_expiry(30s), 否则两侧临界同时到期会产生
# "服务端刚关闭/客户端刚复用"的陈旧连接竞态(ReadError)。
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000", "--timeout-keep-alive", "75"]

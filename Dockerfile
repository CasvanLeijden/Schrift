# syntax=docker/dockerfile:1

# Build the editor bundle and the stylesheet.
FROM node:22-slim AS assets
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && npm ci --no-audit --no-fund
COPY frontend frontend
COPY scripts/tailwind.sh scripts/
COPY schrift/static/css schrift/static/css
COPY schrift/templates schrift/templates
RUN mkdir -p schrift/static/dist && cd frontend && npm run build && cd .. && scripts/tailwind.sh --minify

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    SCHRIFT_DATA_DIR=/data \
    HF_HOME=/data/models/huggingface \
    XDG_CACHE_HOME=/data/cache \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra semantic --no-install-project
COPY schrift schrift
COPY --from=assets /src/schrift/static/dist schrift/static/dist
RUN uv sync --frozen --no-dev --extra semantic

ARG GIT_SHA=dev
ENV SCHRIFT_BUILD=$GIT_SHA

RUN useradd --system --uid 1000 --home-dir /app schrift \
    && mkdir /data && chown schrift:schrift /data
USER schrift
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

CMD ["uvicorn", "schrift.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

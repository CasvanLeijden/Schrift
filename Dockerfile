# syntax=docker/dockerfile:1

# Build the stylesheet with the pinned Tailwind standalone CLI.
FROM debian:trixie-slim AS css
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY scripts/tailwind.sh scripts/
COPY schrift/static/css schrift/static/css
COPY schrift/templates schrift/templates
RUN scripts/tailwind.sh --minify

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    SCHRIFT_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY schrift schrift
COPY --from=css /src/schrift/static/dist schrift/static/dist
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 1000 --home-dir /app schrift \
    && mkdir /data && chown schrift:schrift /data
USER schrift
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

CMD ["uvicorn", "schrift.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

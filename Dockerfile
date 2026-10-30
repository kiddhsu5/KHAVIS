# syntax=docker/dockerfile:1.7
# ---- Stage 1: builder --------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --prefix=/install -r requirements.txt

# ---- Stage 2: runtime --------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="khavis" \
      org.opencontainers.image.description="A plugin-based router for multiple LLM providers" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/kiddhsu5/KHAVIS"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 khavis \
    && useradd  --system --uid 1000 --gid khavis --create-home khavis

WORKDIR /app

COPY --from=builder /install /usr/local
COPY requirements.txt ./
COPY core/        ./core/
COPY providers/   ./providers/
COPY scripts/     ./scripts/
COPY config/      ./config/

RUN chmod +x ./scripts/start.sh && chown -R khavis:khavis /app

USER khavis

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl --fail --silent http://localhost:8080/health || exit 1

ENTRYPOINT ["./scripts/start.sh"]
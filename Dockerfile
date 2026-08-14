FROM python:3.13-alpine AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

FROM base AS builder

RUN apk add --no-cache gcc musl-dev bash

RUN python -m venv /venv

COPY . .

RUN uv sync --locked

RUN scripts/update_protos.sh

RUN uv export --locked --no-dev --no-emit-project \
    --format requirements-txt --output-file /tmp/requirements.txt \
    && /venv/bin/pip install --require-hashes -r /tmp/requirements.txt \
    && uv build \
    && /venv/bin/pip install --no-deps dist/*.whl


FROM node:23-alpine AS frontend_builder

RUN apk add --no-cache protobuf protobuf-dev tree

WORKDIR /app

COPY frontend/package.json frontend/pnpm-lock.yaml /app/frontend/
RUN cd /app/frontend && npm install -g pnpm@10 && pnpm install --frozen-lockfile

COPY frontend /app/frontend
COPY proto /app/proto


RUN cd /app/frontend && pnpm proto-generate && pnpm build

FROM base AS app

ARG BAMBU_SPOOLMAN_VERSION=local
ARG BAMBU_SPOOLMAN_BUILD_NUMBER=local
ARG BAMBU_SPOOLMAN_REVISION=unknown
ARG BAMBU_SPOOLMAN_BUILD_DATE=unknown

RUN apk add --no-cache supervisor nodejs pnpm

ENV LOGURU_LEVEL=INFO \
    BAMBU_SPOOLMAN_VERSION=${BAMBU_SPOOLMAN_VERSION} \
    BAMBU_SPOOLMAN_BUILD_NUMBER=${BAMBU_SPOOLMAN_BUILD_NUMBER} \
    BAMBU_SPOOLMAN_REVISION=${BAMBU_SPOOLMAN_REVISION} \
    BAMBU_SPOOLMAN_BUILD_DATE=${BAMBU_SPOOLMAN_BUILD_DATE}

LABEL org.opencontainers.image.title="bambu-spoolman" \
      org.opencontainers.image.version=${BAMBU_SPOOLMAN_VERSION} \
      org.opencontainers.image.revision=${BAMBU_SPOOLMAN_REVISION} \
      org.opencontainers.image.created=${BAMBU_SPOOLMAN_BUILD_DATE} \
      io.github.mrkirby153.bambu-spoolman.build-number=${BAMBU_SPOOLMAN_BUILD_NUMBER}

COPY --from=builder /venv /venv
COPY --from=frontend_builder /app/frontend/public /app/frontend/public
COPY --from=frontend_builder /app/frontend/.next/standalone /app/frontend
COPY --from=frontend_builder /app/frontend/.next/static /app/frontend/.next/static

COPY conf/supervisord.conf /app/supervisord.conf

CMD ["supervisord", "-c", "/app/supervisord.conf"]

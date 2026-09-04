FROM ghcr.io/astral-sh/uv:0.12.4@sha256:d0a6eca6c669dc7e9c51218707b8438a3d30402733d739dcc00adb3e213e8f5c AS uv

FROM python:3.13-alpine@sha256:7415fbc3c9e4979cc717d92377ab2bc7b2b4a2af1ac03cc52b5f3f88efedaf3a AS python_base

WORKDIR /app

FROM python_base AS build_base

COPY --from=uv /uv /uvx /bin/

FROM build_base AS builder

RUN python -m venv --without-pip /venv

COPY . .

RUN uv sync --locked

RUN scripts/update_protos.sh

RUN uv export --locked --no-dev --no-emit-project \
    --format requirements-txt --output-file /tmp/requirements.txt \
    && uv pip install --python /venv/bin/python \
        --require-hashes -r /tmp/requirements.txt \
    && uv build \
    && uv pip install --python /venv/bin/python --no-deps dist/*.whl

FROM builder AS backend_verifier

RUN .venv/bin/python -m unittest discover -s tests \
    && .venv/bin/ruff check . \
    && .venv/bin/ruff format --check . \
    && touch /tmp/backend-verified


FROM node:24-alpine@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43 AS frontend_builder

RUN apk add --no-cache protobuf protobuf-dev

WORKDIR /app

COPY frontend/package.json frontend/pnpm-lock.yaml /app/frontend/
RUN npm install --global pnpm@10.34.5 \
    && cd /app/frontend \
    && pnpm install --frozen-lockfile

COPY frontend /app/frontend
COPY proto /app/proto


RUN cd /app/frontend && pnpm proto-generate && pnpm build

FROM frontend_builder AS frontend_verifier

RUN cd /app/frontend \
    && pnpm lint \
    && touch /tmp/frontend-verified


FROM scratch AS verify

COPY --from=backend_verifier /tmp/backend-verified /
COPY --from=frontend_verifier /tmp/frontend-verified /

FROM python_base AS app

ARG BAMBU_SPOOLMAN_VERSION=local
ARG BAMBU_SPOOLMAN_BUILD_NUMBER=local
ARG BAMBU_SPOOLMAN_REVISION=unknown
ARG BAMBU_SPOOLMAN_BUILD_DATE=unknown

RUN apk upgrade --no-cache \
    && apk add --no-cache nodejs tini \
    && rm -rf \
        /usr/local/lib/python3.13/site-packages/pip \
        /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
        /usr/local/bin/pip \
        /usr/local/bin/pip3 \
        /usr/local/bin/pip3.13

ENV LOGURU_LEVEL=INFO \
    BAMBU_SPOOLMAN_VERSION=${BAMBU_SPOOLMAN_VERSION} \
    BAMBU_SPOOLMAN_BUILD_NUMBER=${BAMBU_SPOOLMAN_BUILD_NUMBER} \
    BAMBU_SPOOLMAN_REVISION=${BAMBU_SPOOLMAN_REVISION} \
    BAMBU_SPOOLMAN_BUILD_DATE=${BAMBU_SPOOLMAN_BUILD_DATE}

LABEL org.opencontainers.image.title="bambu-spoolman" \
      org.opencontainers.image.version=${BAMBU_SPOOLMAN_VERSION} \
      org.opencontainers.image.revision=${BAMBU_SPOOLMAN_REVISION} \
      org.opencontainers.image.created=${BAMBU_SPOOLMAN_BUILD_DATE} \
      io.github.jeversol.bambu-spoolman.build-number=${BAMBU_SPOOLMAN_BUILD_NUMBER}

COPY --from=builder /venv /venv
COPY --from=frontend_builder /app/frontend/public /app/frontend/public
COPY --from=frontend_builder /app/frontend/.next/standalone /app/frontend
COPY --from=frontend_builder /app/frontend/.next/static /app/frontend/.next/static

COPY --chmod=755 scripts/container-entrypoint.sh /app/container-entrypoint.sh

ENTRYPOINT ["tini", "--", "/app/container-entrypoint.sh"]

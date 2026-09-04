FROM ghcr.io/astral-sh/uv:0.12.4@sha256:d0a6eca6c669dc7e9c51218707b8438a3d30402733d739dcc00adb3e213e8f5c AS uv

FROM python:3.14-alpine@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc AS python_base

WORKDIR /app

FROM python_base AS build_base

COPY --from=uv /uv /uvx /bin/

FROM build_base AS builder

RUN python -m venv --without-pip /venv

COPY pyproject.toml uv.lock README.md ./

# Keep third-party dependencies cached when only application source changes.
RUN uv sync --locked --no-install-project

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


FROM node:24-alpine@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf AS frontend_builder

RUN apk add --no-cache protobuf protobuf-dev

WORKDIR /app

COPY frontend/package.json frontend/pnpm-lock.yaml /app/frontend/
RUN pnpm_version=$(node -p "require('/app/frontend/package.json').packageManager.split('@')[1]") \
    && npm install --global "pnpm@$pnpm_version" \
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
        /usr/local/lib/python3.14/site-packages/pip \
        /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
        /usr/local/bin/pip \
        /usr/local/bin/pip3 \
        /usr/local/bin/pip3.14

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

# Make the CI image depend on both verifier stages without copying their marker
# files into the resulting application image. BuildKit can run the backend and
# frontend verification branches in parallel with independent app-only work.
FROM app AS verified_app

RUN --mount=from=backend_verifier,source=/tmp/backend-verified,target=/tmp/backend-verified,ro \
    --mount=from=frontend_verifier,source=/tmp/frontend-verified,target=/tmp/frontend-verified,ro \
    test -f /tmp/backend-verified \
    && test -f /tmp/frontend-verified

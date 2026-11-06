FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM python:3.14-alpine3.24@sha256:a18b20ad69e74df6cf6d2e1b1a260d7a1ecf5380fd83b310f83ba88c461cf894 AS python_base

WORKDIR /app

FROM python_base AS build_base

COPY --from=uv /uv /uvx /bin/

FROM python_base AS runtime_base

# Keep the security floor while allowing maintained repository revisions.
RUN apk add --no-cache \
        'libuuid>=2.42.3-r0' \
        su-exec \
    && addgroup -S -g 10001 bambu-spoolman \
    && adduser -S -D -H -u 10001 -G bambu-spoolman bambu-spoolman \
    && mkdir -p /config \
    && chown 10001:10001 /config \
    && rm -rf \
        /usr/local/lib/python3.14/site-packages/pip \
        /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
        /usr/local/bin/pip \
        /usr/local/bin/pip3 \
        /usr/local/bin/pip3.14

FROM runtime_base AS runtime_verifier

COPY --chmod=755 scripts/container-init.sh /app/container-init.sh

RUN mkdir -p /tmp/config/checkpoint \
    && touch /tmp/config/settings.json /tmp/config/checkpoint/metadata.json \
    && BAMBU_SPOOLMAN_CONFIG=/tmp/config /app/container-init.sh \
        sh -c 'test "$(id -u)" = 10001 \
            && test "$(id -g)" = 10001 \
            && test -w /tmp/config/settings.json \
            && test -w /tmp/config/checkpoint/metadata.json' \
    && test "$(stat -c %u:%g /tmp/config/settings.json)" = 10001:10001 \
    && test "$(stat -c %u:%g /tmp/config/checkpoint/metadata.json)" = 10001:10001 \
    && touch /tmp/runtime-verified

FROM build_base AS builder

RUN python -m venv --without-pip /venv

COPY pyproject.toml uv.lock README.md ./

# Keep third-party dependencies cached when only application source changes.
RUN uv sync --locked --no-install-project

COPY . .

RUN uv sync --locked

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


# Keep Node independent of Alpine's rotating package revisions. Production runs
# Python and static assets; no Debian runtime libraries are copied into it.
FROM node:24-trixie-slim@sha256:54f84b4cf8432b21aaffd5d3ce8d41e12cea0d10d33c09d2f3c9879d49d6b446 AS frontend_toolchain

WORKDIR /app

COPY frontend/package.json /app/frontend/package.json
RUN pnpm_version=$(node -p "require('/app/frontend/package.json').packageManager.split('@')[1]") \
    && npm install --global "pnpm@$pnpm_version"

FROM frontend_toolchain AS frontend_builder

COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml /app/frontend/
RUN cd /app/frontend && pnpm install --frozen-lockfile

COPY frontend /app/frontend


RUN cd /app/frontend && pnpm build

FROM frontend_builder AS frontend_verifier

RUN cd /app/frontend \
    && pnpm test \
    && pnpm lint \
    && touch /tmp/frontend-verified


FROM scratch AS verify

COPY --from=backend_verifier /tmp/backend-verified /
COPY --from=frontend_verifier /tmp/frontend-verified /

FROM runtime_base AS app

ARG BAMBU_SPOOLMAN_VERSION=local
ARG BAMBU_SPOOLMAN_BUILD_NUMBER=local
ARG BAMBU_SPOOLMAN_REVISION=unknown
ARG BAMBU_SPOOLMAN_BUILD_DATE=unknown

ENV LOGURU_LEVEL=INFO \
    BAMBU_SPOOLMAN_CONFIG=/config \
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
COPY --from=frontend_builder /app/frontend/dist /app/static
# Scanner metadata for the browser's production npm dependency closure. This is
# outside the web root and contains package metadata, not executable Node code.
COPY --from=frontend_builder /app/frontend/inventory /app/frontend-inventory

COPY --chmod=755 scripts/container-init.sh /app/container-init.sh

ENTRYPOINT ["/app/container-init.sh"]

# CI tests and scans this image independently of backend/frontend verification.
FROM app AS runtime_checked_app

RUN --mount=from=runtime_verifier,source=/tmp/runtime-verified,target=/tmp/runtime-verified,ro \
    test -f /tmp/runtime-verified

# Local verification and publishing still require every verifier stage.
FROM runtime_checked_app AS verified_app

RUN --mount=from=backend_verifier,source=/tmp/backend-verified,target=/tmp/backend-verified,ro \
    --mount=from=frontend_verifier,source=/tmp/frontend-verified,target=/tmp/frontend-verified,ro \
    test -f /tmp/backend-verified \
    && test -f /tmp/frontend-verified

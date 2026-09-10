#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backend_image="bambu-spoolman:dependency-audit"

docker build --target builder --tag "$backend_image" "$repository_root"

docker run --rm --entrypoint /bin/sh "$backend_image" -c '
    cd /app
    # Audit the complete environment used by the builder, including tools such
    # as grpcio-tools and Ruff that execute against pull-request content.
    uv export --locked --no-emit-project \
        --format requirements-txt --output-file /tmp/audit-requirements.txt >/dev/null
    uvx pip-audit==2.10.1 --requirement /tmp/audit-requirements.txt \
        --progress-spinner off
'

docker run --rm \
    --volume "$repository_root/frontend:/workspace:ro" \
    --workdir /workspace \
    node:24-alpine@sha256:50c8e8ca1d27439048670df5883f32d57cf81cff6233222c893fd0d9884cbd81 \
    /bin/sh -c '
        pnpm_version=$(node -p "require(\"./package.json\").packageManager.split(\"@\")[1]")
        npm install --global "pnpm@$pnpm_version" >/dev/null
        pnpm audit --audit-level high
    '

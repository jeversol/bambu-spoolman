#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backend_image="bambu-spoolman:dependency-audit"

docker build --target builder --tag "$backend_image" "$repository_root"

docker run --rm --entrypoint /bin/sh "$backend_image" -c '
    cd /app
    uv export --locked --no-dev --no-emit-project \
        --format requirements-txt --output-file /tmp/audit-requirements.txt >/dev/null
    uvx pip-audit==2.10.1 --requirement /tmp/audit-requirements.txt \
        --progress-spinner off
'

docker run --rm \
    --volume "$repository_root/frontend:/workspace:ro" \
    --workdir /workspace \
    node:24-alpine@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43 \
    /bin/sh -c '
        npm install --global pnpm@10.34.5 >/dev/null
        pnpm audit --audit-level high
    '

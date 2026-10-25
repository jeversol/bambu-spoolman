#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image=bambu-spoolman:renovate-validator

docker build --target frontend_toolchain --tag "$image" "$repository_root"
docker run --rm \
    --volume "$repository_root:/workspace:ro" \
    --workdir /workspace \
    --entrypoint node \
    "$image" scripts/validate-renovate.mjs

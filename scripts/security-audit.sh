#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backend_image="bambu-spoolman:dependency-audit"
audit_mode=${1:-gate}

case "$audit_mode" in
    gate|report)
        ;;
    *)
        echo "usage: $0 [gate|report]" >&2
        exit 2
        ;;
esac

docker build --target builder --tag "$backend_image" "$repository_root"

audit_backend() {
    docker run --rm \
        --env AUDIT_MODE="$audit_mode" \
        --entrypoint /bin/sh \
        "$backend_image" -c '
    cd /app
    if [ "$AUDIT_MODE" = gate ]; then
        # Release enforcement covers dependencies copied into the runtime image.
        uv export --locked --no-dev --no-emit-project \
            --format requirements-txt --output-file /tmp/audit-requirements.txt >/dev/null
    else
        # Scheduled reporting also covers build and development tools.
        uv export --locked --no-emit-project \
            --format requirements-txt --output-file /tmp/audit-requirements.txt >/dev/null
    fi
    uvx pip-audit==2.10.1 --requirement /tmp/audit-requirements.txt \
        --progress-spinner off
'
}

audit_frontend() {
    docker run --rm \
        --env AUDIT_MODE="$audit_mode" \
        --volume "$repository_root/frontend:/workspace:ro" \
        --workdir /workspace \
        node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 \
        /bin/sh -c '
        pnpm_version=$(node -p "require(\"./package.json\").packageManager.split(\"@\")[1]")
        npm install --global "pnpm@$pnpm_version" >/dev/null
        if [ "$AUDIT_MODE" = gate ]; then
            # Block high and critical runtime findings, plus critical findings
            # anywhere in the dependency graph. High development-only findings
            # remain visible through Dependabot and scheduled reporting.
            pnpm audit --prod --audit-level high
            pnpm audit --audit-level critical
        else
            pnpm audit --audit-level high
        fi
    '
}

if [ "$audit_mode" = gate ]; then
    audit_backend
    audit_frontend
else
    audit_status=0
    audit_backend || audit_status=1
    audit_frontend || audit_status=1
    exit "$audit_status"
fi

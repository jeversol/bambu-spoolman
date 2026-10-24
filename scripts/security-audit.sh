#!/bin/sh

set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backend_image="bambu-spoolman:dependency-audit"
frontend_image="bambu-spoolman:frontend-audit"
audit_mode=${1:-gate}

case "$audit_mode" in
    gate|report)
        ;;
    *)
        echo "usage: $0 [gate|report]" >&2
        exit 2
        ;;
esac

audit_backend() {
    docker build --target builder --tag "$backend_image" "$repository_root" || return
    docker run --rm \
        --env AUDIT_MODE="$audit_mode" \
        --entrypoint /bin/sh \
        "$backend_image" -ec '
    cd /app
    uv export --locked --no-emit-project \
        --format requirements-txt --output-file /tmp/audit-requirements.txt >/dev/null
    uvx pip-audit==2.10.1 --requirement /tmp/audit-requirements.txt \
        --progress-spinner off
'
}

audit_frontend() {
    docker build --target frontend_toolchain --tag "$frontend_image" "$repository_root" || return
    docker run --rm \
        --env AUDIT_MODE="$audit_mode" \
        --volume "$repository_root/frontend:/workspace:ro" \
        --workdir /workspace \
        --entrypoint /bin/sh \
        "$frontend_image" \
        -ec '
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
    # Python runtime severity is enforced by the production-image scan.
    audit_frontend
else
    audit_status=0
    audit_backend || audit_status=1
    audit_frontend || audit_status=1
    exit "$audit_status"
fi

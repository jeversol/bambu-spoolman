#!/bin/sh

set -eu

image=${1:-bambu-spoolman:ci}
repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
container_id=""
volume=""

cleanup() {
    if [ -n "$container_id" ]; then
        docker rm --force "$container_id" >/dev/null 2>&1 || true
    fi
    if [ -n "$volume" ]; then
        docker volume rm "$volume" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
trap 'exit 1' INT TERM

volume=$(docker volume create)
# Exercise migration of an existing, root-owned configuration volume.
docker run --rm --entrypoint /bin/sh --volume "$volume:/config" "$image" -ec '
    mkdir -p /config/checkpoint
    printf "%s\n" '\''{"trays":{"0":42},"tray_count":4,"locked_trays":[]}'\'' > /config/settings.json
    printf "%s\n" '\''{"smoke_test":"persisted"}'\'' > /config/checkpoint/smoke-test.json
'
container_id=$(docker run --detach \
    --read-only --cap-drop ALL --cap-add CHOWN --cap-add SETGID --cap-add SETUID \
    --security-opt no-new-privileges:true \
    --tmpfs /tmp:uid=10001,gid=10001,mode=1777 \
    --tmpfs /app/frontend/.next/cache:uid=10001,gid=10001,mode=0700 \
    --volume "$volume:/config" \
    --env GRPC_SERVER_URL=http://127.0.0.1:1 \
    --env PRINTER_IP=127.0.0.1 \
    --env PRINTER_SERIAL=container-smoke-test \
    --env PRINTER_ACCESS_CODE=unused \
    --env SPOOLMAN_URL=http://127.0.0.1:50053 \
    "$image")

probe() {
    docker exec --interactive --user 10001:10001 "$container_id" \
        /venv/bin/python - "$1" < "$repository_root/scripts/container_smoke_probe.py"
}

wait_ready() {
    attempt=0
    while [ "$attempt" -lt 30 ]; do
        if [ "$(docker inspect --format '{{.State.Running}}' "$container_id")" != true ]; then
            break
        fi
        if probe "$1" >/dev/null 2>&1; then
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
    docker logs "$container_id"
    probe "$1"
    return 1
}

stop_gracefully() {
    docker stop --timeout 10 "$container_id" >/dev/null
    state=$(docker inspect --format '{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.Running}}' "$container_id")
    if [ "$state" != '0 false false' ]; then
        docker logs "$container_id"
        echo "Container failed graceful shutdown: $state" >&2
        exit 1
    fi
    docker logs "$container_id" 2>&1 | grep -q 'event=container_shutdown_complete'
}

wait_ready 42
docker exec --interactive --user 10001:10001 "$container_id" \
    /venv/bin/python - < "$repository_root/scripts/assignment_smoke_probe.py"
docker exec --interactive --user 10001:10001 "$container_id" /venv/bin/python - <<'PY'
from bambu_spoolman.settings import edit_settings
with edit_settings() as settings:
    settings["trays"]["0"] = 43
PY
probe 43
stop_gracefully
docker start "$container_id" >/dev/null
wait_ready 43
stop_gracefully
docker logs "$container_id"
echo "Container smoke test passed: restricted runtime, frontend, API, persistence, restart, shutdown"

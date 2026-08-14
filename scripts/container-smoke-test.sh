#!/bin/sh

set -eu

image=${1:-bambu-spoolman:ci}
container_id=""

cleanup() {
    if [ -n "$container_id" ]; then
        docker stop --timeout 10 "$container_id" >/dev/null 2>&1 || true
        docker rm --force "$container_id" >/dev/null 2>&1 || true
    fi
}

trap cleanup EXIT INT TERM

container_id=$(docker run --detach \
    --env PRINTER_IP=127.0.0.1 \
    --env PRINTER_SERIAL=container-smoke-test \
    --env PRINTER_ACCESS_CODE=unused \
    --env SPOOLMAN_URL=http://127.0.0.1:1 \
    "$image")

attempt=0
while [ "$attempt" -lt 30 ]; do
    if [ "$(docker inspect --format '{{.State.Running}}' "$container_id")" != "true" ]; then
        docker logs "$container_id"
        echo "Container exited before its frontend became ready" >&2
        exit 1
    fi

    if docker exec "$container_id" /bin/sh -c \
        'wget --quiet --output-document=/dev/null \
            "http://$HOSTNAME:3000/manifest.webmanifest" 2>/dev/null'; then
        logs=$(docker logs "$container_id" 2>&1)
        if echo "$logs" | grep -q "event=service_start" \
            && echo "$logs" | grep -q "event=container_processes_started"; then
            echo "$logs"
            echo "Container smoke test passed"
            exit 0
        fi
    fi

    attempt=$((attempt + 1))
    sleep 1
done

docker logs "$container_id"
echo "Frontend did not become ready within 30 seconds" >&2
exit 1

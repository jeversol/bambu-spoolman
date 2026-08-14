#!/bin/sh

set -eu

node /app/frontend/server.js &
frontend_pid=$!

/venv/bin/bambu-spoolman-broker &
backend_pid=$!

echo "event=container_processes_started frontend_pid=$frontend_pid backend_pid=$backend_pid"

shutdown() {
    trap - TERM INT
    echo "event=container_shutdown_started frontend_pid=$frontend_pid backend_pid=$backend_pid"
    kill -TERM "$frontend_pid" "$backend_pid" 2>/dev/null || true
    wait "$frontend_pid" "$backend_pid" 2>/dev/null || true
    echo "event=container_shutdown_complete"
}

terminate() {
    shutdown
    exit 0
}

trap terminate TERM INT

while kill -0 "$frontend_pid" 2>/dev/null \
    && kill -0 "$backend_pid" 2>/dev/null; do
    sleep 1
done

if ! kill -0 "$frontend_pid" 2>/dev/null; then
    status=0
    wait "$frontend_pid" || status=$?
    echo "event=container_process_exited process=frontend status=$status"
else
    status=0
    wait "$backend_pid" || status=$?
    echo "event=container_process_exited process=backend status=$status"
fi

shutdown
exit "$status"

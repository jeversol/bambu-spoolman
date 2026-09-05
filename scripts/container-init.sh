#!/bin/sh

set -eu

runtime_uid=10001
runtime_gid=10001
configuration_directory=${BAMBU_SPOOLMAN_CONFIG:-/config}

if [ "$#" -eq 0 ]; then
    set -- /app/container-entrypoint.sh
fi

if [ "$(id -u)" -eq 0 ]; then
    if [ ! -d "$configuration_directory" ]; then
        echo "event=config_directory_missing path=$configuration_directory" >&2
        exit 1
    fi

    if find "$configuration_directory" \
        \( ! -user "$runtime_uid" -o ! -group "$runtime_gid" \) \
        -print -quit | grep -q .; then
        echo "event=config_permissions_migration_started path=$configuration_directory uid=$runtime_uid gid=$runtime_gid"
        chown -R "$runtime_uid:$runtime_gid" "$configuration_directory"
        echo "event=config_permissions_migration_complete path=$configuration_directory uid=$runtime_uid gid=$runtime_gid"
    fi

    exec su-exec "$runtime_uid:$runtime_gid" "$@"
fi

if [ "$(id -u)" -ne "$runtime_uid" ] || [ "$(id -g)" -ne "$runtime_gid" ]; then
    echo "event=unexpected_runtime_user expected_uid=$runtime_uid expected_gid=$runtime_gid actual_uid=$(id -u) actual_gid=$(id -g)" >&2
    exit 1
fi

exec "$@"

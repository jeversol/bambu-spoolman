#!/bin/sh

set -eu

revision=${1:?usage: prepare-baseline-image.sh BASE_SHA SOURCE_DIRECTORY REPOSITORY}
source_directory=${2:?Missing baseline source directory}
repository=${3:?Missing GitHub repository}

case "$revision" in
    *[!0-9a-f]*|'') echo "Invalid base revision" >&2; exit 1 ;;
esac
if [ "${#revision}" -ne 40 ]; then
    echo "Base revision must be a full commit SHA" >&2
    exit 1
fi
if [ ! -f "$source_directory/Dockerfile" ]; then
    echo "Baseline Dockerfile is missing" >&2
    exit 1
fi
if [ "$(git -C "$source_directory" rev-parse HEAD)" != "$revision" ] \
    || ! git -C "$source_directory" diff --quiet HEAD --; then
    echo "Baseline source does not match the exact PR base" >&2
    exit 1
fi

repository=$(printf '%s' "$repository" | tr '[:upper:]' '[:lower:]')
published_image="ghcr.io/$repository:sha-$revision"

# Prefer an already published image when an old package is no longer available.
if docker pull "$published_image"; then
    actual_revision=$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$published_image")
    if [ "$actual_revision" != "$revision" ]; then
        echo "Published baseline revision does not match the PR base" >&2
        exit 1
    fi
    docker tag "$published_image" bambu-spoolman:baseline
else
    # Never run old CI gates or treat a failed build as an empty baseline.
    docker build --pull --target app \
        --build-arg "BAMBU_SPOOLMAN_REVISION=$revision" \
        --tag bambu-spoolman:baseline "$source_directory"
fi

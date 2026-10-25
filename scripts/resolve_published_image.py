"""Resolve latest once so image and lockfile scans use one deployed revision."""

import argparse
import json
import os
import re
import subprocess
import sys


def deployment(image, metadata):
    if (
        re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)+", image)
        is None
    ):
        raise ValueError("Expected a lowercase GHCR image name")
    if not isinstance(metadata, dict):
        raise ValueError("Malformed published image metadata")
    config = metadata.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        raise ValueError("Published image has no source labels")
    revision = labels.get("org.opencontainers.image.revision", "")
    if not isinstance(revision, str) or re.fullmatch(r"[a-f0-9]{40}", revision) is None:
        raise ValueError("Published image has no full source revision")
    references = metadata.get("RepoDigests", [])
    if not isinstance(references, list):
        raise ValueError("Published image has no repository digests")
    for reference in references:
        if isinstance(reference, str) and re.fullmatch(
            re.escape(image) + r"@sha256:[a-f0-9]{64}", reference
        ):
            return {"image": reference, "revision": revision}
    raise ValueError(
        "Published image digest is missing or belongs to another repository"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args()
    try:
        tag = f"{args.image}:latest"
        subprocess.run(["docker", "pull", "--platform", "linux/amd64", tag], check=True)
        result = subprocess.run(
            ["docker", "image", "inspect", tag],
            capture_output=True,
            text=True,
            check=True,
        )
        records = json.loads(result.stdout)
        if not isinstance(records, list) or len(records) != 1:
            raise ValueError("Expected exactly one published image")
        resolved = deployment(args.image, records[0])
        print(json.dumps(resolved, sort_keys=True))
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for key, value in resolved.items():
                output.write(f"{key}={value}\n")
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Cannot resolve published image: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

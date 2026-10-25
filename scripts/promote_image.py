"""Promote a gated release digest, publishing its commit tag before latest."""

import argparse
import json
import os
import re
import subprocess
import sys

from scripts.check_ci_results import require_jobs

RELEASE_JOBS = {"candidate", "dependency-gate", "smoke-test", "image-security"}
IMAGE_PATTERN = re.compile(
    r"(ghcr\.io/[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)+)"
    r"@(sha256:[a-f0-9]{64})"
)


def promote(source, revision, results):
    require_jobs(results, {name: "success" for name in RELEASE_JOBS})
    match = IMAGE_PATTERN.fullmatch(source)
    if match is None or re.fullmatch(r"[a-f0-9]{40}", revision) is None:
        raise ValueError("A full source digest and commit SHA are required")
    image, digest = match.groups()
    outputs = results["candidate"].get("outputs", {})
    if (
        not isinstance(outputs, dict)
        or outputs.get("image") != image
        or outputs.get("digest") != digest
    ):
        raise ValueError("Promotion source differs from the verified candidate")
    for tag in (f"{image}:sha-{revision}", f"{image}:latest"):
        subprocess.run(
            [
                "docker",
                "buildx",
                "imagetools",
                "create",
                "--prefer-index=false",
                "--tag",
                tag,
                source,
            ],
            check=True,
        )
        result = subprocess.run(
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                tag,
                "--format",
                "{{.Manifest.Digest}}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout.strip() != digest:
            raise ValueError(
                f"Published tag does not preserve the candidate digest: {tag}"
            )
        print(f"Verified {tag}@{digest}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("revision")
    args = parser.parse_args()
    try:
        promote(args.source, args.revision, json.loads(os.environ["CHECK_RESULTS"]))
    except (KeyError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Image promotion failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

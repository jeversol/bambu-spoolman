"""Fail the required aggregate check on failed, missing, or skipped jobs."""

import json
import os
import sys

REQUIRED_JOBS = frozenset(
    {
        "backend",
        "frontend",
        "build-image",
        "smoke-test",
        "image-security",
        "dependency-audit",
        "dependency-review",
    }
)


def check_results(results, event):
    if event not in {"pull_request", "workflow_dispatch"}:
        raise ValueError(f"Unsupported CI event: {event}")
    if not isinstance(results, dict) or set(results) != REQUIRED_JOBS:
        raise ValueError("Missing or unexpected CI job results")
    for name, job in results.items():
        expected = (
            "skipped"
            if name == "dependency-review" and event == "workflow_dispatch"
            else "success"
        )
        if not isinstance(job, dict) or job.get("result") != expected:
            raise ValueError(f"{name} must be {expected}: {job!r}")


def main():
    try:
        check_results(json.loads(os.environ["CHECK_RESULTS"]), os.environ["EVENT_NAME"])
    except (KeyError, ValueError) as error:
        print(f"CI verification failed: {error}", file=sys.stderr)
        return 1
    print("Every applicable CI check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

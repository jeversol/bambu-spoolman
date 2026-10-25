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


def require_jobs(results, expected_results):
    if not isinstance(results, dict) or set(results) != set(expected_results):
        raise ValueError("Missing or unexpected CI job results")
    for name, job in results.items():
        expected = expected_results[name]
        if not isinstance(job, dict) or job.get("result") != expected:
            raise ValueError(f"{name} must be {expected}: {job!r}")


def check_results(results, event):
    if event not in {"pull_request", "workflow_dispatch"}:
        raise ValueError(f"Unsupported CI event: {event}")
    expected = {name: "success" for name in REQUIRED_JOBS}
    if event == "workflow_dispatch":
        expected["dependency-review"] = "skipped"
    require_jobs(results, expected)


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

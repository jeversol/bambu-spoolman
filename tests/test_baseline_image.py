import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_SHA = "a" * 40
DOCKER_STUB = """import json, os, sys
args = sys.argv[1:]
with open(os.environ['DOCKER_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[0] == 'pull':
    sys.exit(int(os.environ.get('PULL_STATUS', '0')))
if args[:2] == ['image', 'inspect']:
    print(os.environ['IMAGE_REVISION'])
if args[0] == 'build':
    sys.exit(int(os.environ.get('BUILD_STATUS', '0')))
"""


class BaselineImageTests(unittest.TestCase):
    def run_baseline(self, revision=BASE_SHA, **environment):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "Dockerfile").write_text("FROM scratch\n")
            docker = folder / "docker"
            docker.write_text(f"#!{sys.executable}\n{DOCKER_STUB}")
            docker.chmod(0o755)
            git = folder / "git"
            git.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "if 'rev-parse' in sys.argv:\n"
                "    print(os.environ['SOURCE_REVISION'])\n"
                "else:\n"
                "    sys.exit(int(os.environ.get('SOURCE_DIRTY', '0')))\n"
            )
            git.chmod(0o755)
            log = folder / "docker.jsonl"
            result = subprocess.run(
                [
                    "sh",
                    str(ROOT / "scripts/prepare-baseline-image.sh"),
                    revision,
                    directory,
                    "Example/Repository",
                ],
                env={
                    **os.environ,
                    "PATH": str(folder) + os.pathsep + os.environ["PATH"],
                    "DOCKER_LOG": str(log),
                    "IMAGE_REVISION": BASE_SHA,
                    "SOURCE_REVISION": BASE_SHA,
                    **{key: str(value) for key, value in environment.items()},
                },
                capture_output=True,
                check=False,
            )
            calls = (
                [json.loads(line) for line in log.read_text().splitlines()]
                if log.exists()
                else []
            )
            return result.returncode, calls

    def test_published_base_is_verified_without_rebuilding(self):
        status, calls = self.run_baseline()
        self.assertEqual(status, 0)
        self.assertEqual(
            calls[0], ["pull", f"ghcr.io/example/repository:sha-{BASE_SHA}"]
        )
        self.assertEqual(calls[1][:2], ["image", "inspect"])
        self.assertEqual(calls[2][-1], "bambu-spoolman:baseline")
        self.assertFalse(any(call[0] == "build" for call in calls))

    def test_mismatched_published_revision_fails_without_fallback(self):
        status, calls = self.run_baseline(IMAGE_REVISION="b" * 40)
        self.assertNotEqual(status, 0)
        self.assertFalse(any(call[0] in {"build", "tag"} for call in calls))

    def test_missing_published_image_builds_app_without_old_test_gates(self):
        status, calls = self.run_baseline(PULL_STATUS=1)
        self.assertEqual(status, 0)
        build = next(call for call in calls if call[0] == "build")
        self.assertEqual(build[build.index("--target") + 1], "app")
        self.assertIn(f"BAMBU_SPOOLMAN_REVISION={BASE_SHA}", build)

    def test_failed_fallback_build_is_not_a_clean_baseline(self):
        status, _ = self.run_baseline(PULL_STATUS=1, BUILD_STATUS=1)
        self.assertNotEqual(status, 0)

    def test_invalid_revision_never_reaches_docker(self):
        for revision in ("", "main", "-" + "a" * 39, "a" * 39):
            with self.subTest(revision=revision):
                status, calls = self.run_baseline(revision=revision)
                self.assertNotEqual(status, 0)
                self.assertFalse(calls)

    def test_wrong_or_modified_baseline_source_is_rejected(self):
        for environment in ({"SOURCE_REVISION": "b" * 40}, {"SOURCE_DIRTY": 1}):
            with self.subTest(environment=environment):
                status, calls = self.run_baseline(**environment)
                self.assertNotEqual(status, 0)
                self.assertFalse(calls)

import subprocess
import unittest
from unittest.mock import patch

from scripts.promote_image import RELEASE_JOBS, promote
from scripts.resolve_published_image import deployment

IMAGE = "ghcr.io/example/project"
DIGEST = "sha256:" + "a" * 64
SOURCE = IMAGE + "@" + DIGEST
REVISION = "b" * 40


class PromotionTests(unittest.TestCase):
    def results(self):
        results = {name: {"result": "success"} for name in RELEASE_JOBS}
        results["candidate"]["outputs"] = {"image": IMAGE, "digest": DIGEST}
        return results

    @patch("scripts.promote_image.subprocess.run")
    def test_promotes_commit_before_latest_without_changing_digest(self, run):
        run.return_value.stdout = DIGEST + "\n"
        promote(SOURCE, REVISION, self.results())
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0][-2:], [IMAGE + ":sha-" + REVISION, SOURCE])
        self.assertIn("--prefer-index=false", calls[0])
        self.assertEqual(calls[2][-2:], [IMAGE + ":latest", SOURCE])
        self.assertEqual(calls[1][-1], "{{.Manifest.Digest}}")

    @patch("scripts.promote_image.subprocess.run")
    def test_all_gates_must_succeed_before_registry_writes(self, run):
        for name in RELEASE_JOBS:
            for outcome in ("failure", "cancelled", "skipped", None):
                with self.subTest(job=name, outcome=outcome):
                    results = self.results()
                    results[name]["result"] = outcome
                    with self.assertRaises(ValueError):
                        promote(SOURCE, REVISION, results)
            results = self.results()
            del results[name]
            with self.assertRaises(ValueError):
                promote(SOURCE, REVISION, results)
        run.assert_not_called()

    @patch("scripts.promote_image.subprocess.run")
    def test_source_must_match_verified_candidate(self, run):
        for source, revision in ((IMAGE + ":latest", REVISION), (SOURCE, "short")):
            with self.assertRaises(ValueError):
                promote(source, revision, self.results())
        for outputs in (None, {}, {"image": IMAGE, "digest": "sha256:" + "c" * 64}):
            results = self.results()
            results["candidate"]["outputs"] = outputs
            with self.assertRaises(ValueError):
                promote(SOURCE, REVISION, results)
        run.assert_not_called()

    @patch("scripts.promote_image.subprocess.run")
    def test_failed_commit_tag_write_never_updates_latest(self, run):
        run.side_effect = subprocess.CalledProcessError(1, "docker")
        with self.assertRaises(subprocess.CalledProcessError):
            promote(SOURCE, REVISION, self.results())
        self.assertEqual(run.call_count, 1)

    @patch("scripts.promote_image.subprocess.run")
    def test_digest_mismatch_never_updates_latest(self, run):
        run.return_value.stdout = "sha256:" + "c" * 64
        with self.assertRaises(ValueError):
            promote(SOURCE, REVISION, self.results())
        self.assertEqual(run.call_count, 2)


class DeploymentTests(unittest.TestCase):
    def metadata(self):
        return {
            "Config": {"Labels": {"org.opencontainers.image.revision": REVISION}},
            "RepoDigests": [SOURCE],
        }

    def test_resolves_deployed_digest_and_source_together(self):
        self.assertEqual(
            deployment(IMAGE, self.metadata()), {"image": SOURCE, "revision": REVISION}
        )

    def test_missing_or_malformed_source_fails_closed(self):
        for metadata in (None, {}, {"Config": None}, {"Config": {"Labels": None}}):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                deployment(IMAGE, metadata)
        for revision in (None, "main", "a" * 7, "a" * 40 + "\n"):
            metadata = self.metadata()
            metadata["Config"]["Labels"]["org.opencontainers.image.revision"] = revision
            with self.assertRaises(ValueError):
                deployment(IMAGE, metadata)

    def test_missing_or_foreign_digest_fails_closed(self):
        for references in (
            None,
            [],
            ["ghcr.io/other/project@" + DIGEST],
            [IMAGE + ":latest"],
        ):
            metadata = self.metadata()
            metadata["RepoDigests"] = references
            with self.assertRaises(ValueError):
                deployment(IMAGE, metadata)

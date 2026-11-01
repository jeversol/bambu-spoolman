"""Exercise the workflow's real policy command with unavailable base images."""

import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.test_image_vulnerability_policy import finding, image_report

ROOT = Path(__file__).resolve().parents[1]


class ImageSecurityWorkflowTests(unittest.TestCase):
    def run_gate(
        self, candidate, *, outcome="failure", event="pull_request", baseline=None
    ):
        workflow = (ROOT / ".github/workflows/ci.yaml").read_text()
        step = workflow.split("      - name: Enforce image vulnerability policy\n", 1)[
            1
        ]
        command = re.search(r"        run: \|\n((?:          .*\n|\n)+)", step)
        self.assertIsNotNone(command)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            reports = folder / "security-reports"
            reports.mkdir()
            (reports / "candidate.json").write_text(json.dumps(candidate))
            if baseline is not None:
                (reports / "baseline.json").write_text(json.dumps(baseline))
            summary = folder / "summary.md"
            result = subprocess.run(
                ["bash", "-e", "-c", textwrap.dedent(command[1])],
                cwd=ROOT,
                env={
                    **os.environ,
                    "EVENT_NAME": event,
                    "BASELINE_OUTCOME": outcome,
                    "RUNNER_TEMP": directory,
                    "GITHUB_STEP_SUMMARY": str(summary),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            return result, summary.read_text() if summary.exists() else ""

    def test_unavailable_base_accepts_clean_debian_candidate_and_reports_fallback(self):
        result, summary = self.run_gate(
            image_report(os_family="debian", os_version="13")
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Baseline image unavailable", summary)
        self.assertIn("absolute gate", summary)

    def test_unavailable_base_cannot_exempt_candidate_findings(self):
        report = image_report(finding(), os_family="debian", os_version="13")
        # Even a stale on-disk baseline cannot grant an exemption after failure.
        result, summary = self.run_gate(report, baseline=report)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Introduced: 1", summary)
        self.assertIn("absolute gate", summary)

    def test_available_base_preserves_differential_policy(self):
        report = image_report(finding(), os_family="debian", os_version="13")
        result, summary = self.run_gate(report, outcome="success", baseline=report)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PR comparison", summary)
        self.assertIn("Existing: 1", summary)
        self.assertNotIn("Baseline image unavailable", summary)

    def test_available_base_with_missing_or_malformed_report_fails(self):
        for baseline in (None, {}):
            with self.subTest(baseline=baseline):
                result, _ = self.run_gate(
                    image_report(), outcome="success", baseline=baseline
                )
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_manual_verification_uses_absolute_policy(self):
        report = image_report(finding(), os_family="debian", os_version="13")
        result, summary = self.run_gate(
            report, event="workflow_dispatch", outcome="success", baseline=report
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("absolute gate", summary)
        self.assertNotIn("Baseline image unavailable", summary)

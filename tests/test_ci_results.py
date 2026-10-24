import unittest

from scripts.check_ci_results import REQUIRED_JOBS, check_results


class CiResultsTests(unittest.TestCase):
    def results(self):
        return {name: {"result": "success"} for name in REQUIRED_JOBS}

    def test_successful_pull_request(self):
        check_results(self.results(), "pull_request")

    def test_every_required_job_must_succeed(self):
        for name in REQUIRED_JOBS:
            for outcome in ("failure", "cancelled", "skipped", None):
                with self.subTest(job=name, outcome=outcome):
                    results = self.results()
                    results[name]["result"] = outcome
                    with self.assertRaises(ValueError):
                        check_results(results, "pull_request")

    def test_missing_or_unknown_job_fails(self):
        for name in REQUIRED_JOBS:
            results = self.results()
            del results[name]
            with self.assertRaises(ValueError):
                check_results(results, "pull_request")
        results = self.results()
        results["unreviewed-job"] = {"result": "success"}
        with self.assertRaises(ValueError):
            check_results(results, "pull_request")

    def test_manual_run_only_skips_dependency_review(self):
        results = self.results()
        results["dependency-review"]["result"] = "skipped"
        check_results(results, "workflow_dispatch")
        results["backend"]["result"] = "skipped"
        with self.assertRaises(ValueError):
            check_results(results, "workflow_dispatch")

    def test_malformed_results_or_unknown_event_fail(self):
        for results in (None, [], {}, {name: None for name in REQUIRED_JOBS}):
            with self.assertRaises(ValueError):
                check_results(results, "pull_request")
        with self.assertRaises(ValueError):
            check_results(self.results(), "push")

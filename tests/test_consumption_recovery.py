import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from aiohttp.test_utils import TestClient, TestServer

from bambu_spoolman.broker import checkpoint
from bambu_spoolman.broker.consumption_recovery import (
    accounting_lease,
    accounting_status,
    archive_reconciled_print,
    resolve_consumption,
)
from bambu_spoolman.broker.consumption_state import endpoint_scope
from bambu_spoolman.broker.filament_usage_tracker import FilamentUsageTracker
from bambu_spoolman.http_api import create_app

ENDPOINT = "http://spoolman.test"


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        environment = patch.dict(os.environ, BAMBU_SPOOLMAN_CONFIG=directory.name)
        environment.start()
        self.addCleanup(environment.stop)
        self.metadata = {
            "task_id": "original",
            "subtask_id": "subtask",
            "print_name": "Original print",
            "current_layer": 1,
            "spent_layers": [],
            "spent_filaments": {},
            "spent_segments": {},
            "pending_consumption": {
                "request_id": "request",
                "delivery_state": "in_flight",
                "endpoint_scope": endpoint_scope(ENDPOINT),
                "layer": 0,
                "filament": 0,
                "tray": 0,
                "spool_id": 42,
                "length": 10.0,
                "segment_key": "0:0",
                "baseline_used_length": 100,
            },
        }
        checkpoint._save_checkpoint_metadata(self.metadata)
        self.token = accounting_status(ENDPOINT)["pending"]["intentId"]

    def resolve(self, decision="applied", token=None, spool=42, endpoint=ENDPOINT):
        return resolve_consumption(
            token or self.token,
            spool,
            decision,
            "Correlated independent transaction evidence",
            endpoint,
        )

    def test_applied_decision_is_local_and_retains_backup(self):
        result = self.resolve()
        metadata = checkpoint.get_checkpoint_metadata()
        self.assertEqual(metadata["pending_consumption"]["delivery_state"], "confirmed")
        self.assertFalse(result["status"]["blocked"])
        backup = json.loads(Path(result["backup"]).read_text())
        self.assertEqual(backup["checkpoint"], self.metadata)
        self.assertEqual(backup["resolution_requested"]["decision"], "applied")
        self.assertIn("resolution", metadata["pending_consumption"])
        self.assertNotEqual(result["status"]["pending"]["intentId"], self.token)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.resolve()

    def test_not_applied_authorizes_original_spool_and_amount(self):
        self.resolve("not-applied")
        pending = checkpoint.get_checkpoint_metadata()["pending_consumption"]
        self.assertEqual(pending["delivery_state"], "prepared")
        self.assertEqual((pending["spool_id"], pending["length"]), (42, 10))

    def test_wrong_token_spool_server_and_missing_evidence_never_mutate(self):
        for kwargs in (
            {"token": "wrong"},
            {"spool": 43},
            {"endpoint": "http://other.test"},
        ):
            with self.assertRaises(ValueError):
                self.resolve(**kwargs)
            self.assertEqual(checkpoint.get_checkpoint_metadata(), self.metadata)
        with self.assertRaises(ValueError):
            resolve_consumption(self.token, 42, "applied", " ", ENDPOINT)
        self.assertFalse((self.root / "consumption-recovery").exists())

    def test_resolution_and_second_runtime_refuse_active_broker_lease(self):
        with accounting_lease():
            with self.assertRaisesRegex(RuntimeError, "Stop the broker"):
                self.resolve()
            with self.assertRaises(RuntimeError):
                with accounting_lease():
                    self.fail("second runtime admitted")
            self.assertTrue(accounting_status(ENDPOINT)["blocked"])
        self.resolve()

    def test_failed_checkpoint_write_keeps_pending_and_backup_is_only_requested(self):
        with patch(
            "bambu_spoolman.broker.checkpoint.os.replace",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(OSError):
                self.resolve()
        self.assertEqual(checkpoint.get_checkpoint_metadata(), self.metadata)
        backups = list((self.root / "consumption-recovery").glob("*.json"))
        self.assertEqual(len(backups), 1)
        self.assertIn("resolution_requested", json.loads(backups[0].read_text()))
        self.resolve()

    def test_legacy_pending_requires_manual_decision_and_accepts_string_spool_id(self):
        pending = self.metadata["pending_consumption"]
        for key in ("delivery_state", "endpoint_scope", "request_id"):
            pending.pop(key)
        pending["spool_id"] = "42"
        checkpoint._save_checkpoint_metadata(self.metadata)
        status = accounting_status(ENDPOINT)
        self.assertTrue(status["blocked"])
        self.assertEqual(status["pending"]["state"], "unknown")
        self.resolve(token=status["pending"]["intentId"])
        self.assertEqual(
            checkpoint.get_checkpoint_metadata()["pending_consumption"][
                "endpoint_scope"
            ],
            endpoint_scope(ENDPOINT),
        )

    def test_unresolved_checkpoint_survives_print_transitions(
        self,
    ):
        client = Mock(endpoint=ENDPOINT)
        tracker = FilamentUsageTracker(client=client)
        self.addCleanup(tracker.stop)
        original = checkpoint.get_checkpoint_metadata()
        tracker.active_model = {0: {0: 10}}
        tracker.print_started = True
        tracker.current_layer = 1
        for command in (
            {
                "command": "project_file",
                "task_id": "new",
                "url": "http://new/model.3mf",
            },
            {"command": "push_status", "gcode_state": "FAILED"},
            {"command": "push_status", "gcode_state": "FINISH"},
            {"command": "push_status", "print_error": 50348044},
        ):
            tracker.on_message(None, {"print": command})
        tracker._handle_print_start({"task_id": "direct-new"})
        tracker._handle_print_failure("cancelled")
        tracker._handle_print_end()
        self.assertEqual(checkpoint.get_checkpoint_metadata(), original)
        self.assertIsNone(tracker._retry_timer)
        client.consume_spool.assert_not_called()

    def test_status_without_checkpoint_does_not_create_checkpoint_directory(self):
        checkpoint.clear()
        self.assertEqual(
            accounting_status(ENDPOINT), {"blocked": False, "pending": None}
        )
        self.assertFalse((self.root / "checkpoint").exists())

    def test_authorized_saved_intent_settles_before_new_print_can_clear_it(self):
        self.resolve("not-applied")
        client = Mock(endpoint=ENDPOINT)
        tracker = FilamentUsageTracker(client=client)
        self.addCleanup(tracker.stop)
        before = checkpoint.get_checkpoint_metadata()
        self.assertFalse(tracker._accounting_paused())
        client.consume_spool.assert_called_once_with(42, length=10.0)
        after = checkpoint.get_checkpoint_metadata()
        self.assertIsNone(after["pending_consumption"])
        self.assertEqual(after["task_id"], before["task_id"])
        self.assertEqual(after["current_layer"], before["current_layer"])
        self.assertEqual(after["spent_segments"], {"0": ["0:0"]})

    def test_confirmed_saved_intent_needs_no_replay_before_new_print(self):
        self.resolve("applied")
        client = Mock(endpoint=ENDPOINT)
        tracker = FilamentUsageTracker(client=client)
        self.addCleanup(tracker.stop)
        self.assertFalse(tracker._accounting_paused())
        client.consume_spool.assert_not_called()
        self.assertIsNone(checkpoint.get_checkpoint_metadata()["pending_consumption"])

    def test_archive_retains_reconciled_print_model_and_metadata(
        self,
    ):
        model = self.root / "checkpoint" / "model.3mf"
        model.write_bytes(b"original model evidence")
        self.resolve("applied")
        status = accounting_status(ENDPOINT)
        current = checkpoint.get_checkpoint_metadata()
        result = archive_reconciled_print(
            status["pending"]["intentId"],
            "Completed print physically reconciled",
            ENDPOINT,
            print_ended=True,
            all_usage_reconciled=True,
        )
        archived = Path(result["archive"]) / "checkpoint"
        self.assertEqual(
            (archived / "model.3mf").read_bytes(), b"original model evidence"
        )
        self.assertEqual(json.loads((archived / "metadata.json").read_text()), current)
        self.assertEqual(checkpoint.get_checkpoint_metadata(), {})
        self.assertFalse(result["status"]["blocked"])

    def test_archive_cannot_discard_unknown_or_unverified_print(self):
        for flags in (
            {},
            {"print_ended": True},
            {"print_ended": True, "all_usage_reconciled": True},
        ):
            with self.assertRaises(ValueError):
                archive_reconciled_print(
                    self.token, "Insufficient proof", ENDPOINT, **flags
                )
        self.assertEqual(checkpoint.get_checkpoint_metadata(), self.metadata)
        self.resolve("applied")
        token = accounting_status(ENDPOINT)["pending"]["intentId"]
        with accounting_lease():
            with self.assertRaises(RuntimeError):
                archive_reconciled_print(
                    token,
                    "Print ended",
                    ENDPOINT,
                    print_ended=True,
                    all_usage_reconciled=True,
                )
        self.assertTrue((self.root / "checkpoint" / "metadata.json").exists())


class AccountingHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_does_not_depend_on_upstream_connectivity(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with patch.dict(os.environ, BAMBU_SPOOLMAN_CONFIG=directory.name):
            client = Mock(endpoint=ENDPOINT)
            client.get_spools.side_effect = OSError("offline")
            http = TestClient(TestServer(create_app(client)))
            await http.start_server()
            try:
                checkpoint._save_checkpoint_metadata(
                    {"pending_consumption": {"spool_id": 42, "length": 10}}
                )
                response = await asyncio.wait_for(http.get("/api/v1/accounting"), 1)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertTrue((await response.json())["blocked"])
                client.get_spools.assert_not_called()
            finally:
                await http.close()

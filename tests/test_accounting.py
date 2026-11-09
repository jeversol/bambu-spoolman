import asyncio
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from aiohttp.test_utils import TestClient, TestServer

from bambu_spoolman.broker import checkpoint
from bambu_spoolman.broker.accounting import accounting_lease, accounting_status
from bambu_spoolman.broker.filament_usage_tracker import FilamentUsageTracker
from bambu_spoolman.http_api import create_app

ENDPOINT = "http://spoolman.test"


class AccountingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, BAMBU_SPOOLMAN_CONFIG=directory.name)
        environment.start()
        self.addCleanup(environment.stop)

    def test_second_broker_is_rejected(self):
        with accounting_lease():
            with self.assertRaisesRegex(RuntimeError, "already running"):
                with accounting_lease():
                    self.fail("second broker admitted")

    def test_saved_uncertain_update_does_not_block_tracking(self):
        checkpoint._save_checkpoint_metadata(
            {
                "task_id": "original",
                "current_layer": 1,
                "pending_consumption": {
                    "layer": 0,
                    "segment_key": "0:0",
                    "spool_id": 42,
                    "delivery_state": "in_flight",
                },
            }
        )
        client = Mock(endpoint=ENDPOINT)
        tracker = FilamentUsageTracker(client=client)
        self.addCleanup(tracker.stop)
        self.assertFalse(tracker._accounting_paused())
        client.consume_spool.assert_not_called()
        metadata = checkpoint.get_checkpoint_metadata()
        self.assertIsNone(metadata["pending_consumption"])
        self.assertEqual(metadata["spent_segments"], {"0": ["0:0"]})
        self.assertEqual(metadata["task_id"], "original")
        self.assertIn("spool 42", accounting_status()["warning"])


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
                self.assertIsNone((await response.json())["warning"])
                client.get_spools.assert_not_called()
            finally:
                await http.close()

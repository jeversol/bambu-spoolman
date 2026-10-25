import asyncio
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import requests
from aiohttp.test_utils import TestClient, TestServer

from bambu_spoolman.bambu_mqtt import StatefulPrinterInfo
from bambu_spoolman.broker.automatic_spool_switch import AutomaticSpoolSwitch
from bambu_spoolman.http_api import create_app
from bambu_spoolman.settings import load_settings, save_settings

TAG = "AABBCCDDEEFF00112233445566778899"


class RfidHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(
            os.environ,
            BAMBU_SPOOLMAN_CONFIG=directory.name,
            SPOOLMAN_URL="http://spoolman.test",
        )
        env.start()
        self.addCleanup(env.stop)
        save_settings({"trays": {"0": 42}, "locked_trays": [0]})
        self.printer = StatefulPrinterInfo()
        self.printer.connected = True
        self.printer.last_update = 100
        self.printer._info = {
            "print": {
                "ams": {
                    "ams": [
                        {
                            "id": "0",
                            "tray": [{"id": "0", "tray_uuid": TAG, "tray_type": "PLA"}],
                        }
                    ]
                }
            }
        }
        printer = patch(
            "bambu_spoolman.broker.automatic_spool_switch.stateful_printer_info",
            self.printer,
        )
        printer.start()
        self.addCleanup(printer.stop)
        self.switch = AutomaticSpoolSwitch()
        self.switch.sync = Mock()
        self.spoolman = Mock()
        self.spoolman.endpoint = "http://spoolman.test"
        self.spoolman.validate.return_value = True
        self.spoolman.supports_tray_locking.return_value = True
        self.spoolman.get_spool.side_effect = lambda spool: (
            {"id": spool} if spool == 42 else None
        )
        self.spoolman.set_tray_uuid.return_value = True
        self.client = TestClient(
            TestServer(create_app(self.spoolman, self.switch, self.printer))
        )
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_printer_snapshot_and_feature_info(self):
        response = await self.client.get("/api/v1/printer")
        value = await response.json()
        self.assertEqual(
            value,
            {"connected": True, "lastUpdated": 100, "status": self.printer.get_info()},
        )
        self.printer.connected = False
        response = await self.client.get("/api/v1/printer")
        self.assertFalse((await response.json())["connected"])
        response = await self.client.get("/api/v1/info")
        self.assertEqual(
            await response.json(),
            {
                "spoolmanUrl": "http://spoolman.test",
                "spoolmanValid": True,
                "features": {"trayLocking": True},
            },
        )
        self.spoolman.validate.return_value = False
        self.spoolman.supports_tray_locking.return_value = False
        response = await self.client.get("/api/v1/info")
        self.assertFalse((await response.json())["spoolmanValid"])

    async def test_link_uses_shared_service_and_refreshes(self):
        response = await self.client.put("/api/v1/spools/42/rfid", json={"uuid": TAG})
        self.assertEqual(response.status, 204)
        self.spoolman.set_tray_uuid.assert_called_once_with(42, TAG)
        self.switch.sync.assert_called_once_with()

    async def test_override_pauses_real_automatic_mapping_without_removing_assignment(
        self,
    ):
        response = await self.client.post(
            "/api/v1/trays/0/rfid/override", json={"uuid": TAG}
        )
        self.assertEqual(response.status, 204)
        settings = load_settings()
        self.assertEqual(settings["rfid_overrides"], {"0": TAG})
        self.assertEqual(settings["locked_trays"], [])
        self.assertEqual(settings["trays"], {"0": 42})
        self.spoolman.set_tray_uuid.assert_not_called()

    async def test_stale_or_disconnected_tag_is_rejected_without_changes(self):
        for uuid, connected in [("changed-tag", True), (TAG, False)]:
            self.printer.connected = connected
            response = await self.client.post(
                "/api/v1/trays/0/rfid/override", json={"uuid": uuid}
            )
            self.assertEqual(response.status, 400)
            self.assertEqual(load_settings(), {"trays": {"0": 42}, "locked_trays": [0]})

    async def test_unlink_suppresses_relinking_before_removing_permanent_association(
        self,
    ):
        order = Mock()
        self.switch.override_tray = Mock(wraps=self.switch.override_tray)
        order.attach_mock(self.switch.override_tray, "override")
        order.attach_mock(self.spoolman.set_tray_uuid, "unlink")
        response = await self.client.post(
            "/api/v1/trays/0/rfid/unlink", json={"spoolId": 42, "uuid": TAG}
        )
        self.assertEqual(response.status, 204)
        self.assertEqual([call[0] for call in order.mock_calls], ["override", "unlink"])
        self.spoolman.set_tray_uuid.assert_called_once_with(42, "")
        self.assertEqual(load_settings()["rfid_overrides"], {"0": TAG})
        self.assertEqual(load_settings()["locked_trays"], [])

    async def test_stale_assignment_never_unlinks_a_different_spool(self):
        response = await self.client.post(
            "/api/v1/trays/0/rfid/unlink", json={"spoolId": 43, "uuid": TAG}
        )
        self.assertEqual(response.status, 409)
        self.assertNotIn("rfid_overrides", load_settings())
        self.spoolman.set_tray_uuid.assert_not_called()

    async def test_partial_unlink_failure_is_explicit_and_keeps_override(self):
        self.spoolman.set_tray_uuid.return_value = False
        response = await self.client.post(
            "/api/v1/trays/0/rfid/unlink", json={"spoolId": 42, "uuid": TAG}
        )
        self.assertEqual(response.status, 502)
        self.assertIn("paused", (await response.json())["error"])
        self.assertEqual(load_settings()["rfid_overrides"], {"0": TAG})
        self.spoolman.set_tray_uuid.assert_called_once_with(42, "")

    async def test_link_missing_unsupported_and_upstream_failure(self):
        response = await self.client.put("/api/v1/spools/999/rfid", json={"uuid": TAG})
        self.assertEqual(response.status, 404)
        self.spoolman.supports_tray_locking.return_value = False
        response = await self.client.put("/api/v1/spools/42/rfid", json={"uuid": TAG})
        self.assertEqual(response.status, 501)
        self.spoolman.supports_tray_locking.side_effect = requests.Timeout()
        response = await self.client.put("/api/v1/spools/42/rfid", json={"uuid": TAG})
        self.assertEqual(response.status, 502)
        self.spoolman.set_tray_uuid.assert_not_called()

    async def test_invalid_payloads_cannot_bypass_unlink_sequence(self):
        for uuid in (None, 42, True, "", " " + TAG, "x" * 129):
            response = await self.client.put(
                "/api/v1/spools/42/rfid", json={"uuid": uuid}
            )
            self.assertEqual(response.status, 400)
        for spool in (True, "42", -1, 1.5, 2**53):
            response = await self.client.post(
                "/api/v1/trays/0/rfid/unlink", json={"spoolId": spool, "uuid": TAG}
            )
            self.assertEqual(response.status, 400)
        response = await self.client.post(
            "/api/v1/trays/0/rfid/override", json={"uuid": TAG, "extra": 1}
        )
        self.assertEqual(response.status, 400)
        self.spoolman.set_tray_uuid.assert_not_called()
        self.assertNotIn("rfid_overrides", load_settings())

    async def test_failed_refresh_reports_saved_association(
        self,
    ):
        self.switch.sync.side_effect = requests.ConnectionError()
        response = await self.client.put("/api/v1/spools/42/rfid", json={"uuid": TAG})
        self.assertEqual(response.status, 502)
        self.assertIn("association saved", (await response.json())["error"])
        self.spoolman.set_tray_uuid.assert_called_once()

    async def test_slow_feature_detection_keeps_settings_responsive(self):
        started, release = threading.Event(), threading.Event()

        def slow_features():
            started.set()
            release.wait(timeout=5)
            return True

        self.spoolman.supports_tray_locking.side_effect = slow_features
        info = asyncio.create_task(self.client.get("/api/v1/info"))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            response = await asyncio.wait_for(
                self.client.get("/api/v1/settings"), timeout=1
            )
            self.assertEqual(response.status, 200)
        finally:
            release.set()
            await info

    async def test_slow_rfid_refresh_does_not_block_status_polling(self):
        started, release = threading.Event(), threading.Event()

        def slow_sync():
            started.set()
            release.wait(timeout=5)

        self.switch.sync.side_effect = slow_sync
        linking = asyncio.create_task(
            self.client.put("/api/v1/spools/42/rfid", json={"uuid": TAG})
        )
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            response = await asyncio.wait_for(
                self.client.get("/api/v1/printer"), timeout=1
            )
            self.assertEqual(response.status, 200)
        finally:
            release.set()
            self.assertEqual((await linking).status, 204)

    async def test_refresh_and_override_serialize_shared_switch_state(self):
        started, release = threading.Event(), threading.Event()
        overriding, finished = threading.Event(), threading.Event()
        order = []

        def slow_sync(_print):
            started.set()
            release.wait(timeout=5)
            order.append("sync")

        def override():
            overriding.set()
            self.switch.override_tray(0, TAG)
            order.append("override")
            finished.set()

        self.switch._sync_trays = slow_sync
        syncing = asyncio.create_task(
            asyncio.to_thread(AutomaticSpoolSwitch.sync, self.switch)
        )
        task = None
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            task = asyncio.create_task(asyncio.to_thread(override))
            self.assertTrue(await asyncio.to_thread(overriding.wait, 2))
            self.assertFalse(await asyncio.to_thread(finished.wait, 0.1))
        finally:
            release.set()
            await syncing
            if task:
                await task
        self.assertEqual(order, ["sync", "override"])

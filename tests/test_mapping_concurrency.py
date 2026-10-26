import asyncio
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import requests
from aiohttp.test_utils import TestClient, TestServer

from bambu_spoolman.broker.automatic_spool_switch import AutomaticSpoolSwitch
from bambu_spoolman.http_api import create_app
from bambu_spoolman.settings import load_settings, save_settings

TAG = "AABBCCDDEEFF00112233445566778899"


class MappingConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, BAMBU_SPOOLMAN_CONFIG=directory.name)
        environment.start()
        self.addCleanup(environment.stop)
        save_settings({"trays": {"0": 42}, "locked_trays": []})
        self.locations = {42: (1, 1)}
        self.order = []
        self.spoolman = Mock()
        self.spoolman.endpoint = "http://spoolman.test"
        self.spoolman.ams_field_name = "ams"
        self.spoolman.tray_field_name = "tray"
        self.spoolman.ams_field_name = "ams"
        self.spoolman.tray_field_name = "tray"
        self.spoolman.get_spool.side_effect = lambda spool: {"id": spool}
        self.spoolman.set_active_tray.side_effect = self.update_location
        with patch(
            "bambu_spoolman.broker.automatic_spool_switch.new_client",
            return_value=self.spoolman,
        ):
            self.switch = AutomaticSpoolSwitch()
        self.client = TestClient(TestServer(create_app(self.spoolman, self.switch)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    def update_location(self, spool, ams, tray):
        self.order.append((spool, ams, tray))
        self.locations[spool] = (ams, tray)
        return True

    async def assign(self, tray, spool):
        return await self.client.put(f"/api/v1/trays/{tray}", json={"spoolId": spool})

    async def serialized_race(self, *, first_automatic=False, second_automatic=False):
        entered, release = threading.Event(), threading.Event()

        def slow_clear(spool, ams, tray):
            if ams is None:
                entered.set()
                release.wait(5)
            return self.update_location(spool, ams, tray)

        self.spoolman.set_active_tray.side_effect = slow_clear
        if first_automatic:
            first = asyncio.create_task(
                asyncio.to_thread(self.switch._unlock_tray, 0, clear=True)
            )
        else:
            first = asyncio.create_task(self.assign(0, -1))
        second = None
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            if second_automatic:
                self.switch._resolve_spool = Mock(return_value={"id": 42})
                snapshot = {
                    "ams": {
                        "ams": [{"id": "0", "tray": [{"id": "1", "tray_uuid": TAG}]}]
                    }
                }
                second = asyncio.create_task(
                    asyncio.to_thread(self.switch._sync_trays, snapshot)
                )
            else:
                second = asyncio.create_task(self.assign(1, 42))
            # The local settings lock and event loop must remain available while
            # a complete mapping mutation is waiting on remote I/O.
            response = await asyncio.wait_for(self.client.get("/api/v1/settings"), 1)
            self.assertEqual(response.status, 200)
            response = await asyncio.wait_for(self.client.get("/api/v1/printer"), 1)
            self.assertEqual(response.status, 200)
            done, _ = await asyncio.wait({second}, timeout=0.1)
            self.assertFalse(done)
        finally:
            release.set()
            results = await asyncio.wait_for(
                asyncio.gather(first, *([second] if second else [])), 5
            )
        for result in results:
            if result is not None:
                self.assertEqual(result.status, 204)
        self.assertEqual(load_settings()["trays"], {"1": 42})
        self.assertEqual(self.locations[42], (1, 2))
        self.assertEqual(self.order, [(42, None, None), (42, 1, 2)])

    async def test_http_clear_cannot_overwrite_newer_http_assignment(self):
        await self.serialized_race()

    async def test_http_clear_cannot_overwrite_newer_mqtt_assignment(self):
        await self.serialized_race(second_automatic=True)
        self.assertEqual(load_settings()["locked_trays"], [1])

    async def test_mqtt_removal_cannot_overwrite_newer_http_assignment(self):
        await self.serialized_race(first_automatic=True)

    async def test_automatic_move_releases_displaced_lock_and_old_spool_location(self):
        save_settings({"trays": {"0": "42", "1": 43}, "locked_trays": ["0", 1]})
        await asyncio.to_thread(self.switch._lock_spool, 1, 42)
        self.assertEqual(load_settings()["trays"], {"1": 42})
        self.assertEqual(load_settings()["locked_trays"], [1])
        self.assertEqual(self.order, [(42, 1, 2), (43, None, None)])

    async def test_partial_location_failure_is_explicit_and_other_updates_still_run(
        self,
    ):
        def fail_new(spool, ams, tray):
            if spool == 43:
                raise requests.Timeout("outcome unknown")
            return self.update_location(spool, ams, tray)

        self.spoolman.set_active_tray.side_effect = fail_new
        response = await self.assign(0, 43)
        self.assertEqual(response.status, 502)
        self.assertIn("saved locally", (await response.json())["error"])
        self.assertEqual(load_settings()["trays"], {"0": 43})
        self.assertEqual(self.locations[42], (None, None))
        self.assertEqual(self.spoolman.set_active_tray.call_count, 2)
        self.assertEqual((await self.client.get("/api/v1/settings")).status, 200)

    async def test_false_configured_update_is_not_reported_as_success(self):
        self.spoolman.set_active_tray.side_effect = None
        self.spoolman.set_active_tray.return_value = False
        response = await self.assign(0, 43)
        self.assertEqual(response.status, 502)
        self.assertEqual(load_settings()["trays"], {"0": 43})

    async def test_disabled_location_fields_are_a_successful_noop(self):
        self.spoolman.ams_field_name = self.spoolman.tray_field_name = None
        self.spoolman.set_active_tray.side_effect = None
        self.spoolman.set_active_tray.return_value = False
        self.assertEqual((await self.assign(0, 43)).status, 204)

    async def test_unlink_check_override_and_write_are_one_serialized_operation(self):
        entered, release = threading.Event(), threading.Event()

        def slow_override(*args):
            entered.set()
            release.wait(5)
            return True

        self.switch.override_tray = Mock(side_effect=slow_override)
        self.switch.sync = Mock()
        self.spoolman.supports_tray_locking.return_value = True
        self.spoolman.set_tray_uuid.side_effect = lambda spool, uuid: (
            self.order.append(("unlink", spool)) or True
        )
        unlinking = asyncio.create_task(
            self.client.post(
                "/api/v1/trays/0/rfid/unlink", json={"spoolId": 42, "uuid": TAG}
            )
        )
        assigning = None
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            assigning = asyncio.create_task(self.assign(0, 43))
            done, _ = await asyncio.wait({assigning}, timeout=0.1)
            self.assertFalse(done)
            self.assertEqual(load_settings()["trays"], {"0": 42})
        finally:
            release.set()
            results = await asyncio.wait_for(
                asyncio.gather(unlinking, *([assigning] if assigning else [])), 5
            )
        self.assertTrue(all(response.status == 204 for response in results))
        self.assertEqual(self.order[0], ("unlink", 42))
        self.assertEqual(load_settings()["trays"], {"0": 43})

import asyncio
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import requests
from aiohttp.test_utils import TestClient, TestServer

from bambu_spoolman.http_api import MUTATIONS, create_app
from bambu_spoolman.settings import load_settings, save_settings


class HttpApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = patch.dict(os.environ, BAMBU_SPOOLMAN_CONFIG=self.directory.name)
        environment.start()
        self.addCleanup(environment.stop)
        self.spools = [
            {"id": 42, "remaining_weight": 750, "filament": {"name": "PLA"}},
            {"id": 43, "filament": {"name": "PETG"}},
        ]
        self.spoolman = Mock()
        self.spoolman.get_spools.return_value = self.spools
        self.spoolman.get_spool.side_effect = lambda spool_id: next(
            (value for value in self.spools if value["id"] == int(spool_id)), None
        )
        self.client = TestClient(TestServer(create_app(self.spoolman)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def assign(self, tray, spool):
        return await self.client.put(f"/api/v1/trays/{tray}", json={"spoolId": spool})

    async def test_full_assignment_replace_clear_and_restart_persistence(self):
        response = await self.client.get("/api/v1/spools")
        self.assertEqual((await response.json())["spools"], self.spools)
        self.assertEqual((await self.assign(0, 42)).status, 204)
        response = await self.client.get("/api/v1/settings")
        self.assertEqual((await response.json())["trays"], {"0": 42})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        await self.client.close()
        self.client = TestClient(TestServer(create_app(self.spoolman)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        response = await self.client.get("/api/v1/settings")
        self.assertEqual((await response.json())["trays"], {"0": 42})
        self.assertEqual((await self.assign(0, 43)).status, 204)
        self.spoolman.set_active_tray.assert_any_call(42, None, None)
        self.spoolman.set_active_tray.assert_any_call(43, 1, 1)
        self.assertEqual((await self.assign(0, -1)).status, 204)
        self.assertEqual(load_settings()["trays"], {})

    async def test_duplicate_assignment_and_locked_tray_are_rejected(self):
        save_settings({"trays": {"0": 42}, "locked_trays": [1]})
        for tray, spool, message in [(1, 43, "locked"), (255, 42, "Slot 1 in AMS 1")]:
            response = await self.assign(tray, spool)
            self.assertEqual(response.status, 400)
            self.assertIn(message, (await response.json())["error"])
            self.assertEqual(load_settings()["trays"], {"0": 42})
        self.spoolman.set_active_tray.assert_not_called()

    async def test_external_assignment_and_conflict_location(self):
        self.assertEqual((await self.assign(255, 42)).status, 204)
        self.spoolman.set_active_tray.assert_called_with(42, None, None)
        response = await self.assign(3, 42)
        self.assertIn("External Spool Holder", (await response.json())["error"])

    async def test_missing_spool_never_mutates_settings(self):
        self.assertEqual((await self.assign(0, 999)).status, 404)
        self.assertEqual(load_settings()["trays"], {})
        self.assertEqual((await self.client.get("/api/v1/spools/999")).status, 404)

    async def test_identifiers_are_not_coerced(self):
        for value in (True, False, None, "42", 1.5, -2, 2**53):
            with self.subTest(value=value):
                self.assertEqual((await self.assign(0, value)).status, 400)
        self.assertEqual((await self.assign(2**53, 42)).status, 400)
        self.assertEqual(load_settings()["trays"], {})
        self.spoolman.set_active_tray.assert_not_called()

    async def test_json_shape_content_type_and_size_limits(self):
        for body in ([], None, {"spoolId": 42, "extra": 1}, {}):
            response = await self.client.put("/api/v1/trays/0", json=body)
            self.assertIn(response.status, (400, 415))
        response = await self.client.put(
            "/api/v1/trays/0", data="{", headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status, 400)
        response = await self.client.put("/api/v1/trays/0", data="spoolId=42")
        self.assertEqual(response.status, 415)
        response = await self.client.put(
            "/api/v1/trays/0",
            data=" " * 4097,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 413)

    async def test_spoolman_failure_is_retryable_and_does_not_change_mapping(self):
        self.spoolman.get_spool.side_effect = requests.ConnectionError(
            "private endpoint"
        )
        response = await self.assign(0, 42)
        self.assertEqual(response.status, 502)
        self.assertEqual((await response.json())["error"], "Spoolman is unavailable")
        self.assertEqual(load_settings()["trays"], {})
        self.spoolman.get_spools.side_effect = requests.Timeout()
        self.assertEqual((await self.client.get("/api/v1/spools")).status, 502)

    async def test_slow_spoolman_does_not_block_settings_polling(self):
        started = threading.Event()
        release = threading.Event()

        def slow_spools():
            started.set()
            release.wait(timeout=5)
            return self.spools

        self.spoolman.get_spools.side_effect = slow_spools
        listing = asyncio.create_task(self.client.get("/api/v1/spools"))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            response = await asyncio.wait_for(
                self.client.get("/api/v1/settings"), timeout=1
            )
            self.assertEqual(response.status, 200)
        finally:
            release.set()
            await listing

    async def test_unexpected_error_is_json_without_internal_details(self):
        self.spoolman.get_spools.side_effect = RuntimeError("private details")
        response = await self.client.get("/api/v1/spools")
        self.assertEqual(response.status, 500)
        self.assertEqual((await response.json())["error"], "Internal server error")

    async def test_all_write_routes_report_busy_without_starting_mutations(self):
        gate = self.client.server.app[MUTATIONS]
        release = asyncio.Event()
        active = asyncio.create_task(gate.run(release.wait))
        await asyncio.sleep(0)
        queued = [asyncio.create_task(gate.run(release.wait)) for _ in range(8)]
        await asyncio.sleep(0)
        try:
            for method, path, body in [
                ("put", "/api/v1/trays/0", {"spoolId": 42}),
                ("put", "/api/v1/spools/42/rfid", {"uuid": "tag"}),
                ("post", "/api/v1/trays/0/rfid/override", {"uuid": "tag"}),
                ("post", "/api/v1/trays/0/rfid/unlink", {"spoolId": 42, "uuid": "tag"}),
            ]:
                response = await getattr(self.client, method)(path, json=body)
                self.assertEqual(response.status, 503)
                self.assertEqual(response.headers["Retry-After"], "1")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertIn("busy", (await response.json())["error"])
            self.assertEqual((await self.client.get("/api/v1/settings")).status, 200)
            self.spoolman.get_spool.assert_not_called()
        finally:
            release.set()
            await asyncio.gather(active, *queued)

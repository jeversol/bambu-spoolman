import asyncio
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import requests

from bambu_spoolman.assignments import AssignmentError, assign_spool
from bambu_spoolman.bambu_mqtt import DISPATCH_FAULT_KEY, MqttHandler
from bambu_spoolman.broker.automatic_spool_switch import AutomaticSpoolSwitch
from bambu_spoolman.location_sync import (
    PENDING_LOCATIONS,
    LocationQueueFull,
    LocationReconciler,
    reconcile_spool_locations,
    stage_spool_locations,
)
from bambu_spoolman.settings import edit_settings, load_settings, save_settings
from bambu_spoolman.spoolman import SpoolmanClient


class LocationSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, BAMBU_SPOOLMAN_CONFIG=directory.name)
        environment.start()
        self.addCleanup(environment.stop)
        self.client = Mock()
        self.client.endpoint = "http://spoolman.test"
        self.client.ams_field_name = "ams"
        self.client.tray_field_name = "tray"
        self.client.get_spool.side_effect = lambda spool: {"id": spool}
        self.client.set_active_tray.side_effect = requests.Timeout("unknown outcome")
        save_settings({"trays": {"0": 42}, "locked_trays": []})

    async def replace_offline(self):
        with self.assertRaises(AssignmentError) as error:
            await assign_spool(0, 43, self.client)
        self.assertEqual(error.exception.status, 502)

    async def test_failed_replacement_and_old_cleanup_survive_restart(self):
        await self.replace_offline()
        settings = load_settings()
        self.assertEqual(settings["trays"], {"0": 43})
        self.assertEqual(set(settings[PENDING_LOCATIONS]), {"42", "43"})
        # A fresh client/reconciler reads durable work rather than in-memory state.
        client = Mock(
            endpoint=self.client.endpoint, ams_field_name="ams", tray_field_name="tray"
        )
        client.set_active_tray.return_value = True
        with patch("bambu_spoolman.location_sync.time.time", return_value=10**12):
            await asyncio.to_thread(reconcile_spool_locations, client)
        self.assertEqual(load_settings()[PENDING_LOCATIONS], {})
        client.set_active_tray.assert_any_call(42, None, None)
        client.set_active_tray.assert_any_call(43, 1, 1)

    async def test_successful_replacement_is_atomic_before_first_network_write(self):
        observed = []

        def update(spool, ams, tray):
            value = load_settings()
            observed.append((value["trays"], set(value[PENDING_LOCATIONS])))
            return True

        self.client.set_active_tray.side_effect = update
        await assign_spool(0, 43, self.client)
        self.assertEqual(observed[0], ({"0": 43}, {"42", "43"}))
        self.assertEqual(load_settings()[PENDING_LOCATIONS], {})

    async def test_failed_local_commit_never_sends_remote_updates(self):
        with patch(
            "bambu_spoolman.settings.os.replace", side_effect=OSError("disk full")
        ):
            with self.assertRaises(OSError):
                await assign_spool(0, 43, self.client)
        self.client.set_active_tray.assert_not_called()
        self.assertEqual(load_settings()["trays"], {"0": 42})
        self.assertNotIn(PENDING_LOCATIONS, load_settings())

    async def test_newer_move_supersedes_failed_clear(self):
        await self.replace_offline()
        self.client.set_active_tray.side_effect = None
        self.client.set_active_tray.return_value = True
        await assign_spool(1, 42, self.client)
        self.client.set_active_tray.reset_mock()
        with patch("bambu_spoolman.location_sync.time.time", return_value=10**12):
            await asyncio.to_thread(reconcile_spool_locations, self.client)
        self.client.set_active_tray.assert_called_once_with(43, 1, 1)
        self.assertEqual(load_settings()["trays"], {"0": 43, "1": 42})

    async def test_mqtt_removal_is_durable_and_retryable(self):
        with patch(
            "bambu_spoolman.broker.automatic_spool_switch.new_client",
            return_value=self.client,
        ):
            switch = AutomaticSpoolSwitch()
        await asyncio.to_thread(switch._unlock_tray, 0, clear=True)
        self.assertEqual(load_settings()["trays"], {})
        self.assertIn("42", load_settings()[PENDING_LOCATIONS])
        self.client.set_active_tray.side_effect = None
        self.client.set_active_tray.return_value = True
        with patch("bambu_spoolman.location_sync.time.time", return_value=10**12):
            await asyncio.to_thread(reconcile_spool_locations, self.client)
        self.assertEqual(load_settings()[PENDING_LOCATIONS], {})

    async def test_retry_rechecks_latest_local_mapping_and_scope(self):
        await self.replace_offline()
        settings = load_settings()
        settings["trays"] = {"3": 42}
        save_settings(settings)
        self.client.set_active_tray.side_effect = None
        self.client.set_active_tray.return_value = True
        self.client.set_active_tray.reset_mock()
        with patch("bambu_spoolman.location_sync.time.time", return_value=10**12):
            await asyncio.to_thread(reconcile_spool_locations, self.client)
        self.client.set_active_tray.assert_any_call(42, 1, 4)
        self.client.set_active_tray.assert_any_call(43, None, None)

    async def test_changed_server_or_fields_never_receive_stale_intents(self):
        await self.replace_offline()
        before = load_settings()[PENDING_LOCATIONS]
        for attribute, value in [
            ("endpoint", "http://different.test"),
            ("ams_field_name", "other"),
        ]:
            old = getattr(self.client, attribute)
            setattr(self.client, attribute, value)
            self.client.set_active_tray.reset_mock()
            with patch("bambu_spoolman.location_sync.time.time", return_value=10**12):
                await asyncio.to_thread(reconcile_spool_locations, self.client)
            self.client.set_active_tray.assert_not_called()
            self.assertEqual(load_settings()[PENDING_LOCATIONS], before)
            setattr(self.client, attribute, old)

    async def test_failed_acknowledgment_is_safely_replayed(self):
        await self.replace_offline()
        self.client.set_active_tray.side_effect = None
        self.client.set_active_tray.return_value = True
        with (
            patch("bambu_spoolman.location_sync.time.time", return_value=10**12),
            patch(
                "bambu_spoolman.settings.os.replace", side_effect=OSError("disk full")
            ),
        ):
            with self.assertRaises(OSError):
                reconcile_spool_locations(self.client)
        self.assertEqual(len(load_settings()[PENDING_LOCATIONS]), 2)
        with patch("bambu_spoolman.location_sync.time.time", return_value=10**12):
            reconcile_spool_locations(self.client)
        self.assertEqual(load_settings()[PENDING_LOCATIONS], {})

    async def test_backoff_does_not_hammer_failed_spools(self):
        with patch("bambu_spoolman.location_sync.time.time", return_value=100):
            await self.replace_offline()
        self.client.set_active_tray.reset_mock()
        with patch("bambu_spoolman.location_sync.time.time", return_value=129):
            reconcile_spool_locations(self.client)
        self.client.set_active_tray.assert_not_called()
        with patch("bambu_spoolman.location_sync.time.time", return_value=130):
            reconcile_spool_locations(self.client)
        self.assertEqual(self.client.set_active_tray.call_count, 2)
        self.assertEqual(load_settings()[PENDING_LOCATIONS]["42"]["retry_after"], 190)

    async def test_outbox_capacity_rejects_mapping_atomically(self):
        with patch("bambu_spoolman.location_sync.MAX_PENDING_LOCATIONS", 1):
            with self.assertRaises(LocationQueueFull):
                await assign_spool(0, 43, self.client)
        self.assertEqual(load_settings()["trays"], {"0": 42})
        self.assertNotIn(PENDING_LOCATIONS, load_settings())
        self.client.set_active_tray.assert_not_called()

    async def test_stop_prevents_new_retry_requests(self):
        await self.replace_offline()
        stop = threading.Event()
        stop.set()
        self.client.set_active_tray.reset_mock()
        with patch("bambu_spoolman.location_sync.time.time", return_value=10**12):
            reconcile_spool_locations(self.client, stop)
        self.client.set_active_tray.assert_not_called()
        worker = LocationReconciler(self.client)
        worker.stop()
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())

    async def test_slow_retry_cannot_clear_a_newer_assignment(self):
        with edit_settings() as settings:
            settings["trays"] = {}
            stage_spool_locations(settings, self.client, [42])
        entered, release = threading.Event(), threading.Event()
        remote = []

        def delayed(spool, ams, tray):
            if ams is None:
                entered.set()
                release.wait(3)
            remote.append((spool, ams, tray))
            return True

        self.client.set_active_tray.side_effect = delayed
        retry = asyncio.create_task(
            asyncio.to_thread(reconcile_spool_locations, self.client)
        )
        changing = None
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            changing = asyncio.create_task(assign_spool(1, 42, self.client))
            done, _ = await asyncio.wait({changing}, timeout=0.05)
            self.assertFalse(done)
            self.assertEqual(
                await asyncio.wait_for(asyncio.to_thread(load_settings), 1),
                load_settings(),
            )
        finally:
            release.set()
            await asyncio.gather(retry, *([changing] if changing else []))
        self.assertEqual(remote, [(42, None, None), (42, 1, 2)])
        self.assertEqual(load_settings()["trays"], {"1": 42})
        self.assertEqual(load_settings()[PENDING_LOCATIONS], {})

    async def test_retry_batches_are_bounded_and_failures_do_not_starve_next_spools(
        self,
    ):
        with edit_settings() as settings:
            stage_spool_locations(settings, self.client, range(100, 110))
        self.client.set_active_tray.reset_mock()
        with patch("bambu_spoolman.location_sync.time.time", return_value=100):
            reconcile_spool_locations(self.client)
            self.assertEqual(self.client.set_active_tray.call_count, 8)
            self.client.set_active_tray.reset_mock()
            reconcile_spool_locations(self.client)
            self.assertEqual(self.client.set_active_tray.call_count, 2)
            self.assertEqual(
                {call.args[0] for call in self.client.set_active_tray.call_args_list},
                {108, 109},
            )

    async def test_stopping_active_worker_keeps_remaining_intents(self):
        with edit_settings() as settings:
            stage_spool_locations(settings, self.client, [42, 43])
        entered, release = threading.Event(), threading.Event()

        def delayed(*args):
            entered.set()
            release.wait(3)
            return True

        self.client.set_active_tray.side_effect = delayed
        worker = LocationReconciler(self.client)
        worker.start()
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            worker.stop()
            await asyncio.to_thread(worker.join, 0.01)
            self.assertTrue(worker.is_alive())
        finally:
            release.set()
            worker.stop()
            await asyncio.to_thread(worker.join, 1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(set(load_settings()[PENDING_LOCATIONS]), {"43"})
        self.assertEqual(self.client.set_active_tray.call_count, 1)

    async def test_full_outbox_during_physical_switch_stops_accounting(self):
        snapshot = {
            "print": {
                "ams": {
                    "ams": [{"id": "0", "tray": [{"id": "0", "tray_uuid": "tag-43"}]}]
                }
            }
        }
        self.client.lookup_by_tray_uuid.return_value = {"id": 43}
        with (
            patch(
                "bambu_spoolman.broker.automatic_spool_switch.new_client",
                return_value=self.client,
            ),
            patch(
                "bambu_spoolman.broker.automatic_spool_switch.stateful_printer_info.get_info",
                return_value=snapshot,
            ),
            patch.object(MqttHandler, "_create_client"),
            patch("bambu_spoolman.location_sync.MAX_PENDING_LOCATIONS", 0),
        ):
            switch = AutomaticSpoolSwitch()
            mqtt = MqttHandler("printer.test", "serial", "unused")
            stopped = Mock()
            mqtt.add_on_failure_callback(stopped)
            await asyncio.to_thread(
                switch.on_message,
                mqtt,
                {"print": {"command": "push_status", "ams": {}}},
            )
        self.assertEqual(mqtt.failure, "Spoolman location backlog is full")
        stopped.assert_called_once_with(mqtt)
        self.assertIn(DISPATCH_FAULT_KEY, load_settings())
        self.assertEqual(load_settings()["trays"], {"0": 42})
        self.client.set_active_tray.assert_not_called()


class LocationPatchTests(unittest.TestCase):
    @patch("bambu_spoolman.spoolman.requests.patch")
    def test_location_patch_never_copies_unrelated_extra_fields(self, patch_request):
        client = SpoolmanClient("http://spoolman.test")
        client.ams_field_name = "ams"
        client.tray_field_name = "tray"
        client.get_info = Mock(return_value={"version": "0.27.0"})
        client.get_spool = Mock(
            return_value={"extra": {"rfid": "old tag", "other": "old value"}}
        )
        self.assertTrue(client.set_active_tray(42, 1, 2))
        self.assertEqual(
            patch_request.call_args.kwargs["json"],
            {"extra": {"ams": json.dumps("1"), "tray": json.dumps("2")}},
        )
        client.get_spool.assert_not_called()

    @patch("bambu_spoolman.spoolman.requests.patch")
    def test_old_or_unknown_servers_cannot_replace_unrelated_metadata(
        self, patch_request
    ):
        for version in ("0.24.0", "", "not-a-version"):
            client = SpoolmanClient("http://spoolman.test")
            client.ams_field_name = "ams"
            client.tray_field_name = "tray"
            client.get_info = Mock(return_value={"version": version})
            self.assertFalse(client.set_active_tray(42, 1, 2))
        patch_request.assert_not_called()

    @patch("bambu_spoolman.spoolman.requests.patch")
    def test_pending_locations_detect_a_server_upgrade_without_app_restart(
        self, patch_request
    ):
        client = SpoolmanClient("http://spoolman.test")
        client.ams_field_name = "ams"
        client.tray_field_name = "tray"
        client.get_info = Mock(
            side_effect=[{"version": "0.24.0"}, {"version": "0.27.0"}]
        )
        self.assertFalse(client.set_active_tray(42, 1, 2))
        patch_request.assert_not_called()
        self.assertTrue(client.set_active_tray(42, 1, 2))
        patch_request.assert_called_once()

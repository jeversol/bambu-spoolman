import json
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from bambu_spoolman.bambu_mqtt import (
    DISPATCH_FAULT_KEY,
    MqttHandler,
    require_dispatch_reconciliation,
)
from bambu_spoolman.settings import edit_settings, load_settings


class MqttBacklogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(
            os.environ,
            BAMBU_SPOOLMAN_CONFIG=directory.name,
            BAMBU_SPOOLMAN_MQTT_MAX_QUEUED_MESSAGES="2",
            BAMBU_SPOOLMAN_MQTT_MAX_QUEUED_BYTES="1024",
            BAMBU_SPOOLMAN_MQTT_MAX_PAYLOAD_BYTES="256",
            BAMBU_SPOOLMAN_MQTT_MAX_PENDING_MESSAGES="2",
        )
        environment.start()
        self.addCleanup(environment.stop)
        client = patch.object(MqttHandler, "_create_client")
        client.start()
        self.addCleanup(client.stop)
        self.handler = MqttHandler("printer.test", "serial", "secret")

    def send(self, value):
        self.handler._on_message(
            None,
            None,
            Mock(topic="device/serial/report", payload=json.dumps(value).encode()),
        )

    def assert_fault(self):
        self.assertIsNotNone(self.handler.failure)
        self.assertTrue(self.handler._shutdown_requested.is_set())
        self.assertIn(DISPATCH_FAULT_KEY, load_settings())
        with self.assertRaisesRegex(RuntimeError, "requires reconciliation"):
            require_dispatch_reconciliation()

    def test_count_overflow_stops_dispatch_without_running_queued_transitions(self):
        entered = threading.Event()
        release = threading.Event()
        delivered = []

        def slow_callback(handler, message):
            delivered.append(message["print"]["layer_num"])
            entered.set()
            release.wait(3)

        failure = Mock()
        self.handler.add_callback(slow_callback)
        self.handler.add_on_failure_callback(failure)
        self.handler.message_dispatcher.start()
        try:
            self.send({"print": {"layer_num": 1}})
            self.assertTrue(entered.wait(1))
            self.send({"print": {"layer_num": 2}})
            self.send({"print": {"layer_num": 3}})
            self.send({"print": {"layer_num": 4}})
            self.assert_fault()
            self.assertEqual(self.handler.message_queue.qsize(), 2)
            self.send({"print": {"layer_num": 5}})
            failure.assert_called_once_with(self.handler)
        finally:
            release.set()
            self.handler.stop()
            self.handler.message_dispatcher.join(1)
        self.assertFalse(self.handler.message_dispatcher.is_alive())
        self.assertEqual(delivered, [1])

    def test_byte_budget_overflow_and_persistent_recovery_latch(self):
        self.handler.max_queued_bytes = 25
        self.send({"print": {"layer_num": 1}})
        self.assert_fault()
        self.assertEqual(self.handler.message_queue.qsize(), 0)
        with edit_settings() as settings:
            settings.pop(DISPATCH_FAULT_KEY)
        require_dispatch_reconciliation()

    def test_oversize_payload_is_rejected_before_json_parsing(self):
        message = Mock(topic="report", payload=b" " * 257)
        with patch("bambu_spoolman.bambu_mqtt.json.loads") as decode:
            self.handler._on_message(None, None, message)
        decode.assert_not_called()
        self.assert_fault()

    def test_normal_delivery_is_fifo_and_releases_byte_budget(self):
        seen = []
        finished = threading.Event()

        def receive(handler, value):
            seen.append(value["print"]["layer_num"])
            if len(seen) == 2:
                finished.set()

        self.handler.add_callback(receive)
        for layer in (1, 2):
            self.send({"print": {"layer_num": layer}})
        self.assertGreater(self.handler._queued_bytes, 0)
        self.handler.message_dispatcher.start()
        try:
            self.assertTrue(finished.wait(1))
        finally:
            self.handler.stop()
            self.handler.message_dispatcher.join(1)
        self.assertEqual(seen, [1, 2])
        self.assertEqual(self.handler._queued_bytes, 0)
        self.assertIsNone(self.handler.failure)

    def test_stop_with_full_queue_is_nonblocking_and_never_dispatches_pending(self):
        for layer in (1, 2):
            self.send({"print": {"layer_num": layer}})
        callback = Mock()
        self.handler.add_callback(callback)
        self.handler.stop()
        self.handler.message_dispatcher.start()
        self.handler.message_dispatcher.join(1)
        self.assertFalse(self.handler.message_dispatcher.is_alive())
        callback.assert_not_called()

    def test_disconnected_publishes_deduplicate_and_remain_bounded(self):
        for _ in range(100):
            self.handler.publish({"pushing": {"command": "pushall"}})
        self.assertEqual(len(self.handler.pending_messages), 1)
        self.handler.publish({"command": "second"})
        self.handler.publish({"command": "third"})
        self.assert_fault()
        self.assertEqual(len(self.handler.pending_messages), 2)

    def test_reconnect_flushes_pending_snapshot_once(self):
        value = {"pushing": {"command": "pushall"}}
        self.handler.publish(value)
        self.handler.publish(value)
        self.handler._on_connect(None, None, None, 0)
        self.assertEqual(self.handler.pending_messages, [])
        self.handler.client.publish.assert_called_once_with(
            "device/serial/request", json.dumps(value)
        )

    def test_non_object_print_field_does_not_break_network_callback(self):
        for value in (None, [], 1, "bad"):
            self.send({"print": value})
        self.assertTrue(self.handler.message_queue.empty())
        self.assertIsNone(self.handler.failure)

    def test_limits_reject_nonpositive_values(self):
        with patch.dict(os.environ, BAMBU_SPOOLMAN_MQTT_MAX_QUEUED_MESSAGES="0"):
            with self.assertRaisesRegex(ValueError, "must be positive"):
                MqttHandler("printer.test", "serial", "secret")

    def test_aggregate_byte_budget_is_independent_of_count(self):
        self.handler.message_queue.maxsize = 10
        value = {"print": {"layer_num": 1}}
        size = len(json.dumps(value).encode())
        self.handler.max_queued_bytes = size * 2
        self.send(value)
        self.send(value)
        self.assertIsNone(self.handler.failure)
        self.send(value)
        self.assert_fault()
        self.assertEqual(self.handler.message_queue.qsize(), 2)
        self.assertEqual(self.handler._queued_bytes, size * 2)

    def test_identical_non_snapshot_commands_are_not_coalesced(self):
        self.handler.publish({"command": "action"})
        self.handler.publish({"command": "action"})
        self.assertEqual(len(self.handler.pending_messages), 2)
        self.assertIsNone(self.handler.failure)

    def test_fault_remains_latched_when_marker_is_empty(self):
        with edit_settings() as settings:
            settings[DISPATCH_FAULT_KEY] = {}
        with self.assertRaisesRegex(RuntimeError, "requires reconciliation"):
            require_dispatch_reconciliation()

    def test_fault_persistence_failure_is_reported_and_still_stops(self):
        failure = Mock()
        self.handler.add_on_failure_callback(failure)
        with (
            patch(
                "bambu_spoolman.bambu_mqtt.edit_settings",
                side_effect=OSError("disk full"),
            ),
            patch("bambu_spoolman.bambu_mqtt.logger.exception") as log,
        ):
            self.handler.fail_dispatch("test failure")
        failure.assert_called_once_with(self.handler)
        self.assertTrue(self.handler._shutdown_requested.is_set())
        log.assert_called_once_with("event=mqtt_dispatch_fault_persistence_failed")
        self.handler.client.disconnect.assert_called_once_with()

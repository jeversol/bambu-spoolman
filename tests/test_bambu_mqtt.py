import json
import struct
import unittest
from unittest.mock import Mock, call, patch

from bambu_spoolman.bambu_mqtt import MqttHandler, recursive_merge


class RecursiveMergeTests(unittest.TestCase):
    def test_replaces_a_scalar_with_a_nested_delta(self):
        existing = {"print": {"ams": 0}}

        recursive_merge(existing, {"print": {"ams": {"ams": []}}})

        self.assertEqual(existing, {"print": {"ams": {"ams": []}}})


class MqttHandlerRecoveryTests(unittest.TestCase):
    @patch.object(MqttHandler, "_create_client")
    def test_reset_replaces_client_and_notifies_disconnect_callbacks(
        self, create_client
    ):
        old_client = Mock()
        new_client = Mock()
        create_client.side_effect = [old_client, new_client]
        handler = MqttHandler("printer.test", "serial", "access-code")
        on_disconnect = Mock()
        handler.add_on_disconnect_callback(on_disconnect)
        handler.connected = True

        handler._reset_client()

        self.assertIs(handler.client, new_client)
        self.assertFalse(handler.connected)
        old_client.disconnect.assert_called_once_with()
        on_disconnect.assert_called_once_with(handler)

    @patch("bambu_spoolman.bambu_mqtt.time.sleep")
    @patch.object(MqttHandler, "_create_client")
    def test_parser_error_recreates_client_before_retry(self, create_client, sleep):
        broken_client = Mock()
        replacement_client = Mock()
        broken_client.loop_forever.side_effect = struct.error(
            "bad char in struct format"
        )
        replacement_client.loop_forever.side_effect = KeyboardInterrupt()
        create_client.side_effect = [broken_client, replacement_client]
        handler = MqttHandler("printer.test", "serial", "access-code")

        with self.assertRaises(KeyboardInterrupt):
            handler.run()

        self.assertEqual(
            create_client.call_args_list,
            [call(), call()],
        )
        broken_client.disconnect.assert_called_once_with()
        replacement_client.connect.assert_called_once_with(
            "printer.test", 8883, keepalive=5
        )
        sleep.assert_called_once_with(1)

    @patch.object(MqttHandler, "_create_client")
    def test_mqtt_callback_queues_work_off_the_network_thread(self, create_client):
        handler = MqttHandler("printer.test", "serial", "access-code")
        message = Mock(
            topic="device/serial/report",
            payload=json.dumps({"print": {"command": "push_status"}}).encode(),
        )

        handler._on_message(None, None, message)

        self.assertEqual(
            handler.message_queue.get_nowait(),
            {"print": {"command": "push_status"}},
        )

    @patch.object(MqttHandler, "_create_client")
    def test_malformed_mqtt_payload_is_ignored(self, create_client):
        handler = MqttHandler("printer.test", "serial", "access-code")
        message = Mock(topic="device/serial/report", payload=b"not-json")

        handler._on_message(None, None, message)

        self.assertTrue(handler.message_queue.empty())

    @patch.object(MqttHandler, "_create_client")
    def test_non_object_mqtt_payload_is_ignored(self, create_client):
        handler = MqttHandler("printer.test", "serial", "access-code")
        message = Mock(topic="device/serial/report", payload=b"[]")

        handler._on_message(None, None, message)

        self.assertTrue(handler.message_queue.empty())


if __name__ == "__main__":
    unittest.main()

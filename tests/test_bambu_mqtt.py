import json
import struct
import unittest
from unittest.mock import Mock, call, patch

from bambu_spoolman.bambu_mqtt import (
    MqttHandler,
    StatefulPrinterInfo,
    recursive_merge,
)


class RecursiveMergeTests(unittest.TestCase):
    def test_replaces_a_scalar_with_a_nested_delta(self):
        existing = {"print": {"ams": 0}}

        recursive_merge(existing, {"print": {"ams": {"ams": []}}})

        self.assertEqual(existing, {"print": {"ams": {"ams": []}}})

    def test_merges_partial_ams_and_tray_lists_by_id(self):
        existing = {
            "print": {
                "ams": {
                    "ams": [
                        {
                            "id": "0",
                            "humidity": "3",
                            "tray": [
                                {"id": "0", "tray_type": "PLA"},
                                {"id": "1", "tray_type": "PETG"},
                            ],
                        },
                        {"id": "1", "humidity": "4", "tray": []},
                    ]
                }
            }
        }
        delta = {
            "print": {
                "ams": {
                    "ams": [
                        {
                            "id": 0,
                            "tray": [{"id": 1, "tray_type": "ABS"}],
                        }
                    ]
                }
            }
        }

        recursive_merge(existing, delta)

        units = existing["print"]["ams"]["ams"]
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0]["humidity"], "3")
        self.assertEqual(units[0]["tray"][0]["tray_type"], "PLA")
        self.assertEqual(units[0]["tray"][1]["tray_type"], "ABS")
        self.assertEqual(units[1]["humidity"], "4")

    def test_non_keyed_lists_are_replaced(self):
        existing = {"print": {"s_obj": [1, 2]}}

        recursive_merge(existing, {"print": {"s_obj": [3]}})

        self.assertEqual(existing["print"]["s_obj"], [3])


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


class StatefulPrinterInfoTests(unittest.TestCase):
    def test_presence_masks_remove_stale_units_and_trays(self):
        printer = StatefulPrinterInfo()
        printer.update_tray_count = Mock()
        printer.handle_message(
            None,
            {
                "print": {
                    "command": "push_status",
                    "ams": {
                        "ams": [
                            {
                                "id": "0",
                                "tray": [{"id": "0"}, {"id": "1"}],
                            },
                            {"id": "1", "tray": [{"id": "0"}]},
                        ]
                    },
                }
            },
        )

        printer.handle_message(
            None,
            {
                "print": {
                    "command": "push_status",
                    "ams": {"ams_exist_bits": "1", "tray_exist_bits": "1"},
                }
            },
        )

        units = printer.get_info()["print"]["ams"]["ams"]
        self.assertEqual(units, [{"id": "0", "tray": [{"id": "0"}]}])


if __name__ == "__main__":
    unittest.main()

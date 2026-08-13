import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from bambu_spoolman.broker.automatic_spool_switch import (
    UNKNOWN_TRAY,
    AutomaticSpoolSwitch,
)


def printer_status(uuid):
    return {
        "print": {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [{"id": "0", "tray_uuid": uuid}],
                    }
                ]
            }
        }
    }


class AutomaticSpoolSwitchOverrideTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.config_directory = self.temp_directory.name

        environment = patch.dict(
            os.environ,
            {"BAMBU_SPOOLMAN_CONFIG": self.config_directory},
        )
        environment.start()
        self.addCleanup(environment.stop)

        self.settings_path = os.path.join(self.config_directory, "settings.json")
        self.write_settings(
            {
                "trays": {"0": 32},
                "tray_count": 4,
                "locked_trays": [0],
                "rfid_overrides": {},
            }
        )

        client_patch = patch(
            "bambu_spoolman.broker.automatic_spool_switch.new_client"
        )
        new_client = client_patch.start()
        self.addCleanup(client_patch.stop)
        self.client = Mock()
        new_client.return_value = self.client
        self.switch = AutomaticSpoolSwitch()

    def write_settings(self, settings):
        with open(self.settings_path, "w") as settings_file:
            json.dump(settings, settings_file)

    def read_settings(self):
        with open(self.settings_path) as settings_file:
            return json.load(settings_file)

    @patch("bambu_spoolman.broker.automatic_spool_switch.stateful_printer_info")
    def test_override_is_persisted_and_unlocks_without_clearing_mapping(
        self, printer_info
    ):
        printer_info.connected = True
        printer_info.get_info.return_value = printer_status("tag-32")

        result = self.switch.override_tray(0, "tag-32")

        self.assertTrue(result)
        settings = self.read_settings()
        self.assertEqual(settings["rfid_overrides"], {"0": "tag-32"})
        self.assertEqual(settings["locked_trays"], [])
        self.assertEqual(settings["trays"], {"0": 32})

    @patch("bambu_spoolman.broker.automatic_spool_switch.stateful_printer_info")
    def test_override_rejects_a_tag_that_is_not_in_the_tray(self, printer_info):
        printer_info.connected = True
        printer_info.get_info.return_value = printer_status("another-tag")

        result = self.switch.override_tray(0, "tag-32")

        self.assertFalse(result)
        self.assertEqual(self.read_settings()["rfid_overrides"], {})

    def test_initial_sync_respects_a_persisted_override(self):
        settings = self.read_settings()
        settings["rfid_overrides"] = {"0": "tag-32"}
        self.write_settings(settings)
        self.switch.tray_mapping = {}

        self.switch._initial_sync(printer_status("tag-32")["print"])

        self.client.lookup_by_tray_uuid.assert_not_called()
        settings = self.read_settings()
        self.assertEqual(settings["locked_trays"], [])
        self.assertEqual(settings["trays"], {"0": 32})

    def test_removing_spool_clears_override_and_mapping(self):
        settings = self.read_settings()
        settings["locked_trays"] = []
        settings["rfid_overrides"] = {"0": "tag-32"}
        self.write_settings(settings)
        self.switch.tray_mapping = {0: "tag-32"}

        self.switch._sync(printer_status(UNKNOWN_TRAY)["print"])

        settings = self.read_settings()
        self.assertEqual(settings["rfid_overrides"], {})
        self.assertEqual(settings["trays"], {})
        self.client.set_active_tray.assert_called_once_with(32, None, None)


if __name__ == "__main__":
    unittest.main()

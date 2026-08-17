import os
import unittest
from unittest.mock import Mock, patch

import requests

from bambu_spoolman.spoolman import SpoolmanClient


class SpoolmanClientTests(unittest.TestCase):
    def test_endpoint_is_required_and_normalized(self):
        with self.assertRaises(ValueError):
            SpoolmanClient(None)

        client = SpoolmanClient(" http://spoolman.test/ ")

        self.assertEqual(client.endpoint, "http://spoolman.test")

    @patch("bambu_spoolman.spoolman.requests.put")
    def test_consume_spool_raises_for_an_unsuccessful_response(self, put):
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("server error")
        put.return_value = response

        client = SpoolmanClient("http://spoolman.test")

        with self.assertRaises(requests.HTTPError):
            client.consume_spool(42, length=10)

    @patch("bambu_spoolman.spoolman.requests.put")
    def test_consume_spool_uses_configured_timeout(self, put):
        put.return_value = Mock()
        with patch.dict(os.environ, {"BAMBU_SPOOLMAN_HTTP_TIMEOUT": "12.5"}):
            client = SpoolmanClient("http://spoolman.test")

        client.consume_spool(42, length=10)

        put.assert_called_once_with(
            "http://spoolman.test/api/v1/spool/42/use",
            json={"use_length": 10, "use_weight": None},
            verify=True,
            timeout=12.5,
        )

    def test_consume_spool_requires_one_positive_amount(self):
        client = SpoolmanClient("http://spoolman.test")

        for arguments in ({}, {"length": 0}, {"weight": -1}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                client.consume_spool(42, **arguments)

        with self.assertRaises(ValueError):
            client.consume_spool(42, length=10, weight=5)

    @patch("bambu_spoolman.spoolman.requests.patch")
    def test_empty_tray_uuid_removes_rfid_field(self, patch_request):
        patch_request.return_value = Mock()
        client = SpoolmanClient("http://spoolman.test")
        client.get_spool = Mock(
            return_value={"id": 32, "extra": {"rfid_tag": '"tag-32"'}}
        )

        with patch.dict(os.environ, {"SPOOLMAN_RFID_FIELD_KEY": "rfid_tag"}):
            result = client.set_tray_uuid(32, "")

        self.assertTrue(result)
        patch_request.assert_called_once_with(
            "http://spoolman.test/api/v1/spool/32",
            json={"extra": {}},
            verify=True,
            timeout=30.0,
        )

    @patch("bambu_spoolman.spoolman.requests.patch")
    def test_set_active_tray_sends_one_url_and_clears_fields(self, patch_request):
        patch_request.return_value = Mock()
        client = SpoolmanClient("http://spoolman.test/")
        client.get_spool = Mock(return_value={"id": 32, "extra": {}})
        client.ams_field_name = "ams"
        client.tray_field_name = "tray"

        result = client.set_active_tray(32)

        self.assertTrue(result)
        patch_request.assert_called_once_with(
            "http://spoolman.test/api/v1/spool/32",
            json={"extra": {"ams": '""', "tray": '""'}},
            verify=True,
            timeout=30.0,
        )

    def test_external_filament_color_matching_is_case_insensitive(self):
        client = SpoolmanClient("http://spoolman.test")
        client.get_external_filaments = Mock(
            return_value=[
                {
                    "id": "bambu_pla_basic",
                    "manufacturer": "Bambu Lab",
                    "material": "PLA",
                    "color_hex": "#a1b2c3ff",
                }
            ]
        )

        matched = client.match_external_filament(
            {"tray_type": "PLA", "tray_color": "A1B2C3FF"}
        )

        self.assertEqual(matched["id"], "bambu_pla_basic")


if __name__ == "__main__":
    unittest.main()

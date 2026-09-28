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

    def test_native_tag_support_uses_spoolman_version(self):
        client = SpoolmanClient("http://spoolman.test")
        client.get_info = Mock(return_value={"version": "v0.27.0"})

        self.assertTrue(client.supports_native_tags())
        self.assertTrue(client.supports_native_tags())
        client.get_info.assert_called_once_with()

    def test_old_spoolman_does_not_support_native_tags(self):
        client = SpoolmanClient("http://spoolman.test")
        client.get_info = Mock(return_value={"version": "0.26.1"})

        self.assertFalse(client.supports_native_tags())

    @patch("bambu_spoolman.spoolman.requests.post")
    def test_scan_tag_reports_bambu_reader_and_returns_spool(self, post):
        response = Mock()
        response.json.return_value = {"spool": {"id": 32}}
        post.return_value = response
        client = SpoolmanClient("http://spoolman.test")
        client._supports_native_tags = True

        spool = client.scan_tag(
            "A1B2C3D4", reader_id="bambu-ams-1-slot-1", name="Bambu AMS 1 slot 1"
        )

        self.assertEqual(spool, {"id": 32})
        post.assert_called_once_with(
            "http://spoolman.test/api/v1/tag/scan",
            json={
                "uid": "A1B2C3D4",
                "format": "bambu",
                "reader_id": "bambu-ams-1-slot-1",
                "name": "Bambu AMS 1 slot 1",
            },
            verify=True,
            timeout=30.0,
        )

    @patch("bambu_spoolman.spoolman.requests.get")
    def test_tray_uuid_lookup_prefers_native_tag(self, get):
        response = Mock()
        response.json.return_value = [{"id": 32}]
        get.return_value = response
        client = SpoolmanClient("http://spoolman.test")
        client._supports_native_tags = True

        spool = client.lookup_by_tray_uuid("AABBCCDD")

        self.assertEqual(spool, {"id": 32})
        get.assert_called_once_with(
            "http://spoolman.test/api/v1/spool?tag=AABBCCDD&allow_archived=false",
            verify=True,
            timeout=30.0,
        )

    @patch("bambu_spoolman.spoolman.requests.post")
    def test_set_tray_uuid_uses_native_tags_without_custom_field(self, post):
        post.return_value = Mock()
        client = SpoolmanClient("http://spoolman.test")
        client._supports_native_tags = True
        client.get_spool = Mock(return_value={"id": 32, "extra": {}, "tags": []})

        with patch.dict(os.environ, {}, clear=True):
            result = client.set_tray_uuid(32, "AABBCCDD")

        self.assertTrue(result)
        post.assert_called_once_with(
            "http://spoolman.test/api/v1/spool/32/tag",
            json={"uid": "AABBCCDD", "format": "bambu-tray"},
            verify=True,
            timeout=30.0,
        )

    @patch("bambu_spoolman.spoolman.requests.delete")
    def test_unlink_removes_only_tags_managed_by_this_integration(self, delete):
        delete.return_value = Mock()
        client = SpoolmanClient("http://spoolman.test")
        client._supports_native_tags = True
        client.get_spool = Mock(
            return_value={
                "id": 32,
                "extra": {},
                "tags": [
                    {"uid": "AAAA", "format": "bambu"},
                    {"uid": "BBBB", "format": "bambu-tray"},
                    {"uid": "CCCC", "format": "ntag"},
                ],
            }
        )

        with patch.dict(os.environ, {}, clear=True):
            result = client.set_tray_uuid(32, "")

        self.assertTrue(result)
        self.assertEqual(delete.call_count, 2)
        self.assertEqual(
            [request_call.args[0] for request_call in delete.call_args_list],
            [
                "http://spoolman.test/api/v1/spool/32/tag/AAAA",
                "http://spoolman.test/api/v1/spool/32/tag/BBBB",
            ],
        )

    @patch("bambu_spoolman.spoolman.requests.patch")
    def test_empty_tray_uuid_removes_rfid_field(self, patch_request):
        patch_request.return_value = Mock()
        client = SpoolmanClient("http://spoolman.test")
        client._supports_native_tags = False
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

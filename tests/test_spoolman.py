import os
import unittest
from unittest.mock import Mock, patch

import requests

from bambu_spoolman.spoolman import SpoolmanClient


class SpoolmanClientTests(unittest.TestCase):
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
        with patch.dict(
            os.environ, {"BAMBU_SPOOLMAN_HTTP_TIMEOUT": "12.5"}
        ):
            client = SpoolmanClient("http://spoolman.test")

        client.consume_spool(42, length=10)

        put.assert_called_once_with(
            "http://spoolman.test/api/v1/spool/42/use",
            json={"use_length": 10, "use_weight": None},
            verify=True,
            timeout=12.5,
        )


if __name__ == "__main__":
    unittest.main()

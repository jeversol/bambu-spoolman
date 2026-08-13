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


if __name__ == "__main__":
    unittest.main()

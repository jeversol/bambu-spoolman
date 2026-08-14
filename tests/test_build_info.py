import os
import unittest
from unittest.mock import patch

from bambu_spoolman.build_info import BuildInfo, get_build_info


class BuildInfoTest(unittest.TestCase):
    def test_reads_embedded_build_information(self):
        environment = {
            "BAMBU_SPOOLMAN_VERSION": "v1.2.3",
            "BAMBU_SPOOLMAN_BUILD_NUMBER": "42.1",
            "BAMBU_SPOOLMAN_REVISION": "abcdef123456",
            "BAMBU_SPOOLMAN_BUILD_DATE": "2026-08-14T12:34:56Z",
        }

        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                get_build_info(),
                BuildInfo(
                    version="v1.2.3",
                    build_number="42.1",
                    revision="abcdef123456",
                    build_date="2026-08-14T12:34:56Z",
                ),
            )

    def test_local_build_defaults_are_unambiguous(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                get_build_info(),
                BuildInfo(
                    version="local",
                    build_number="local",
                    revision="unknown",
                    build_date="unknown",
                ),
            )


if __name__ == "__main__":
    unittest.main()

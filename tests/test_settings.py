import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from bambu_spoolman.settings import edit_settings, load_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        environment = patch.dict(
            os.environ, {"BAMBU_SPOOLMAN_CONFIG": self.temp_directory.name}
        )
        environment.start()
        self.addCleanup(environment.stop)

    def test_concurrent_edits_do_not_overwrite_each_other(self):
        first_edit_started = threading.Event()
        allow_first_edit_to_finish = threading.Event()

        def first_edit():
            with edit_settings() as settings:
                settings["tray_count"] = 4
                first_edit_started.set()
                allow_first_edit_to_finish.wait(timeout=2)

        def second_edit():
            first_edit_started.wait(timeout=2)
            with edit_settings() as settings:
                settings["trays"]["0"] = 42

        first = threading.Thread(target=first_edit)
        second = threading.Thread(target=second_edit)
        first.start()
        second.start()
        allow_first_edit_to_finish.set()
        first.join(timeout=2)
        second.join(timeout=2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        settings = load_settings()
        self.assertEqual(settings["tray_count"], 4)
        self.assertEqual(settings["trays"], {"0": 42})


if __name__ == "__main__":
    unittest.main()

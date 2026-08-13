import os
import tempfile
import unittest
import zipfile

from bambu_spoolman.gcode.bambu import open_gcode


class GcodeArchiveTests(unittest.TestCase):
    def create_archive(self, members):
        archive_file = tempfile.NamedTemporaryFile(suffix=".3mf", delete=False)
        archive_file.close()
        self.addCleanup(os.remove, archive_file.name)

        with zipfile.ZipFile(archive_file.name, "w") as archive:
            for path, content in members.items():
                archive.writestr(path, content)
        return archive_file.name

    def test_streams_gcode_discovered_from_model_settings(self):
        archive_path = self.create_archive(
            {
                "Metadata/model_settings.config": (
                    '<root><plate><metadata key="gcode_file" '
                    'value="Metadata/plate_1.gcode"/></plate></root>'
                ),
                "Metadata/plate_1.gcode": "M620 S0\nG1 E10\n",
            }
        )

        with open_gcode(archive_path) as gcode:
            self.assertEqual(list(gcode), ["M620 S0\n", "G1 E10\n"])

    def test_streams_explicit_gcode_path_without_model_settings(self):
        archive_path = self.create_archive(
            {"Metadata/plate_2.gcode": "M620 S1\nG1 E20\n"}
        )

        with open_gcode(archive_path, "/Metadata/plate_2.gcode") as gcode:
            self.assertEqual(list(gcode), ["M620 S1\n", "G1 E20\n"])

    def test_returns_none_when_gcode_member_is_missing(self):
        archive_path = self.create_archive({"other.txt": "content"})

        with open_gcode(archive_path, "missing.gcode") as gcode:
            self.assertIsNone(gcode)

    def test_returns_none_when_model_settings_are_missing(self):
        archive_path = self.create_archive({"other.txt": "content"})

        with open_gcode(archive_path) as gcode:
            self.assertIsNone(gcode)

    def test_returns_none_for_corrupt_archive(self):
        archive_file = tempfile.NamedTemporaryFile(suffix=".3mf", delete=False)
        archive_file.write(b"not a zip archive")
        archive_file.close()
        self.addCleanup(os.remove, archive_file.name)

        with open_gcode(archive_file.name) as gcode:
            self.assertIsNone(gcode)

    def test_returns_none_for_malformed_model_settings(self):
        archive_path = self.create_archive(
            {"Metadata/model_settings.config": "<root><plate>"}
        )

        with open_gcode(archive_path) as gcode:
            self.assertIsNone(gcode)

    def test_returns_none_when_model_settings_have_no_plate(self):
        archive_path = self.create_archive(
            {"Metadata/model_settings.config": "<root/>"}
        )

        with open_gcode(archive_path) as gcode:
            self.assertIsNone(gcode)


if __name__ == "__main__":
    unittest.main()

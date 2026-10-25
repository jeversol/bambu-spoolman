import io
import os
import tempfile
import time
import unittest
import zipfile
from unittest.mock import patch

from bambu_spoolman.gcode.bambu import bounded_lines, open_gcode
from bambu_spoolman.model_limits import ModelLimitError, ModelLimits


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

    def test_rejects_oversized_archive_and_selected_member(self):
        path = self.create_archive({"plate.gcode": "M620 S0\nG1 E10\n"})
        for limits in (ModelLimits(download_bytes=1), ModelLimits(gcode_bytes=1)):
            with self.assertRaises(ModelLimitError):
                with open_gcode(path, "plate.gcode", limits=limits) as gcode:
                    list(gcode)

    def test_compressed_oversized_gcode_is_rejected_before_inflation(self):
        file = tempfile.NamedTemporaryFile(suffix=".3mf", delete=False)
        file.close()
        self.addCleanup(os.remove, file.name)
        with zipfile.ZipFile(
            file.name, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr("plate.gcode", b"A" * 100_000)
        with patch(
            "bambu_spoolman.gcode.bambu.zipfile.ZipFile.open",
            side_effect=AssertionError("Member must not be inflated"),
        ):
            with self.assertRaises(ModelLimitError):
                with open_gcode(
                    file.name, "plate.gcode", limits=ModelLimits(gcode_bytes=10)
                ):
                    self.fail("Oversized compressed member was accepted")

    def test_rejects_too_many_archive_entries(self):
        path = self.create_archive({"a": "", "plate.gcode": ""})
        with self.assertRaises(ModelLimitError):
            with open_gcode(path, "plate.gcode", limits=ModelLimits(archive_entries=1)):
                self.fail("Oversized member index was accepted")

    def test_rejects_oversized_metadata_before_xml_parse(self):
        path = self.create_archive({"Metadata/model_settings.config": "<root/>"})
        with self.assertRaises(ModelLimitError):
            with open_gcode(path, limits=ModelLimits(metadata_bytes=2)):
                self.fail("Oversized XML was accepted")

    def test_rejects_dtds_in_utf8_and_utf16_metadata(self):
        xml = '<!DOCTYPE root [<!ENTITY expanded "value">]><root>&expanded;</root>'
        for encoding in ("utf-8", "utf-16"):
            path = self.create_archive(
                {"Metadata/model_settings.config": xml.encode(encoding)}
            )
            with self.assertRaises(ModelLimitError):
                with open_gcode(path):
                    self.fail("XML DTD was accepted")

    def test_enforces_line_count_line_bytes_and_actual_total_bytes(self):
        for content, limits in (
            ("ab\ncd\n", ModelLimits(gcode_lines=1)),
            ("abcde\n", ModelLimits(line_bytes=5)),
            ("éé\n", ModelLimits(line_bytes=4)),
            ("ab\ncd\n", ModelLimits(gcode_bytes=5)),
        ):
            with self.subTest(content=content, limits=limits):
                with self.assertRaises(ModelLimitError):
                    list(bounded_lines(io.StringIO(content), limits, time.monotonic()))

    def test_exact_line_and_byte_limits_are_accepted(self):
        lines = list(
            bounded_lines(
                io.StringIO("ab\ncd\n"),
                ModelLimits(gcode_bytes=6, line_bytes=3, gcode_lines=2),
                time.monotonic(),
            )
        )
        self.assertEqual(lines, ["ab\n", "cd\n"])


if __name__ == "__main__":
    unittest.main()

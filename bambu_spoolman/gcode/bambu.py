import io
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager

from loguru import logger


@contextmanager
def open_gcode(path, gcode_path=None):
    """Open a G-code file inside a 3MF archive as a streaming text reader."""
    logger.debug("Opening GCODE from {}", path)

    with zipfile.ZipFile(path, "r") as archive:
        if gcode_path is None:
            metadata_path = "Metadata/model_settings.config"
            logger.debug("Looking for GCODE in {}", metadata_path)
            try:
                with archive.open(metadata_path) as metadata_file:
                    root = ET.parse(metadata_file).getroot()
            except KeyError:
                logger.error("Could not find model settings in 3MF archive")
                yield None
                return

            plate = root[0]
            for item in plate:
                if item.attrib.get("key") == "gcode_file":
                    gcode_path = item.attrib.get("value")
                    break

        if gcode_path is None:
            logger.error("Could not find GCODE file")
            yield None
            return

        # ZIP members always use forward slashes. A leading slash would make
        # sense on the printer filesystem but is not part of the archive name.
        archive_path = gcode_path.replace("\\", "/").lstrip("/")
        logger.debug("Found GCODE file at {}", archive_path)

        try:
            raw_gcode = archive.open(archive_path)
        except KeyError:
            logger.error("Could not find GCODE file {} in 3MF archive", archive_path)
            yield None
            return

        with raw_gcode:
            with io.TextIOWrapper(raw_gcode, encoding="utf-8") as gcode:
                yield gcode

import io
import os
import time
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager

from loguru import logger

from bambu_spoolman.model_limits import ModelLimitError, get_model_limits


@contextmanager
def open_gcode(path, gcode_path=None, *, limits=None):
    """Open a G-code file inside a 3MF archive as a streaming text reader."""
    logger.debug("Opening GCODE from {}", path)
    limits = limits or get_model_limits()
    started = time.monotonic()
    try:
        if os.path.getsize(path) > limits.download_bytes:
            raise ModelLimitError("3MF archive exceeds the download byte budget")
        archive = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as e:
        logger.error("Could not open 3MF archive: {}", e)
        yield None
        return

    with archive:
        if len(archive.infolist()) > limits.archive_entries:
            raise ModelLimitError("3MF archive contains too many entries")
        if gcode_path is None:
            metadata_path = "Metadata/model_settings.config"
            logger.debug("Looking for GCODE in {}", metadata_path)
            try:
                if archive.getinfo(metadata_path).file_size > limits.metadata_bytes:
                    raise ModelLimitError("3MF metadata exceeds its byte budget")
                with archive.open(metadata_path) as metadata_file:
                    metadata = metadata_file.read(limits.metadata_bytes + 1)
                if len(metadata) > limits.metadata_bytes:
                    raise ModelLimitError("3MF metadata exceeds its byte budget")
                root = ET.fromstring(
                    metadata, parser=ET.XMLParser(target=NoDtdTreeBuilder())
                )
            except KeyError:
                logger.error("Could not find model settings in 3MF archive")
                yield None
                return
            except (ET.ParseError, OSError) as e:
                logger.error("Could not parse model settings: {}", e)
                yield None
                return

            if not len(root):
                logger.error("Model settings do not contain a plate")
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
            if archive.getinfo(archive_path).file_size > limits.gcode_bytes:
                raise ModelLimitError("G-code exceeds its uncompressed byte budget")
            raw_gcode = archive.open(archive_path)
        except KeyError:
            logger.error("Could not find GCODE file {} in 3MF archive", archive_path)
            yield None
            return

        with raw_gcode:
            with io.TextIOWrapper(raw_gcode, encoding="utf-8") as gcode:
                yield bounded_lines(gcode, limits, started)


class NoDtdTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ModelLimitError("3MF metadata must not contain a DTD")


def bounded_lines(gcode, limits, started):
    total_bytes = 0
    line_count = 0
    while True:
        if time.monotonic() - started > limits.seconds:
            raise ModelLimitError("G-code parsing exceeded its time budget")
        # A bounded readline also prevents one giant line allocating the whole
        # member before the parser can enforce its operation/line budget.
        line = gcode.readline(limits.line_bytes + 1)
        if not line:
            return
        size = len(line.encode("utf-8"))
        total_bytes += size
        line_count += 1
        if size > limits.line_bytes:
            raise ModelLimitError("G-code line exceeds its byte budget")
        if total_bytes > limits.gcode_bytes:
            raise ModelLimitError("G-code exceeds its uncompressed byte budget")
        if line_count > limits.gcode_lines:
            raise ModelLimitError("G-code contains too many lines")
        yield line

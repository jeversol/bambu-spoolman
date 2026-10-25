"""Own model transport and parsing; callers own checkpoint installation."""

import os
import tempfile
import time
import zipfile
from urllib.parse import urlparse

import requests
from loguru import logger

from bambu_spoolman.bambu_ftp import retrieve_3mf
from bambu_spoolman.gcode.bambu import open_gcode
from bambu_spoolman.gcode.parser import evaluate_gcode
from bambu_spoolman.model_limits import (
    DownloadBudget,
    ModelLimitError,
    get_model_limits,
)
from bambu_spoolman.settings import get_http_timeout

MODEL_DOWNLOAD_CHUNK_SIZE = 64 * 1024


def retrieve_model(model_url):

    if not model_url:
        logger.warning("Print announcement did not include a model URL")
        return None

    ftp_uris = ("file", "ftp", "brtc")

    # Turn URL into a URI
    uri = urlparse(model_url)

    if uri.scheme == "https" or uri.scheme == "http":
        return download_model(model_url)
    elif uri.scheme in ftp_uris:
        path = f"{uri.netloc}{uri.path}"
        return retrieve_model_from_ftp(path)
    else:
        logger.warning("Unsupported model URL: {}", model_url)
        return None


def download_model(model_url, *, limits=None):
    logger.info("event=model_download_started scheme=http")

    temp_file_name = None
    response = None
    downloaded_bytes = 0
    limits = limits or get_model_limits()
    started_at = time.monotonic()
    try:
        with tempfile.NamedTemporaryFile(suffix=".3mf", delete=False) as model_file:
            temp_file_name = model_file.name
            response = requests.get(
                model_url,
                timeout=get_http_timeout(),
                stream=True,
            )
            response.raise_for_status()
            length = response.headers.get("Content-Length")
            if isinstance(length, str) and int(length) > limits.download_bytes:
                raise ModelLimitError("Model exceeds the download byte budget")
            budget = DownloadBudget(model_file, limits)

            for chunk in response.iter_content(chunk_size=MODEL_DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    budget.write(chunk)
                    downloaded_bytes = budget.bytes
    except Exception as e:
        logger.error("Failed to download model: {}", e)
        if temp_file_name is not None:
            try:
                os.remove(temp_file_name)
            except FileNotFoundError:
                pass
        return None
    finally:
        if response is not None:
            response.close()

    logger.info(
        "event=model_download_complete bytes={} duration_seconds={:.3f}",
        downloaded_bytes,
        time.monotonic() - started_at,
    )
    return temp_file_name


def retrieve_model_from_ftp(model_path):
    for prefix in ("/sdcard/", "/media/usb0/"):
        if model_path.startswith(prefix):
            model_path = model_path.removeprefix(prefix)
            break
    return retrieve_3mf(model_path)


def load_model(model_path, gcode_file=None):
    try:
        with open_gcode(model_path, gcode_file) as gcode:
            if gcode is None:
                return None
            return evaluate_gcode(gcode)
    except (
        OSError,
        UnicodeError,
        ValueError,
        zipfile.BadZipFile,
        RuntimeError,
        EOFError,
    ) as error:
        logger.error("Failed to parse GCODE from model: {}", error)
        return None

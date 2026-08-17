import json
import os
import tempfile
import threading
from contextlib import contextmanager

EXTERNAL_SPOOL_ID = 255
DEFAULT_HTTP_TIMEOUT = 30.0
RFID_FIELD_KEY_ENV = "SPOOLMAN_RFID_FIELD_KEY"
LEGACY_RFID_FIELD_KEY_ENV = "SPOOLMAN_SPOOL_FIELD_NAME"
_settings_lock = threading.RLock()


def get_http_timeout():
    return float(os.environ.get("BAMBU_SPOOLMAN_HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT))


def get_rfid_field_key():
    """Return the Spoolman spool-field key used to store printer RFID UUIDs."""
    field_key = os.environ.get(RFID_FIELD_KEY_ENV)
    if field_key is None:
        field_key = os.environ.get(LEGACY_RFID_FIELD_KEY_ENV)
    if field_key is None:
        return None
    return field_key.strip() or None


def get_configuration_path(path):
    configuration_directory = os.environ.get("BAMBU_SPOOLMAN_CONFIG")
    if configuration_directory is None:
        return path
    return os.path.join(configuration_directory, path)


def _settings_file():
    return get_configuration_path("settings.json")


def save_settings(settings):
    with _settings_lock:
        _save_settings(settings)


def _save_settings(settings):
    settings_path = _settings_file()
    settings_directory = os.path.dirname(os.path.abspath(settings_path))
    os.makedirs(settings_directory, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=settings_directory, delete=False
        ) as settings_file:
            temporary_path = settings_file.name
            json.dump(settings, settings_file)
            settings_file.flush()
            os.fsync(settings_file.fileno())
        os.replace(temporary_path, settings_path)
    finally:
        if temporary_path is not None and os.path.exists(temporary_path):
            os.remove(temporary_path)


def load_settings():
    with _settings_lock:
        return _load_settings()


def _load_settings():
    settings_file_path = _settings_file()
    if os.path.exists(settings_file_path):
        with open(settings_file_path) as f:
            data = json.load(f)

            if get_rfid_field_key() is None:
                data["locked_trays"] = []
            return data
    return {
        "trays": {},
        "tray_count": 0,
        "locked_trays": [],
        "rfid_overrides": {},
    }


@contextmanager
def edit_settings():
    """Atomically load, mutate, and save settings across application threads."""
    with _settings_lock:
        settings = _load_settings()
        yield settings
        _save_settings(settings)

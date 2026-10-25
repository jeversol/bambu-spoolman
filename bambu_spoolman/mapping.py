"""Serialize mapping mutations across API workers and MQTT callbacks.

The shared lock spans local persistence and optional remote location updates.
The settings file lock is separate and is never held during network calls, so
reads remain responsive. Async callers must move the whole operation to a worker;
never hold this thread lock across an await.
"""

import threading
from functools import wraps

from loguru import logger

mapping_lock = threading.RLock()


def serialized_mapping(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with mapping_lock:
            return function(*args, **kwargs)

    return wrapped


def sync_spool_locations(client, updates):
    """Try every location update and return IDs whose outcome is unconfirmed."""
    failed = []
    for spool_id, ams, tray in updates:
        try:
            result = client.set_active_tray(spool_id, ams, tray)
            configured = getattr(client, "ams_field_name", None) or getattr(
                client, "tray_field_name", None
            )
            if result is False and configured:
                raise RuntimeError("Configured Spoolman location update failed")
        except Exception:
            logger.exception("event=spool_location_sync_failed spool_id={}", spool_id)
            failed.append(spool_id)
    return failed

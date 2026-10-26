"""Durable, superseding intents for optional Spoolman location metadata."""

import hashlib
import json
import threading
import time

from loguru import logger

from bambu_spoolman.mapping import serialized_mapping
from bambu_spoolman.settings import edit_settings, load_settings

PENDING_LOCATIONS = "pending_spool_locations"
MAX_PENDING_LOCATIONS = 1024
RETRY_SECONDS = 30
MAX_RETRY_SECONDS = 300
BATCH_SIZE = 8


class LocationQueueFull(ValueError):
    status = 503


def _scope(client):
    # Bind retries to their server and field configuration without persisting
    # endpoint credentials. Changing configuration requires explicit review.
    identity = [client.endpoint, client.ams_field_name, client.tray_field_name]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def _location(settings, spool_id):
    for tray, spool in settings.get("trays", {}).items():
        if str(spool) == str(spool_id):
            tray = int(tray)
            if tray != 255:
                return (tray // 4) + 1, (tray % 4) + 1
    return None, None


def stage_spool_locations(settings, client, spool_ids):
    """Stage within the SAME settings transaction that changes the mapping."""
    pending = dict(settings.get(PENDING_LOCATIONS, {}))
    scope = _scope(client)
    for spool_id in spool_ids:
        ams, tray = _location(settings, spool_id)
        pending[str(int(spool_id))] = {
            "scope": scope,
            "ams": ams,
            "tray": tray,
            "attempts": 0,
            "retry_after": 0,
        }
    if len(pending) > MAX_PENDING_LOCATIONS:
        raise LocationQueueFull(
            "Pending Spoolman locations are full; restore synchronization "
            "before changing more mappings"
        )
    settings[PENDING_LOCATIONS] = pending


@serialized_mapping
def _deliver(client, spool_id, stop=None, *, force=False):
    if stop is not None and stop.is_set():
        return None
    settings = load_settings()
    entry = settings.get(PENDING_LOCATIONS, {}).get(str(spool_id))
    if entry is None:
        return None
    if entry["scope"] != _scope(client):
        logger.warning("event=spool_location_scope_mismatch spool_id={}", spool_id)
        return False
    now = time.time()
    if not force and entry["retry_after"] > now:
        return None
    # Always use the current authoritative mapping. A stale clear must never
    # undo a newer assignment, including after recovery from an edited config.
    ams, tray = _location(settings, spool_id)
    if (entry["ams"], entry["tray"]) != (ams, tray):
        with edit_settings() as settings:
            stage_spool_locations(settings, client, [spool_id])
            entry = settings[PENDING_LOCATIONS][str(spool_id)]
    try:
        result = client.set_active_tray(int(spool_id), ams, tray)
        if result is False and (client.ams_field_name or client.tray_field_name):
            raise RuntimeError("Configured Spoolman location update failed")
    except Exception:
        logger.exception("event=spool_location_sync_failed spool_id={}", spool_id)
        with edit_settings() as settings:
            pending = settings.get(PENDING_LOCATIONS, {})
            if pending.get(str(spool_id)) == entry:
                attempts = min(entry["attempts"] + 1, 5)
                entry = dict(entry, attempts=attempts)
                entry["retry_after"] = time.time() + min(
                    RETRY_SECONDS * 2 ** (attempts - 1), MAX_RETRY_SECONDS
                )
                pending[str(spool_id)] = entry
        return False
    # A crash or failed acknowledgment leaves an idempotent intent on disk.
    with edit_settings() as settings:
        pending = settings.get(PENDING_LOCATIONS, {})
        if pending.get(str(spool_id)) == entry:
            pending.pop(str(spool_id))
    logger.info("event=spool_location_synced spool_id={}", spool_id)
    return True


def sync_spool_locations(client, spool_ids):
    """Attempt all newly staged IDs, retaining unconfirmed writes for retry."""
    return [
        spool_id
        for spool_id in dict.fromkeys(spool_ids)
        if _deliver(client, spool_id, force=True) is False
    ]


def reconcile_spool_locations(client, stop=None):
    pending = load_settings().get(PENDING_LOCATIONS, {})
    # Earliest due first: failed/deleted spools cannot starve later intents.
    due = sorted(pending, key=lambda key: pending[key]["retry_after"])
    scope = _scope(client)
    blocked = sum(entry["scope"] != scope for entry in pending.values())
    if blocked:
        logger.warning("event=spool_location_scope_mismatch pending={}", blocked)
    attempted = 0
    for spool_id in due:
        if stop is not None and stop.is_set():
            break
        entry = pending[spool_id]
        if entry["scope"] != scope or entry["retry_after"] > time.time():
            continue
        if attempted >= BATCH_SIZE:
            break
        _deliver(client, spool_id, stop)
        attempted += 1


class LocationReconciler(threading.Thread):
    def __init__(self, client):
        super().__init__(name="SpoolLocationReconciler", daemon=True)
        self.client = client
        self.stopping = threading.Event()

    def stop(self):
        self.stopping.set()

    def run(self):
        while not self.stopping.is_set():
            try:
                reconcile_spool_locations(self.client, self.stopping)
            except Exception:
                logger.exception("event=spool_location_reconciliation_failed")
            self.stopping.wait(RETRY_SECONDS)

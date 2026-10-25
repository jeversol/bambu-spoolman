"""RFID operations shared by HTTP and the transitional gRPC adapter."""

import asyncio

import requests
from loguru import logger

from bambu_spoolman.broker.automatic_spool_switch import AutomaticSpoolSwitch
from bambu_spoolman.settings import load_settings
from bambu_spoolman.spoolman import instance as spoolman_instance


class RfidError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def identifier(value, name):
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise RfidError(f"Invalid {name}")
    return value


def tag_uuid(value, *, allow_empty=False):
    if not isinstance(value, str) or len(value) > 128:
        raise RfidError("Invalid RFID UUID")
    if value != value.strip() or (not value and not allow_empty):
        raise RfidError("Invalid RFID UUID")
    return value


def switch_instance(switch):
    return switch if switch is not None else AutomaticSpoolSwitch.get_instance()


async def set_spool_rfid(spool_id, uuid, client=None, switch=None):
    identifier(spool_id, "spool ID")
    tag_uuid(uuid, allow_empty=True)
    client = client if client is not None else spoolman_instance()
    if await asyncio.to_thread(client.get_spool, spool_id) is None:
        raise RfidError("Spool not found", status=404)
    if not await asyncio.to_thread(client.supports_tray_locking):
        raise RfidError("Spoolman instance does not support tray locking", status=501)
    if not await asyncio.to_thread(client.set_tray_uuid, spool_id, uuid):
        raise RfidError("Failed to set RFID association for spool", status=502)
    try:
        await asyncio.to_thread(switch_instance(switch).sync)
    except Exception as error:
        logger.exception("event=rfid_refresh_failed spool_id={}", spool_id)
        raise RfidError(
            "RFID association saved, but tray refresh failed. Refresh status "
            "before retrying.",
            status=502,
        ) from error
    logger.info("event=spool_rfid_updated spool_id={} linked={}", spool_id, bool(uuid))


async def override_rfid(tray_id, uuid, switch=None):
    identifier(tray_id, "tray ID")
    tag_uuid(uuid)
    if not await asyncio.to_thread(
        switch_instance(switch).override_tray, tray_id, uuid
    ):
        raise RfidError("The RFID tag is no longer present in this tray")


async def unlink_rfid(tray_id, spool_id, uuid, client=None, switch=None):
    identifier(tray_id, "tray ID")
    identifier(spool_id, "spool ID")
    tag_uuid(uuid)
    settings = await asyncio.to_thread(load_settings)
    if str(settings.get("trays", {}).get(str(tray_id))) != str(spool_id):
        raise RfidError(
            "Tray assignment changed. Refresh before unlinking.", status=409
        )
    switch = switch_instance(switch)
    # Keep this order: otherwise the MQTT path may recreate the association.
    await override_rfid(tray_id, uuid, switch)
    try:
        await set_spool_rfid(spool_id, "", client, switch)
    except (RfidError, requests.RequestException) as error:
        raise RfidError(
            "Automatic mapping is paused for this insertion, but permanent "
            "unlinking could not be confirmed. Refresh and try again.",
            status=502,
        ) from error

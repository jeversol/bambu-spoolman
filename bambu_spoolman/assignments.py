"""Tray-assignment behavior shared by the HTTP and transitional gRPC APIs."""

import asyncio

from loguru import logger

from bambu_spoolman.settings import edit_settings
from bambu_spoolman.spoolman import instance as spoolman_instance


class AssignmentError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


async def assign_spool(tray_id_int, spool_id, client=None):
    # JSON must not coerce booleans, fractions, or strings to identifiers.
    if type(tray_id_int) is not int or not 0 <= tray_id_int <= 2**53 - 1:
        raise AssignmentError("Invalid tray ID")
    if type(spool_id) is not int or not -1 <= spool_id <= 2**53 - 1:
        raise AssignmentError("Invalid spool ID")
    client = client if client is not None else spoolman_instance()
    tray_id = str(tray_id_int)
    logger.info(
        "event=tray_assignment_requested tray={} spool_id={}",
        tray_id_int,
        spool_id,
    )

    if spool_id != -1:
        spool = await asyncio.to_thread(client.get_spool, spool_id)
        if spool is None:
            raise AssignmentError("Spool not found", status=404)

    validation_error = None
    with edit_settings() as settings:
        trays = settings.get("trays", {})

        locked_trays = settings.get("locked_trays", [])
        logger.debug("Locked trays: {}", locked_trays)
        if any(str(locked_id) == tray_id for locked_id in locked_trays):
            validation_error = "This spool is locked and cannot be modified"

        old_spool_id = trays.get(tray_id)
        if old_spool_id is None:
            old_spool_id = trays.get(tray_id_int)

        conflict_tray = next(
            (
                str(existing_tray_id)
                for existing_tray_id, existing_spool_id in trays.items()
                if str(existing_tray_id) != tray_id
                and str(existing_spool_id) == str(spool_id)
            ),
            None,
        )
        if validation_error is None and spool_id != -1 and conflict_tray is not None:
            if conflict_tray == "255":
                location = "the External Spool Holder"
            else:
                conflict_id = int(conflict_tray)
                location = f"Slot {conflict_id % 4 + 1} in AMS {conflict_id // 4 + 1}"
            validation_error = f"This spool is already assigned to {location}"

        if validation_error is None and spool_id == -1:
            trays.pop(tray_id, None)
            trays.pop(tray_id_int, None)
        elif validation_error is None:
            trays.pop(tray_id_int, None)
            trays[tray_id] = spool_id
        settings["trays"] = trays

    if validation_error is not None:
        logger.warning(
            "event=tray_assignment_rejected tray={} spool_id={} reason={}",
            tray_id_int,
            spool_id,
            validation_error,
        )
        raise AssignmentError(validation_error)

    try:
        if spool_id == -1:
            if old_spool_id is not None:
                await asyncio.to_thread(
                    client.set_active_tray, old_spool_id, None, None
                )
        else:
            ams_num = None if tray_id_int == 255 else (tray_id_int // 4) + 1
            tray_num = None if tray_id_int == 255 else (tray_id_int % 4) + 1
            await asyncio.to_thread(client.set_active_tray, spool_id, ams_num, tray_num)
            if old_spool_id is not None and str(old_spool_id) != str(spool_id):
                await asyncio.to_thread(
                    client.set_active_tray, old_spool_id, None, None
                )
    except Exception as e:
        logger.error("Failed to update spool tray fields: {}", e)
    logger.info(
        "event=tray_assignment_updated tray={} old_spool_id={} spool_id={}",
        tray_id_int,
        old_spool_id,
        spool_id,
    )

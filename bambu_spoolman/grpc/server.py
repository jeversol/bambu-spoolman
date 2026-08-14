import asyncio

import grpc
from google.protobuf.empty_pb2 import Empty
from google.protobuf.json_format import ParseDict
from grpc.aio import ServicerContext
from loguru import logger

import bambu_spoolman.grpc.bambu_spoolman_pb2 as pb2
import bambu_spoolman.grpc.spoolman_pb2 as spoolman_pb2
from bambu_spoolman.bambu_mqtt import stateful_printer_info
from bambu_spoolman.broker.automatic_spool_switch import AutomaticSpoolSwitch
from bambu_spoolman.grpc import bambu_spoolman_pb2_grpc
from bambu_spoolman.settings import edit_settings, load_settings
from bambu_spoolman.spoolman import instance as spoolman_instance


class BambuSpoolmanServicer(bambu_spoolman_pb2_grpc.BambuSpoolmanServicer):
    def __init__(self):
        pass

    async def GetTrayCount(self, request: Empty, context: ServicerContext):
        if stateful_printer_info.connected:
            if ams := stateful_printer_info.get_info().get("print", {}).get("ams"):
                tray_count = len(ams.get("ams", [])) * 4
            else:
                tray_count = 0
        else:
            tray_count = 0
        return pb2.TrayCountResponse(count=tray_count)

    async def GetPrinterStatus(self, request: Empty, context: ServicerContext):
        return pb2.PrinterStatusResponse(
            last_updated=stateful_printer_info.last_update,
            connected=stateful_printer_info.connected,
            status=stateful_printer_info.get_info(),
        )

    async def Info(self, request: Empty, context: ServicerContext):
        client = spoolman_instance()
        features = pb2.Features(tray_locking=client.supports_tray_locking())
        return pb2.InfoResponse(
            spoolman_url=client.endpoint,
            spoolman_valid=await asyncio.to_thread(client.validate),
            features=features,
        )

    async def GetSpools(self, request: pb2.GetSpoolsRequest, context: ServicerContext):
        client = spoolman_instance()
        if len(request.spool_id) == 0:
            # Retrieve all spools
            spools = await asyncio.to_thread(client.get_spools)
        else:
            # Retrieve specific spools by ID
            spools = await asyncio.gather(
                *(
                    asyncio.to_thread(client.get_spool, spool_id)
                    for spool_id in request.spool_id
                )
            )
        return pb2.GetSpoolsResponse(
            spools=[
                ParseDict(spool, spoolman_pb2.Spool(), ignore_unknown_fields=True)
                for spool in spools
                if spool is not None
            ]
        )

    async def GetSettings(self, request: Empty, context: ServicerContext):
        settings = load_settings()
        return pb2.SettingsResponse(
            trays=settings.get("trays", {}),
            tray_count=settings.get("tray_count", 0),
            locked_trays=settings.get("locked_trays", []),
        )

    async def UpdateTray(
        self, request: pb2.UpdateTrayRequest, context: ServicerContext
    ):
        tray_id_int = int(request.tray_id)
        tray_id = str(tray_id_int)
        spool_id = int(request.spool_id)
        client = spoolman_instance()
        logger.info(
            "event=tray_assignment_requested tray={} spool_id={}",
            tray_id_int,
            spool_id,
        )

        if tray_id_int < 0:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid tray ID")
        if spool_id < -1:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid spool ID")
        if spool_id != -1:
            spool = await asyncio.to_thread(client.get_spool, spool_id)
            if spool is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "Spool not found")

        validation_error = None
        with edit_settings() as settings:
            trays = settings.get("trays", {})

            locked_trays = settings.get("locked_trays", [])
            logger.debug("Locked trays: {}", locked_trays)
            if any(str(locked_id) == tray_id for locked_id in locked_trays):
                validation_error = "Tray is locked and cannot be changed"

            old_spool_id = trays.get(tray_id)
            if old_spool_id is None:
                old_spool_id = trays.get(tray_id_int)

            assigned_elsewhere = any(
                str(existing_tray_id) != tray_id
                and str(existing_spool_id) == str(spool_id)
                for existing_tray_id, existing_spool_id in trays.items()
            )
            if validation_error is None and spool_id != -1 and assigned_elsewhere:
                validation_error = "Spool is already assigned to a different tray"

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
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, validation_error)

        try:
            if spool_id == -1:
                if old_spool_id is not None:
                    await asyncio.to_thread(
                        client.set_active_tray, old_spool_id, None, None
                    )
            else:
                ams_num = None if tray_id_int == 255 else (tray_id_int // 4) + 1
                tray_num = None if tray_id_int == 255 else (tray_id_int % 4) + 1
                await asyncio.to_thread(
                    client.set_active_tray, spool_id, ams_num, tray_num
                )
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
        return Empty()

    async def GetSpoolByUUID(
        self, request: pb2.GetSpoolbyUUIDRequest, context: ServicerContext
    ):
        spool = await asyncio.to_thread(
            spoolman_instance().lookup_by_tray_uuid, request.uuid
        )
        if spool is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Spool not found")
        return ParseDict(spool, spoolman_pb2.Spool(), ignore_unknown_fields=True)

    async def SetTrayUUID(
        self, request: pb2.SetSpoolUUIDRequest, context: ServicerContext
    ):
        tray_uuid = request.uuid
        spool_id = request.spool_id

        spool = await asyncio.to_thread(spoolman_instance().get_spool, spool_id)

        logger.debug(f"spool: {spool}")

        if spool is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Spool not found")

        if not spoolman_instance().supports_tray_locking():
            await context.abort(
                grpc.StatusCode.UNIMPLEMENTED,
                "Spoolman instance does not support tray locking",
            )

        success = await asyncio.to_thread(
            spoolman_instance().set_tray_uuid, spool_id, tray_uuid
        )
        if not success:
            await context.abort(
                grpc.StatusCode.INTERNAL, "Failed to set tray UUID for spool"
            )

        AutomaticSpoolSwitch.get_instance().sync()
        logger.info(
            "event=spool_rfid_updated spool_id={} linked={}",
            spool_id,
            bool(tray_uuid),
        )
        return Empty()

    async def OverrideTrayRFID(
        self, request: pb2.OverrideTrayRFIDRequest, context: ServicerContext
    ):
        success = AutomaticSpoolSwitch.get_instance().override_tray(
            int(request.tray_id), request.uuid
        )
        if not success:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "The RFID tag is no longer present in this tray",
            )
        return Empty()


async def serve(host: str = "0.0.0.0", port: int = 50051):
    server = grpc.aio.server()
    bambu_spoolman_pb2_grpc.add_BambuSpoolmanServicer_to_server(
        BambuSpoolmanServicer(), server
    )
    server.add_insecure_port(f"{host}:{port}")
    await server.start()
    logger.info(f"gRPC server started on {host}:{port}")

    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down gRPC server...")
        await server.stop(grace=5)

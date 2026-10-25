import asyncio

import grpc
from google.protobuf.empty_pb2 import Empty
from google.protobuf.json_format import ParseDict
from grpc.aio import ServicerContext
from loguru import logger

import bambu_spoolman.grpc.bambu_spoolman_pb2 as pb2
import bambu_spoolman.grpc.spoolman_pb2 as spoolman_pb2
from bambu_spoolman.assignments import AssignmentError, assign_spool
from bambu_spoolman.bambu_mqtt import stateful_printer_info
from bambu_spoolman.grpc import bambu_spoolman_pb2_grpc
from bambu_spoolman.rfid import RfidError, override_rfid, set_spool_rfid
from bambu_spoolman.settings import load_settings
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
        try:
            await assign_spool(int(request.tray_id), int(request.spool_id))
        except AssignmentError as error:
            code = (
                grpc.StatusCode.NOT_FOUND
                if error.status == 404
                else grpc.StatusCode.INVALID_ARGUMENT
            )
            await context.abort(code, str(error))
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
        try:
            await set_spool_rfid(int(request.spool_id), request.uuid)
        except RfidError as error:
            await context.abort(
                {
                    404: grpc.StatusCode.NOT_FOUND,
                    501: grpc.StatusCode.UNIMPLEMENTED,
                    502: grpc.StatusCode.INTERNAL,
                }.get(error.status, grpc.StatusCode.INVALID_ARGUMENT),
                str(error),
            )
        return Empty()

    async def OverrideTrayRFID(
        self, request: pb2.OverrideTrayRFIDRequest, context: ServicerContext
    ):
        try:
            await override_rfid(int(request.tray_id), request.uuid)
        except RfidError as error:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
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

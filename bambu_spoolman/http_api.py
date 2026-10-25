"""Loopback HTTP API for the incremental migration away from internal gRPC."""

import asyncio
import json

import requests
from aiohttp import web
from loguru import logger

from bambu_spoolman.assignments import AssignmentError, assign_spool
from bambu_spoolman.bambu_mqtt import stateful_printer_info
from bambu_spoolman.rfid import (
    RfidError,
    override_rfid,
    set_spool_rfid,
    tag_uuid,
    unlink_rfid,
)
from bambu_spoolman.settings import load_settings
from bambu_spoolman.spoolman import instance as spoolman_instance

CLIENT = web.AppKey("spoolman", object)
SWITCH = web.AppKey("rfid_switch", object)
PRINTER = web.AppKey("printer", object)


@web.middleware
async def errors(request, handler):
    try:
        response = await handler(request)
    except (AssignmentError, RfidError) as error:
        response = web.json_response({"error": str(error)}, status=error.status)
    except requests.RequestException:
        logger.exception("event=http_spoolman_unavailable")
        response = web.json_response({"error": "Spoolman is unavailable"}, status=502)
    except web.HTTPException as error:
        response = web.json_response({"error": error.reason}, status=error.status)
    except Exception:
        logger.exception("event=http_request_failed")
        response = web.json_response({"error": "Internal server error"}, status=500)
    response.headers["Cache-Control"] = "no-store"
    return response


async def settings(request):
    value = await asyncio.to_thread(load_settings)
    return web.json_response(
        {
            "trays": value.get("trays", {}),
            "trayCount": value.get("tray_count", 0),
            "lockedTrays": value.get("locked_trays", []),
        }
    )


async def spools(request):
    values = await asyncio.to_thread(request.app[CLIENT].get_spools)
    return web.json_response({"spools": values})


async def spool(request):
    value = await asyncio.to_thread(
        request.app[CLIENT].get_spool, int(request.match_info["spool_id"])
    )
    if value is None:
        raise web.HTTPNotFound(reason="Spool not found")
    return web.json_response(value)


async def json_body(request, fields):
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(reason="Expected application/json")
    try:
        body = await request.json()
    except json.JSONDecodeError, UnicodeDecodeError:
        raise web.HTTPBadRequest(reason="Invalid JSON") from None
    if not isinstance(body, dict) or set(body) != set(fields):
        raise web.HTTPBadRequest(reason="Unexpected request fields")
    return body


async def assignment(request):
    body = await json_body(request, {"spoolId"})
    await assign_spool(
        int(request.match_info["tray_id"]), body["spoolId"], request.app[CLIENT]
    )
    return web.Response(status=204)


async def printer_status(request):
    printer = request.app[PRINTER]
    return web.json_response(
        {
            "connected": printer.connected,
            "lastUpdated": printer.last_update,
            "status": printer.get_info(),
        }
    )


async def info(request):
    client = request.app[CLIENT]
    locking, valid = await asyncio.gather(
        asyncio.to_thread(client.supports_tray_locking),
        asyncio.to_thread(client.validate),
    )
    return web.json_response(
        {
            "spoolmanUrl": client.endpoint,
            "spoolmanValid": valid,
            "features": {"trayLocking": locking},
        }
    )


async def rfid_link(request):
    body = await json_body(request, {"uuid"})
    tag_uuid(body["uuid"])
    await set_spool_rfid(
        int(request.match_info["spool_id"]),
        body["uuid"],
        request.app[CLIENT],
        request.app[SWITCH],
    )
    return web.Response(status=204)


async def rfid_override(request):
    body = await json_body(request, {"uuid"})
    await override_rfid(
        int(request.match_info["tray_id"]), body["uuid"], request.app[SWITCH]
    )
    return web.Response(status=204)


async def rfid_unlink(request):
    body = await json_body(request, {"spoolId", "uuid"})
    await unlink_rfid(
        int(request.match_info["tray_id"]),
        body["spoolId"],
        body["uuid"],
        request.app[CLIENT],
        request.app[SWITCH],
    )
    return web.Response(status=204)


def create_app(client=None, switch=None, printer=None):
    app = web.Application(middlewares=[errors], client_max_size=4096)
    app[CLIENT] = client if client is not None else spoolman_instance()
    app[SWITCH] = switch
    app[PRINTER] = printer if printer is not None else stateful_printer_info
    app.add_routes(
        [
            web.get("/api/v1/settings", settings),
            web.get("/api/v1/printer", printer_status),
            web.get("/api/v1/info", info),
            web.put(r"/api/v1/spools/{spool_id:\d+}/rfid", rfid_link),
            web.post(r"/api/v1/trays/{tray_id:\d+}/rfid/override", rfid_override),
            web.post(r"/api/v1/trays/{tray_id:\d+}/rfid/unlink", rfid_unlink),
            web.get("/api/v1/spools", spools),
            web.get(r"/api/v1/spools/{spool_id:\d+}", spool),
            web.put(r"/api/v1/trays/{tray_id:\d+}", assignment),
        ]
    )
    return app


async def serve(host="127.0.0.1", port=50052):
    runner = web.AppRunner(create_app(), shutdown_timeout=5)
    try:
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        logger.info("event=http_server_started host={} port={}", host, port)
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()

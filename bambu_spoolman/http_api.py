"""Loopback HTTP API for the incremental migration away from internal gRPC."""

import asyncio
import json

import requests
from aiohttp import web
from loguru import logger

from bambu_spoolman.assignments import AssignmentError, assign_spool
from bambu_spoolman.settings import load_settings
from bambu_spoolman.spoolman import instance as spoolman_instance

CLIENT = web.AppKey("spoolman", object)


@web.middleware
async def errors(request, handler):
    try:
        response = await handler(request)
    except AssignmentError as error:
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


async def assignment(request):
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(reason="Expected application/json")
    try:
        body = await request.json()
    except json.JSONDecodeError, UnicodeDecodeError:
        raise web.HTTPBadRequest(reason="Invalid JSON") from None
    if not isinstance(body, dict) or set(body) != {"spoolId"}:
        raise web.HTTPBadRequest(reason="Expected only spoolId")
    await assign_spool(
        int(request.match_info["tray_id"]), body["spoolId"], request.app[CLIENT]
    )
    return web.Response(status=204)


def create_app(client=None):
    app = web.Application(middlewares=[errors], client_max_size=4096)
    app[CLIENT] = client if client is not None else spoolman_instance()
    app.add_routes(
        [
            web.get("/api/v1/settings", settings),
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

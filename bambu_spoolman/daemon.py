import asyncio
import datetime
import os
import platform
import signal

from dotenv import load_dotenv
from loguru import logger

from bambu_spoolman.bambu_mqtt import MqttHandler, stateful_printer_info
from bambu_spoolman.broker.automatic_spool_switch import AutomaticSpoolSwitch
from bambu_spoolman.broker.filament_usage_tracker import FilamentUsageTracker
from bambu_spoolman.build_info import get_build_info
from bambu_spoolman.http_api import serve as run_http_server


async def async_main():
    build = get_build_info()
    printer_ip = os.environ.get("PRINTER_IP")
    printer_serial = os.environ.get("PRINTER_SERIAL")
    # Always listen for AMS changes. On Spoolman v0.27+ this also reports physical
    # tag scans and learns both Bambu tags without requiring a custom field.
    automatic_switching = True
    logger.info(
        "event=service_start application=bambu-spoolman version={} "
        "build_number={} revision={} build_date={} "
        "printer_ip={} printer_serial={} "
        "spoolman_url_configured={} "
        "config_directory={} automatic_spool_switching={} log_level={}",
        build.version,
        build.build_number,
        build.revision,
        build.build_date,
        printer_ip,
        printer_serial,
        bool(os.environ.get("SPOOLMAN_URL")),
        os.environ.get("BAMBU_SPOOLMAN_CONFIG"),
        automatic_switching,
        os.environ.get("LOGURU_LEVEL", "INFO"),
    )
    logger.info("event=container_runtime python={}", platform.python_version())
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopping.set)
    server = asyncio.create_task(run_http_server())
    stopped = asyncio.create_task(stopping.wait())
    mqtt = MqttHandler(
        printer_ip,
        printer_serial,
        os.environ.get("PRINTER_ACCESS_CODE"),
    )

    stateful_printer_info.mqtt_handler = mqtt

    mqtt.add_callback(stateful_printer_info.handle_message)
    mqtt.add_on_connect_callback(stateful_printer_info.on_connect)
    mqtt.add_on_disconnect_callback(stateful_printer_info.on_disconnect)

    usage_tracker = FilamentUsageTracker()
    mqtt.add_callback(usage_tracker.on_message)

    logger.info("event=automatic_spool_switching_enabled")
    mqtt.add_callback(AutomaticSpoolSwitch.get_instance().on_message)

    mqtt.start()

    try:
        done, _ = await asyncio.wait(
            {server, stopped}, return_when=asyncio.FIRST_COMPLETED
        )
        if server in done:
            await server  # Propagate listener/startup failures to the container.
            raise RuntimeError("HTTP server stopped unexpectedly")
    finally:
        logger.info("event=container_shutdown_started")
        mqtt.stop()
        server.cancel()
        stopped.cancel()
        await asyncio.gather(server, stopped, return_exceptions=True)
        await asyncio.to_thread(mqtt.join, 6)
        await asyncio.to_thread(mqtt.message_dispatcher.join, 1)
        if mqtt.is_alive() or mqtt.message_dispatcher.is_alive():
            logger.warning("event=mqtt_shutdown_pending_background_work")
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)
        logger.info("event=container_shutdown_complete")


def main():
    load_dotenv()
    asyncio.run(async_main())


def testing():
    load_dotenv()

    mqtt = MqttHandler(
        os.environ.get("PRINTER_IP"),
        os.environ.get("PRINTER_SERIAL"),
        os.environ.get("PRINTER_ACCESS_CODE"),
    )

    stateful_printer_info.mqtt_handler = mqtt

    mqtt.add_callback(stateful_printer_info.handle_message)

    file = open("messages.log", "w")

    def handle_message(mqtt_handler, message):
        ts = datetime.datetime.now().isoformat()
        file.write(f"[{ts}]: {message}\n")
        file.flush()

    mqtt.add_callback(handle_message)

    mqtt.start()
    mqtt.join()

import asyncio
import datetime
import os

from dotenv import load_dotenv
from loguru import logger

from bambu_spoolman.bambu_mqtt import MqttHandler, stateful_printer_info
from bambu_spoolman.broker.automatic_spool_switch import AutomaticSpoolSwitch
from bambu_spoolman.broker.filament_usage_tracker import FilamentUsageTracker
from bambu_spoolman.build_info import get_build_info
from bambu_spoolman.grpc.server import serve as run_grpc_server


async def async_main():
    build = get_build_info()
    printer_ip = os.environ.get("PRINTER_IP")
    printer_serial = os.environ.get("PRINTER_SERIAL")
    automatic_switching = os.environ.get("SPOOLMAN_SPOOL_FIELD_NAME") is not None
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
    loop = asyncio.get_event_loop()
    tasks = []
    tasks.append(loop.create_task(run_grpc_server()))
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

    if automatic_switching:
        logger.info("event=automatic_spool_switching_enabled")
        mqtt.add_callback(AutomaticSpoolSwitch.get_instance().on_message)

    mqtt.start()

    await asyncio.gather(*tasks)
    mqtt.join()


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

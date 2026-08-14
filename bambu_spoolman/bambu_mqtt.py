import copy
import json
import queue
import ssl
import threading
import time
from typing import Callable

import paho.mqtt.client as mqtt
from loguru import logger

from bambu_spoolman.settings import edit_settings


def recursive_merge(dict1, dict2):
    for key, value in dict2.items():
        if key in dict1 and isinstance(dict1[key], dict) and isinstance(value, dict):
            recursive_merge(dict1[key], value)
        elif (
            key in dict1
            and isinstance(dict1[key], list)
            and isinstance(value, list)
            and value
            and _is_keyed_object_list(dict1[key])
            and _is_keyed_object_list(value)
        ):
            _merge_keyed_object_list(dict1[key], value)
        else:
            dict1[key] = value


def _is_keyed_object_list(value):
    return all(isinstance(item, dict) and "id" in item for item in value)


def _merge_keyed_object_list(existing, delta):
    """Merge P-series list deltas by their stable object IDs.

    P-series printers commonly send only the AMS unit or tray which changed.
    Replacing the entire list loses every omitted unit/tray. Empty lists remain
    authoritative and are handled by ``recursive_merge`` as replacements.
    """
    existing_by_id = {str(item["id"]): item for item in existing}
    for item in delta:
        item_id = str(item["id"])
        if item_id in existing_by_id:
            recursive_merge(existing_by_id[item_id], item)
        else:
            existing.append(item)
            existing_by_id[item_id] = item


class StatefulPrinterInfo:
    def __init__(self):
        self._info = {}
        self.mqtt_handler = None
        self.last_update = 0
        self.connected = False
        self.tray_count = 0
        self._lock = threading.RLock()

    def handle_message(self, mqtt_handler, message):
        if "print" not in message:
            return

        print_status = message["print"]
        if "command" not in print_status or print_status["command"] != "push_status":
            logger.debug("Ignoring message: {}", message)
            return  # Not a status message
        # Merge the new info with the old info
        with self._lock:
            recursive_merge(self._info, message)
            raw_ams = print_status.get("ams")
            if isinstance(raw_ams, dict):
                _prune_ams_by_presence(
                    self._info.get("print", {}).get("ams", {}), raw_ams
                )
            self.last_update = int(time.time())
            tray_count = (
                len(self._info.get("print", {}).get("ams", {}).get("ams", [])) * 4
            )
        self.update_tray_count(tray_count)

    def update_tray_count(self, count):
        if self.tray_count != count:
            with edit_settings() as settings:
                settings["tray_count"] = count
            logger.info(
                "event=printer_tray_count_changed previous={} current={}",
                self.tray_count,
                count,
            )

        self.tray_count = count

    def on_connect(self, mqtt_handler):
        logger.info("Connected to printer")
        self.solicit()
        self.connected = True

    def on_disconnect(self, mqtt_handler):
        self.connected = False
        logger.info("event=printer_status_disconnected")

    def solicit(self):
        logger.info("event=printer_snapshot_requested command=pushall")
        self.mqtt_handler.publish(
            {"pushing": {"sequence_id": "0", "command": "pushall"}}
        )

    def get_info(self):
        with self._lock:
            return copy.deepcopy(self._info)


stateful_printer_info = StatefulPrinterInfo()


def _prune_ams_by_presence(merged_ams, raw_ams):
    units = merged_ams.get("ams")
    if not isinstance(units, list):
        return

    if "ams_exist_bits" in raw_ams:
        ams_bits = _presence_bits(raw_ams.get("ams_exist_bits"))
        if ams_bits is not None:
            units[:] = [unit for unit in units if _bit_is_set(ams_bits, unit.get("id"))]

    if "tray_exist_bits" in raw_ams:
        tray_bits = _presence_bits(raw_ams.get("tray_exist_bits"))
        if tray_bits is not None:
            for unit in units:
                trays = unit.get("tray")
                if not isinstance(trays, list):
                    continue
                try:
                    unit_id = int(unit.get("id"))
                except (TypeError, ValueError):
                    continue
                trays[:] = [
                    tray
                    for tray in trays
                    if _tray_bit_is_set(tray_bits, unit_id, tray.get("id"))
                ]


def _presence_bits(value):
    try:
        return value if isinstance(value, int) else int(str(value), 16)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid AMS presence bitmask: {}", value)
        return None


def _bit_is_set(bits, index):
    try:
        return bool(bits & (1 << int(index)))
    except (TypeError, ValueError):
        return True


def _tray_bit_is_set(bits, unit_id, tray_id):
    try:
        return _bit_is_set(bits, unit_id * 4 + int(tray_id))
    except (TypeError, ValueError):
        return True


MAX_BACKOFF_DURATION = 60


class MqttHandler(threading.Thread):
    def __init__(self, printer_ip, printer_serial, printer_access_code):
        self.printer_ip = printer_ip
        self.printer_serial = printer_serial
        self.printer_access_code = printer_access_code
        self.connected = False
        self.pending_messages = []
        self.pending_messages_lock = threading.Lock()

        self.client = self._create_client()
        self.callbacks = {"on_connect": [], "on_message": [], "on_disconnect": []}
        self.message_queue = queue.Queue()
        self.message_dispatcher = threading.Thread(
            target=self._dispatch_messages,
            daemon=True,
            name=f"MqttDispatcher-{printer_serial}",
        )

        self.backoff = None

        super().__init__()
        self.daemon = True
        self.name = f"MqttHandler-{printer_serial}"

    def run(self):
        self.message_dispatcher.start()
        logger.info(
            "event=mqtt_dispatcher_started printer_serial={}", self.printer_serial
        )
        last_error = None
        while True:
            try:
                logger.info(
                    "event=mqtt_connect_attempt printer_serial={} printer_ip={} "
                    "port=8883 keepalive_seconds=5",
                    self.printer_serial,
                    self.printer_ip,
                )
                self.client.connect(self.printer_ip, 8883, keepalive=5)
                self.client.loop_forever(retry_first_connection=True)
            except TimeoutError:
                if last_error != "TimeoutError":
                    logger.warning(
                        f"Connection to printer {self.printer_serial} timed out"
                    )
                last_error = "TimeoutError"
                time.sleep(5)
            except ConnectionError:
                if last_error != "ConnectionError":
                    logger.warning(
                        f"Connection to printer {self.printer_serial} failed"
                    )
                last_error = "ConnectionError"
                time.sleep(5)
            except OSError as e:
                if e.errno == 113:
                    if last_error != "oserror113":
                        logger.warning(
                            "Connection to printer {} failed: No route to host",
                            self.printer_serial,
                        )
                    last_error = "oserror113"
                    time.sleep(5)
                else:
                    duration = self._backoff()
                    logger.error(
                        f"Error occurred in MQTT loop. Retrying in {duration}s: {e}"
                    )
                    self._reset_client()
                    time.sleep(duration)
            except Exception as e:
                duration = self._backoff()
                logger.exception(
                    f"Error occurred in MQTT loop. Retrying in {duration}s: {e}"
                )
                self._reset_client()
                time.sleep(duration)

    def add_callback(self, callback: Callable[["MqttHandler", dict], None]):
        self.callbacks["on_message"].append(callback)

    def add_on_connect_callback(self, callback: Callable[["MqttHandler"], None]):
        self.callbacks["on_connect"].append(callback)

    def add_on_disconnect_callback(self, callback: Callable[["MqttHandler"], None]):
        self.callbacks["on_disconnect"].append(callback)

    def _on_connect(self, client, userdata, flags, rc):
        logger.info(
            "event=mqtt_connected printer_serial={} result_code={}",
            self.printer_serial,
            rc,
        )
        self.connected = True
        self._subscribe()

        for callback in self.callbacks["on_connect"]:
            self._run_callback("on_connect", callback, self)

        with self.pending_messages_lock:
            pending_messages = self.pending_messages
            self.pending_messages = []
        logger.debug("Pending messages: {}", pending_messages)
        for message in pending_messages:
            self.publish(message)
        self.backoff = None

    def _on_message(self, client, userdata, msg):
        logger.trace("MQTT topic={} payload={}", msg.topic, msg.payload)
        try:
            message = json.loads(msg.payload)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.error("Ignoring malformed MQTT message: {}", e)
            return
        if not isinstance(message, dict):
            logger.error(
                "Ignoring MQTT payload with unexpected JSON type: {}",
                type(message).__name__,
            )
            return

        # Model downloads, G-code parsing, and Spoolman requests can take
        # longer than the MQTT keepalive. Dispatch them in order off the Paho
        # network thread so status processing cannot disconnect the printer.
        print_obj = message.get("print", {})
        logger.debug(
            "event=mqtt_message_received topic={} command={} state={} layer={} "
            "task_id={}",
            msg.topic,
            print_obj.get("command"),
            print_obj.get("gcode_state"),
            print_obj.get("layer_num"),
            print_obj.get("task_id"),
        )
        self.message_queue.put(message)
        queue_depth = self.message_queue.qsize()
        if queue_depth >= 10 and queue_depth % 10 == 0:
            logger.warning(
                "event=mqtt_dispatch_backlog printer_serial={} queued_messages={}",
                self.printer_serial,
                queue_depth,
            )

    def _dispatch_messages(self):
        while True:
            message = self.message_queue.get()
            started_at = time.monotonic()
            try:
                for callback in self.callbacks["on_message"]:
                    self._run_callback("on_message", callback, self, message)
            finally:
                duration = time.monotonic() - started_at
                command = message.get("print", {}).get("command")
                if duration >= 1:
                    logger.warning(
                        "event=mqtt_message_slow command={} duration_seconds={:.3f} "
                        "queued_messages={}",
                        command,
                        duration,
                        self.message_queue.qsize(),
                    )
                else:
                    logger.debug(
                        "event=mqtt_message_processed command={} "
                        "duration_seconds={:.3f}",
                        command,
                        duration,
                    )
                self.message_queue.task_done()

    def _on_disconnect(self, client, userdata, rc):
        logger.warning(
            "event=mqtt_disconnected printer_serial={} result_code={}",
            self.printer_serial,
            rc,
        )
        self.connected = False
        for callback in self.callbacks["on_disconnect"]:
            self._run_callback("on_disconnect", callback, self)

    def publish(self, message, wait=False):
        with self.pending_messages_lock:
            if not self.connected:
                self.pending_messages.append(message)
                logger.info(
                    "event=mqtt_publish_queued printer_serial={} queued_messages={}",
                    self.printer_serial,
                    len(self.pending_messages),
                )
                return

        if isinstance(message, dict):
            message = json.dumps(message)
        result = self.client.publish(f"device/{self.printer_serial}/request", message)
        logger.debug(
            "event=mqtt_message_published printer_serial={} bytes={} wait={}",
            self.printer_serial,
            len(message),
            wait,
        )
        if wait:
            result.wait_for_publish()
        return

    def _create_client(self):
        client = mqtt.Client()
        client.username_pw_set("bblp", self.printer_access_code)
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        client.tls_set_context(ssl_ctx)
        client.tls_insecure_set(True)

        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect

        return client

    def _reset_client(self):
        """Replace a client whose packet parser may be in an invalid state."""
        old_client = self.client

        if self.connected:
            self.connected = False
            for callback in self.callbacks["on_disconnect"]:
                self._run_callback("on_disconnect", callback, self)

        try:
            old_client.disconnect()
        except Exception as e:
            logger.debug("Failed to disconnect broken MQTT client: {}", e)

        self.client = self._create_client()

    def _subscribe(self):
        topic = f"device/{self.printer_serial}/report"
        self.client.subscribe(topic)
        logger.info("event=mqtt_subscribed topic={}", topic)

    def _backoff(self):
        if self.backoff is None:
            self.backoff = 0
            return 2**0
        else:
            self.backoff += 1
            return min(2**self.backoff, MAX_BACKOFF_DURATION)

    def _run_callback(self, location, callback, *args, **kwargs):
        try:
            callback(*args, **kwargs)
        except Exception as e:
            logger.exception(f"Error occurred in {location} callback: {e}")

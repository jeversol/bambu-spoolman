import os
import tempfile
import time
import zipfile
from urllib.parse import urlparse

import requests
from loguru import logger

from bambu_spoolman.bambu_ftp import retrieve_3mf
from bambu_spoolman.broker.checkpoint import (
    clear as clear_checkpoint,
)
from bambu_spoolman.broker.checkpoint import (
    mark_print_started,
    recover_model,
    save_checkpoint,
    update_layer,
)
from bambu_spoolman.gcode.bambu import open_gcode
from bambu_spoolman.gcode.parser import evaluate_gcode
from bambu_spoolman.settings import (
    EXTERNAL_SPOOL_ID,
    get_http_timeout,
    load_settings,
)
from bambu_spoolman.spoolman import new_client

MODEL_DOWNLOAD_CHUNK_SIZE = 64 * 1024
ACTIVE_GCODE_STATES = frozenset({"PAUSE", "RUNNING"})
FAILED_GCODE_STATES = frozenset({"FAILED", "FAILURE"})
RECOVERABLE_GCODE_STATES = ACTIVE_GCODE_STATES | {"FINISH"}
CANCELED_PRINT_ERROR = 50348044


class FilamentUsageTracker:
    def __init__(self):
        self.spoolman_client = new_client()
        self.active_model = None
        self.ams_mapping = None
        self.spent_layers = set()
        self.spent_filaments = {}
        self.using_ams = False

        self.gcode_state = None
        self.current_layer = None
        self.print_started = False
        self.task_id = None
        self.subtask_id = None

    def on_message(self, mqtt_handler, message):
        print_obj = message.get("print", {})
        command = print_obj.get("command")

        previous_gcode_state = self.gcode_state
        self.gcode_state = print_obj.get("gcode_state", self.gcode_state)

        if previous_gcode_state != self.gcode_state:
            logger.info(
                "event=print_state task_id={} subtask_id={} previous={} current={}",
                _normalize_identifier(print_obj.get("task_id")) or self.task_id,
                _normalize_identifier(print_obj.get("subtask_id"))
                or self.subtask_id,
                previous_gcode_state,
                self.gcode_state,
            )

        if command == "project_file":
            if self._is_current_print(print_obj):
                logger.info(
                    "event=duplicate_print_announcement task_id={} subtask_id={}",
                    self.task_id,
                    self.subtask_id,
                )
            else:
                self._handle_print_start(print_obj)

        if command == "push_status":
            if (
                self.active_model is None
                and previous_gcode_state != self.gcode_state
                and self.gcode_state in RECOVERABLE_GCODE_STATES
            ):
                # Recover before handling the current status. A recovered
                # checkpoint records whether this print had actually started.
                self._attempt_print_resume(
                    print_obj.get("task_id"), print_obj.get("subtask_id")
                )

            if (
                self.active_model is not None
                and not self.print_started
                and self.gcode_state in ACTIVE_GCODE_STATES
            ):
                self._mark_print_started()

            if "layer_num" in print_obj:
                last_layer = self.current_layer
                try:
                    layer = int(print_obj["layer_num"])
                except (TypeError, ValueError):
                    logger.warning(
                        "Ignoring invalid layer number: {}", print_obj["layer_num"]
                    )
                    layer = last_layer
                if self.print_started and layer != last_layer:
                    logger.info(
                        "event=layer_started task_id={} subtask_id={} layer={} "
                        "previous_layer={}",
                        self.task_id,
                        self.subtask_id,
                        layer,
                        last_layer,
                    )
                    self.current_layer = layer
                    self._handle_layer_change(layer)

            if self.gcode_state == "FINISH":
                if self.active_model is not None and self.print_started:
                    self._handle_print_end()
                elif self.active_model is not None:
                    logger.warning(
                        "Ignoring FINISH status because the new print has not "
                        "entered an active state"
                    )

            if (
                self.gcode_state in FAILED_GCODE_STATES
                and previous_gcode_state not in FAILED_GCODE_STATES
                and self.active_model is not None
                and self.print_started
            ):
                self._handle_print_failure(f"gcode_state={self.gcode_state}")

            if (
                str(print_obj.get("print_error")) == str(CANCELED_PRINT_ERROR)
                and self.active_model is not None
                and self.print_started
            ):
                self._handle_print_failure(
                    f"print_error={print_obj.get('print_error')}"
                )

    def _handle_print_start(self, print_obj):
        clear_checkpoint()
        model_url = print_obj.get("url")

        self.active_model = None
        self.ams_mapping = None
        self.current_layer = None
        self.print_started = print_obj.get("gcode_state") in ACTIVE_GCODE_STATES
        self.spent_layers = set()
        self.spent_filaments = {}
        self.using_ams = False
        self.task_id = _normalize_identifier(print_obj.get("task_id"))
        self.subtask_id = _normalize_identifier(print_obj.get("subtask_id"))
        logger.info(
            "event=print_announced task_id={} subtask_id={} file={} "
            "use_ams={} state={}",
            self.task_id,
            self.subtask_id,
            print_obj.get("param"),
            print_obj.get("use_ams", False),
            print_obj.get("gcode_state"),
        )

        model = self._retrieve_model(model_url)
        if model is None:
            logger.error("Failed to retrieve model. Print will not be tracked")
            return

        ams_mapping = [
            _normalize_mapping(mapping)
            for mapping in (print_obj.get("ams_mapping") or [])
        ]
        if print_obj.get("use_ams", False) or (
            ams_mapping and ams_mapping[0] not in (-1, 255)
        ):
            self.using_ams = True
            self.ams_mapping = ams_mapping
            logger.info(
                "event=print_ams_mapping task_id={} using_ams=true mapping={}",
                self.task_id,
                self.ams_mapping,
            )
        else:
            self.using_ams = False
            self.ams_mapping = None  # Ensure this is cleared out
            logger.info(
                "event=print_ams_mapping task_id={} using_ams=false",
                self.task_id,
            )

        gcode_file_name = print_obj.get("param")

        if not self._load_model(model, gcode_file_name):
            os.remove(model)
            return

        save_checkpoint(
            model_path=model,
            current_layer=0,
            spent_layers=self.spent_layers,
            spent_filaments=self.spent_filaments,
            task_id=print_obj.get("task_id"),
            subtask_id=print_obj.get("subtask_id"),
            ams_mapping=self.ams_mapping,
            gcode_file_name=gcode_file_name,
            using_ams=self.using_ams,
            print_started=self.print_started,
        )

        # Delete the downloaded model
        os.remove(model)

    def _retrieve_model(self, model_url):
        logger.debug("Loading model from URL: {}", model_url)

        if not model_url:
            logger.warning("Print announcement did not include a model URL")
            return None

        ftp_uris = ("file", "ftp", "brtc")

        # Turn URL into a URI
        uri = urlparse(model_url)

        if uri.scheme == "https" or uri.scheme == "http":
            return self._download_model(model_url)
        elif uri.scheme in ftp_uris:
            path = f"{uri.netloc}{uri.path}"
            return self._retrieve_model_from_ftp(path)
        else:
            logger.warning("Unsupported model URL: {}", model_url)
            return None

    def _handle_layer_change(self, layer):
        if self.active_model is None:
            logger.debug("Skipping layer change because no model is loaded")
            return
        self.current_layer = layer
        # Bambu Studio emits M73 L<n> at the start of layer n. Only earlier
        # layers are known to be complete at that point. Include any earlier
        # failed layers so a later status update retries them.
        to_spend = set(
            model_layer
            for model_layer in self.active_model
            if model_layer < layer and model_layer not in self.spent_layers
        )
        if to_spend:
            logger.info(
                "event=layers_ready task_id={} reported_layer={} layers={} "
                "already_accounted={}",
                self.task_id,
                layer,
                sorted(to_spend),
                len(self.spent_layers),
            )

        for layer_to_spend in sorted(to_spend):
            try:
                spent = self._spend_filament_for_layer(layer_to_spend)
            except Exception as e:
                logger.error(
                    "Failed to spend filament for layer {}: {}", layer_to_spend, e
                )
                continue

            if spent:
                self.spent_layers.add(layer_to_spend)
                logger.info(
                    "event=layer_accounted task_id={} layer={} accounted_layers={} "
                    "total_layers={}",
                    self.task_id,
                    layer_to_spend,
                    len(self.spent_layers),
                    len(self.active_model),
                )
        self._save_progress(layer)

    def _save_progress(self, fallback_layer=None):
        checkpoint_layer = (
            self.current_layer if self.current_layer is not None else fallback_layer
        )
        update_layer(
            checkpoint_layer,
            set(self.spent_layers),
            {
                spent_layer: set(filaments)
                for spent_layer, filaments in self.spent_filaments.items()
            },
        )

    def _handle_print_end(self):
        # Spend all layers that haven't already been spent
        remaining_layers = set(self.active_model or {}) - self.spent_layers
        logger.info(
            "event=print_finish_received task_id={} subtask_id={} "
            "accounted_layers={} total_layers={} pending_layers={}",
            self.task_id,
            self.subtask_id,
            len(self.spent_layers),
            len(self.active_model or {}),
            len(remaining_layers),
        )
        if remaining_layers:
            for layer in sorted(remaining_layers):
                logger.debug(
                    f"Spending layer {layer} as it was not spent during the print"
                )
            # A single layer-change pass includes every earlier unspent model
            # layer, so each outstanding layer is attempted at most once for
            # this status update.
            self._handle_layer_change(max(remaining_layers) + 1)

        remaining_layers = set(self.active_model or {}) - self.spent_layers
        if remaining_layers:
            logger.warning(
                "Print ended with unspent layers {}. Will retry on the next "
                "status update",
                sorted(remaining_layers),
            )
            return False

        task_id = self.task_id
        subtask_id = self.subtask_id
        total_layers = len(self.active_model or {})
        self.active_model = None
        self.ams_mapping = None
        self.spent_layers = set()
        self.spent_filaments = {}
        self.using_ams = False
        self.current_layer = None
        self.print_started = False
        self.task_id = None
        self.subtask_id = None

        clear_checkpoint()
        logger.info(
            "event=print_tracking_complete task_id={} subtask_id={} layers={}",
            task_id,
            subtask_id,
            total_layers,
        )
        return True

    def _handle_print_failure(self, reason="unknown"):
        logger.warning(
            "event=print_tracking_stopped task_id={} subtask_id={} reason={} "
            "accounted_layers={}",
            self.task_id,
            self.subtask_id,
            reason,
            len(self.spent_layers),
        )

        self.active_model = None
        self.ams_mapping = None
        self.spent_layers = set()
        self.spent_filaments = {}
        self.using_ams = False
        self.current_layer = None
        self.print_started = False
        self.task_id = None
        self.subtask_id = None

        clear_checkpoint()

    def _spend_filament_for_layer(self, layer):
        if self.active_model is None:
            return False
        logger.debug("Spending filament for layer {}", layer)

        layer_usage = self.active_model.get(int(layer))
        if layer_usage is None:
            logger.error("Failed to find filament usage for layer {}", layer)
            return False

        config = load_settings()

        trays = config.get("trays", {})
        spent_filaments = self.spent_filaments.setdefault(int(layer), set())

        for filament, usage in layer_usage.items():
            if filament in spent_filaments:
                logger.debug(
                    "Skipping filament {} for layer {} because it is already spent",
                    filament,
                    layer,
                )
                continue

            logger.debug("Spending {}mm of filament {}", usage, filament)

            # Use the external spool ID if we're not using an AMS
            if self.using_ams and self.ams_mapping:
                if filament >= len(self.ams_mapping):
                    logger.error(
                        "Filament {} has no entry in AMS mapping {}",
                        filament,
                        self.ams_mapping,
                    )
                    return False
                real_mapping = self.ams_mapping[filament]
            else:
                real_mapping = EXTERNAL_SPOOL_ID

            logger.debug("Real mapping for filament {} is {}", filament, real_mapping)

            # Load the filament from the configuration
            spoolman_spool = trays.get(str(real_mapping))
            if spoolman_spool is None:
                logger.error("Failed to find tray for filament {}", filament)
                return False

            logger.debug(
                "Spoolman spool for filament {} is {}", filament, spoolman_spool
            )

            # Spend the filament
            self.spoolman_client.consume_spool(spoolman_spool, length=usage)
            logger.info(
                "event=filament_consumed task_id={} layer={} logical_filament={} "
                "tray={} spool_id={} length_mm={:.3f}",
                self.task_id,
                layer,
                filament,
                real_mapping,
                spoolman_spool,
                usage,
            )
            spent_filaments.add(filament)
            # Narrow the unavoidable cross-service crash window: persist each
            # successful Spoolman update instead of waiting for the whole
            # layer (which may contain several filaments) to finish.
            self._save_progress(layer)

        return spent_filaments.issuperset(layer_usage)

    def _download_model(self, model_url):
        logger.info("event=model_download_started scheme=http")

        temp_file_name = None
        response = None
        downloaded_bytes = 0
        started_at = time.monotonic()
        try:
            with tempfile.NamedTemporaryFile(suffix=".3mf", delete=False) as model_file:
                temp_file_name = model_file.name
                response = requests.get(
                    model_url,
                    timeout=get_http_timeout(),
                    stream=True,
                )
                response.raise_for_status()

                for chunk in response.iter_content(
                    chunk_size=MODEL_DOWNLOAD_CHUNK_SIZE
                ):
                    if chunk:
                        model_file.write(chunk)
                        downloaded_bytes += len(chunk)
        except Exception as e:
            logger.error("Failed to download model: {}", e)
            if temp_file_name is not None:
                try:
                    os.remove(temp_file_name)
                except FileNotFoundError:
                    pass
            return None
        finally:
            if response is not None:
                response.close()

        logger.info(
            "event=model_download_complete bytes={} duration_seconds={:.3f}",
            downloaded_bytes,
            time.monotonic() - started_at,
        )
        return temp_file_name

    def _retrieve_model_from_ftp(self, model_path):
        logger.debug("Retrieving model from FTP path: {}", model_path)

        mount_prefixes = ("/sdcard/", "/media/usb0/")

        # Remove fs mount prefixes
        for p in mount_prefixes:
            if model_path.startswith(p):
                model_path = model_path.removeprefix(p)
                break

        # Retrieve from FTP server
        return retrieve_3mf(model_path)

    def _load_model(self, model_path, gcode_file):
        self.active_model = None
        started_at = time.monotonic()
        try:
            with open_gcode(model_path, gcode_file) as gcode:
                if gcode is None:
                    logger.error("Failed to extract gcode from model")
                    return False
                active_model = evaluate_gcode(gcode)
        except (OSError, UnicodeError, ValueError, zipfile.BadZipFile) as e:
            logger.error("Failed to parse GCODE from model: {}", e)
            return False

        self.active_model = active_model
        total_filament_usage = {}

        for layer, layer_usage in self.active_model.items():
            for filament, usage in layer_usage.items():
                total_filament_usage[filament] = (
                    total_filament_usage.get(filament, 0) + usage
                )

        logger.info(
            "event=model_loaded task_id={} layers={} filaments={} "
            "usage_mm={} duration_seconds={:.3f}",
            self.task_id,
            len(self.active_model),
            sorted(total_filament_usage),
            {
                filament: round(usage, 3)
                for filament, usage in total_filament_usage.items()
            },
            time.monotonic() - started_at,
        )

        return True

    def _attempt_print_resume(self, task_id, subtask_id):
        result = recover_model(task_id, subtask_id)
        if result is None:
            return
        (
            model_path,
            gcode_file_name,
            current_layer,
            spent_layers,
            spent_filaments,
            ams_mapping,
            using_ams,
            print_started,
            checkpoint_task_id,
            checkpoint_subtask_id,
        ) = result

        self.task_id = checkpoint_task_id
        self.subtask_id = checkpoint_subtask_id
        if not self._load_model(model_path, gcode_file_name):
            return
        self.spent_layers = set(spent_layers)
        self.spent_filaments = {
            layer: set(filaments) for layer, filaments in spent_filaments.items()
        }
        self.ams_mapping = ams_mapping
        self.current_layer = current_layer
        self.using_ams = using_ams
        self.print_started = print_started
        logger.info(
            "event=print_recovered task_id={} subtask_id={} current_layer={} "
            "accounted_layers={} print_started={} using_ams={}",
            self.task_id,
            self.subtask_id,
            self.current_layer,
            len(self.spent_layers),
            self.print_started,
            self.using_ams,
        )

    def _mark_print_started(self):
        mark_print_started()
        self.print_started = True
        logger.info(
            "event=print_tracking_started task_id={} subtask_id={} state={}",
            self.task_id,
            self.subtask_id,
            self.gcode_state,
        )

    def _is_current_print(self, print_obj):
        if self.active_model is None:
            return False

        task_id = _normalize_identifier(print_obj.get("task_id"))
        subtask_id = _normalize_identifier(print_obj.get("subtask_id"))
        supplied_identifiers = (
            (task_id, self.task_id),
            (subtask_id, self.subtask_id),
        )
        comparable = [
            (incoming, current)
            for incoming, current in supplied_identifiers
            if incoming is not None and current is not None
        ]
        return bool(comparable) and all(
            incoming == current for incoming, current in comparable
        )


def _normalize_mapping(mapping):
    try:
        return int(mapping)
    except (TypeError, ValueError):
        return mapping


def _normalize_identifier(identifier):
    if identifier is None or identifier == "":
        return None
    return str(identifier)

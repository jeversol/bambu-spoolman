import os
import re
import threading
import time

from loguru import logger

from bambu_spoolman.broker.consumption import ConsumptionDelivery
from bambu_spoolman.broker.filament_accounting import (
    mapping_at_position,
    plan_layer_usage,
)
from bambu_spoolman.broker.model_loader import (
    load_model,
    retrieve_model,
)
from bambu_spoolman.broker.print_checkpoint import PrintCheckpoint
from bambu_spoolman.settings import (
    load_settings,
)
from bambu_spoolman.spoolman import new_client

ACTIVE_GCODE_STATES = frozenset({"PAUSE", "RUNNING"})
FAILED_GCODE_STATES = frozenset({"FAILED", "FAILURE"})
RECOVERABLE_GCODE_STATES = ACTIVE_GCODE_STATES | {"FINISH"}
CANCELED_PRINT_ERROR = 50348044
SPOOLMAN_RETRY_INITIAL_SECONDS = 1
SPOOLMAN_RETRY_MAX_SECONDS = 60
INACTIVE_AMS_TRAYS = frozenset({-1, 254, 255})
MAX_STANDARD_AMS_TRAY = 15


class FilamentUsageTracker:
    def __init__(self, client=None):
        self.spoolman_client = client if client is not None else new_client()
        self.checkpoint = PrintCheckpoint(self._checkpoint_context)
        self._lock = threading.RLock()
        self._retry_timer = None
        self._retry_generation = 0
        self._stopping = threading.Event()
        self.active_model = None
        self.ams_mapping = None
        self.checkpoint.reset_progress()
        self.using_ams = False

        self.gcode_state = None
        self.current_layer = None
        self.print_started = False
        self.task_id = None
        self.subtask_id = None
        self.print_name = None
        self.ams_mapping_history = []
        self.active_logical_filament = None
        self.last_active_tray = None
        self.skipped_object_layers = {}
        self.skipped_object_lines = {}
        self._reset_checkpoint_recovery_guard()
        self._reset_spoolman_retry()

    def _is_stopping(self):
        stopping = getattr(self, "_stopping", None)
        return stopping is not None and stopping.is_set()

    def stop(self):
        """Reject new work immediately, without waiting for an active request."""
        self._stopping.set()
        retry_timer = self._retry_timer
        if retry_timer is not None:
            retry_timer.cancel()

    def close(self, timeout=30):
        """Cancel retries and wait up to timeout seconds for active tracker work.

        True means the active callback finished and the current timer exited.
        False leaves any durable intent/checkpoint available for restart; a
        synchronous request already in flight cannot be forcibly interrupted.
        """
        self.stop()
        deadline = time.monotonic() + timeout
        if not self._lock.acquire(timeout=max(0, timeout)):
            return False
        try:
            retry_timer = self._retry_timer
            self._reset_spoolman_retry()
        finally:
            self._lock.release()
        # A timer may be waiting for _lock. Never join while holding that lock.
        if retry_timer is not None and retry_timer is not threading.current_thread():
            retry_timer.join(timeout=max(0, deadline - time.monotonic()))
            return not retry_timer.is_alive()
        return True

    def on_message(self, mqtt_handler, message):
        if self._is_stopping():
            return
        lock = getattr(self, "_lock", None)
        if lock is None:
            return self._on_message(mqtt_handler, message)
        with lock:
            if not self._is_stopping():
                return self._on_message(mqtt_handler, message)

    def _on_message(self, mqtt_handler, message):
        print_obj = message.get("print", {})
        command = print_obj.get("command")

        previous_gcode_state = self.gcode_state
        self.gcode_state = print_obj.get("gcode_state", self.gcode_state)

        if (
            previous_gcode_state in RECOVERABLE_GCODE_STATES
            and self.gcode_state not in RECOVERABLE_GCODE_STATES
        ):
            self._reset_checkpoint_recovery_guard()

        if previous_gcode_state != self.gcode_state:
            logger.info(
                "event=print_state task_id={} subtask_id={} previous={} current={}",
                _normalize_identifier(print_obj.get("task_id")) or self.task_id,
                _normalize_identifier(print_obj.get("subtask_id")) or self.subtask_id,
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
                and self.gcode_state in RECOVERABLE_GCODE_STATES
            ):
                # Recover before handling the current status. A recovered
                # checkpoint records whether this print had actually started.
                self._attempt_print_resume_once(print_obj)

            if (
                self.active_model is not None
                and not self.print_started
                and self.gcode_state in ACTIVE_GCODE_STATES
            ):
                self._mark_print_started()

            layer_changed = False
            reported_layer = self.current_layer
            if "layer_num" in print_obj:
                last_layer = self.current_layer
                try:
                    reported_layer = int(print_obj["layer_num"])
                except TypeError, ValueError:
                    logger.warning(
                        "Ignoring invalid layer number: {}", print_obj["layer_num"]
                    )
                    reported_layer = last_layer
                if self.print_started and reported_layer != last_layer:
                    logger.info(
                        "event=layer_started task_id={} subtask_id={} layer={} "
                        "previous_layer={}",
                        self.task_id,
                        self.subtask_id,
                        reported_layer,
                        last_layer,
                    )
                    self.current_layer = reported_layer
                    layer_changed = True

            if self.active_model is not None:
                if layer_changed:
                    effective_layer = reported_layer
                elif (
                    self.current_layer is not None
                    and _normalize_line_number(print_obj.get("mc_print_line_number"))
                    is not None
                ):
                    effective_layer = self.current_layer
                elif self.current_layer is not None:
                    effective_layer = self.current_layer + 1
                else:
                    effective_layer = 0
                self._handle_status_updates(print_obj, effective_layer)

            if layer_changed:
                self._handle_layer_change(reported_layer)

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
        self.checkpoint.clear()
        self._reset_checkpoint_recovery_guard()
        model_url = print_obj.get("url")

        self.active_model = None
        self.ams_mapping = None
        self.current_layer = None
        self.print_started = print_obj.get("gcode_state") in ACTIVE_GCODE_STATES
        self.checkpoint.reset_progress()
        self.using_ams = False
        self.task_id = _normalize_identifier(print_obj.get("task_id"))
        self.subtask_id = _normalize_identifier(print_obj.get("subtask_id"))
        self.print_name = _print_name(print_obj)
        self.ams_mapping_history = []
        self.active_logical_filament = None
        self.last_active_tray = None
        self.skipped_object_layers = {}
        self.skipped_object_lines = {}
        self._reset_spoolman_retry()
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

        try:
            ams_mapping = [
                _normalize_mapping(mapping)
                for mapping in (print_obj.get("ams_mapping") or [])
            ]
            if print_obj.get("use_ams", False) or (
                ams_mapping and ams_mapping[0] not in (-1, 255)
            ):
                self.using_ams = True
                self.ams_mapping = ams_mapping
                self.ams_mapping_history = [
                    {"layer": 0, "mapping": list(self.ams_mapping)}
                ]
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
                return

            self.checkpoint.create(
                model_path=model,
                task_id=print_obj.get("task_id"),
                subtask_id=print_obj.get("subtask_id"),
                ams_mapping=self.ams_mapping,
                gcode_file_name=gcode_file_name,
                using_ams=self.using_ams,
                print_started=self.print_started,
                print_name=self.print_name,
            )

        finally:
            os.remove(model)

    def _retrieve_model(self, model_url):
        return retrieve_model(model_url)

    def _handle_layer_change(self, layer):
        if self.active_model is None:
            logger.debug("Skipping layer change because no model is loaded")
            return
        self.current_layer = layer
        retry_at = getattr(self, "spoolman_retry_at", 0)
        now = time.monotonic()
        if retry_at > now:
            logger.debug(
                "event=spoolman_retry_deferred task_id={} layer={} "
                "retry_in_seconds={:.3f}",
                self.task_id,
                layer,
                retry_at - now,
            )
            self._save_progress(layer)
            return
        # Bambu Studio emits M73 L<n> at the start of layer n. Only earlier
        # layers are known to be complete at that point. Include any earlier
        # failed layers so a later status update retries them.
        to_spend = set(
            model_layer
            for model_layer in self.active_model
            if model_layer < layer
            and model_layer not in self.checkpoint.progress.spent_layers
        )
        if to_spend:
            logger.info(
                "event=layers_ready task_id={} reported_layer={} layers={} "
                "already_accounted={}",
                self.task_id,
                layer,
                sorted(to_spend),
                len(self.checkpoint.progress.spent_layers),
            )

        failed = False
        for layer_to_spend in sorted(to_spend):
            try:
                spent = self._spend_filament_for_layer(layer_to_spend)
            except Exception as e:
                logger.error(
                    "Failed to spend filament for layer {}: {}", layer_to_spend, e
                )
                self._schedule_spoolman_retry(layer_to_spend, str(e))
                failed = True
                break

            if spent:
                self.checkpoint.progress.spent_layers.add(layer_to_spend)
                logger.info(
                    "event=layer_accounted task_id={} layer={} accounted_layers={} "
                    "total_layers={}",
                    self.task_id,
                    layer_to_spend,
                    len(self.checkpoint.progress.spent_layers),
                    len(self.active_model),
                )
            else:
                self._schedule_spoolman_retry(
                    layer_to_spend, "filament mapping is not ready"
                )
                failed = True
                break
        if to_spend and not failed:
            self._reset_spoolman_retry()
        self._save_progress(layer)

    def _checkpoint_context(self):
        return {
            "current_layer": self.current_layer,
            "ams_mapping_history": self.ams_mapping_history,
            "active_logical_filament": self.active_logical_filament,
            "last_active_tray": self.last_active_tray,
            "skipped_object_layers": self.skipped_object_layers,
            "skipped_object_lines": self.skipped_object_lines,
        }

    def _save_progress(self, fallback_layer=None):
        self.checkpoint.save(fallback_layer)

    def _schedule_spoolman_retry(self, layer, reason):
        if self._is_stopping():
            return
        generation = getattr(self, "_retry_generation", 0) + 1
        self._retry_generation = generation
        delay = getattr(self, "spoolman_retry_delay", SPOOLMAN_RETRY_INITIAL_SECONDS)
        self.spoolman_retry_at = time.monotonic() + delay
        self.spoolman_retry_delay = min(delay * 2, SPOOLMAN_RETRY_MAX_SECONDS)
        retry_timer = getattr(self, "_retry_timer", None)
        if retry_timer is not None:
            retry_timer.cancel()
        if hasattr(self, "_lock"):
            retry_timer = threading.Timer(
                delay, lambda: self._retry_pending_usage(generation)
            )
            retry_timer.daemon = True
            self._retry_timer = retry_timer
            retry_timer.start()
            # stop() does not take _lock, so cover a concurrent stop between
            # the initial guard and starting this timer.
            if self._is_stopping():
                retry_timer.cancel()
                return
        logger.warning(
            "event=spoolman_retry_scheduled task_id={} layer={} "
            "retry_in_seconds={} reason={}",
            self.task_id,
            layer,
            delay,
            reason,
        )

    def _reset_spoolman_retry(self):
        self._retry_generation = getattr(self, "_retry_generation", 0) + 1
        retry_timer = getattr(self, "_retry_timer", None)
        if retry_timer is not None:
            retry_timer.cancel()
        self._retry_timer = None
        self.spoolman_retry_at = 0
        self.spoolman_retry_delay = SPOOLMAN_RETRY_INITIAL_SECONDS

    def _retry_pending_usage(self, generation):
        with self._lock:
            if self._is_stopping() or generation != self._retry_generation:
                return
            self._retry_timer = None
            if self.active_model is None or not self.print_started:
                return
            self.spoolman_retry_at = 0
            logger.info(
                "event=spoolman_retry_started task_id={} state={} layer={}",
                self.task_id,
                self.gcode_state,
                self.current_layer,
            )
            if self.gcode_state == "FINISH":
                self._handle_print_end()
            elif self.current_layer is not None:
                self._handle_layer_change(self.current_layer)

    def _handle_print_end(self):
        # Spend all layers that haven't already been spent
        remaining_layers = (
            set(self.active_model or {}) - self.checkpoint.progress.spent_layers
        )
        logger.info(
            "event=print_finish_received task_id={} subtask_id={} "
            "accounted_layers={} total_layers={} pending_layers={}",
            self.task_id,
            self.subtask_id,
            len(self.checkpoint.progress.spent_layers),
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

        remaining_layers = (
            set(self.active_model or {}) - self.checkpoint.progress.spent_layers
        )
        if remaining_layers:
            logger.warning(
                "Print ended with unspent layers {}. Automatic retry remains scheduled",
                sorted(remaining_layers),
            )
            return False

        task_id = self.task_id
        subtask_id = self.subtask_id
        print_name = getattr(self, "print_name", None)
        total_layers = len(self.active_model or {})
        self.active_model = None
        self.ams_mapping = None
        self.checkpoint.reset_progress()
        self.using_ams = False
        self.current_layer = None
        self.print_started = False
        self.task_id = None
        self.subtask_id = None
        self.print_name = None
        self.ams_mapping_history = []
        self.active_logical_filament = None
        self.last_active_tray = None
        self.skipped_object_layers = {}
        self.skipped_object_lines = {}
        self._reset_spoolman_retry()

        # The printer commonly continues sending FINISH deltas after a print
        # has been fully accounted. Remember that identity so those deltas do
        # not repeatedly probe for the checkpoint we just cleared.
        self._recovery_task_id = task_id
        self._recovery_subtask_id = subtask_id
        self._recovery_print_name = print_name
        self._last_checkpoint_recovery_key = (
            self.gcode_state,
            task_id,
            subtask_id,
            print_name,
        )

        self.checkpoint.clear()
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
            len(self.checkpoint.progress.spent_layers),
        )

        self.active_model = None
        self.ams_mapping = None
        self.checkpoint.reset_progress()
        self.using_ams = False
        self.current_layer = None
        self.print_started = False
        self.task_id = None
        self.subtask_id = None
        self.print_name = None
        self.ams_mapping_history = []
        self.active_logical_filament = None
        self.last_active_tray = None
        self.skipped_object_layers = {}
        self.skipped_object_lines = {}
        self._reset_spoolman_retry()

        self.checkpoint.clear()

    def _spend_filament_for_layer(self, layer):
        if self._is_stopping() or self.active_model is None:
            return False
        logger.debug("Spending filament for layer {}", layer)

        segment_usage = plan_layer_usage(
            self.active_model,
            layer,
            using_ams=self.using_ams,
            mapping=self.ams_mapping,
            mapping_history=getattr(self, "ams_mapping_history", []),
            skipped_object_layers=getattr(self, "skipped_object_layers", {}),
            skipped_object_lines=getattr(self, "skipped_object_lines", {}),
        )
        if segment_usage is None:
            return False

        delivery = ConsumptionDelivery(
            self.spoolman_client,
            self.checkpoint,
            task_id=self.task_id,
            stopped=self._is_stopping,
        )
        return delivery.deliver_layer(
            layer, segment_usage, load_settings().get("trays", {})
        )

    def _mapping_for_layer(self, layer):
        return self._mapping_for_position(layer)

    def _mapping_for_position(self, layer, line=None):
        return mapping_at_position(
            self.ams_mapping,
            getattr(self, "ams_mapping_history", []),
            layer,
            line,
        )

    def _handle_status_updates(self, print_obj, effective_layer):
        line_number = _normalize_line_number(print_obj.get("mc_print_line_number"))
        changed = self._update_skipped_objects(print_obj, effective_layer, line_number)
        changed = (
            self._update_active_ams_tray(print_obj, effective_layer, line_number)
            or changed
        )
        if changed:
            self._save_progress(effective_layer)

    def _update_skipped_objects(self, print_obj, effective_layer, line_number=None):
        if "s_obj" not in print_obj:
            return False

        skipped_objects = _normalize_object_ids(print_obj.get("s_obj"))
        changed = False
        tracked = getattr(self, "skipped_object_layers", {})
        self.skipped_object_layers = tracked
        tracked_lines = getattr(self, "skipped_object_lines", {})
        self.skipped_object_lines = tracked_lines
        for object_id in skipped_objects:
            if object_id in tracked:
                continue
            tracked[object_id] = int(effective_layer)
            if line_number is not None:
                tracked_lines[object_id] = line_number
            changed = True
            logger.info(
                "event=object_skipped task_id={} object_id={} effective_layer={} "
                "gcode_line={}",
                self.task_id,
                object_id,
                effective_layer,
                line_number,
            )
        return changed

    def _update_active_ams_tray(self, print_obj, effective_layer, line_number=None):
        if not getattr(self, "using_ams", False):
            return False
        ams_status = print_obj.get("ams")
        if not isinstance(ams_status, dict) or "tray_now" not in ams_status:
            return False

        tray = _normalize_mapping(ams_status.get("tray_now"))
        if not isinstance(tray, int) or tray in INACTIVE_AMS_TRAYS:
            return False

        current_mapping = list(self._mapping_for_position(effective_layer, line_number))
        if tray in current_mapping:
            logical_filament = current_mapping.index(tray)
            changed = (
                getattr(self, "active_logical_filament", None) != logical_filament
                or getattr(self, "last_active_tray", None) != tray
            )
            self.active_logical_filament = logical_filament
            self.last_active_tray = tray
            return changed

        if not getattr(self, "print_started", False):
            logger.debug(
                "event=ams_refill_mapping_deferred task_id={} tray={} "
                "reason=print_not_started",
                self.task_id,
                tray,
            )
            return False
        if tray < 0 or tray > MAX_STANDARD_AMS_TRAY:
            logger.warning(
                "event=ams_tray_ignored task_id={} tray={} reason=out_of_range",
                self.task_id,
                tray,
            )
            return False

        logical_filament = getattr(self, "active_logical_filament", None)
        last_active_tray = getattr(self, "last_active_tray", None)
        if logical_filament is None and last_active_tray in current_mapping:
            logical_filament = current_mapping.index(last_active_tray)
        if logical_filament is None:
            model_filaments = {
                filament
                for layer_usage in (self.active_model or {}).values()
                for filament in layer_usage
            }
            if len(model_filaments) == 1:
                logical_filament = next(iter(model_filaments))

        if logical_filament is None:
            logger.warning(
                "event=ams_refill_mapping_ambiguous task_id={} tray={} "
                "mapping={} effective_layer={}",
                self.task_id,
                tray,
                current_mapping,
                effective_layer,
            )
            changed = getattr(self, "last_active_tray", None) != tray
            self.last_active_tray = tray
            return changed

        while len(current_mapping) <= logical_filament:
            current_mapping.append(-1)
        previous_tray = current_mapping[logical_filament]
        current_mapping[logical_filament] = tray
        transition = {
            "layer": int(effective_layer),
            "line": line_number,
            "mapping": current_mapping,
        }
        history = getattr(self, "ams_mapping_history", [])
        self.ams_mapping_history = history
        if (
            history
            and int(history[-1].get("layer", 0)) == int(effective_layer)
            and history[-1].get("line") == line_number
        ):
            history[-1] = transition
        else:
            history.append(transition)
        self.ams_mapping = current_mapping
        self.active_logical_filament = logical_filament
        self.last_active_tray = tray
        logger.info(
            "event=ams_refill_mapping_changed task_id={} logical_filament={} "
            "previous_tray={} current_tray={} effective_layer={} gcode_line={}",
            self.task_id,
            logical_filament,
            previous_tray,
            tray,
            effective_layer,
            line_number,
        )
        return True

    def _load_model(self, model_path, gcode_file):
        self.active_model = None
        started_at = time.monotonic()
        active_model = load_model(model_path, gcode_file)
        if active_model is None:
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

    def _attempt_print_resume(self, task_id, subtask_id, print_name=None):
        result = self.checkpoint.recover(task_id, subtask_id, print_name)
        if result is None:
            return
        self.task_id = result.task_id
        self.subtask_id = result.subtask_id
        self.print_name = result.print_name
        if not self._load_model(result.model_path, result.gcode_file_name):
            return
        self.checkpoint.progress = result.progress
        self.ams_mapping = result.ams_mapping
        self.ams_mapping_history = result.ams_mapping_history or (
            [{"layer": 0, "mapping": list(result.ams_mapping)}]
            if result.ams_mapping
            else []
        )
        self.active_logical_filament = result.active_logical_filament
        self.last_active_tray = result.last_active_tray
        self.skipped_object_layers = result.skipped_object_layers or {}
        self.skipped_object_lines = result.skipped_object_lines or {}
        self.current_layer = result.current_layer
        self.using_ams = result.using_ams
        self.print_started = result.print_started
        self._reset_spoolman_retry()
        logger.info(
            "event=print_recovered task_id={} subtask_id={} current_layer={} "
            "accounted_layers={} print_started={} using_ams={}",
            self.task_id,
            self.subtask_id,
            self.current_layer,
            len(self.checkpoint.progress.spent_layers),
            self.print_started,
            self.using_ams,
        )

    def _attempt_print_resume_once(self, print_obj):
        task_id = _normalize_identifier(print_obj.get("task_id"))
        subtask_id = _normalize_identifier(print_obj.get("subtask_id"))
        print_name = _print_name(print_obj)

        if task_id is not None:
            self._recovery_task_id = task_id
        if subtask_id is not None:
            self._recovery_subtask_id = subtask_id
        if print_name is not None:
            self._recovery_print_name = print_name

        recovery_key = (
            self.gcode_state,
            getattr(self, "_recovery_task_id", None),
            getattr(self, "_recovery_subtask_id", None),
            getattr(self, "_recovery_print_name", None),
        )
        if recovery_key == getattr(self, "_last_checkpoint_recovery_key", None):
            return

        # Record the attempt before doing I/O. A failed or unavailable
        # checkpoint is final for this identity unless a later MQTT delta adds
        # information, such as a P-series local print name.
        self._last_checkpoint_recovery_key = recovery_key
        self._attempt_print_resume(*recovery_key[1:])

    def _reset_checkpoint_recovery_guard(self):
        self._recovery_task_id = None
        self._recovery_subtask_id = None
        self._recovery_print_name = None
        self._last_checkpoint_recovery_key = None

    def _mark_print_started(self):
        self.checkpoint.mark_started()
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
        if not comparable or not all(
            incoming == current for incoming, current in comparable
        ):
            return False

        incoming_name = _print_name(print_obj)
        current_name = getattr(self, "print_name", None)
        ambiguous_ids = all(
            identifier in (None, "0")
            for identifier in (task_id, subtask_id, self.task_id, self.subtask_id)
        )
        if ambiguous_ids:
            return (
                incoming_name is not None
                and current_name is not None
                and incoming_name == current_name
            )
        return not (
            incoming_name is not None
            and current_name is not None
            and incoming_name != current_name
        )


def _normalize_mapping(mapping):
    try:
        return int(mapping)
    except TypeError, ValueError:
        return mapping


def _normalize_identifier(identifier):
    if identifier is None or identifier == "":
        return None
    return str(identifier)


def _print_name(print_obj):
    value = print_obj.get("subtask_name") or print_obj.get("param")
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _normalize_object_ids(value):
    if value is None:
        return set()
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return set()
        value = re.split(r"[\s,]+", value)
    elif not isinstance(value, (list, tuple, set)):
        value = [value]

    object_ids = set()
    for object_id in value:
        try:
            object_ids.add(int(object_id))
        except TypeError, ValueError:
            logger.warning("Ignoring invalid skipped object ID: {}", object_id)
    return object_ids


def _normalize_line_number(value):
    try:
        line = int(value)
    except TypeError, ValueError:
        return None
    return line if line > 0 else None

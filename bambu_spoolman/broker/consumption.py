"""Journaled consumption delivery, independent of MQTT and model loading."""

import math
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from loguru import logger

from bambu_spoolman.broker.accounting import warn_uncertain_consumption
from bambu_spoolman.broker.consumption_state import endpoint_scope, is_uncertain


@dataclass
class ConsumptionProgress:
    spent_layers: set[int] = field(default_factory=set)
    spent_filaments: dict[int, set[int]] = field(default_factory=dict)
    spent_segments: dict[int, set[str]] = field(default_factory=dict)
    pending_consumption: dict | None = None


class ConsumptionJournal(Protocol):
    progress: ConsumptionProgress

    def save(self, fallback_layer=None): ...


class ConsumptionClient(Protocol):
    endpoint: str

    def get_spool(self, spool_id): ...

    def consume_spool(self, spool_id, *, length): ...


class ConsumptionDelivery:
    def __init__(
        self,
        client: ConsumptionClient,
        journal: ConsumptionJournal,
        *,
        task_id=None,
        stopped=lambda: False,
    ):
        self.client = client
        self.journal = journal
        self.task_id = task_id
        self.stopped = stopped

    def settle_pending(self):
        if self.stopped():
            return False
        pending = self.journal.progress.pending_consumption
        if pending is not None:
            layer = int(pending["layer"])
            segments = self.journal.progress.spent_segments.setdefault(layer, set())
            self._reconcile_pending_consumption(layer, segments)
        return True

    def deliver_layer(self, layer, segment_usage, trays):
        if self.stopped():
            return False
        spent_filaments = self.journal.progress.spent_filaments.setdefault(
            int(layer), set()
        )
        spent_segments = self.journal.progress.spent_segments.setdefault(
            int(layer), set()
        )
        self._reconcile_pending_consumption(layer, spent_segments)

        segment_keys_by_filament = {}
        for filament, real_mapping in segment_usage:
            segment_keys_by_filament.setdefault(filament, set()).add(
                _segment_key(filament, real_mapping)
            )

        for (filament, real_mapping), usage in segment_usage.items():
            if self.stopped():
                return False
            segment_key = _segment_key(filament, real_mapping)
            if filament in spent_filaments or segment_key in spent_segments:
                logger.debug(
                    "Skipping accounted segment {} for layer {}", segment_key, layer
                )
                continue

            spoolman_spool = trays.get(str(real_mapping))
            if spoolman_spool is None:
                logger.error(
                    "Failed to find tray {} for filament {}", real_mapping, filament
                )
                return False

            self._consume_segment_durably(
                layer, filament, real_mapping, spoolman_spool, usage, segment_key
            )
            spent_segments.add(segment_key)
            pending = self.journal.progress.pending_consumption
            self.journal.progress.pending_consumption = None
            if segment_keys_by_filament[filament].issubset(spent_segments):
                spent_filaments.add(filament)
            try:
                self.journal.save(layer)
            except Exception:
                spent_segments.discard(segment_key)
                spent_filaments.discard(filament)
                self.journal.progress.pending_consumption = pending
                raise

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

        required_segments = {
            _segment_key(filament, real_mapping)
            for filament, real_mapping in segment_usage
            if filament not in spent_filaments
        }
        return not required_segments or required_segments.issubset(spent_segments)

    def _consume_segment_durably(
        self, layer, filament, tray, spool_id, length, segment_key
    ):
        if self.journal.progress.pending_consumption is not None:
            raise RuntimeError("A different consumption intent is still pending")

        spool = self.client.get_spool(spool_id)
        baseline = _spool_used_length(spool)
        self.journal.progress.pending_consumption = {
            "request_id": uuid.uuid4().hex,
            "delivery_state": "prepared",
            "endpoint_scope": endpoint_scope(self.client.endpoint),
            "layer": int(layer),
            "filament": int(filament),
            "tray": tray,
            "spool_id": spool_id,
            "length": float(length),
            "segment_key": segment_key,
            "baseline_used_length": baseline,
        }
        self.journal.save(layer)
        logger.debug(
            "event=consumption_intent_saved task_id={} layer={} segment={} "
            "spool_id={} baseline_used_length={} length_mm={:.3f}",
            self.task_id,
            layer,
            segment_key,
            spool_id,
            baseline,
            length,
        )
        self._send_pending(layer)

    def _send_pending(self, layer):
        pending = self.journal.progress.pending_consumption
        if pending.get("endpoint_scope") != endpoint_scope(self.client.endpoint):
            warn_uncertain_consumption(pending)
            pending["delivery_state"] = "confirmed"
            return
        pending["delivery_state"] = "in_flight"
        try:
            self.journal.save(layer)
        except Exception:
            pending["delivery_state"] = "prepared"
            raise
        try:
            self.client.consume_spool(pending["spool_id"], length=pending["length"])
        except Exception:
            warn_uncertain_consumption(pending)
        # Avoid repeating a request whose response was lost. A warning is enough
        # for approximate inventory tracking; later segments can continue.
        pending["delivery_state"] = "confirmed"

    def _reconcile_pending_consumption(self, layer, spent_segments):
        pending = self.journal.progress.pending_consumption
        if pending is None:
            return
        if is_uncertain(pending, self.client.endpoint):
            warn_uncertain_consumption(pending)
            pending["delivery_state"] = "confirmed"
        if int(pending.get("layer", -1)) != int(layer):
            raise RuntimeError(
                "Consumption intent belongs to a different pending layer"
            )
        if pending["delivery_state"] == "prepared":
            # Retry only work known to be unsent, using its original spool.
            self._send_pending(layer)
        segment_key = str(pending["segment_key"])
        was_spent = segment_key in spent_segments
        spent_segments.add(segment_key)
        self.journal.progress.pending_consumption = None
        try:
            self.journal.save(layer)
        except Exception:
            self.journal.progress.pending_consumption = pending
            if not was_spent:
                spent_segments.discard(segment_key)
            raise


def _segment_key(filament, tray):
    return f"{filament}:{tray}"


def _spool_used_length(spool):
    if not isinstance(spool, dict):
        raise RuntimeError("Spoolman did not return spool state for reconciliation")
    try:
        used_length = float(spool["used_length"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Spoolman spool response has no valid used_length") from exc
    if not math.isfinite(used_length) or used_length < 0:
        raise RuntimeError("Spoolman spool used_length is invalid")
    return used_length

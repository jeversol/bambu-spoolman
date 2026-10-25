"""Filament usage planning, independent of delivery, retries and checkpoints.

Inputs are parsed usage and the print's mapping/skip history. Planning does not
mutate them or make requests; the tracker decides when to persist and deliver
these segments to Spoolman. None means usage/mapping is not ready; an empty plan
means the layer has no remaining extrusion to account for.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Protocol

from loguru import logger

from bambu_spoolman.settings import EXTERNAL_SPOOL_ID


class UsageSource(Protocol):
    """Object-aware usage supplied by a parser or recovered model."""

    def for_layer(
        self,
        layer: int,
        skipped_objects: set[int],
        skipped_object_lines: Mapping[int, int],
    ) -> Mapping[int, float] | None: ...

    def events_for_layer(
        self,
        layer: int,
        skipped_objects: set[int],
        skipped_object_lines: Mapping[int, int],
    ) -> Iterable[tuple[int, int | None, int, float]]: ...


def mapping_at_position(mapping, history, layer, line=None):
    """Select the mapping effective at this layer and optional G-code line."""
    selected = mapping or []
    for transition in sorted(
        history,
        key=lambda item: (int(item.get("layer", 0)), int(item.get("line") or -1)),
    ):
        transition_layer = int(transition.get("layer", 0))
        transition_line = transition.get("line")
        if transition_layer > int(layer):
            break
        if transition_layer == int(layer) and transition_line is not None:
            if line is None or int(transition_line) > int(line):
                continue
        selected = transition.get("mapping") or selected
    return list(selected)


def plan_layer_usage(
    model: UsageSource | Mapping[int, Mapping[int, float]],
    layer: int,
    *,
    using_ams: bool,
    mapping: Sequence[int] | None,
    mapping_history=(),
    skipped_object_layers=None,
    skipped_object_lines=None,
) -> dict[tuple[int, int], float] | None:
    """Group extrusion by logical filament and physical tray without side effects.

    Legacy layer-total dictionaries remain supported alongside object/line-aware
    models. A line-specific skip takes precedence over a whole-layer skip for
    that object, retaining extrusion before the reported cancellation line.
    """
    skipped_object_lines = skipped_object_lines or {}
    skipped_objects = {
        object_id
        for object_id, effective_layer in (skipped_object_layers or {}).items()
        if int(layer) >= effective_layer and object_id not in skipped_object_lines
    }
    if hasattr(model, "for_layer"):
        layer_usage = model.for_layer(layer, skipped_objects, skipped_object_lines)
    else:
        layer_usage = model.get(int(layer))
    if layer_usage is None:
        logger.error("Failed to find filament usage for layer {}", layer)
        return None

    def tray_for(filament, line=None):
        if not using_ams or not mapping:
            return EXTERNAL_SPOOL_ID
        current = mapping_at_position(mapping, mapping_history, layer, line)
        if filament >= len(current):
            logger.error(
                "Filament {} has no entry in AMS mapping {}", filament, current
            )
            return None
        return current[filament]

    segments = {}
    if hasattr(model, "events_for_layer"):
        events = model.events_for_layer(layer, skipped_objects, skipped_object_lines)
        for line, _object_id, filament, usage in events:
            tray = tray_for(filament, line)
            if tray is None:
                return None
            key = (filament, tray)
            segments[key] = segments.get(key, 0) + usage
    else:
        for filament, usage in layer_usage.items():
            tray = tray_for(filament)
            if tray is None:
                return None
            segments[(filament, tray)] = usage
    return segments

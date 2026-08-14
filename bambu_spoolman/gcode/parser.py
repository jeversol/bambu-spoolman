import io
import math
import re

from loguru import logger


class FilamentUsage(dict):
    """Layer totals plus the object-specific contribution to each layer."""

    def __init__(self, layer_filaments, object_filaments=None, extrusion_events=None):
        super().__init__(layer_filaments)
        self.object_filaments = object_filaments or {}
        self.extrusion_events = extrusion_events or {}

    def for_layer(self, layer, skipped_objects=(), skipped_object_lines=None):
        if skipped_object_lines:
            usage = {}
            for _line, _object_id, filament, length in self.events_for_layer(
                layer, skipped_objects, skipped_object_lines
            ):
                usage[filament] = usage.get(filament, 0) + length
            return usage

        usage = dict(self.get(int(layer), {}))
        layer_objects = self.object_filaments.get(int(layer), {})
        for object_id in skipped_objects:
            for filament, length in layer_objects.get(int(object_id), {}).items():
                remaining = usage.get(filament, 0) - length
                if remaining > 1e-9:
                    usage[filament] = remaining
                else:
                    usage.pop(filament, None)
        return usage

    def events_for_layer(self, layer, skipped_objects=(), skipped_object_lines=None):
        skipped_objects = set(skipped_objects)
        skipped_object_lines = skipped_object_lines or {}
        events = []
        for line, object_id, filament, length in self.extrusion_events.get(
            int(layer), []
        ):
            if object_id in skipped_objects:
                continue
            skip_line = skipped_object_lines.get(object_id)
            if skip_line is not None and line >= skip_line:
                continue
            events.append((line, object_id, filament, length))
        return events


class GCodeOperation:
    def __init__(self, raw_line, line_number=None):
        self.operation = None
        self.params = {}
        self.comment = None
        self.line_number = line_number

        self._parse(raw_line)

    def _parse(self, raw_line):
        # Split the line into operation and comment
        parts = list(map(lambda x: x.strip(), raw_line.split(";", 1)))
        if len(parts) > 1:
            self.comment = parts[1].strip()

        # Split the operation into parts
        parts = re.split(r"\s+", parts[0])
        self.operation = parts[0]
        for part in parts[1:]:
            key = part[0]
            value = part[1:]
            self.params[key] = value

    def __repr__(self):
        return f"<GCodeOperation {self.operation} {self.params} {self.comment}>"


def parse_gcode(gcode):
    lines = io.StringIO(gcode) if isinstance(gcode, str) else gcode

    for line_number, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue

        # logger.debug(f"Parsing line: {line}")
        yield GCodeOperation(line, line_number)


def evaluate_gcode(gcode):
    """
    Evaluate the gcode and return the filament usage (in mm) per layer
    """
    current_layer = 0  # The current layer
    active_filament = None  # The currently active filament
    active_object = None  # Bambu's object label ID, when inside an object block
    relative_extrusion = False
    extrusion_positions = {}
    retracted_filament = {}
    slicer_filament_totals = None

    layer_filaments = {}  # Total filament usage per layer
    object_filaments = {}  # Object-specific usage per layer and object ID
    extrusion_events = {}  # Net extrusion with raw G-code line positions

    operation_count = 0
    for operation in parse_gcode(gcode):
        operation_count += 1
        if operation.comment:
            object_start = re.match(
                r"start printing object.*:\s*(-?\d+)",
                operation.comment,
                re.IGNORECASE,
            )
            if object_start:
                active_object = int(object_start.group(1))
            elif re.match(r"stop printing object", operation.comment, re.IGNORECASE):
                active_object = None

            match = re.match(
                r"total filament length \[mm\]\s*:\s*(.+)",
                operation.comment,
                re.IGNORECASE,
            )
            if match:
                try:
                    slicer_filament_totals = [
                        float(value.strip()) for value in match.group(1).split(",")
                    ]
                except ValueError:
                    logger.warning(
                        "Ignoring malformed slicer filament totals: {}",
                        match.group(1),
                    )

        if operation.operation == "M82":
            relative_extrusion = False

        if operation.operation == "M83":
            relative_extrusion = True

        if operation.operation == "G92" and active_filament is not None:
            if extrusion := operation.params.get("E"):
                extrusion_positions[active_filament] = float(extrusion)

        if operation.operation == "M73":  # Layer change
            if layer := operation.params.get("L"):
                next_layer = int(layer)
                logger.debug(f"Layer change: {current_layer} -> {next_layer}")
                current_layer = next_layer

        if operation.operation == "M620":  # Tool change
            if filament := operation.params.get("S"):
                if filament == "255" or filament == "65535":
                    logger.debug("Full unload")
                    active_filament = None
                    continue
                if not filament[-1].isdigit():
                    filament = filament[:-1]
                filament = int(filament)
                if filament > 65000:
                    logger.debug(f"Ignoring bogus filament {filament}")
                    continue
                logger.debug(f"Filament change from {active_filament} to {filament}")
                active_filament = filament

        if operation.operation in ["G0", "G1", "G2", "G3"]:  # Extrusion
            if extrusion := operation.params.get("E"):
                if active_filament is None:
                    logger.error("No active filament")
                    continue

                extrusion_position = float(extrusion)
                if relative_extrusion:
                    extrusion_amount = extrusion_position
                else:
                    previous_position = extrusion_positions.get(active_filament, 0)
                    extrusion_amount = extrusion_position - previous_position
                    extrusion_positions[active_filament] = extrusion_position

                if extrusion_amount < 0:
                    retracted_filament[active_filament] = (
                        retracted_filament.get(active_filament, 0) - extrusion_amount
                    )
                    continue

                retracted = retracted_filament.get(active_filament, 0)
                unretracted = min(retracted, extrusion_amount)
                retracted_filament[active_filament] = retracted - unretracted
                extrusion_amount -= unretracted
                if extrusion_amount <= 0:
                    continue

                layer_usage = layer_filaments.setdefault(current_layer, {})
                layer_usage[active_filament] = (
                    layer_usage.get(active_filament, 0) + extrusion_amount
                )
                extrusion_events.setdefault(current_layer, []).append(
                    (
                        operation.line_number,
                        active_object,
                        active_filament,
                        extrusion_amount,
                    )
                )
                if active_object is not None:
                    object_usage = object_filaments.setdefault(
                        current_layer, {}
                    ).setdefault(active_object, {})
                    object_usage[active_filament] = (
                        object_usage.get(active_filament, 0) + extrusion_amount
                    )
    logger.debug("Found {} operations", operation_count)

    scales = _normalize_filament_totals(layer_filaments, slicer_filament_totals)
    _apply_filament_scales(object_filaments, scales)
    _apply_event_scales(extrusion_events, scales)
    return FilamentUsage(layer_filaments, object_filaments, extrusion_events)


def _normalize_filament_totals(layer_filaments, slicer_filament_totals):
    if slicer_filament_totals is None:
        return {}

    filament_ids = sorted(
        {
            filament
            for layer_usage in layer_filaments.values()
            for filament in layer_usage
        }
    )
    if len(filament_ids) != len(slicer_filament_totals):
        logger.warning(
            "Cannot reconcile {} parsed filaments with {} slicer totals",
            len(filament_ids),
            len(slicer_filament_totals),
        )
        return {}

    scales = {}
    for filament, slicer_total in zip(filament_ids, slicer_filament_totals):
        if not math.isfinite(slicer_total) or slicer_total < 0:
            logger.warning(
                "Ignoring invalid slicer filament total for filament {}: {}",
                filament,
                slicer_total,
            )
            continue
        parsed_total = sum(
            layer_usage.get(filament, 0) for layer_usage in layer_filaments.values()
        )
        if parsed_total <= 0:
            continue

        scale = slicer_total / parsed_total
        scales[filament] = scale
        for layer_usage in layer_filaments.values():
            if filament in layer_usage:
                layer_usage[filament] *= scale
    return scales


def _apply_filament_scales(object_filaments, scales):
    for layer_objects in object_filaments.values():
        for object_usage in layer_objects.values():
            for filament, scale in scales.items():
                if filament in object_usage:
                    object_usage[filament] *= scale


def _apply_event_scales(extrusion_events, scales):
    for layer, events in extrusion_events.items():
        extrusion_events[layer] = [
            (line, object_id, filament, length * scales.get(filament, 1))
            for line, object_id, filament, length in events
        ]

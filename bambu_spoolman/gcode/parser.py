import io
import math
import re

from loguru import logger


class GCodeOperation:
    def __init__(self, raw_line):
        self.operation = None
        self.params = {}
        self.comment = None

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

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # logger.debug(f"Parsing line: {line}")
        yield GCodeOperation(line)


def evaluate_gcode(gcode):
    """
    Evaluate the gcode and return the filament usage (in mm) per layer
    """
    current_layer = 0  # The current layer
    current_extrusion = {}  # Running total of extrusion per filament on this layer
    active_filament = None  # The currently active filament
    relative_extrusion = False
    extrusion_positions = {}
    retracted_filament = {}
    slicer_filament_totals = None

    layer_filaments = {}  # Filament usage per layer

    operation_count = 0
    for operation in parse_gcode(gcode):
        operation_count += 1
        if operation.comment:
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

                if current_extrusion:
                    # Layer change, record the filament usage
                    layer_filaments[current_layer] = current_extrusion.copy()
                    current_extrusion = {}

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

                current_extruded = current_extrusion.get(active_filament, 0)
                current_extrusion[active_filament] = current_extruded + extrusion_amount
    logger.debug("Found {} operations", operation_count)
    # Finished
    if current_extrusion:
        layer_filaments[current_layer] = current_extrusion.copy()

    _normalize_filament_totals(layer_filaments, slicer_filament_totals)
    return layer_filaments


def _normalize_filament_totals(layer_filaments, slicer_filament_totals):
    if slicer_filament_totals is None:
        return

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
        return

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
        for layer_usage in layer_filaments.values():
            if filament in layer_usage:
                layer_usage[filament] *= scale

"""Shared resource budgets for printer model downloads and G-code parsing."""

import math
import os
import time
from dataclasses import dataclass, fields


class ModelLimitError(ValueError):
    pass


@dataclass(frozen=True)
class ModelLimits:
    download_bytes: int = 256 * 1024 * 1024
    gcode_bytes: int = 512 * 1024 * 1024
    metadata_bytes: int = 2 * 1024 * 1024
    archive_entries: int = 4096
    gcode_lines: int = 5_000_000
    line_bytes: int = 64 * 1024
    seconds: float = 300

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(
                    f"Model limit {field.name} must be finite and positive"
                )


def get_model_limits():
    values = {}
    for field in fields(ModelLimits):
        value = os.environ.get(f"BAMBU_SPOOLMAN_MODEL_{field.name.upper()}")
        if value is not None:
            values[field.name] = float(value) if field.name == "seconds" else int(value)
    return ModelLimits(**values)


class DownloadBudget:
    def __init__(self, file, limits):
        self.file = file
        self.limits = limits
        self.bytes = 0
        self.started = time.monotonic()

    def write(self, chunk):
        if time.monotonic() - self.started > self.limits.seconds:
            raise ModelLimitError("Model download exceeded its time budget")
        if self.bytes + len(chunk) > self.limits.download_bytes:
            raise ModelLimitError("Model download exceeded its byte budget")
        self.file.write(chunk)
        self.bytes += len(chunk)

"""Own consumption progress and adapt print context to the persisted checkpoint."""

from typing import NamedTuple, Protocol, TypedDict

from bambu_spoolman.broker import checkpoint
from bambu_spoolman.broker.consumption import ConsumptionProgress


class CheckpointContext(TypedDict):
    current_layer: int | None
    ams_mapping_history: list
    active_logical_filament: int | None
    last_active_tray: int | None
    skipped_object_layers: dict
    skipped_object_lines: dict


class ContextSource(Protocol):
    def __call__(self) -> CheckpointContext: ...


class RecoveredPrint(NamedTuple):
    model_path: str
    gcode_file_name: str
    current_layer: int
    spent_layers: set
    spent_filaments: dict
    ams_mapping: list | None
    using_ams: bool
    print_started: bool
    task_id: str | None
    subtask_id: str | None
    print_name: str | None
    ams_mapping_history: list
    active_logical_filament: int | None
    last_active_tray: int | None
    skipped_object_layers: dict
    skipped_object_lines: dict
    spent_segments: dict
    pending_consumption: dict | None

    @property
    def progress(self):
        return ConsumptionProgress(
            set(self.spent_layers),
            {layer: set(values) for layer, values in self.spent_filaments.items()},
            {
                layer: set(values)
                for layer, values in (self.spent_segments or {}).items()
            },
            self.pending_consumption,
        )


class PrintCheckpoint:
    def __init__(self, context: ContextSource):
        self.context = context
        self.progress = ConsumptionProgress()

    def reset_progress(self):
        self.progress = ConsumptionProgress()

    def _progress_fields(self):
        return {
            "spent_layers": set(self.progress.spent_layers),
            "spent_filaments": {
                layer: set(values)
                for layer, values in self.progress.spent_filaments.items()
            },
            "spent_segments": {
                layer: set(values)
                for layer, values in self.progress.spent_segments.items()
            },
            "pending_consumption": self.progress.pending_consumption,
        }

    def save(self, fallback_layer=None):
        context = dict(self.context())
        layer = context.pop("current_layer")
        if layer is None:
            layer = fallback_layer
        fields = self._progress_fields()
        checkpoint.update_layer(
            layer,
            fields.pop("spent_layers"),
            fields.pop("spent_filaments"),
            **context,
            **fields,
        )

    def create(self, model_path, **identity):
        context = dict(self.context(), current_layer=0)
        checkpoint.save_checkpoint(
            model_path=model_path, **context, **self._progress_fields(), **identity
        )

    def recover(self, task_id, subtask_id, print_name=None):
        result = checkpoint.recover_model(task_id, subtask_id, print_name)
        if result is None:
            return None
        # Older checkpoint adapters may omit later fields. Disk migration remains
        # in checkpoint.recover_model, shared with the legacy module interface.
        result = tuple(result) + (None,) * (18 - len(result))
        return RecoveredPrint(*result[:18])

    def mark_started(self):
        checkpoint.mark_print_started()

    def clear(self):
        checkpoint.clear()

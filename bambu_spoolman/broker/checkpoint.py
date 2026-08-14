import json
import os
import shutil
import tempfile

from loguru import logger

from bambu_spoolman.settings import get_configuration_path


def checkpoint_directory():
    path = get_configuration_path("checkpoint")
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def get_checkpoint_metadata():
    metadata_path = os.path.join(checkpoint_directory(), "metadata.json")

    if not os.path.exists(metadata_path):
        return {}
    with open(metadata_path) as f:
        return json.load(f)


def _save_checkpoint_metadata(metadata):
    directory = checkpoint_directory()
    metadata_path = os.path.join(directory, "metadata.json")
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=directory, delete=False
        ) as metadata_file:
            temporary_path = metadata_file.name
            json.dump(metadata, metadata_file)
            metadata_file.flush()
            os.fsync(metadata_file.fileno())
        os.replace(temporary_path, metadata_path)
    finally:
        if temporary_path is not None and os.path.exists(temporary_path):
            os.remove(temporary_path)


def save_checkpoint(
    *,
    model_path,
    current_layer,
    spent_layers,
    spent_filaments,
    task_id,
    subtask_id,
    ams_mapping,
    gcode_file_name,
    using_ams,
    print_started,
    print_name=None,
    ams_mapping_history=None,
    active_logical_filament=None,
    last_active_tray=None,
    skipped_object_layers=None,
    skipped_object_lines=None,
    spent_segments=None,
    pending_consumption=None,
):
    shutil.copy(model_path, os.path.join(checkpoint_directory(), "model.3mf"))

    existing_metadata = get_checkpoint_metadata()
    existing_metadata["task_id"] = task_id
    existing_metadata["subtask_id"] = subtask_id
    existing_metadata["current_layer"] = current_layer
    existing_metadata["spent_layers"] = sorted(spent_layers)
    existing_metadata["spent_filaments"] = {
        str(layer): sorted(filaments) for layer, filaments in spent_filaments.items()
    }
    existing_metadata["ams_mapping"] = ams_mapping
    existing_metadata["gcode_file_name"] = gcode_file_name
    existing_metadata["using_ams"] = using_ams
    existing_metadata["print_started"] = print_started
    existing_metadata["print_name"] = print_name
    existing_metadata["ams_mapping_history"] = ams_mapping_history or []
    existing_metadata["active_logical_filament"] = active_logical_filament
    existing_metadata["last_active_tray"] = last_active_tray
    existing_metadata["skipped_object_layers"] = {
        str(object_id): int(layer)
        for object_id, layer in (skipped_object_layers or {}).items()
    }
    existing_metadata["skipped_object_lines"] = {
        str(object_id): int(line)
        for object_id, line in (skipped_object_lines or {}).items()
    }
    existing_metadata["spent_segments"] = {
        str(layer): sorted(segments)
        for layer, segments in (spent_segments or {}).items()
    }
    existing_metadata["pending_consumption"] = pending_consumption
    _save_checkpoint_metadata(existing_metadata)
    logger.info(
        "event=checkpoint_created task_id={} subtask_id={} layer={} "
        "print_started={} using_ams={}",
        task_id,
        subtask_id,
        current_layer,
        print_started,
        using_ams,
    )


def clear():
    directory = get_configuration_path("checkpoint")
    if os.path.exists(directory):
        shutil.rmtree(directory)
        logger.info("event=checkpoint_cleared")


def update_layer(
    layer,
    spent_layers,
    spent_filaments,
    *,
    ams_mapping_history=None,
    active_logical_filament=None,
    last_active_tray=None,
    skipped_object_layers=None,
    skipped_object_lines=None,
    spent_segments=None,
    pending_consumption=None,
):
    metadata = get_checkpoint_metadata()
    metadata["current_layer"] = layer
    metadata["spent_layers"] = sorted(spent_layers)
    metadata["spent_filaments"] = {
        str(spent_layer): sorted(filaments)
        for spent_layer, filaments in spent_filaments.items()
    }
    if ams_mapping_history is not None:
        metadata["ams_mapping_history"] = ams_mapping_history
    metadata["active_logical_filament"] = active_logical_filament
    metadata["last_active_tray"] = last_active_tray
    if skipped_object_layers is not None:
        metadata["skipped_object_layers"] = {
            str(object_id): int(effective_layer)
            for object_id, effective_layer in skipped_object_layers.items()
        }
    if skipped_object_lines is not None:
        metadata["skipped_object_lines"] = {
            str(object_id): int(line)
            for object_id, line in skipped_object_lines.items()
        }
    if spent_segments is not None:
        metadata["spent_segments"] = {
            str(spent_layer): sorted(segments)
            for spent_layer, segments in spent_segments.items()
        }
    metadata["pending_consumption"] = pending_consumption
    _save_checkpoint_metadata(metadata)
    logger.debug(
        "event=checkpoint_updated layer={} accounted_layers={} "
        "partially_accounted_layers={}",
        layer,
        len(spent_layers),
        len(spent_filaments),
    )


def mark_print_started():
    metadata = get_checkpoint_metadata()
    if not metadata:
        logger.warning("Cannot mark print as started because no checkpoint is saved")
        return

    metadata["print_started"] = True
    _save_checkpoint_metadata(metadata)
    logger.info(
        "event=checkpoint_print_started task_id={} subtask_id={}",
        metadata.get("task_id"),
        metadata.get("subtask_id"),
    )


def recover_model(task_id, subtask_id, print_name=None):
    logger.info(
        "event=checkpoint_recovery_attempt task_id={} subtask_id={}",
        task_id,
        subtask_id,
    )
    metadata = get_checkpoint_metadata()

    if not metadata:
        logger.warning(
            "event=checkpoint_recovery_unavailable task_id={} reason=not_found",
            task_id,
        )
        return None

    checkpoint_task_id = _normalize_identifier(metadata.get("task_id"))
    checkpoint_subtask_id = _normalize_identifier(metadata.get("subtask_id"))
    checkpoint_print_name = _normalize_print_name(metadata.get("print_name"))
    task_id = _normalize_identifier(task_id)
    subtask_id = _normalize_identifier(subtask_id)
    print_name = _normalize_print_name(print_name)

    task_mismatch = (
        task_id is not None
        and checkpoint_task_id is not None
        and checkpoint_task_id != task_id
    )
    subtask_mismatch = (
        subtask_id is not None
        and checkpoint_subtask_id is not None
        and checkpoint_subtask_id != subtask_id
    )
    print_name_mismatch = (
        print_name is not None
        and checkpoint_print_name is not None
        and checkpoint_print_name != print_name
    )
    ambiguous_checkpoint = _is_generic_identifier(
        checkpoint_task_id
    ) and _is_generic_identifier(checkpoint_subtask_id)
    ambiguous_identity_unverified = ambiguous_checkpoint and (
        print_name is None
        or checkpoint_print_name is None
        or checkpoint_print_name != print_name
    )
    if task_mismatch or subtask_mismatch or print_name_mismatch:
        logger.error(
            "Recovered task does not match current task. Expected task id {} "
            "and subtask id {}, got task id {} and subtask id {}",
            checkpoint_task_id,
            checkpoint_subtask_id,
            task_id,
            subtask_id,
        )
        return None
    if ambiguous_identity_unverified:
        logger.error(
            "event=checkpoint_recovery_rejected reason=ambiguous_local_identity "
            "task_id={} subtask_id={} checkpoint_print_name={} print_name={}",
            task_id,
            subtask_id,
            checkpoint_print_name,
            print_name,
        )
        return None
    # Checkpoint is valid, load the model

    model_path = os.path.join(checkpoint_directory(), "model.3mf")

    if not os.path.exists(model_path):
        logger.error("Model file does not exist")
        return None

    current_layer = metadata.get("current_layer")
    spent_layers = metadata.get("spent_layers")
    spent_filaments = {
        int(layer): filaments
        for layer, filaments in metadata.get("spent_filaments", {}).items()
    }
    ams_mapping = metadata.get("ams_mapping")
    gcode_file_name = metadata.get("gcode_file_name")
    using_ams = metadata.get("using_ams")
    # Checkpoints created before lifecycle tracking was added belong to prints
    # that were already being tracked, so preserve their recovery behavior.
    print_started = metadata.get("print_started", True)
    ams_mapping_history = metadata.get("ams_mapping_history") or []
    active_logical_filament = metadata.get("active_logical_filament")
    last_active_tray = metadata.get("last_active_tray")
    skipped_object_layers = {
        int(object_id): int(effective_layer)
        for object_id, effective_layer in metadata.get(
            "skipped_object_layers", {}
        ).items()
    }
    skipped_object_lines = {
        int(object_id): int(line)
        for object_id, line in metadata.get("skipped_object_lines", {}).items()
    }
    spent_segments = {
        int(layer): segments
        for layer, segments in metadata.get("spent_segments", {}).items()
    }
    pending_consumption = metadata.get("pending_consumption")

    if using_ams is None:
        # This is an old checkpoint, we can guess whether AMS was used based on
        # the mapping.
        using_ams = bool(ams_mapping and ams_mapping[0] not in (-1, 255))

    if current_layer is None or gcode_file_name is None:
        logger.error("Checkpoint metadata is incomplete")
        return None

    if spent_layers is None:
        # Checkpoints created before spent_layers was added assumed that every
        # layer through current_layer had been consumed successfully.
        spent_layers = list(range(current_layer + 1))

    return (
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
        checkpoint_print_name,
        ams_mapping_history,
        active_logical_filament,
        last_active_tray,
        skipped_object_layers,
        skipped_object_lines,
        spent_segments,
        pending_consumption,
    )


def _normalize_identifier(identifier):
    if identifier is None or identifier == "":
        return None
    return str(identifier)


def _normalize_print_name(print_name):
    if print_name is None:
        return None
    print_name = str(print_name).strip()
    return print_name or None


def _is_generic_identifier(identifier):
    return identifier in (None, "0")

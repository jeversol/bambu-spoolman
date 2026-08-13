import json
import os
import shutil

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
    with open(os.path.join(checkpoint_directory(), "metadata.json"), "w") as f:
        json.dump(metadata, f)


def save_checkpoint(
    *,
    model_path,
    current_layer,
    spent_layers,
    task_id,
    subtask_id,
    ams_mapping,
    gcode_file_name,
    using_ams,
):
    shutil.copy(model_path, os.path.join(checkpoint_directory(), "model.3mf"))

    existing_metadata = get_checkpoint_metadata()
    existing_metadata["task_id"] = task_id
    existing_metadata["subtask_id"] = subtask_id
    existing_metadata["current_layer"] = current_layer
    existing_metadata["spent_layers"] = sorted(spent_layers)
    existing_metadata["ams_mapping"] = ams_mapping
    existing_metadata["gcode_file_name"] = gcode_file_name
    existing_metadata["using_ams"] = using_ams
    _save_checkpoint_metadata(existing_metadata)


def clear():
    if os.path.exists(checkpoint_directory()):
        logger.debug("Clearing checkpoint")
        shutil.rmtree(checkpoint_directory())


def update_layer(layer, spent_layers):
    metadata = get_checkpoint_metadata()
    metadata["current_layer"] = layer
    metadata["spent_layers"] = sorted(spent_layers)
    _save_checkpoint_metadata(metadata)


def recover_model(task_id, subtask_id):
    logger.info("Attempting to recover task {} subtask {}", task_id, subtask_id)
    metadata = get_checkpoint_metadata()

    if not metadata:
        logger.error("No checkpoint saved")
        return None

    checkpoint_task_id = metadata.get("task_id")
    checkpoint_subtask_id = metadata.get("subtask_id")

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
    if task_mismatch or subtask_mismatch:
        logger.error(
            "Recovered task does not match current task. Expected task id {} and subtask id {}, got task id {} and subtask id {}",
            checkpoint_task_id,
            checkpoint_subtask_id,
            task_id,
            subtask_id,
        )
        return None
    # Checkpoint is valid, load the model

    model_path = os.path.join(checkpoint_directory(), "model.3mf")

    if not os.path.exists(model_path):
        logger.error("Model file does not exist")
        return None

    current_layer = metadata.get("current_layer")
    spent_layers = metadata.get("spent_layers")
    ams_mapping = metadata.get("ams_mapping")
    gcode_file_name = metadata.get("gcode_file_name")
    using_ams = metadata.get("using_ams")

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
        ams_mapping,
        using_ams,
    )

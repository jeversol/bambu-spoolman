import json
import os
import tempfile
import unittest
from unittest.mock import patch

from bambu_spoolman.broker.checkpoint import (
    clear,
    mark_print_started,
    recover_model,
    save_checkpoint,
)


class CheckpointRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)

        self.config_directory = os.path.join(self.temp_directory.name, "config")
        self.model_path = os.path.join(self.temp_directory.name, "source.3mf")
        with open(self.model_path, "wb") as model:
            model.write(b"model")

        environment = patch.dict(
            os.environ, {"BAMBU_SPOOLMAN_CONFIG": self.config_directory}
        )
        environment.start()
        self.addCleanup(environment.stop)

    def save_checkpoint(
        self,
        task_id="task-1",
        subtask_id="subtask-1",
        spent_layers=(0, 1, 3),
        spent_filaments=None,
        print_started=True,
    ):
        if spent_filaments is None:
            spent_filaments = {0: (0,), 1: (0, 1)}
        save_checkpoint(
            model_path=self.model_path,
            current_layer=12,
            spent_layers=spent_layers,
            spent_filaments=spent_filaments,
            task_id=task_id,
            subtask_id=subtask_id,
            ams_mapping=[0, -1, -1, -1],
            gcode_file_name="plate_1.gcode",
            using_ams=True,
            print_started=print_started,
        )

    def test_recovers_when_status_omits_task_identifiers(self):
        self.save_checkpoint()

        result = recover_model(None, None)

        self.assertIsNotNone(result)

    def test_clearing_without_a_checkpoint_does_not_create_its_directory(self):
        checkpoint_path = os.path.join(self.config_directory, "checkpoint")

        clear()

        self.assertFalse(os.path.exists(checkpoint_path))

    def test_recovers_when_checkpoint_also_has_no_task_identifiers(self):
        self.save_checkpoint(task_id=None, subtask_id=None)

        result = recover_model(None, None)

        self.assertIsNotNone(result)

    def test_recovers_when_status_has_blank_task_identifiers(self):
        self.save_checkpoint()

        result = recover_model("", "")

        self.assertIsNotNone(result)

    def test_numeric_and_string_task_identifiers_match(self):
        self.save_checkpoint(task_id=123, subtask_id=456)

        result = recover_model("123", "456")

        self.assertIsNotNone(result)

    def test_rejects_checkpoint_when_supplied_identifiers_conflict(self):
        self.save_checkpoint()

        result = recover_model("another-task", "another-subtask")

        self.assertIsNone(result)

    def test_recovers_exact_spent_layers(self):
        self.save_checkpoint(spent_layers=(0, 2, 5))

        result = recover_model("task-1", "subtask-1")

        self.assertEqual(result[3], [0, 2, 5])

    def test_recovers_exact_spent_filaments(self):
        self.save_checkpoint(spent_filaments={2: (0,), 5: (0, 1)})

        result = recover_model("task-1", "subtask-1")

        self.assertEqual(result[4], {2: [0], 5: [0, 1]})

    def test_recovers_print_lifecycle_state(self):
        self.save_checkpoint(print_started=False)

        result = recover_model("task-1", "subtask-1")

        self.assertFalse(result[7])

    def test_marks_checkpoint_print_as_started(self):
        self.save_checkpoint(print_started=False)

        mark_print_started()
        result = recover_model("task-1", "subtask-1")

        self.assertTrue(result[7])

    def test_legacy_checkpoint_is_treated_as_started(self):
        self.save_checkpoint(print_started=False)
        metadata_path = os.path.join(
            self.config_directory, "checkpoint", "metadata.json"
        )
        with open(metadata_path) as metadata_file:
            metadata = json.load(metadata_file)
        metadata.pop("print_started")
        with open(metadata_path, "w") as metadata_file:
            json.dump(metadata, metadata_file)

        result = recover_model("task-1", "subtask-1")

        self.assertTrue(result[7])

    def test_recovers_legacy_checkpoint_from_current_layer(self):
        self.save_checkpoint()
        metadata_path = os.path.join(
            self.config_directory, "checkpoint", "metadata.json"
        )
        with open(metadata_path) as metadata_file:
            metadata = json.load(metadata_file)
        metadata.pop("spent_layers")
        with open(metadata_path, "w") as metadata_file:
            json.dump(metadata, metadata_file)

        result = recover_model("task-1", "subtask-1")

        self.assertEqual(result[3], list(range(13)))


if __name__ == "__main__":
    unittest.main()

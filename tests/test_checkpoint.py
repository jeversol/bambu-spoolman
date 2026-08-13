import os
import tempfile
import unittest
from unittest.mock import patch

from bambu_spoolman.broker.checkpoint import recover_model, save_checkpoint


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

    def save_checkpoint(self, task_id="task-1", subtask_id="subtask-1"):
        save_checkpoint(
            model_path=self.model_path,
            current_layer=12,
            task_id=task_id,
            subtask_id=subtask_id,
            ams_mapping=[0, -1, -1, -1],
            gcode_file_name="plate_1.gcode",
            using_ams=True,
        )

    def test_recovers_when_status_omits_task_identifiers(self):
        self.save_checkpoint()

        result = recover_model(None, None)

        self.assertIsNotNone(result)

    def test_recovers_when_checkpoint_also_has_no_task_identifiers(self):
        self.save_checkpoint(task_id=None, subtask_id=None)

        result = recover_model(None, None)

        self.assertIsNotNone(result)

    def test_rejects_checkpoint_when_supplied_identifiers_conflict(self):
        self.save_checkpoint()

        result = recover_model("another-task", "another-subtask")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

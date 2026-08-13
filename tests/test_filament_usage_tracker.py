import unittest
from unittest.mock import Mock, call, patch

from bambu_spoolman.broker.filament_usage_tracker import FilamentUsageTracker


class FilamentUsageTrackerLayerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = FilamentUsageTracker.__new__(FilamentUsageTracker)
        self.tracker.active_model = {0: {0: 10}, 1: {0: 20}}
        self.tracker.spent_layers = set()
        self.tracker.spent_filaments = {}
        self.tracker.current_layer = None
        self.tracker._spend_filament_for_layer = Mock()
        self.tracker._spend_filament_for_layer.return_value = True

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    def test_spends_layer_zero_when_it_is_the_first_reported_layer(self, update_layer):
        self.tracker._handle_layer_change(0)

        self.tracker._spend_filament_for_layer.assert_called_once_with(0)
        update_layer.assert_called_once_with(0, {0}, {})

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    def test_spends_layer_one_after_layer_zero(self, update_layer):
        self.tracker.current_layer = 0
        self.tracker.spent_layers = {0}

        self.tracker._handle_layer_change(1)

        self.tracker._spend_filament_for_layer.assert_called_once_with(1)
        update_layer.assert_called_once_with(1, {0, 1}, {})

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    def test_marks_skipped_intermediate_layers_as_spent(self, update_layer):
        self.tracker.active_model = {
            1: {0: 10},
            2: {0: 20},
            3: {0: 30},
            4: {0: 40},
        }
        self.tracker.current_layer = 1
        self.tracker.spent_layers = {1}

        self.tracker._handle_layer_change(4)

        self.assertEqual(self.tracker.spent_layers, {1, 2, 3, 4})
        self.assertEqual(
            self.tracker._spend_filament_for_layer.call_args_list,
            [call(2), call(3), call(4)],
        )
        update_layer.assert_called_once_with(4, {1, 2, 3, 4}, {})

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    def test_retries_a_layer_after_spoolman_update_fails(self, update_layer):
        self.tracker._spend_filament_for_layer.side_effect = [
            RuntimeError("Spoolman unavailable"),
            True,
            True,
        ]

        self.tracker._handle_layer_change(0)
        self.assertNotIn(0, self.tracker.spent_layers)

        self.tracker.current_layer = 0
        self.tracker._handle_layer_change(1)

        self.assertIn(0, self.tracker.spent_layers)
        self.assertIn(1, self.tracker.spent_layers)
        self.assertEqual(
            self.tracker._spend_filament_for_layer.call_args_list,
            [call(0), call(0), call(1)],
        )

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    @patch("bambu_spoolman.broker.filament_usage_tracker.load_settings")
    def test_retries_only_failed_filament_within_layer(
        self, load_settings, update_layer
    ):
        self.tracker.active_model = {0: {0: 10, 1: 20}}
        self.tracker.using_ams = True
        self.tracker.ams_mapping = [0, 1]
        self.tracker.spoolman_client = Mock()
        self.tracker.spoolman_client.consume_spool.side_effect = [
            None,
            RuntimeError("Spoolman unavailable"),
            None,
        ]
        self.tracker._spend_filament_for_layer = (
            FilamentUsageTracker._spend_filament_for_layer.__get__(self.tracker)
        )
        load_settings.return_value = {"trays": {"0": 10, "1": 11}}

        self.tracker._handle_layer_change(0)
        self.assertEqual(self.tracker.spent_filaments, {0: {0}})
        self.assertNotIn(0, self.tracker.spent_layers)

        self.tracker.current_layer = 0
        self.tracker._handle_layer_change(0)

        self.assertEqual(
            self.tracker.spoolman_client.consume_spool.call_args_list,
            [
                call(10, length=10),
                call(11, length=20),
                call(11, length=20),
            ],
        )
        self.assertEqual(self.tracker.spent_filaments, {0: {0, 1}})
        self.assertIn(0, self.tracker.spent_layers)
        self.assertEqual(
            update_layer.call_args_list,
            [
                call(0, set(), {0: {0}}),
                call(0, {0}, {0: {0, 1}}),
            ],
        )

    @patch("bambu_spoolman.broker.filament_usage_tracker.recover_model")
    def test_restores_exact_spent_layers_from_checkpoint(self, recover_model):
        recover_model.return_value = (
            "/config/checkpoint/model.3mf",
            "plate_1.gcode",
            5,
            [0, 2, 5],
            {5: [0, 1]},
            [0, -1, -1, -1],
            True,
        )
        self.tracker._load_model = Mock()

        self.tracker._attempt_print_resume("task-1", "subtask-1")

        self.assertEqual(self.tracker.spent_layers, {0, 2, 5})
        self.assertEqual(self.tracker.spent_filaments, {5: {0, 1}})
        self.assertEqual(self.tracker.current_layer, 5)


if __name__ == "__main__":
    unittest.main()

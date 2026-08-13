import unittest
from unittest.mock import Mock, call, patch

from bambu_spoolman.broker.filament_usage_tracker import FilamentUsageTracker


class FilamentUsageTrackerLayerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = FilamentUsageTracker.__new__(FilamentUsageTracker)
        self.tracker.active_model = {0: {0: 10}, 1: {0: 20}}
        self.tracker.spent_layers = set()
        self.tracker.current_layer = None
        self.tracker._spend_filament_for_layer = Mock()

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    def test_spends_layer_zero_when_it_is_the_first_reported_layer(self, update_layer):
        self.tracker._handle_layer_change(0)

        self.tracker._spend_filament_for_layer.assert_called_once_with(0)
        update_layer.assert_called_once_with(0)

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    def test_spends_layer_one_after_layer_zero(self, update_layer):
        self.tracker.current_layer = 0

        self.tracker._handle_layer_change(1)

        self.tracker._spend_filament_for_layer.assert_called_once_with(1)
        update_layer.assert_called_once_with(1)

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    def test_marks_skipped_intermediate_layers_as_spent(self, update_layer):
        self.tracker.active_model = {
            1: {0: 10},
            2: {0: 20},
            3: {0: 30},
            4: {0: 40},
        }
        self.tracker.current_layer = 1

        self.tracker._handle_layer_change(4)

        self.assertEqual(self.tracker.spent_layers, {2, 3, 4})
        self.assertEqual(
            self.tracker._spend_filament_for_layer.call_args_list,
            [call(2), call(3), call(4)],
        )
        update_layer.assert_called_once_with(4)


if __name__ == "__main__":
    unittest.main()

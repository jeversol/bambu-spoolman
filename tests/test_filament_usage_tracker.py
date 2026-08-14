import os
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import Mock, call, patch

import requests

from bambu_spoolman.broker.filament_usage_tracker import FilamentUsageTracker
from bambu_spoolman.gcode.parser import evaluate_gcode
from bambu_spoolman.settings import EXTERNAL_SPOOL_ID


class FilamentUsageTrackerLayerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = FilamentUsageTracker.__new__(FilamentUsageTracker)
        self.tracker.active_model = {0: {0: 10}, 1: {0: 20}}
        self.tracker.spent_layers = set()
        self.tracker.spent_filaments = {}
        self.tracker.current_layer = None
        self.tracker.print_started = True
        self.tracker.task_id = "task-1"
        self.tracker.subtask_id = "subtask-1"
        self.tracker._spend_filament_for_layer = Mock()
        self.tracker._spend_filament_for_layer.return_value = True

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    def test_spends_layer_zero_when_layer_one_starts(self, update_layer):
        self.tracker._handle_layer_change(1)

        self.tracker._spend_filament_for_layer.assert_called_once_with(0)
        update_layer.assert_called_once_with(1, {0}, {})

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    def test_spends_layer_one_after_layer_zero(self, update_layer):
        self.tracker.current_layer = 1
        self.tracker.spent_layers = {0}

        self.tracker._handle_layer_change(2)

        self.tracker._spend_filament_for_layer.assert_called_once_with(1)
        update_layer.assert_called_once_with(2, {0, 1}, {})

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

        self.assertEqual(self.tracker.spent_layers, {1, 2, 3})
        self.assertEqual(
            self.tracker._spend_filament_for_layer.call_args_list,
            [call(2), call(3)],
        )
        update_layer.assert_called_once_with(4, {1, 2, 3}, {})

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    def test_retries_a_layer_after_spoolman_update_fails(self, update_layer):
        self.tracker._spend_filament_for_layer.side_effect = [
            RuntimeError("Spoolman unavailable"),
            True,
            True,
        ]

        self.tracker._handle_layer_change(1)
        self.assertNotIn(0, self.tracker.spent_layers)

        self.tracker.spoolman_retry_at = 0
        self.tracker.current_layer = 2
        self.tracker._handle_layer_change(2)

        self.assertIn(0, self.tracker.spent_layers)
        self.assertIn(1, self.tracker.spent_layers)
        self.assertEqual(
            self.tracker._spend_filament_for_layer.call_args_list,
            [call(0), call(0), call(1)],
        )

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    def test_outage_attempts_only_one_pending_layer_before_backoff(self, update_layer):
        self.tracker.active_model = {layer: {0: 1} for layer in range(100)}
        self.tracker._spend_filament_for_layer.side_effect = RuntimeError(
            "Spoolman unavailable"
        )

        self.tracker._handle_layer_change(100)
        self.tracker._handle_layer_change(101)

        self.tracker._spend_filament_for_layer.assert_called_once_with(0)
        self.assertGreater(self.tracker.spoolman_retry_at, 0)

    @patch("bambu_spoolman.broker.filament_usage_tracker.threading.Timer")
    def test_retry_timer_progresses_without_another_mqtt_status(self, timer):
        retry_timer = Mock()
        timer.return_value = retry_timer
        self.tracker._lock = threading.RLock()
        self.tracker._retry_timer = None
        self.tracker.gcode_state = "RUNNING"
        self.tracker.current_layer = 2
        self.tracker._handle_layer_change = Mock()

        self.tracker._schedule_spoolman_retry(0, "unavailable")
        timer_callback = timer.call_args.args[1]
        timer_callback()

        retry_timer.start.assert_called_once_with()
        self.tracker._handle_layer_change.assert_called_once_with(2)

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

        self.tracker.current_layer = 1
        self.tracker._handle_layer_change(1)
        self.assertEqual(self.tracker.spent_filaments, {0: {0}})
        self.assertNotIn(0, self.tracker.spent_layers)

        self.tracker.spoolman_retry_at = 0
        self.tracker.current_layer = 1
        self.tracker._handle_layer_change(1)

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
                call(1, set(), {0: {0}}),
                call(1, set(), {0: {0}}),
                call(1, set(), {0: {0, 1}}),
                call(1, {0}, {0: {0, 1}}),
            ],
        )

    @patch("bambu_spoolman.broker.filament_usage_tracker.clear_checkpoint")
    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    def test_print_end_keeps_checkpoint_when_usage_remains(
        self, update_layer, clear_checkpoint
    ):
        self.tracker._spend_filament_for_layer.side_effect = RuntimeError(
            "Spoolman unavailable"
        )

        completed = self.tracker._handle_print_end()

        self.assertFalse(completed)
        self.assertIsNotNone(self.tracker.active_model)
        self.assertEqual(self.tracker.spent_layers, set())
        clear_checkpoint.assert_not_called()

    @patch("bambu_spoolman.broker.filament_usage_tracker.clear_checkpoint")
    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    def test_retries_finish_status_until_all_usage_succeeds(
        self, update_layer, clear_checkpoint
    ):
        self.tracker.gcode_state = "RUNNING"
        self.tracker.ams_mapping = None
        self.tracker.using_ams = False
        self.tracker._spend_filament_for_layer.side_effect = [
            RuntimeError("Spoolman unavailable"),
            True,
            True,
        ]
        finish_message = {"print": {"command": "push_status", "gcode_state": "FINISH"}}

        self.tracker.on_message(None, finish_message)
        self.assertIsNotNone(self.tracker.active_model)
        clear_checkpoint.assert_not_called()

        self.tracker.spoolman_retry_at = 0
        self.tracker.on_message(None, finish_message)

        self.assertIsNone(self.tracker.active_model)
        self.assertEqual(self.tracker.spent_layers, set())
        clear_checkpoint.assert_called_once_with()
        self.assertEqual(
            self.tracker._spend_filament_for_layer.call_args_list,
            [call(0), call(0), call(1)],
        )

    def test_stale_finish_does_not_consume_or_end_new_print(self):
        self.tracker.gcode_state = "FINISH"
        self.tracker.current_layer = 0
        self.tracker.print_started = False
        self.tracker._handle_layer_change = Mock()
        self.tracker._handle_print_end = Mock()
        stale_finish = {
            "print": {
                "command": "push_status",
                "gcode_state": "FINISH",
                "layer_num": 200,
            }
        }

        self.tracker.on_message(None, stale_finish)

        self.tracker._handle_layer_change.assert_not_called()
        self.tracker._handle_print_end.assert_not_called()
        self.assertEqual(self.tracker.current_layer, 0)
        self.assertFalse(self.tracker.print_started)

    def test_prepare_status_does_not_consume_new_print_layers(self):
        self.tracker.gcode_state = "FINISH"
        self.tracker.current_layer = 0
        self.tracker.print_started = False
        self.tracker._handle_layer_change = Mock()
        prepare = {
            "print": {
                "command": "push_status",
                "gcode_state": "PREPARE",
                "layer_num": 200,
            }
        }

        self.tracker.on_message(None, prepare)

        self.tracker._handle_layer_change.assert_not_called()
        self.assertEqual(self.tracker.current_layer, 0)
        self.assertFalse(self.tracker.print_started)

    @patch("bambu_spoolman.broker.filament_usage_tracker.mark_print_started")
    def test_running_status_activates_new_print_before_consuming(self, mark_started):
        self.tracker.gcode_state = "PREPARE"
        self.tracker.current_layer = 0
        self.tracker.print_started = False
        self.tracker._handle_layer_change = Mock()
        running = {
            "print": {
                "command": "push_status",
                "gcode_state": "RUNNING",
                "layer_num": 1,
            }
        }

        self.tracker.on_message(None, running)

        mark_started.assert_called_once_with()
        self.assertTrue(self.tracker.print_started)
        self.tracker._handle_layer_change.assert_called_once_with(1)
        self.assertEqual(self.tracker.current_layer, 1)

    def test_finish_is_handled_after_new_print_becomes_active(self):
        self.tracker.gcode_state = "RUNNING"
        self.tracker.print_started = True
        self.tracker._handle_print_end = Mock()
        finish = {"print": {"command": "push_status", "gcode_state": "FINISH"}}

        self.tracker.on_message(None, finish)

        self.tracker._handle_print_end.assert_called_once_with()

    def test_failed_state_clears_active_print_without_spending_current_layer(self):
        self.tracker.gcode_state = "RUNNING"
        self.tracker.print_started = True
        self.tracker._handle_print_failure = Mock()
        failed = {"print": {"command": "push_status", "gcode_state": "FAILED"}}

        self.tracker.on_message(None, failed)

        self.tracker._handle_print_failure.assert_called_once_with("gcode_state=FAILED")

    def test_cancellation_error_clears_active_print(self):
        self.tracker.gcode_state = "RUNNING"
        self.tracker.print_started = True
        self.tracker._handle_print_failure = Mock()
        canceled = {
            "print": {
                "command": "push_status",
                "print_error": "50348044",
            }
        }

        self.tracker.on_message(None, canceled)

        self.tracker._handle_print_failure.assert_called_once_with(
            "print_error=50348044"
        )

    def test_duplicate_project_announcement_does_not_reset_tracking(self):
        self.tracker.gcode_state = None
        self.tracker.task_id = "task-1"
        self.tracker.subtask_id = "subtask-1"
        self.tracker._handle_print_start = Mock()

        self.tracker.on_message(
            None,
            {
                "print": {
                    "command": "project_file",
                    "task_id": "task-1",
                    "subtask_id": "subtask-1",
                }
            },
        )

        self.tracker._handle_print_start.assert_not_called()

    def test_generic_local_ids_do_not_make_a_different_print_a_duplicate(self):
        self.tracker.task_id = "0"
        self.tracker.subtask_id = "0"
        self.tracker.print_name = "Old Print"

        current = self.tracker._is_current_print(
            {
                "task_id": "0",
                "subtask_id": "0",
                "subtask_name": "New Print",
            }
        )

        self.assertFalse(current)

    def test_invalid_layer_number_is_ignored(self):
        self.tracker.gcode_state = "RUNNING"
        self.tracker.current_layer = 2
        self.tracker._handle_layer_change = Mock()

        self.tracker.on_message(
            None,
            {
                "print": {
                    "command": "push_status",
                    "layer_num": "not-a-number",
                }
            },
        )

        self.tracker._handle_layer_change.assert_not_called()
        self.assertEqual(self.tracker.current_layer, 2)

    def test_recovers_pending_checkpoint_when_starting_in_finish_state(self):
        self.tracker.active_model = None
        self.tracker.gcode_state = None
        self.tracker._attempt_print_resume = Mock()
        self.tracker._attempt_print_resume.side_effect = lambda *_: setattr(
            self.tracker, "active_model", {0: {0: 10}}
        )
        self.tracker._handle_print_end = Mock()
        finish_message = {
            "print": {
                "command": "push_status",
                "gcode_state": "FINISH",
                "task_id": "task-1",
                "subtask_id": "subtask-1",
            }
        }

        self.tracker.on_message(None, finish_message)

        self.tracker._attempt_print_resume.assert_called_once_with(
            "task-1", "subtask-1", None
        )
        self.tracker._handle_print_end.assert_called_once_with()

    def test_retries_recovery_when_later_delta_supplies_local_print_name(self):
        self.tracker.active_model = None
        self.tracker.gcode_state = None
        self.tracker._attempt_print_resume = Mock()

        self.tracker.on_message(
            None,
            {
                "print": {
                    "command": "push_status",
                    "gcode_state": "RUNNING",
                    "task_id": "0",
                    "subtask_id": "0",
                }
            },
        )
        self.tracker.on_message(
            None,
            {
                "print": {
                    "command": "push_status",
                    "gcode_state": "RUNNING",
                    "task_id": "0",
                    "subtask_id": "0",
                    "subtask_name": "Local Benchy",
                }
            },
        )

        self.assertEqual(
            self.tracker._attempt_print_resume.call_args_list,
            [call("0", "0", None), call("0", "0", "Local Benchy")],
        )

    def test_does_not_finish_recovered_print_that_never_became_active(self):
        self.tracker.active_model = None
        self.tracker.gcode_state = None
        self.tracker._attempt_print_resume = Mock()

        def recover_unstarted_print(*_):
            self.tracker.active_model = {0: {0: 10}}
            self.tracker.print_started = False

        self.tracker._attempt_print_resume.side_effect = recover_unstarted_print
        self.tracker._handle_print_end = Mock()
        finish_message = {
            "print": {
                "command": "push_status",
                "gcode_state": "FINISH",
                "task_id": "task-1",
                "subtask_id": "task-1",
            }
        }

        self.tracker.on_message(None, finish_message)

        self.tracker._attempt_print_resume.assert_called_once_with(
            "task-1", "task-1", None
        )
        self.tracker._handle_print_end.assert_not_called()

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
            True,
            "task-1",
            "subtask-1",
        )
        self.tracker._load_model = Mock()

        self.tracker._attempt_print_resume("task-1", "subtask-1")

        self.assertEqual(self.tracker.spent_layers, {0, 2, 5})
        self.assertEqual(self.tracker.spent_filaments, {5: {0, 1}})
        self.assertEqual(self.tracker.current_layer, 5)

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    @patch("bambu_spoolman.broker.filament_usage_tracker.load_settings")
    def test_auto_refill_uses_new_tray_only_for_later_layers(
        self, load_settings, update_layer
    ):
        self.tracker.active_model = {
            0: {0: 10},
            1: {0: 20},
            2: {0: 30},
        }
        self.tracker.using_ams = True
        self.tracker.ams_mapping = [0]
        self.tracker.ams_mapping_history = [{"layer": 0, "mapping": [0]}]
        self.tracker.active_logical_filament = None
        self.tracker.last_active_tray = None
        self.tracker.skipped_object_layers = {}
        self.tracker.spoolman_client = Mock()
        self.tracker._spend_filament_for_layer = (
            FilamentUsageTracker._spend_filament_for_layer.__get__(self.tracker)
        )
        load_settings.return_value = {"trays": {"0": 10, "1": 11}}

        self.tracker._update_active_ams_tray({"ams": {"tray_now": "0"}}, 0)
        self.tracker._update_active_ams_tray({"ams": {"tray_now": "1"}}, 2)
        self.tracker._spend_filament_for_layer(1)
        self.tracker._spend_filament_for_layer(2)

        self.assertEqual(self.tracker._mapping_for_layer(1), [0])
        self.assertEqual(self.tracker._mapping_for_layer(2), [1])
        self.assertEqual(
            self.tracker.spoolman_client.consume_spool.call_args_list,
            [call(10, length=20), call(11, length=30)],
        )

    def test_line_position_splits_refill_usage_within_a_layer(self):
        self.tracker.active_model = evaluate_gcode(
            """M620 S0
M83
G1 E2
G1 X1
G1 E3
"""
        )
        self.tracker.using_ams = True
        self.tracker.ams_mapping = [1]
        self.tracker.ams_mapping_history = [
            {"layer": 0, "mapping": [0]},
            {"layer": 0, "line": 4, "mapping": [1]},
        ]

        groups = self.tracker._segment_usage_for_layer(
            0,
            self.tracker.active_model.for_layer(0),
            set(),
            {},
        )

        self.assertEqual(groups, {(0, 0): 2.0, (0, 1): 3.0})

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    @patch("bambu_spoolman.broker.filament_usage_tracker.load_settings")
    def test_reconciles_timed_out_consumption_without_duplicate_request(
        self, load_settings, update_layer
    ):
        self.tracker.active_model = {0: {0: 10}}
        self.tracker.using_ams = False
        self.tracker.ams_mapping = None
        self.tracker.ams_mapping_history = []
        self.tracker.active_logical_filament = None
        self.tracker.last_active_tray = None
        self.tracker.skipped_object_layers = {}
        self.tracker.skipped_object_lines = {}
        self.tracker.spent_segments = {}
        self.tracker.pending_consumption = None
        self.tracker._spend_filament_for_layer = (
            FilamentUsageTracker._spend_filament_for_layer.__get__(self.tracker)
        )
        self.tracker.spoolman_client = Mock()
        self.tracker.spoolman_client.get_spool.side_effect = [
            {"used_length": 100},
            {"used_length": 110},
        ]
        self.tracker.spoolman_client.consume_spool.side_effect = TimeoutError(
            "response lost"
        )
        load_settings.return_value = {"trays": {str(EXTERNAL_SPOOL_ID): 99}}

        self.tracker._handle_layer_change(1)
        self.tracker.spoolman_retry_at = 0
        self.tracker._handle_layer_change(1)

        self.tracker.spoolman_client.consume_spool.assert_called_once_with(
            99, length=10
        )
        self.assertIn(0, self.tracker.spent_layers)
        self.assertIsNone(self.tracker.pending_consumption)

    def test_prepare_status_cannot_remap_print_to_previously_loaded_tray(self):
        self.tracker.active_model = {0: {0: 10}}
        self.tracker.using_ams = True
        self.tracker.ams_mapping = [0]
        self.tracker.ams_mapping_history = [{"layer": 0, "mapping": [0]}]
        self.tracker.active_logical_filament = None
        self.tracker.last_active_tray = None
        self.tracker.print_started = False

        changed = self.tracker._update_active_ams_tray({"ams": {"tray_now": "3"}}, 0)

        self.assertFalse(changed)
        self.assertEqual(self.tracker.ams_mapping, [0])
        self.assertEqual(
            self.tracker.ams_mapping_history,
            [{"layer": 0, "mapping": [0]}],
        )

    @patch("bambu_spoolman.broker.filament_usage_tracker.update_layer")
    @patch("bambu_spoolman.broker.filament_usage_tracker.load_settings")
    def test_skipped_object_excludes_only_its_object_extrusion(
        self, load_settings, update_layer
    ):
        self.tracker.active_model = evaluate_gcode(
            """
M620 S0
M83
G1 E2
; start printing object, unique label id: 7
G1 E3
; stop printing object, unique label id: 7
; start printing object, unique label id: 8
G1 E5
; stop printing object, unique label id: 8
"""
        )
        self.tracker.using_ams = False
        self.tracker.ams_mapping = None
        self.tracker.ams_mapping_history = []
        self.tracker.active_logical_filament = None
        self.tracker.last_active_tray = None
        self.tracker.skipped_object_layers = {7: 0}
        self.tracker.spoolman_client = Mock()
        self.tracker._spend_filament_for_layer = (
            FilamentUsageTracker._spend_filament_for_layer.__get__(self.tracker)
        )
        load_settings.return_value = {"trays": {str(EXTERNAL_SPOOL_ID): 99}}

        spent = self.tracker._spend_filament_for_layer(0)

        self.assertTrue(spent)
        self.tracker.spoolman_client.consume_spool.assert_called_once_with(
            99, length=7.0
        )

    @patch("bambu_spoolman.broker.filament_usage_tracker.requests.get")
    def test_model_download_uses_configured_timeout(self, get):
        get.return_value.iter_content.return_value = [b"model"]

        with patch.dict(os.environ, {"BAMBU_SPOOLMAN_HTTP_TIMEOUT": "12.5"}):
            model_path = self.tracker._download_model("http://printer/model.3mf")

        self.addCleanup(os.remove, model_path)
        get.assert_called_once_with(
            "http://printer/model.3mf", timeout=12.5, stream=True
        )

    @patch("bambu_spoolman.broker.filament_usage_tracker.requests.get")
    def test_model_download_writes_streamed_chunks(self, get):
        get.return_value.iter_content.return_value = [b"first", b"", b"second"]

        model_path = self.tracker._download_model("http://printer/model.3mf")

        self.addCleanup(os.remove, model_path)
        with open(model_path, "rb") as model_file:
            self.assertEqual(model_file.read(), b"firstsecond")
        get.return_value.raise_for_status.assert_called_once_with()
        get.return_value.iter_content.assert_called_once_with(chunk_size=64 * 1024)
        get.return_value.close.assert_called_once_with()

    @patch("bambu_spoolman.broker.filament_usage_tracker.requests.get")
    def test_model_download_removes_partial_file_after_stream_error(self, get):
        created_files = []
        real_named_temporary_file = tempfile.NamedTemporaryFile

        def track_temporary_file(*args, **kwargs):
            temporary_file = real_named_temporary_file(*args, **kwargs)
            created_files.append(temporary_file.name)
            return temporary_file

        get.return_value.iter_content.side_effect = (
            requests.exceptions.ChunkedEncodingError("connection interrupted")
        )

        with patch(
            "bambu_spoolman.broker.filament_usage_tracker.tempfile.NamedTemporaryFile",
            side_effect=track_temporary_file,
        ):
            model_path = self.tracker._download_model("http://printer/model.3mf")

        self.assertIsNone(model_path)
        self.assertEqual(len(created_files), 1)
        self.assertFalse(os.path.exists(created_files[0]))
        get.return_value.close.assert_called_once_with()

    def test_load_model_rejects_invalid_utf8_gcode(self):
        archive_file = tempfile.NamedTemporaryFile(suffix=".3mf", delete=False)
        archive_file.close()
        self.addCleanup(os.remove, archive_file.name)
        with zipfile.ZipFile(archive_file.name, "w") as archive:
            archive.writestr("Metadata/plate.gcode", b"M620 S0\nG1 E\xff\n")

        loaded = self.tracker._load_model(archive_file.name, "Metadata/plate.gcode")

        self.assertFalse(loaded)
        self.assertIsNone(self.tracker.active_model)

    def test_load_model_rejects_malformed_gcode(self):
        archive_file = tempfile.NamedTemporaryFile(suffix=".3mf", delete=False)
        archive_file.close()
        self.addCleanup(os.remove, archive_file.name)
        with zipfile.ZipFile(archive_file.name, "w") as archive:
            archive.writestr("Metadata/plate.gcode", "M620 S0\nM73 Lnot-a-layer\n")

        loaded = self.tracker._load_model(archive_file.name, "Metadata/plate.gcode")

        self.assertFalse(loaded)
        self.assertIsNone(self.tracker.active_model)

    @patch("bambu_spoolman.broker.filament_usage_tracker.save_checkpoint")
    @patch("bambu_spoolman.broker.filament_usage_tracker.clear_checkpoint")
    def test_print_start_does_not_checkpoint_invalid_model(
        self, clear_checkpoint, save_checkpoint
    ):
        model_file = tempfile.NamedTemporaryFile(suffix=".3mf", delete=False)
        model_file.close()
        self.tracker._retrieve_model = Mock(return_value=model_file.name)
        self.tracker._load_model = Mock(return_value=False)

        self.tracker._handle_print_start({"url": "file:///model.3mf", "use_ams": False})

        save_checkpoint.assert_not_called()
        self.assertFalse(os.path.exists(model_file.name))

    @patch("bambu_spoolman.broker.filament_usage_tracker.save_checkpoint")
    @patch("bambu_spoolman.broker.filament_usage_tracker.clear_checkpoint")
    def test_print_start_checkpoint_waits_for_active_state(
        self, clear_checkpoint, save_checkpoint
    ):
        model_file = tempfile.NamedTemporaryFile(suffix=".3mf", delete=False)
        model_file.close()
        self.tracker.gcode_state = "FINISH"
        self.tracker._retrieve_model = Mock(return_value=model_file.name)
        self.tracker._load_model = Mock(return_value=True)
        self.tracker._handle_layer_change = Mock()

        self.tracker._handle_print_start(
            {
                "url": "file:///model.3mf",
                "param": "plate_1.gcode",
                "use_ams": False,
            }
        )

        self.assertFalse(self.tracker.print_started)
        self.assertFalse(save_checkpoint.call_args.kwargs["print_started"])


if __name__ == "__main__":
    unittest.main()

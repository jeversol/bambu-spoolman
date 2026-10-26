import concurrent.futures
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from bambu_spoolman.broker.checkpoint import get_checkpoint_metadata
from bambu_spoolman.broker.filament_usage_tracker import FilamentUsageTracker


class TrackerShutdownTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, BAMBU_SPOOLMAN_CONFIG=directory.name)
        environment.start()
        self.addCleanup(environment.stop)

    def make_tracker(self):
        with patch("bambu_spoolman.broker.filament_usage_tracker.new_client"):
            tracker = FilamentUsageTracker()
        tracker.spoolman_client.endpoint = "http://spoolman.test"
        self.addCleanup(tracker.stop)
        return tracker

    def test_close_cancels_and_joins_real_pending_timer_without_clearing_progress(self):
        tracker = self.make_tracker()
        tracker.active_model = {0: {0: 10}}
        tracker.checkpoint.progress.pending_consumption = {"spool_id": 42, "length": 10}
        tracker.checkpoint.progress.spent_segments = {0: {"0:0"}}
        tracker.spoolman_retry_delay = 60
        with tracker._lock:
            tracker._schedule_spoolman_retry(0, "offline")
        timer = tracker._retry_timer
        with patch("bambu_spoolman.broker.checkpoint.clear") as clear:
            self.assertTrue(tracker.close(timeout=2))
            self.assertTrue(tracker.close(timeout=2))
        self.assertFalse(timer.is_alive())
        self.assertEqual(
            tracker.checkpoint.progress.pending_consumption,
            {"spool_id": 42, "length": 10},
        )
        self.assertEqual(tracker.checkpoint.progress.spent_segments, {0: {"0:0"}})
        clear.assert_not_called()
        tracker.spoolman_client.consume_spool.assert_not_called()

    @patch("bambu_spoolman.broker.filament_usage_tracker.threading.Timer")
    def test_replaced_callback_cannot_consume_or_detach_current_timer(self, timer):
        tracker = self.make_tracker()
        tracker.active_model = {0: {0: 10}}
        tracker.print_started = True
        tracker.gcode_state = "RUNNING"
        tracker.current_layer = 1
        tracker._handle_layer_change = Mock()
        timer.side_effect = [Mock(), Mock()]
        with tracker._lock:
            tracker._schedule_spoolman_retry(0, "first failure")
            old_callback = timer.call_args.args[1]
            tracker._schedule_spoolman_retry(0, "second failure")
            current_callback = timer.call_args.args[1]
        current_timer = tracker._retry_timer
        old_callback()
        self.assertIs(tracker._retry_timer, current_timer)
        tracker._handle_layer_change.assert_not_called()
        current_callback()
        tracker._handle_layer_change.assert_called_once_with(1)

    @patch("bambu_spoolman.broker.filament_usage_tracker.threading.Timer")
    def test_reset_invalidates_retry_from_previous_print(self, timer):
        tracker = self.make_tracker()
        tracker.active_model = {0: {0: 10}}
        tracker.print_started = True
        tracker.current_layer = 1
        tracker._handle_layer_change = Mock()
        with tracker._lock:
            tracker._schedule_spoolman_retry(0, "old print")
            callback = timer.call_args.args[1]
            tracker._reset_spoolman_retry()
        callback()
        tracker._handle_layer_change.assert_not_called()

    def test_stop_rejects_real_timer_already_waiting_for_tracker_lock(self):
        tracker = self.make_tracker()
        tracker.active_model = {0: {0: 10}}
        tracker.print_started = True
        tracker.current_layer = 1
        tracker._handle_layer_change = Mock()
        entered = threading.Event()
        retry = tracker._retry_pending_usage

        def waiting_callback(generation):
            entered.set()
            retry(generation)

        with patch.object(
            tracker, "_retry_pending_usage", side_effect=waiting_callback
        ):
            with tracker._lock:
                tracker.spoolman_retry_delay = 0
                tracker._schedule_spoolman_retry(0, "offline")
                self.assertTrue(entered.wait(5))
                tracker.stop()
            self.assertTrue(tracker.close(timeout=2))
        tracker._handle_layer_change.assert_not_called()
        tracker.spoolman_client.consume_spool.assert_not_called()

    def test_close_timeout_is_explicit_and_queued_messages_are_rejected(self):
        tracker = self.make_tracker()
        entered, release = threading.Event(), threading.Event()

        def callback(*args):
            entered.set()
            release.wait(5)

        tracker._on_message = Mock(side_effect=callback)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(tracker.on_message, None, {})
            try:
                self.assertTrue(entered.wait(5))
                self.assertFalse(tracker.close(timeout=0))
                tracker.on_message(None, {})
            finally:
                release.set()
                work.result(timeout=5)
        self.assertTrue(tracker.close(timeout=2))
        tracker._on_message.assert_called_once_with(None, {})

    @patch("bambu_spoolman.broker.filament_usage_tracker.load_settings")
    def run_segment_during_stop(self, settings, *, timeout=False):
        settings.return_value = {"trays": {"0": 42, "1": 43}}
        tracker = self.make_tracker()
        tracker.active_model = {0: {0: 10, 1: 20}}
        tracker.using_ams = True
        tracker.ams_mapping = [0, 1]
        tracker.print_started = True
        tracker.spoolman_client.get_spool.return_value = {"used_length": 100}
        entered, release = threading.Event(), threading.Event()

        def consume(*args, **kwargs):
            entered.set()
            release.wait(5)
            if timeout:
                raise TimeoutError("response lost")

        tracker.spoolman_client.consume_spool.side_effect = consume
        tracker._on_message = lambda *args: tracker._handle_layer_change(1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(tracker.on_message, None, {})
            try:
                self.assertTrue(entered.wait(5))
                tracker.stop()
            finally:
                release.set()
                work.result(timeout=5)
        self.assertTrue(tracker.close(timeout=2))
        tracker.spoolman_client.consume_spool.assert_called_once_with(42, length=10)
        metadata = get_checkpoint_metadata()
        if timeout:
            self.assertEqual(metadata["pending_consumption"]["segment_key"], "0:0")
            self.assertEqual(
                metadata["pending_consumption"]["baseline_used_length"], 100
            )
            self.assertEqual(metadata["pending_consumption"]["length"], 10)
            self.assertEqual(metadata["spent_segments"], {"0": []})
        else:
            self.assertEqual(metadata["spent_segments"], {"0": ["0:0"]})
            self.assertIsNone(metadata["pending_consumption"])
        self.assertEqual(metadata["spent_layers"], [])
        self.assertNotIn(0, tracker.checkpoint.progress.spent_layers)
        self.assertIsNone(tracker._retry_timer)

    def test_stop_finishes_current_segment_checkpoint_without_starting_next(self):
        self.run_segment_during_stop()

    def test_stop_retains_timed_out_consumption_intent_on_disk(self):
        self.run_segment_during_stop(timeout=True)

    def test_stopped_tracker_cannot_schedule_new_retries(self):
        tracker = self.make_tracker()
        tracker.stop()
        with patch(
            "bambu_spoolman.broker.filament_usage_tracker.threading.Timer"
        ) as timer:
            tracker._schedule_spoolman_retry(0, "late failure")
        timer.assert_not_called()

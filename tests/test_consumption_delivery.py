import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from bambu_spoolman.broker import checkpoint
from bambu_spoolman.broker.consumption import ConsumptionDelivery
from bambu_spoolman.broker.print_checkpoint import PrintCheckpoint


class Spools:
    def __init__(self):
        self.used = {42: 100, 43: 200}
        self.requests = []
        self.before_send = lambda spool: None
        self.fail_before = None
        self.fail_after = None

    def get_spool(self, spool):
        return {"used_length": self.used[spool]}

    def consume_spool(self, spool, *, length):
        self.before_send(spool)
        self.requests.append((spool, length))
        if self.fail_before == spool:
            raise TimeoutError("request not applied")
        self.used[spool] += length
        if self.fail_after == spool:
            raise TimeoutError("response lost")


class ConsumptionDeliveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, BAMBU_SPOOLMAN_CONFIG=directory.name)
        environment.start()
        self.addCleanup(environment.stop)
        self.model = Path(directory.name) / "source.3mf"
        self.model.write_bytes(b"checkpoint model fixture")
        self.context = {
            "current_layer": 1,
            "ams_mapping_history": [{"layer": 0, "mapping": [0, 1]}],
            "active_logical_filament": None,
            "last_active_tray": None,
            "skipped_object_layers": {},
            "skipped_object_lines": {},
        }
        self.journal = PrintCheckpoint(lambda: self.context)
        self.journal.create(
            str(self.model),
            task_id="task",
            subtask_id="subtask",
            ams_mapping=[0, 1],
            gcode_file_name="plate.gcode",
            using_ams=True,
            print_started=True,
            print_name="Fixture",
        )
        self.client = Spools()
        self.plan = {(0, 0): 10, (1, 1): 20}
        self.trays = {"0": 42, "1": 43}

    def deliver(self, journal=None, plan=None, **kwargs):
        return ConsumptionDelivery(
            self.client, journal or self.journal, **kwargs
        ).deliver_layer(0, self.plan if plan is None else plan, self.trays)

    def restart(self):
        journal = PrintCheckpoint(lambda: self.context)
        recovered = journal.recover("task", "subtask", "Fixture")
        self.assertIsNotNone(recovered)
        journal.progress = recovered.progress
        return journal

    def test_intent_is_on_disk_before_each_request_and_acknowledgments_survive_recovery(
        self,
    ):
        def inspect(spool):
            metadata = checkpoint.get_checkpoint_metadata()
            self.assertEqual(metadata["pending_consumption"]["spool_id"], spool)
            self.assertEqual(
                metadata["pending_consumption"]["baseline_used_length"],
                self.client.used[spool],
            )

        self.client.before_send = inspect
        self.assertTrue(self.deliver())
        recovered = self.restart()
        self.assertEqual(recovered.progress.spent_segments, {0: {"0:0", "1:1"}})
        self.assertEqual(recovered.progress.spent_filaments, {0: {0, 1}})
        self.assertIsNone(recovered.progress.pending_consumption)
        self.assertTrue(self.deliver(recovered))
        self.assertEqual(self.client.requests, [(42, 10), (43, 20)])

    def test_partial_failure_recovery_does_not_repeat_successful_segment(self):
        self.client.fail_before = 43
        with self.assertRaises(TimeoutError):
            self.deliver()
        recovered = self.restart()
        self.assertEqual(recovered.progress.spent_segments, {0: {"0:0"}})
        self.assertEqual(recovered.progress.pending_consumption["spool_id"], 43)
        self.client.fail_before = None
        self.assertTrue(self.deliver(recovered))
        self.assertEqual(self.client.requests, [(42, 10), (43, 20), (43, 20)])
        self.assertEqual(self.client.used, {42: 110, 43: 220})

    def test_existing_timeout_policy_reconciles_applied_request_after_restart(self):
        self.client.fail_after = 42
        with self.assertRaises(TimeoutError):
            self.deliver(plan={(0, 0): 10})
        self.assertTrue(self.deliver(self.restart(), plan={(0, 0): 10}))
        self.assertEqual(self.client.requests, [(42, 10)])
        self.assertIsNone(checkpoint.get_checkpoint_metadata()["pending_consumption"])

    def test_failed_intent_save_never_sends_consumption(self):
        with patch(
            "bambu_spoolman.broker.checkpoint.os.replace",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(OSError):
                self.deliver()
        self.assertEqual(self.client.requests, [])

    def test_failed_acknowledgment_restores_partial_filament_and_pending_intent(self):
        save = checkpoint._save_checkpoint_metadata

        def fail_last_ack(metadata):
            if metadata["spent_segments"].get("0") == ["0:0", "0:1"]:
                raise OSError("acknowledgment failed")
            save(metadata)

        with patch(
            "bambu_spoolman.broker.checkpoint._save_checkpoint_metadata",
            side_effect=fail_last_ack,
        ):
            with self.assertRaises(OSError):
                self.deliver(plan={(0, 0): 10, (0, 1): 20})
        self.assertEqual(self.journal.progress.spent_segments, {0: {"0:0"}})
        self.assertEqual(self.journal.progress.spent_filaments, {0: set()})
        self.assertEqual(
            self.journal.progress.pending_consumption["segment_key"], "0:1"
        )
        self.assertEqual(
            checkpoint.get_checkpoint_metadata()["pending_consumption"]["segment_key"],
            "0:1",
        )
        self.assertTrue(self.deliver(self.restart(), plan={(0, 0): 10, (0, 1): 20}))
        self.assertEqual(self.client.requests, [(42, 10), (43, 20)])

    def test_failed_reconciliation_save_keeps_intent_until_persisted(self):
        self.client.fail_after = 42
        with self.assertRaises(TimeoutError):
            self.deliver(plan={(0, 0): 10})
        recovered = self.restart()
        with patch(
            "bambu_spoolman.broker.checkpoint.os.replace",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(OSError):
                self.deliver(recovered, plan={(0, 0): 10})
        self.assertIsNotNone(recovered.progress.pending_consumption)
        self.assertEqual(recovered.progress.spent_segments, {0: set()})
        self.assertTrue(self.deliver(recovered, plan={(0, 0): 10}))
        self.assertEqual(self.client.requests, [(42, 10)])

    def test_stop_finishes_active_acknowledgment_and_leaves_next_segment_unsent(self):
        stopping = threading.Event()
        self.client.before_send = lambda spool: stopping.set()
        self.assertFalse(self.deliver(stopped=stopping.is_set))
        self.assertEqual(self.client.requests, [(42, 10)])
        metadata = checkpoint.get_checkpoint_metadata()
        self.assertEqual(metadata["spent_segments"], {"0": ["0:0"]})
        self.assertIsNone(metadata["pending_consumption"])

    def test_stopped_delivery_performs_no_reconciliation_reads(self):
        self.journal.progress.pending_consumption = {"layer": 0}
        with patch.object(self.client, "get_spool") as read:
            self.assertFalse(self.deliver(stopped=lambda: True))
            read.assert_not_called()

    def test_recovery_rejects_another_print_and_preserves_its_checkpoint(self):
        self.assertIsNone(self.journal.recover("different", "subtask", "Fixture"))
        self.assertEqual(checkpoint.get_checkpoint_metadata()["task_id"], "task")
        self.assertEqual(
            Path(checkpoint.checkpoint_directory(), "model.3mf").read_bytes(),
            self.model.read_bytes(),
        )

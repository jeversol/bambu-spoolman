import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from bambu_spoolman.broker import checkpoint
from bambu_spoolman.broker.consumption import ConsumptionDelivery
from bambu_spoolman.broker.consumption_recovery import (
    accounting_status,
    resolve_consumption,
)
from bambu_spoolman.broker.consumption_state import AmbiguousConsumption
from bambu_spoolman.broker.print_checkpoint import PrintCheckpoint


class Spools:
    endpoint = "http://spoolman.test"

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
        with self.assertRaises(AmbiguousConsumption):
            self.deliver()
        recovered = self.restart()
        self.assertEqual(recovered.progress.spent_segments, {0: {"0:0"}})
        self.assertEqual(recovered.progress.pending_consumption["spool_id"], 43)
        self.client.fail_before = None
        with self.assertRaises(AmbiguousConsumption):
            self.deliver(recovered)
        status = accounting_status(self.client.endpoint)
        resolve_consumption(
            status["pending"]["intentId"],
            43,
            "not-applied",
            "Independent fixture evidence: request did not apply",
            self.client.endpoint,
        )
        self.assertTrue(self.deliver(self.restart()))
        self.assertEqual(self.client.requests, [(42, 10), (43, 20), (43, 20)])
        self.assertEqual(self.client.used, {42: 110, 43: 220})

    def test_applied_timeout_requires_explicit_confirmation_after_restart(self):
        self.client.fail_after = 42
        with self.assertRaises(AmbiguousConsumption):
            self.deliver(plan={(0, 0): 10})
        with self.assertRaises(AmbiguousConsumption):
            self.deliver(self.restart(), plan={(0, 0): 10})
        status = accounting_status(self.client.endpoint)
        resolve_consumption(
            status["pending"]["intentId"],
            42,
            "applied",
            "Fixture transaction committed",
            self.client.endpoint,
        )
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
        with self.assertRaises(AmbiguousConsumption):
            self.deliver(self.restart(), plan={(0, 0): 10, (0, 1): 20})
        # The still-running process observed success and retained that fact.
        self.assertTrue(self.deliver(self.journal, plan={(0, 0): 10, (0, 1): 20}))
        self.assertEqual(self.client.requests, [(42, 10), (43, 20)])

    def test_failed_reconciliation_save_keeps_confirmed_intent_until_persisted(self):
        save = checkpoint._save_checkpoint_metadata

        def fail_ack(metadata):
            if metadata.get("pending_consumption") is None:
                raise OSError("ack failed")
            save(metadata)

        with patch(
            "bambu_spoolman.broker.checkpoint._save_checkpoint_metadata",
            side_effect=fail_ack,
        ):
            with self.assertRaises(OSError):
                self.deliver(plan={(0, 0): 10})
        self.journal.save(0)  # Persist the observed response before restart.
        recovered = self.restart()
        with patch(
            "bambu_spoolman.broker.checkpoint.os.replace",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(OSError):
                self.deliver(recovered, plan={(0, 0): 10})
        self.assertEqual(
            recovered.progress.pending_consumption["delivery_state"], "confirmed"
        )
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

    def test_changed_external_totals_cannot_authorize_acknowledgment_or_replay(self):
        self.client.fail_after = 42
        with self.assertRaises(AmbiguousConsumption):
            self.deliver(plan={(0, 0): 10})
        for observed in (90, 100, 110, 130):
            self.client.used[42] = observed
            with patch.object(self.client, "get_spool") as read:
                with self.assertRaises(AmbiguousConsumption):
                    self.deliver(self.restart(), plan={(0, 0): 10})
                read.assert_not_called()
        self.assertEqual(self.client.requests, [(42, 10)])
        self.assertTrue(accounting_status(self.client.endpoint)["blocked"])

    def test_failed_inflight_save_is_known_unsent_and_can_resume_original_spool(self):
        save = checkpoint._save_checkpoint_metadata

        def fail_issue(metadata):
            if (metadata.get("pending_consumption") or {}).get(
                "delivery_state"
            ) == "in_flight":
                raise OSError("disk full")
            save(metadata)

        with patch(
            "bambu_spoolman.broker.checkpoint._save_checkpoint_metadata",
            side_effect=fail_issue,
        ):
            with self.assertRaises(OSError):
                self.deliver(plan={(0, 0): 10})
        self.assertEqual(self.client.requests, [])
        self.assertEqual(
            self.journal.progress.pending_consumption["delivery_state"], "prepared"
        )
        self.trays["0"] = 999  # Must not redirect saved usage to a replacement.
        self.assertTrue(self.deliver(self.restart(), plan={(0, 0): 10}))
        self.assertEqual(self.client.requests, [(42, 10)])

    def test_changed_server_blocks_prepared_work(self):
        save = checkpoint._save_checkpoint_metadata

        def fail_issue(metadata):
            if (metadata.get("pending_consumption") or {}).get(
                "delivery_state"
            ) == "in_flight":
                raise OSError("disk full")
            save(metadata)

        with patch(
            "bambu_spoolman.broker.checkpoint._save_checkpoint_metadata",
            side_effect=fail_issue,
        ):
            with self.assertRaises(OSError):
                self.deliver(plan={(0, 0): 10})
        self.client.endpoint = "http://different.test"
        with self.assertRaises(AmbiguousConsumption):
            self.deliver(self.restart(), plan={(0, 0): 10})
        self.assertEqual(self.client.requests, [])

    def test_active_request_does_not_report_a_false_pause(self):
        entered, release = threading.Event(), threading.Event()
        self.client.fail_after = 42

        def delay(spool):
            entered.set()
            release.wait(3)

        self.client.before_send = delay
        errors = []

        def send():
            try:
                self.deliver(plan={(0, 0): 10})
            except AmbiguousConsumption as error:
                errors.append(error)

        worker = threading.Thread(target=send)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            status = accounting_status(self.client.endpoint)
            self.assertFalse(status["blocked"])
            self.assertEqual(status["pending"]["state"], "sending")
        finally:
            release.set()
            worker.join(1)
        self.assertEqual(len(errors), 1)
        self.assertTrue(accounting_status(self.client.endpoint)["blocked"])

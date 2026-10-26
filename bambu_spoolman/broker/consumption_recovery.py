"""Offline inspection and explicit recovery of uncertain consumption."""

import argparse
import fcntl
import hashlib
import json
import math
import os
import tempfile
import time
import uuid
from contextlib import contextmanager

from dotenv import load_dotenv

from bambu_spoolman.broker import checkpoint
from bambu_spoolman.broker.consumption_state import (
    endpoint_scope,
    is_blocked,
    observation_snapshot,
)
from bambu_spoolman.settings import get_configuration_path


def intent_id(metadata):
    # Bind the command to the entire pending record and print identity, including
    # legacy records without a UUID. A changed resolution invalidates this token.
    identity = {
        key: metadata.get(key)
        for key in ("task_id", "subtask_id", "print_name", "pending_consumption")
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def accounting_status(endpoint=None):
    with observation_snapshot() as observation:
        metadata = checkpoint.get_checkpoint_metadata()
        pending = metadata.get("pending_consumption")
        value = {"blocked": is_blocked(pending, endpoint), "pending": None}
        if pending is not None:
            live = (
                observation[1]
                if observation and observation[0] == pending.get("request_id")
                else None
            )
            if live is not None and (
                endpoint is None
                or pending.get("endpoint_scope") == endpoint_scope(endpoint)
            ):
                value["blocked"] = False
            value["pending"] = {
                "intentId": intent_id(metadata),
                "spoolId": pending.get("spool_id"),
                "lengthMm": pending.get("length"),
                "layer": pending.get("layer"),
                "segment": pending.get("segment_key"),
                "state": live or pending.get("delivery_state", "unknown"),
            }
        return value


@contextmanager
def accounting_lease():
    path = get_configuration_path("accounting.lock")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Stop the broker before consumption recovery") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def resolve_consumption(expected_id, spool_id, decision, note, endpoint):
    if (
        decision not in {"applied", "not-applied"}
        or not note.strip()
        or len(note) > 2048
    ):
        raise ValueError("Supply a decision and an evidence note of 1..2048 characters")
    if not endpoint or not endpoint.strip():
        raise ValueError("SPOOLMAN_URL must identify the server being reconciled")
    with accounting_lease():
        metadata = checkpoint.get_checkpoint_metadata()
        pending = metadata.get("pending_consumption")
        if pending is None or intent_id(metadata) != expected_id:
            raise ValueError("Pending intent changed; inspect status again")
        if str(pending.get("spool_id")) != str(spool_id):
            raise ValueError("Spool ID does not match the pending intent")
        if pending.get("delivery_state") == "confirmed":
            raise ValueError("Intent is already confirmed; no new decision is required")
        scope = endpoint_scope(endpoint)
        if pending.get("endpoint_scope") not in {None, scope}:
            raise ValueError("SPOOLMAN_URL differs from the original request's server")
        # Do not authorize a malformed/unsupported legacy record for replay.
        filament, tray = pending.get("filament"), pending.get("tray")
        if (
            type(filament) is not int
            or filament < 0
            or type(tray) is not int
            or tray < 0
            or pending.get("segment_key") != f"{filament}:{tray}"
        ):
            raise ValueError("Pending intent has invalid segment identity")
        layer = pending.get("layer")
        length = pending.get("length")
        if (
            type(layer) is not int
            or layer < 0
            or not isinstance(length, (int, float))
            or isinstance(length, bool)
            or not math.isfinite(length)
            or length <= 0
        ):
            raise ValueError("Pending intent has invalid layer or length")
        resolution = {
            "decision": decision,
            "note": note.strip(),
            "timestamp": int(time.time()),
            "intent_id": expected_id,
        }
        # An immutable backup precedes mutation. It records a REQUESTED decision;
        # if the checkpoint write fails, it does not claim the change committed.
        directory = get_configuration_path("consumption-recovery")
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", prefix="resolution-", suffix=".json", dir=directory, delete=False
        ) as backup:
            json.dump(
                {"checkpoint": metadata, "resolution_requested": resolution}, backup
            )
            backup.flush()
            os.fsync(backup.fileno())
            backup_path = backup.name
        metadata["pending_consumption"] = dict(
            pending,
            request_id=pending.get("request_id") or uuid.uuid4().hex,
            delivery_state="confirmed" if decision == "applied" else "prepared",
            endpoint_scope=scope,
            resolution=resolution,
        )
        checkpoint._save_checkpoint_metadata(metadata)
        return {"backup": backup_path, "status": accounting_status(endpoint)}


def archive_reconciled_print(
    expected_id, note, endpoint, *, print_ended=False, all_usage_reconciled=False
):
    if (
        not print_ended
        or not all_usage_reconciled
        or not note.strip()
        or len(note) > 2048
    ):
        raise ValueError(
            "Confirm the print ended and all usage was reconciled; "
            "supply an evidence note"
        )
    if not endpoint or not endpoint.strip():
        raise ValueError("SPOOLMAN_URL must identify the reconciled server")
    with accounting_lease():
        metadata = checkpoint.get_checkpoint_metadata()
        pending = metadata.get("pending_consumption")
        if pending is None or intent_id(metadata) != expected_id:
            raise ValueError("Pending intent changed; inspect status again")
        if pending.get("delivery_state") != "confirmed" or is_blocked(
            pending, endpoint
        ):
            raise ValueError(
                "Resolve the request as represented in reconciled usage "
                "before archiving"
            )
        directory = get_configuration_path("consumption-recovery")
        os.makedirs(directory, exist_ok=True)
        archive = tempfile.mkdtemp(prefix="completed-print-", dir=directory)
        with open(os.path.join(archive, "archive-request.json"), "w") as manifest:
            json.dump(
                {
                    "intent_id": expected_id,
                    "note": note.strip(),
                    "timestamp": int(time.time()),
                    "print_ended": True,
                    "all_usage_reconciled": True,
                },
                manifest,
            )
            manifest.flush()
            os.fsync(manifest.fileno())
        # One same-volume rename preserves BOTH the model and metadata. A failed
        # rename leaves the original checkpoint in place; no network write occurs.
        os.rename(
            get_configuration_path("checkpoint"), os.path.join(archive, "checkpoint")
        )
        return {"archive": archive, "status": accounting_status(endpoint)}


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    resolve = commands.add_parser("resolve")
    resolve.add_argument("--intent-id", required=True)
    resolve.add_argument("--spool-id", type=int, required=True)
    resolve.add_argument(
        "--decision", choices=["applied", "not-applied"], required=True
    )
    resolve.add_argument("--note", required=True)
    archive = commands.add_parser("archive-print")
    archive.add_argument("--intent-id", required=True)
    archive.add_argument("--print-ended", action="store_true", required=True)
    archive.add_argument("--all-usage-reconciled", action="store_true", required=True)
    archive.add_argument("--note", required=True)
    arguments = parser.parse_args()
    try:
        endpoint = os.environ.get("SPOOLMAN_URL")
        if arguments.command == "status":
            result = accounting_status(endpoint)
        elif arguments.command == "archive-print":
            result = archive_reconciled_print(
                arguments.intent_id,
                arguments.note,
                endpoint,
                print_ended=arguments.print_ended,
                all_usage_reconciled=arguments.all_usage_reconciled,
            )
        else:
            result = resolve_consumption(
                arguments.intent_id,
                arguments.spool_id,
                arguments.decision,
                arguments.note,
                endpoint,
            )
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()

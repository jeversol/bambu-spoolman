"""Local accounting warnings and the single-broker lease."""

import fcntl
import os
from contextlib import contextmanager

from loguru import logger

from bambu_spoolman.broker import checkpoint
from bambu_spoolman.settings import get_configuration_path


def accounting_status(endpoint=None):
    return {"warning": checkpoint.get_checkpoint_metadata().get("accounting_warning")}


def warn_uncertain_consumption(pending):
    message = (
        f"A filament usage update to spool {pending.get('spool_id')} "
        "could not be confirmed. Usage totals may be inaccurate. "
        "Tracking will continue without retrying this update."
    )
    logger.warning(message)
    metadata = checkpoint.get_checkpoint_metadata()
    metadata["accounting_warning"] = message
    checkpoint._save_checkpoint_metadata(metadata)


@contextmanager
def accounting_lease():
    path = get_configuration_path("accounting.lock")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another broker is already running") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)

"""Consumption outcome policy and bounded, process-local request observations."""

import hashlib
import threading
from contextlib import contextmanager


class AmbiguousConsumption(RuntimeError):
    def __init__(self):
        super().__init__(
            "Consumption outcome requires operator recovery; accounting is paused"
        )


def endpoint_scope(endpoint):
    return hashlib.sha256(endpoint.strip().rstrip("/").encode()).hexdigest()


def is_blocked(pending, endpoint=None):
    if pending is None:
        return False
    if pending.get("delivery_state") not in {"prepared", "confirmed"}:
        return True
    return bool(
        endpoint is not None
        and pending.get("endpoint_scope") != endpoint_scope(endpoint)
    )


_observation_lock = threading.Lock()
_observation = None  # One outstanding request in the supported single broker.


@contextmanager
def observing_request(pending):
    global _observation
    request_id = pending["request_id"]
    with _observation_lock:
        _observation = (request_id, "sending")
    try:
        yield
    except BaseException:
        forget_observation(pending)
        raise
    else:
        with _observation_lock:
            _observation = (request_id, "confirmed")


def forget_observation(pending):
    global _observation
    with _observation_lock:
        if _observation and _observation[0] == pending.get("request_id"):
            _observation = None


@contextmanager
def observation_snapshot():
    # Keep the file read and local observation coherent with the final ack: an
    # old in-flight snapshot must not look uncertain after its ack just cleared.
    with _observation_lock:
        yield _observation

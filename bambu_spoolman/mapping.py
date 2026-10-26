"""Serialize mapping mutations across API workers and MQTT callbacks.

The shared lock spans local persistence and optional remote location updates.
The settings file lock is separate and is never held during network calls, so
reads remain responsive. Async callers must move the whole operation to a worker;
never hold this thread lock across an await.
"""

import threading
from functools import wraps

mapping_lock = threading.RLock()


def serialized_mapping(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with mapping_lock:
            return function(*args, **kwargs)

    return wrapped

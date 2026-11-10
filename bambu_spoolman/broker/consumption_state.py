"""Identify consumption requests with uncertain outcomes."""

import hashlib


def endpoint_scope(endpoint):
    return hashlib.sha256(endpoint.strip().rstrip("/").encode()).hexdigest()


def is_uncertain(pending, endpoint=None):
    if pending is None:
        return False
    if pending.get("delivery_state") not in {"prepared", "confirmed"}:
        return True
    return bool(
        endpoint is not None
        and pending.get("endpoint_scope") != endpoint_scope(endpoint)
    )

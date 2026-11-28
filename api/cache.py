"""Tiny in-process TTL cache (one API instance; good enough for public data feeds)."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

_store: dict[Any, tuple[float, Any]] = {}
_locks: dict[Any, threading.Lock] = {}
_guard = threading.Lock()


def cached(key: Any, ttl: float, compute: Callable[[], Any]) -> Any:
    """Return a fresh cached value or compute it once (concurrent callers wait, not duplicate)."""
    now = time.time()
    hit = _store.get(key)
    if hit and hit[0] > now:
        return hit[1]
    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        hit = _store.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        value = compute()
        _store[key] = (time.time() + ttl, value)
        return value


def clear() -> None:
    _store.clear()

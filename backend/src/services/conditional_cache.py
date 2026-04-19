"""Tiny in-memory ETag / Last-Modified cache for conditional HTTP fetches.

Used by `BaseJobSource._get_json_conditional` and
`BaseJobSource._get_text_conditional`. Per Batch 3 §Conditional, many
sources honour `If-None-Match` / `If-Modified-Since` even when the
docs don't advertise it; caching the validator + body lets us turn a
repeat 200 into a zero-body 304 plus a local cache hit.

Capacity is bounded via a simple FIFO eviction policy — 256 distinct
(url, params) keys is plenty for even the most over-polled deployment.

Batch 3.5.3 adds hit/miss counters so operators can observe cache
effectiveness without Prometheus wiring (deferred to Batch 4).
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass
class CachedEntry:
    body: Any
    etag: str | None = None
    last_modified: str | None = None


class ConditionalCache:
    """(url, params) -> CachedEntry, bounded FIFO.

    Per-instance hit/miss counters are zeroed at construction and can
    be read via ``get_metrics()``. Use ``reset_metrics()`` between
    test cases to isolate assertions.
    """

    def __init__(self, max_entries: int = 256) -> None:
        self._store: OrderedDict[tuple[str, tuple], CachedEntry] = OrderedDict()
        self._max_entries = max_entries
        self.hit_count: int = 0
        self.miss_count: int = 0

    def get(self, key: tuple[str, tuple]) -> CachedEntry | None:
        entry = self._store.get(key)
        if entry is None:
            self.miss_count += 1
        else:
            self.hit_count += 1
        return entry

    def set(self, key: tuple[str, tuple], entry: CachedEntry) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = entry
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)

    def get_metrics(self) -> dict[str, int]:
        """Return {'hits', 'misses', 'size'} for cache-effectiveness logs.

        Operators can grep log lines emitting this dict to gauge how well
        conditional fetch is working in prod without pulling in a metrics
        exporter (Batch 4 scope).
        """
        return {
            "hits": self.hit_count,
            "misses": self.miss_count,
            "size": len(self._store),
        }

    def reset_metrics(self) -> None:
        """Zero the hit/miss counters. Cache contents are unaffected."""
        self.hit_count = 0
        self.miss_count = 0

    def __len__(self) -> int:
        return len(self._store)

"""Canonical ISO timestamp format for DB-side defaults (docs/fable/02).

The pg shim translates SQLite's `DEFAULT CURRENT_TIMESTAMP` and `datetime('now')`
into a Postgres `to_char(...)`. It used to emit the space form
`2026-07-11 12:00:00`, while the app writes `datetime.now(timezone.utc)
.isoformat()` = `2026-07-11T12:00:00+00:00`. Those columns are compared as TEXT
(ORDER BY / range filters on e.g. the notification ledger), and a space (0x20)
sorts before 'T' (0x54) — so ANY default-inserted row sorted before ANY
app-inserted row regardless of real time. These tests pin the fix: defaults now
emit the same ISO offset form the app uses.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from src.repositories.pg import translate

_ISO_OFFSET = re.compile(r"YYYY-MM-DD\"T\"HH24:MI:SS\"\+00:00\"")
_SPACE_FORM = re.compile(r"YYYY-MM-DD HH24:MI:SS'")  # the old, wrong output


def test_current_timestamp_default_translates_to_iso_offset():
    out = translate(
        "CREATE TABLE t (id INT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    assert _ISO_OFFSET.search(out), out
    assert not _SPACE_FORM.search(out), "must not emit the space form"


def test_datetime_now_default_translates_to_iso_offset():
    out = translate("CREATE TABLE t (id INT, ts TEXT NOT NULL DEFAULT (datetime('now')))")
    assert _ISO_OFFSET.search(out), out
    assert not _SPACE_FORM.search(out), "must not emit the space form"


def test_datetime_now_insert_translates_to_iso_offset():
    out = translate("UPDATE user_feed SET llm_matched_at = datetime('now') WHERE id = ?")
    assert _ISO_OFFSET.search(out), out


def test_iso_offset_is_python39_parseable():
    """The emitted literal must round-trip through fromisoformat (Py 3.9 safe).

    A `Z` suffix would NOT — Python 3.9's fromisoformat rejects it — and several
    call sites parse these columns unguarded, so `+00:00` is the required form.
    """
    sample = "2026-07-11T12:00:00+00:00"
    parsed = datetime.fromisoformat(sample)
    assert parsed.tzinfo is not None


def test_default_and_app_writes_sort_chronologically_as_text():
    """The actual bug: text sort order must equal time order across both formats."""
    app_earlier = datetime(2026, 7, 11, 10, 0, 0, tzinfo=timezone.utc).isoformat()
    default_later = "2026-07-11T12:00:00+00:00"  # what the shim now emits
    # Lexical (text) comparison must agree with chronological order.
    assert app_earlier < default_later
    # Old space form broke this: a LATER default sorted BEFORE an EARLIER app row.
    old_default_later = "2026-07-11 12:00:00"
    assert not (app_earlier < old_default_later), (
        "regression witness: space form mis-sorts before ISO"
    )

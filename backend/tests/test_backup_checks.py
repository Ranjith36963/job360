"""scripts/backup_checks.py: the row-count verify and the freshness alert (gap 7, G410).

WHY THESE EXIST
---------------
The nightly restore-verify only asked `users >= 1` and `migrations >= 20`, so a
dump that restored but silently dropped most of the application spine still went
green. And nothing looked at the bucket afterwards, so a nightly that stopped
uploading was invisible. Both rules live in a script so they can be tested here
instead of only in the workflow's bash.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load_module():
    """Import scripts/backup_checks.py without putting scripts/ on sys.path."""
    path = REPO / "scripts" / "backup_checks.py"
    spec = importlib.util.spec_from_file_location("backup_checks_under_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bc = _load_module()

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def test_parse_counts_reads_table_count_lines():
    assert bc.parse_counts("users 3\n\napplications 12\n") == {"users": 3, "applications": 12}


def test_parse_counts_rejects_garbage():
    with pytest.raises(ValueError):
        bc.parse_counts("users three\n")


def test_compare_passes_when_restore_matches_or_gained_rows():
    rows, ok = bc.compare_counts({"users": 3, "application_events": 100}, {"users": 3, "application_events": 104})
    assert ok
    assert rows == [
        ("application_events", 100, 104, "ok (+4 written during dump)"),
        ("users", 3, 3, "ok"),
    ]


def test_compare_fails_when_restore_lost_rows():
    rows, ok = bc.compare_counts({"application_receipts": 10}, {"application_receipts": 7})
    assert not ok
    assert rows == [("application_receipts", 10, 7, "LOST 3 rows")]


def test_compare_fails_when_a_table_is_missing_from_the_restore():
    rows, ok = bc.compare_counts({"profile_edits": 5}, {})
    assert not ok
    assert rows == [("profile_edits", 5, "-", "MISSING in restore")]


def test_compare_fails_on_empty_source_list():
    assert bc.compare_counts({}, {"users": 1})[1] is False


def test_render_counts_names_the_failure_in_the_summary():
    rows, ok = bc.compare_counts({"users": 2}, {"users": 1})
    text = bc.render_counts(rows, ok)
    assert "ROW COUNT MISMATCH" in text
    assert "| users | 2 | 1 | LOST 1 rows |" in text


def test_main_compare_exit_codes(tmp_path, capsys):
    src, ok_f, bad_f = tmp_path / "s.txt", tmp_path / "ok.txt", tmp_path / "bad.txt"
    src.write_text("users 3\napplications 8\n")
    ok_f.write_text("users 3\napplications 9\n")
    bad_f.write_text("users 3\napplications 2\n")
    assert bc.main(["compare", str(src), str(ok_f)]) == 0
    assert bc.main(["compare", str(src), str(bad_f)]) == 1
    assert "LOST 6 rows" in capsys.readouterr().out


def _listing(*pairs: tuple[str, datetime]) -> str:
    return "\n".join(f"{k}\t{t.isoformat()}" for k, t in pairs)


def test_freshness_ok_when_newest_object_is_recent():
    listing = _listing(
        ("job360-20260418T021700Z.sql.gz.gpg", NOW - timedelta(days=2)),
        ("job360-20260420T021700Z.sql.gz.gpg", NOW - timedelta(hours=10)),
    )
    ok, msg = bc.check_freshness(listing, NOW, 36)
    assert ok
    assert "job360-20260420T021700Z.sql.gz.gpg is 10.0 h old" in msg


def test_freshness_fails_when_newest_object_is_older_than_limit():
    listing = _listing(("job360-20260418T021700Z.sql.gz.gpg", NOW - timedelta(hours=36, minutes=6)))
    ok, msg = bc.check_freshness(listing, NOW, 36)
    assert not ok
    assert "36.1 h old" in msg


def test_freshness_fails_on_empty_bucket_and_ignores_other_keys():
    assert bc.check_freshness("None", NOW, 36) == (False, "no nightly backup object found in the bucket")
    # A fresh object under another name must not count as a nightly backup.
    other = _listing(("monthly/job360-20260420T021700Z.sql.gz.gpg", NOW), ("notes.txt", NOW))
    assert bc.check_freshness(other, NOW, 36)[0] is False


def test_freshness_reads_z_suffix_timestamps():
    listing = "job360-20260420T021700Z.sql.gz.gpg\t2026-10-20T08:00:00Z"
    ok, msg = bc.check_freshness(listing, NOW, 36)
    assert ok and "4.0 h old" in msg

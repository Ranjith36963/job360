"""`main()` in scripts/product_assertions.py — the guard itself, not its helpers.

WHY THIS FILE EXISTS. `test_feed_lag_edge.py` has 22 tests and every one of them
exercises a PURE FUNCTION. That is not the same thing as testing the guard, and
the difference is this repo's recurring failure: `main()` is where the
comparison lives, where the constant is applied, and where the column names are
chosen — and it had ZERO coverage. Three realistic regressions (drop the
comparison, invert it, read the wrong column) left the whole suite green.

So these tests drive `main()` end to end against a STUB CURSOR that returns the
numbers production actually returned on 2026-08-15, and assert on the table row
the owner reads. They are MUTATION-TESTED: each of those three regressions must
turn at least one of them red. A test that survives every mutation is
decoration, not a test.

No database. No network. The stub replaces `psycopg` in `sys.modules` before
`main()` imports it.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

# product_assertions.py lives at the REPO ROOT (scripts/), not backend/scripts/,
# so it is not importable by name. Load it by path — its module-level imports
# are stdlib only (psycopg is imported inside main()), so this stays offline.
_PA_PATH = Path(__file__).resolve().parents[2] / "scripts" / "product_assertions.py"
_WORKER_SETTINGS = Path(__file__).resolve().parents[1] / "src" / "workers" / "settings.py"


@pytest.fixture()
def pa() -> Any:
    spec = importlib.util.spec_from_file_location("_pa_main_under_test", _PA_PATH)
    assert spec and spec.loader, f"cannot load {_PA_PATH}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── The incident, measured read-only from prod on 2026-08-15 ───────────────
#
#     newest job (jobs.first_seen_at)   2026-08-15T04:05:15.113456+00:00
#     newest feed row (user_feed)       2026-08-11T07:02:12.125739+00:00
#     jobs ingested in between          1,345
#     lag                               93.05 HOURS
#     catalog age at that moment        10.9h  (the catalog was HEALTHY)
#     job_enrichment max(enriched_at)   2026-08-08T14:38:24+00:00  (6 days)
#
# The lag is DERIVED from the two timestamps rather than typed again, so the
# fixture cannot drift away from the outage it claims to reproduce.
PROD_NEWEST_JOB = "2026-08-15T04:05:15.113456+00:00"
PROD_NEWEST_FEED = "2026-08-11T07:02:12.125739+00:00"
PROD_LAG_HOURS = (
    datetime.fromisoformat(PROD_NEWEST_JOB) - datetime.fromisoformat(PROD_NEWEST_FEED)
).total_seconds() / 3600
PROD_STRANDED = 1345
# Anchoring the fixture to a live `now` (rather than the frozen 2026-08-15
# literals) keeps the CATALOG check green forever, so the only thing these
# tests can turn red is the edge they are about.
PROD_CATALOG_AGE_HOURS = 10.9
PROD_ENRICHMENT_FREEZE_HOURS = 6 * 24


def _norm(sql: str) -> str:
    return " ".join(sql.split()).lower()


class _StubCursor:
    """Answers main()'s SQL from a canned table. First matching fragment wins.

    Fragment order is LOAD-BEARING: ``select count(*) from jobs`` is a prefix of
    ``select count(*) from jobs where first_seen_at > %s``, so the specific one
    must be listed first. An unmatched query RAISES rather than returning None —
    silently answering None is exactly how a stub lets a broken guard look
    healthy, which is the bug this file exists to stop.
    """

    def __init__(self, plan: list[tuple[str, Any]]) -> None:
        self._plan = plan
        self._pending: Any = None
        self.seen: list[str] = []

    def execute(self, sql: str, params: Any = None) -> None:
        key = _norm(sql)
        self.seen.append(key)
        for fragment, value in self._plan:
            if fragment in key:
                if isinstance(value, Exception):
                    raise value
                self._pending = value
                return
        raise AssertionError(f"stub cursor has no answer for: {key}")

    def fetchone(self) -> Any:
        return self._pending

    def __enter__(self) -> _StubCursor:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _StubConn:
    def __init__(self, cur: _StubCursor) -> None:
        self._cur = cur

    def cursor(self) -> _StubCursor:
        return self._cur

    def rollback(self) -> None:
        pass

    def __enter__(self) -> _StubConn:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def _plan(
    *,
    feed_lag_hours: float = PROD_LAG_HOURS,
    searches_since: int | None = 0,
    enrichment_age_hours: float = 0.5,
    embedding_age_hours: float = 0.5,
) -> list[tuple[str, Any]]:
    """The rows prod returned, anchored to a live `now`."""
    now = datetime.now(timezone.utc)
    newest_job = now - timedelta(hours=PROD_CATALOG_AGE_HOURS)
    newest_feed = newest_job - timedelta(hours=feed_lag_hours)
    search_row: Any = (searches_since,) if searches_since is not None else RuntimeError(
        "audit_log does not exist"
    )
    return [
        # -- the edge, and the two endpoints it spans ----------------------
        ("select count(*) from jobs where first_seen_at >", (PROD_STRANDED,)),
        ("select max(first_seen_at) from jobs", (newest_job.isoformat(),)),
        ("select max(created_at) from user_feed", (newest_feed.isoformat(),)),
        # The wrong-column mutation reads the CATALOG timestamp where the FEED
        # one belongs. Answering it proves that mutation yields a plausible
        # `ok` rather than a crash the test would catch for the wrong reason.
        ("select max(created_at) from jobs", (newest_job.isoformat(),)),
        ("from audit_log where event = 'search_started' and occurred_at >", search_row),
        ("select count(*) from jobs", (10257,)),
        # -- feature freshness --------------------------------------------
        ("count(*) from job_enrichment", (6560,)),
        (
            "max(enriched_at) from job_enrichment",
            ((now - timedelta(hours=enrichment_age_hours)).isoformat(),),
        ),
        ("count(*) from job_embeddings", (687,)),
        (
            "max(embedding_updated_at) from job_embeddings",
            ((now - timedelta(hours=embedding_age_hours)).isoformat(),),
        ),
        # -- everything else, pinned healthy so only the edge can move ------
        ("interval '30 minutes'", (0,)),
        ("event = 'client_error'", (0,)),
        ("count(*), coalesce(max(score), 0) from user_feed", (10508, 80)),
        ("from notification_rules", (60, 60)),
        ("select count(*) from user_feed where score >= %s", (138,)),
        ("llm_fit_score is not null", (185, 10508)),
        ("select max(llm_matched_at)", ((now - timedelta(days=4)).isoformat(),)),
        ("count(distinct user_id) from user_feed", (2,)),
        ("having count(*) filter", (0,)),
        ("select avg(vis)", (100.0,)),
    ]


def _run_main(pa: Any, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> tuple[int, str]:
    """Drive main() against the stub cursor. Returns (exit code, stdout)."""
    cur = _StubCursor(_plan(**kwargs))
    fake = types.ModuleType("psycopg")
    fake.connect = lambda dsn, connect_timeout=None: _StubConn(cur)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    monkeypatch.setenv("PROD_DATABASE_URL", "postgresql://stub/stub")
    # The enrichment gate is read from the ambient env; pin it so a developer's
    # shell cannot change the verdict of a test about something else.
    monkeypatch.delenv("ENRICHMENT_THRESHOLD", raising=False)
    monkeypatch.delenv("ENRICHMENT_MIN_SCORE", raising=False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = pa.main()
    return code, buf.getvalue()


def _row(out: str, needle: str) -> tuple[str, str]:
    """(value, verdict) of the markdown row whose check name contains `needle`."""
    for line in out.splitlines():
        if line.startswith("|") and needle in line:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 3 and cells[1] != "---":
                return cells[1].strip("`"), cells[2]
    raise AssertionError(f"no table row named {needle!r} in:\n{out}")


# ═══════════════════════════════════════════════════════════════════════════
# HIGH 1 — the row the owner actually reads
# ═══════════════════════════════════════════════════════════════════════════


def test_main_prints_broken_on_the_real_production_numbers(
    pa: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pinned outage — 93.1h of lag, 1,345 jobs stranded — with searches
    that DID run. That is a severed pipe: a real break, alarmed normally."""
    code, out = _run_main(pa, monkeypatch, searches_since=7)
    value, verdict = _row(out, "feed lag behind catalog")
    assert value == "93.1h, 1345 jobs stranded, severed", value
    assert verdict == "**BROKEN**", out
    assert code == 1, out


def test_main_prints_ok_when_the_feed_is_fresh(
    pa: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same query shapes, a feed that kept up: the row is green and exit is 0."""
    code, out = _run_main(pa, monkeypatch, feed_lag_hours=2.0, searches_since=7)
    value, verdict = _row(out, "feed lag behind catalog")
    assert value == "2.0h, 1345 jobs stranded, severed", value
    assert verdict == "ok", out
    assert code == 0, out


def test_main_is_still_broken_when_the_cause_cannot_be_read(
    pa: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`unknown` is not permission to stay quiet — a lag nobody can explain is
    still a lag. Only the STARVED case is a decision."""
    code, out = _run_main(pa, monkeypatch, searches_since=None)
    value, verdict = _row(out, "feed lag behind catalog")
    assert "unknown" in value
    assert verdict == "**BROKEN**", out
    assert code == 1, out


# ═══════════════════════════════════════════════════════════════════════════
# HIGH 2 — a product DECISION must not behave like a break
#
# Measured: red on ~23% of days in multi-day streaks, and product-health.yml
# auto-closes its own issue the moment anybody searches. So it opened, spammed
# comments, closed itself, and came back the next week — for a PRODUCT GAP that
# no amount of alarming can fix. That is how every other alarm in this repo
# died: it became wallpaper.
# ═══════════════════════════════════════════════════════════════════════════


def test_a_starved_feed_is_a_decision_not_a_break(
    pa: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody searched. Nothing is crashing. This is the owner's call, so it
    must NOT land in the recurring 'something is WRONG' alarm."""
    code, out = _run_main(pa, monkeypatch, searches_since=0)
    value, verdict = _row(out, "feed lag behind catalog")
    assert value == "93.1h, 1345 jobs stranded, starved", value
    assert verdict == "**DECISION**", out
    # Exit 3 = "a decision is waiting", distinct from 1 = "something broke".
    # The workflow routes them to completely different places.
    assert code == 3, out
    assert "Something is WRONG" not in out, "a decision must not open the break alarm"


def test_the_decision_is_machine_extractable_and_states_the_choice(
    pa: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workflow slices the Slack message out of stdout at this marker, and
    the message is useless unless it names the actual options."""
    _code, out = _run_main(pa, monkeypatch, searches_since=0)
    assert pa.DECISION_MARKER in out
    body = out.split(pa.DECISION_MARKER, 1)[1]
    assert "93.1" in body and "1345" in body
    for option in ("OPTION A", "OPTION B", "OPTION C"):
        assert option in body, body


def test_a_severed_feed_never_becomes_a_decision(
    pa: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The split must be real in BOTH directions, or it is just a mute button."""
    _code, out = _run_main(pa, monkeypatch, searches_since=7)
    assert pa.DECISION_MARKER not in out


def test_a_real_break_outranks_a_pending_decision(
    pa: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 1 must win over exit 3 — otherwise a starved feed would mask a
    genuinely broken enrichment sweep, and the canary (which forces a red run
    and demands exit 1) would start failing for the wrong reason."""
    code, out = _run_main(
        pa,
        monkeypatch,
        searches_since=0,
        enrichment_age_hours=PROD_ENRICHMENT_FREEZE_HOURS,
    )
    assert _row(out, "feed lag behind catalog")[1] == "**DECISION**"
    assert _row(out, "LLM enrichment freshness")[1] == "**BROKEN**"
    assert code == 1, out


# ═══════════════════════════════════════════════════════════════════════════
# HIGH 3 — feature freshness on an HOURS scale
#
# Both freshness rows printed `ok` while enrichment and embeddings had been
# frozen since 2026-08-08, because the earlier fix restored the ROW but left it
# on STALE_FEATURE_DAYS = 14. They could not have gone red before 2026-08-22.
# ═══════════════════════════════════════════════════════════════════════════


def test_a_six_day_enrichment_freeze_goes_red(
    pa: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact live state on 2026-08-15, which the guard called healthy."""
    code, out = _run_main(
        pa,
        monkeypatch,
        searches_since=7,
        feed_lag_hours=2.0,
        enrichment_age_hours=PROD_ENRICHMENT_FREEZE_HOURS,
        embedding_age_hours=PROD_ENRICHMENT_FREEZE_HOURS,
    )
    assert _row(out, "LLM enrichment freshness")[1] == "**BROKEN**", out
    assert _row(out, "semantic embeddings freshness")[1] == "**BROKEN**", out
    assert code == 1, out


def test_a_sweep_that_ran_this_hour_is_ok(
    pa: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out = _run_main(pa, monkeypatch, feed_lag_hours=2.0, searches_since=7)
    assert _row(out, "LLM enrichment freshness")[1] == "ok", out
    assert _row(out, "semantic embeddings freshness")[1] == "ok", out
    assert code == 0, out


def test_the_old_fortnight_constant_could_not_have_fired(pa: Any) -> None:
    """The finding in one assertion: 6 days sits inside 14 days, so the
    resurrected row was structurally incapable of going red during a live
    freeze."""
    assert PROD_ENRICHMENT_FREEZE_HOURS < pa.STALE_FEATURE_DAYS * 24
    assert PROD_ENRICHMENT_FREEZE_HOURS > pa.FEATURE_FRESHNESS_MAX_HOURS


def test_feature_freshness_is_tied_to_the_real_cron_cadence(pa: Any) -> None:
    """The constant is not a taste call — it is READ from the worker's cron.

    `enrichment_sweep` runs every 30 minutes and `refresh_catalog` daily at
    04:00, so new candidates arrive once a day and the sweep gets 48 chances to
    pick each batch up. One quiet day is therefore explainable; two is not.
    If either cadence changes, this test fails instead of the constant quietly
    becoming wrong — the same cross-artifact pin as the column-name test.
    """
    settings = _WORKER_SETTINGS.read_text(encoding="utf-8")

    sweep = re.search(r"_cron\(enrichment_sweep,\s*minute=\{([0-9,\s]+)\}\)", settings)
    assert sweep, "enrichment_sweep cron not found - re-derive the constant"
    minutes = sorted(int(m) for m in sweep.group(1).split(","))
    assert minutes == [10, 40], f"sweep cadence changed to {minutes}"
    sweep_gap_hours = (minutes[1] - minutes[0]) / 60.0  # 0.5h

    refresh = re.search(r"_cron\(refresh_catalog,\s*hour=(\d+),\s*minute=(\d+)\)", settings)
    assert refresh, "refresh_catalog cron not found - re-derive the constant"
    candidate_arrival_hours = 24.0  # a daily cron -> new candidates once a day

    assert pa.FEATURE_FRESHNESS_MAX_HOURS == candidate_arrival_hours * 2 == 48
    assert pa.FEATURE_FRESHNESS_MAX_HOURS > sweep_gap_hours
    # Its own knob, exactly like FEED_LAG_MAX_HOURS. Reusing the fortnight is
    # what disarmed this guard in the first place.
    assert pa.FEATURE_FRESHNESS_MAX_HOURS < pa.STALE_FEATURE_DAYS * 24


def test_feature_freshness_constant_is_env_overridable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A canary must be able to force this row red on demand too."""
    monkeypatch.setenv("PA_FEATURE_FRESHNESS_MAX_HOURS", "1")
    spec = importlib.util.spec_from_file_location("_pa_reload_ff", _PA_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.FEATURE_FRESHNESS_MAX_HOURS == 1

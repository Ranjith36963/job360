"""post-merge-watch must not roll prod back on deploy noise (FC-008, 2026-10-03).

Three rollbacks in one afternoon (#722, #724, #725; trip run 37131014974) were caused by
3 Sentry issues - `ClientDisconnect` on /api/mcp and two `event loop blocked for 3.13s`,
2 events and 0 users each - that the OLD container logs while Railway swaps it out.
`scripts/fixtures/sentry_deploy_noise.json` holds those 3 issues as recorded.

These tests are the drill for `scripts/sentry_poll.py::decide` and
`scripts/deploy_settle.py` (scripts/drill_registry.py runs the same cases offline). The
negative controls matter as much: a filter that ignores everything would pass the noise
cases, so errors that MUST still trip a rollback are asserted here too.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


def _load(name: str) -> ModuleType:
    """Import a repo-root script (the root `scripts/` has no __init__.py)."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sp = _load("sentry_poll")
ds = _load("deploy_settle")

NOISE = json.loads((SCRIPTS / "fixtures" / "sentry_deploy_noise.json").read_text(encoding="utf-8"))["issues"]
# The values post-merge-watch.yml sets for a push.
WATCH = sp.Knobs(
    min_events=5,
    min_users=1,
    noise_titles=("clientdisconnect", "event loop blocked"),
    noise_escalate_events=20,
)
DEPLOY_CREATED = sp.parse_instant("2026-10-03T14:57:52Z")  # deploy 62f9a919, per `railway deployment list`


def _issue(title: str, count: int, users: int, first: str = "2026-10-03T15:01:00Z") -> dict:
    return {"shortId": "X-1", "title": title, "count": str(count), "userCount": users,
            "firstSeen": first, "culprit": ""}


def test_fixture_is_the_three_real_issues() -> None:
    assert [i["shortId"] for i in NOISE] == ["PYTHON-FASTAPI-W", "PYTHON-FASTAPI-V", "PYTHON-FASTAPI-T"]
    assert all(i["count"] == "2" and i["userCount"] == 0 for i in NOISE)


def test_recorded_deploy_noise_does_not_trip() -> None:
    verdict = sp.decide(NOISE, WATCH, DEPLOY_CREATED)
    assert verdict.tripped == []
    assert len(verdict.ignored) == 3  # listed, never silently dropped
    assert all(why for _, why in verdict.ignored)


def test_recorded_noise_tripped_under_the_old_rule() -> None:
    """The legacy defaults (every new issue trips) are what rolled prod back."""
    assert len(sp.decide(NOISE, sp.Knobs(), None).tripped) == 3


# ── negative controls: these MUST still trip ────────────────────────────────


def test_error_with_a_user_trips() -> None:
    assert sp.decide([_issue("ValueError: boom", 2, 1)], WATCH, DEPLOY_CREATED).tripped


def test_noise_title_that_hurts_a_user_still_trips() -> None:
    assert sp.decide([_issue("ClientDisconnect", 2, 1)], WATCH, DEPLOY_CREATED).tripped


@pytest.mark.parametrize("title", ["ClientDisconnect", "event loop blocked for 3.13s"])
def test_flood_of_a_noise_title_trips(title: str) -> None:
    assert sp.decide([_issue(title, 20, 0)], WATCH, DEPLOY_CREATED).tripped
    assert not sp.decide([_issue(title, 19, 0)], WATCH, DEPLOY_CREATED).tripped


def test_non_noise_error_with_enough_events_trips() -> None:
    assert sp.decide([_issue("KeyError: 'id'", 5, 0)], WATCH, DEPLOY_CREATED).tripped
    assert not sp.decide([_issue("KeyError: 'id'", 4, 0)], WATCH, DEPLOY_CREATED).tripped


def test_unreadable_counts_fail_toward_the_alarm() -> None:
    assert sp.decide([{"title": "?", "count": "n/a", "userCount": None}], WATCH, DEPLOY_CREATED).tripped


def test_mixed_batch_trips_on_the_real_one_and_lists_the_noise() -> None:
    verdict = sp.decide([*NOISE, _issue("ValueError: boom", 3, 2)], WATCH, DEPLOY_CREATED)
    assert [i["title"] for i, _ in verdict.tripped] == ["ValueError: boom"]
    assert len(verdict.ignored) == 3


def test_issue_older_than_the_deploy_is_ignored_but_a_newer_one_is_not() -> None:
    """Run 3 of the incident (deploy 15:42) tripped on issues first seen at 15:01."""
    later = sp.parse_instant("2026-10-03T15:42:01Z")
    assert not sp.decide(NOISE, sp.Knobs(), later).tripped
    assert sp.decide([_issue("KeyError: 'id'", 1, 0, first="2026-10-03T15:50:00Z")], sp.Knobs(), later).tripped


def test_legacy_defaults_still_trip_everything() -> None:
    """external-health.yml shares the script; its nightly poll must not get quieter."""
    assert sp.decide([_issue("KeyError: 'id'", 1, 0)], sp.Knobs(), None).tripped


# ── the script end to end: env knobs -> exit code ───────────────────────────


def _run_main(monkeypatch: pytest.MonkeyPatch, issues: list[dict], **env: str) -> int:
    monkeypatch.setattr(sp, "_get", lambda _url, _token: issues)
    monkeypatch.setenv("SENTRY_API_TOKEN", "x")
    for key in ("SENTRY_MIN_EVENTS", "SENTRY_MIN_USERS", "SENTRY_NOISE_TITLES",
                "SENTRY_NOISE_ESCALATE_EVENTS", "SENTRY_SINCE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return sp.main()


WATCH_ENV = {
    "SENTRY_MIN_EVENTS": "5",
    "SENTRY_NOISE_TITLES": "ClientDisconnect,event loop blocked",
    "SENTRY_NOISE_ESCALATE_EVENTS": "20",
    "SENTRY_SINCE": "2026-10-03T14:57:52Z",
}


def test_main_exits_0_on_the_recorded_noise(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run_main(monkeypatch, NOISE, **WATCH_ENV) == 0
    out = capsys.readouterr().out
    assert "IGNORED" in out and "PYTHON-FASTAPI-W" in out  # visible, not hidden


def test_main_exits_1_on_a_user_facing_error(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_main(monkeypatch, [*NOISE, _issue("ValueError: boom", 2, 1)], **WATCH_ENV) == 1


def test_main_exits_1_on_a_noise_flood(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_main(monkeypatch, [_issue("ClientDisconnect", 40, 0)], **WATCH_ENV) == 1


def test_main_with_no_knobs_is_the_old_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_main(monkeypatch, NOISE) == 1


def test_main_bad_knob_is_a_loud_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_main(monkeypatch, NOISE, SENTRY_MIN_EVENTS="five") == 2
    assert _run_main(monkeypatch, NOISE, SENTRY_SINCE="yesterday") == 2


# ── deploy_settle: the wait must be for THIS commit's deploy ────────────────

SHA = "41504c58755579fc1686cd23a578f8291a2086f7"
OTHER = "62f9a919" + "0" * 32


def _dep(status: str, commit: str) -> dict:
    return {"id": f"d-{status}", "status": status, "createdAt": "2026-10-03T14:49:36.382Z",
            "meta": {"commitHash": commit}}


def test_old_container_answering_is_not_this_commit_being_live() -> None:
    # What the 2026-10-03 deploy list looked like mid-roll: new one building, old one SUCCESS.
    assert ds.assess([_dep("BUILDING", SHA), _dep("SUCCESS", OTHER)], SHA)[0] == "pending"
    assert ds.assess([_dep("SUCCESS", OTHER)], SHA)[0] == "not_found"


def test_success_for_this_sha_is_live() -> None:
    assert ds.assess([_dep("SUCCESS", SHA), _dep("REMOVED", OTHER)], SHA)[0] == "live"


def test_replaced_and_failed_deploys_are_named_not_waited_on() -> None:
    assert ds.assess([_dep("SUCCESS", OTHER), _dep("REMOVED", SHA)], SHA)[0] == "superseded"
    assert ds.assess([_dep("FAILED", SHA), _dep("SUCCESS", OTHER)], SHA)[0] == "failed"


def test_settle_clock() -> None:
    assert ds.settle_remaining(1000.0, None, 940.0, 300.0, 240.0) == 240.0  # saw SUCCESS 1 min ago
    assert ds.settle_remaining(10000.0, 8200.0, None, 300.0, 240.0) == 0.0  # queued run, long settled
    assert ds.settle_remaining(10000.0, 9940.0, None, 300.0, 240.0) == 480.0  # created 1 min ago

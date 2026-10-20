"""post-merge-watch verifies the deploy and rolls back only on a real signal (FC-009, 2026-10-20).

Five rollbacks on noise (#722 #724 #725 on 2026-10-03, #910 #911 on 2026-10-19), and a
"wait for the deploy" that the OLD container satisfied at 0 s. Ported from PR #728 and
rebuilt around GET /api/version:

* `scripts/deploy_settle.py` waits until /api/version serves THIS sha; the old sha
  still serving is a red "deploy never went live", never a pass.
* `scripts/sentry_poll.py::decide` ignores issues first seen before the swap and the
  known swap noise, and names the service each real error came from.
* the workflow's own `decide` step (executed by bash) rolls back only on a real signal;
  that drill needs pyyaml, so `scripts/drill_registry.py` runs it in ci.yml, not here.

`scripts/drill_registry.py` runs the same worlds offline; these tests keep them in the
backend suite. The negative controls matter as much as the noise cases: a filter that
ignores everything would pass every "must not trip" case.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
WORKFLOW = REPO / ".github" / "workflows" / "post-merge-watch.yml"


def _load(name: str) -> ModuleType:
    """Import a repo-root script (the root `scripts/` has no __init__.py).

    Registered under a prefixed name, so a later test importing a real module
    called `sentry_poll` or `deploy_settle` never gets this copy.
    """
    qualified = f"_post_merge_watch_{name}"
    spec = importlib.util.spec_from_file_location(qualified, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = mod  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)
    return mod


sp = _load("sentry_poll")
ds = _load("deploy_settle")

NOISE = json.loads((SCRIPTS / "fixtures" / "sentry_deploy_noise.json").read_text(encoding="utf-8"))["issues"]
WATCH = sp.Knobs.from_env({"SENTRY_PRESET": "post-merge-watch"})
LIVE = sp.parse_instant("2026-10-03T14:59:00Z")
BACKEND = {"slug": "python-fastapi", "platform": "python"}
FRONTEND = {"slug": "javascript-nextjs", "platform": "javascript-nextjs"}


def _issue(title: str, count: int, users: int, first: str = "2026-10-03T15:05:00Z",
           project: dict | None = None) -> dict:
    return {"shortId": "X-1", "title": title, "count": str(count), "userCount": users,
            "firstSeen": first, "culprit": "", "project": BACKEND if project is None else project}


# ── Sentry: noise does not trip, real errors trip on the right service ─────────


def test_fixture_is_the_three_real_issues() -> None:
    assert [i["shortId"] for i in NOISE] == ["PYTHON-FASTAPI-W", "PYTHON-FASTAPI-V", "PYTHON-FASTAPI-T"]
    assert all(i["count"] == "2" and i["userCount"] == 0 for i in NOISE)


def test_recorded_swap_noise_does_not_trip_and_is_listed() -> None:
    verdict = sp.decide([{**i, "project": BACKEND} for i in NOISE], WATCH, LIVE)
    assert verdict.tripped == []
    assert len(verdict.ignored) == 3 and all(why for _, why in verdict.ignored)


def test_the_old_rule_tripped_on_that_noise() -> None:
    """The legacy defaults (every new issue trips) are what rolled prod back."""
    assert len(sp.decide(NOISE, sp.Knobs(), None).tripped) == 3


def test_issue_first_seen_before_the_deploy_went_live_does_not_trip() -> None:
    old = _issue("KeyError: 'id'", 50, 3, first="2026-10-03T14:00:00Z")
    assert sp.decide([old], WATCH, LIVE).tripped == []


def test_new_backend_error_trips_backend_only() -> None:
    verdict = sp.decide([_issue("ValueError: boom", 2, 1)], WATCH, LIVE)
    assert verdict.services == ["backend"]


def test_new_frontend_error_trips_frontend_only() -> None:
    """#911 rolled the BACKEND back for a frontend-test-only commit."""
    verdict = sp.decide([_issue("TypeError: x", 3, 1, project=FRONTEND)], WATCH, LIVE)
    assert verdict.services == ["frontend"]


def test_noise_flood_and_unreadable_counts_still_trip() -> None:
    assert sp.decide([_issue("ClientDisconnect", 20, 0)], WATCH, LIVE).tripped
    unreadable = {"title": "?", "count": "n/a", "userCount": None, "project": BACKEND}
    assert sp.decide([unreadable], WATCH, LIVE).tripped


def test_issue_in_neither_project_is_ignored_and_missing_project_fails_toward_alarm() -> None:
    other = _issue("Boom", 99, 9, project={"slug": "side-project", "platform": "go"})
    assert sp.decide([other], WATCH, LIVE).tripped == []
    assert sp.decide([_issue("Boom", 9, 1, project={})], WATCH, LIVE).services == [sp.ALL]


def test_nightly_poll_defaults_are_unchanged() -> None:
    """external-health.yml shares the script: no preset = every new issue trips."""
    assert sp.Knobs.from_env({}) == sp.Knobs()
    assert sp.decide([_issue("x", 1, 0, project={"slug": "anything"})], sp.Knobs(), None).tripped


def test_sentry_drill_and_its_negative_control() -> None:
    assert sp.drill() == 0
    assert sp.drill(blind=True) == 1


# ── deploy identity: the old sha still serving is RED ──────────────────────────


def test_old_sha_still_live_is_never_live() -> None:
    clock = [0.0]

    def sleep(s: float) -> None:
        clock[0] += s

    out = ds.wait_for_sha("a" * 40, ["https://job360.uk/api/version"], 600, 60,
                          fetch=lambda url: "b" * 40, is_ancestor=lambda a, b: False,
                          diagnose=lambda: "failed", now=lambda: clock[0], sleep=sleep)
    assert out["deploy_state"] == "never_live"
    assert "never went live" in out["detail"]


def test_deploy_wait_worlds_and_their_negative_control() -> None:
    """The workflow's decide-step worlds need pyyaml, so drill_registry runs those (ci.yml)."""
    assert all(ok for _, ok in ds.wait_worlds())
    assert not all(ok for _, ok in ds.wait_worlds(blind=True))


def test_a_newer_merge_missing_from_the_clone_or_superseded_by_railway_is_not_red() -> None:
    """Merges land close together: a newer sha the run's clone lacks is fetched, and a
    Railway 'superseded' at the timeout is superseded, never 'deploy never went live'."""
    worlds = ds.git_worlds()  # real git on throwaway repos
    assert worlds and all(ok for _, ok in worlds), worlds
    clock = [0.0]
    out = ds.wait_for_sha("a" * 40, ["https://job360.uk/api/version"], 120, 60,
                          fetch=lambda url: "b" * 40, is_ancestor=lambda a, b: False,
                          diagnose=lambda: "superseded", now=lambda: clock[0],
                          sleep=lambda s: clock.__setitem__(0, clock[0] + s))
    assert out["deploy_state"] == "superseded"


# ── wiring: the pieces are actually connected (plain text: no pyyaml in the backend) ──


def test_workflow_waits_on_api_version_at_the_domain_and_feeds_sentry_the_swap_time() -> None:
    wf = WORKFLOW.read_text(encoding="utf-8")
    assert "DOMAIN_URL: https://job360.uk" in wf
    assert '--url "$DOMAIN_URL/api/version"' in wf
    assert "SENTRY_PRESET: post-merge-watch" in wf
    assert "LIVE_SINCE: ${{ steps.wait.outputs.since }}" in wf
    # Sentry and the probes only judge a deploy that is actually serving.
    assert wf.count("if: steps.wait.outputs.deploy_state == 'live'") == 2
    # The old "any readyz 200 means deployed" wait is gone.
    assert "wait for readyz to answer 200" not in wf


def test_railway_healthcheck_is_readyz_and_readyz_is_a_real_route() -> None:
    cfg = json.loads((REPO / "backend" / "railway.json").read_text(encoding="utf-8"))
    assert cfg["deploy"]["healthcheckPath"] == "/api/readyz"
    from src.api.routes import health

    paths = {getattr(r, "path", "") for r in health.router.routes}
    assert {"/readyz", "/version"} <= paths

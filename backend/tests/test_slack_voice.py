"""The harness's voice: it must SPEAK, it must FAIL LOUD, and it must SHUT UP.

WHY THIS TEST EXISTS
--------------------
Nine Slack channels exist, a live `SLACK_BOT_TOKEN` exists as a repo secret, and
`git grep -il slack` over `.github/workflows/` on `origin/main` returns NOTHING.
Twenty-three workflows, not one sender. The plumbing is complete and there is no
water in it.

The previous attempt at a voice (`.github/actions/alert`, Resend email) failed in
the most expensive way available: its contract was "NEVER fail the caller", so a
missing `RESEND_API_KEY` printed a `::warning::` and exited 0 GREEN. Every alert
the harness ever tried to send reached nobody, for weeks, while every workflow
looked healthy. Measured consequence, recorded in that action's own header: 7 open
`triage:needs-human` issues, 6 with ZERO human comments for 3-8 days.

So this file pins THREE properties, and they pull against each other on purpose:

  1. IT SPEAKS.        A well-formed message reaches the API with the right
                       channel and the right text. Asserted against a value the
                       stub actually received, never against the exit code alone
                       (rule #21: value-presence beats schema-presence).
  2. IT FAILS LOUD.    Missing token, `ok:false`, a non-200 -- every one is a
                       non-zero exit with the reason printed. A silent skip on an
                       alerting path IS the bug: it manufactures confidence, so
                       you stop checking, believing you would have been told.
  3. IT SHUTS UP.      green->red is news. red->red is noise. red->green is how
                       he stops worrying. green->green is silence. A notifier
                       that fires on every red run is a notifier that gets muted,
                       and a muted channel is identical to no channel at all.

Property 3 is not an optimisation, it is the difference between a channel that is
read and one that is not. MEASURED over 400 real runs (`gh run list`, 3.61 days,
2026-08-13..17): posting every failure = 62.0 messages/week; posting only
transitions = 19.4/week. `synthetic-live` alone was 15/15 red -> 29 msg/week of
pure repetition, cut to 0.

None of these tests touch the network. The send path talks to a stub HTTP server
on localhost, which is what `SLACK_API_BASE` exists for -- a guard that has only
ever been reasoned about is not a guard (rule #4: mock all HTTP, the suite runs
offline).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
POST_SH = REPO / ".github" / "actions" / "slack" / "post.sh"
TRANSITION = REPO / "scripts" / "slack_transition.py"

BASH = shutil.which("bash")

# `bash`, `curl` and `jq` are all present on the CI runner (ubuntu-latest) and in
# Git Bash locally. If one is genuinely absent we skip LOUDLY rather than quietly
# passing -- a green tick for a test that never ran is the exact lie this whole
# file exists to prevent.
_MISSING = [t for t in ("bash", "curl", "jq") if shutil.which(t) is None]
needs_shell = pytest.mark.skipif(
    bool(_MISSING),
    reason=f"send-path tests need {_MISSING} on PATH — NOT a pass, the test did not run",
)


# ─────────────────────────────────────────────────────────────────────────────
# A stub Slack. It records what it was actually sent, so the happy-path test can
# assert on a real value instead of trusting a zero exit code.
# ─────────────────────────────────────────────────────────────────────────────
class _StubSlack:
    def __init__(self, status: int = 200, payload: dict | None = None) -> None:
        self.status = status
        self.payload = payload if payload is not None else {"ok": True, "ts": "1.23"}
        self.received: list[dict] = []
        self.auth: list[str] = []
        recv, auth, status_, body = self.received, self.auth, status, self.payload

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n).decode("utf-8", "replace")
                try:
                    recv.append(json.loads(raw))
                except json.JSONDecodeError:
                    recv.append({"__unparseable__": raw})
                auth.append(self.headers.get("Authorization", ""))
                out = json.dumps(body).encode()
                self.send_response(status_)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a: object) -> None:  # keep pytest output clean
                return

        self._srv = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self._srv.server_port}"

    def __enter__(self) -> _StubSlack:
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def run_post(env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the REAL post.sh the workflows run — same file, no re-implementation."""
    env = dict(os.environ)
    env.update(env_extra)
    env.pop("GITHUB_STEP_SUMMARY", None)
    assert BASH, "bash missing"
    return subprocess.run(
        [BASH, str(POST_SH)],
        capture_output=True,
        # text=True alone decodes with the Windows locale and dies on an em-dash;
        # post.sh prints em-dashes. There is a guard in this repo for exactly this.
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. IT FAILS LOUD  — the inversion of the old alert action
# ─────────────────────────────────────────────────────────────────────────────
@needs_shell
def test_missing_token_is_a_failure_not_a_skip() -> None:
    """The exact bug that muted this harness for weeks. Blank key => RED."""
    r = run_post({"SLACK_BOT_TOKEN": "", "SLACK_CHANNEL": "log-github-ci", "SLACK_TITLE": "x"})
    assert r.returncode != 0, (
        "A missing SLACK_BOT_TOKEN exited 0. This is the RESEND_API_KEY failure "
        f"reproduced exactly: the harness is mute and looks healthy.\n{r.stdout}{r.stderr}"
    )
    assert "SLACK_BOT_TOKEN" in (r.stdout + r.stderr), "failed without naming the missing key"


@needs_shell
def test_slack_saying_ok_false_is_a_failure() -> None:
    """Slack answers HTTP 200 for application errors. `ok` is the real verdict."""
    with _StubSlack(200, {"ok": False, "error": "channel_not_found"}) as slack:
        r = run_post(
            {
                "SLACK_BOT_TOKEN": "xoxb-test-not-a-real-token",
                "SLACK_CHANNEL": "zzz-does-not-exist",
                "SLACK_TITLE": "x",
                "SLACK_API_BASE": slack.base,
            }
        )
    assert r.returncode != 0, "ok:false exited 0 — a message that never arrived looked sent"
    assert "channel_not_found" in (r.stdout + r.stderr), (
        "the Slack error string is the whole diagnosis and it was not printed"
    )


@needs_shell
def test_non_200_is_a_failure() -> None:
    """Note the second assertion: `returncode != 0` ALONE is a vacuous test.

    A missing post.sh also exits non-zero, so this test passed while nothing was
    implemented. It must prove the request actually reached the API and THEN was
    judged a failure — otherwise it is a guard reporting on a different question,
    which is precisely the shape of the ten dead guards this repo has shipped.
    """
    with _StubSlack(500, {"ok": False, "error": "server_error"}) as slack:
        r = run_post(
            {
                "SLACK_BOT_TOKEN": "xoxb-test-not-a-real-token",
                "SLACK_CHANNEL": "log-github-ci",
                "SLACK_TITLE": "x",
                "SLACK_API_BASE": slack.base,
            }
        )
        got = list(slack.received)
    assert got, "post.sh never reached the API at all — this test would pass vacuously"
    assert r.returncode != 0, "HTTP 500 exited 0"
    assert "500" in (r.stdout + r.stderr), "the HTTP status was not reported"


# ─────────────────────────────────────────────────────────────────────────────
# 2. IT SPEAKS — assert the VALUE that arrived, not that the call returned 0
# ─────────────────────────────────────────────────────────────────────────────
@needs_shell
def test_happy_path_actually_delivers_channel_and_text() -> None:
    with _StubSlack(200, {"ok": True, "ts": "1786796696.205229"}) as slack:
        r = run_post(
            {
                "SLACK_BOT_TOKEN": "xoxb-test-not-a-real-token",
                "SLACK_CHANNEL": "#production-incidents",
                "SLACK_TITLE": "backend is down",
                "SLACK_BODY": "readyz returned 503 — run 42",
                "SLACK_SEVERITY": "critical",
                "SLACK_API_BASE": slack.base,
            }
        )
        got = list(slack.received)
        auth = list(slack.auth)
    assert r.returncode == 0, f"happy path failed: {r.stdout}{r.stderr}"
    assert len(got) == 1, f"expected exactly one POST, got {len(got)}"
    # The leading '#' must be stripped — chat.postMessage rejects '#name'.
    assert got[0]["channel"] == "production-incidents", got[0]
    assert "backend is down" in got[0]["text"]
    assert "readyz returned 503" in got[0]["text"], "the body was dropped"
    # The em-dash must survive the jq -> curl -> HTTP round trip intact. This is
    # not decoration: a locale-decoded pipe anywhere in that path mangles it, and
    # that exact bug crashed the merge cage on 3 of 6 PRs.
    assert "—" in got[0]["text"], f"the em-dash was mangled in transit: {got[0]['text']!r}"
    assert auth[0].startswith("Bearer "), "token was not sent as a bearer credential"


# ─────────────────────────────────────────────────────────────────────────────
# 3. IT SHUTS UP — the volume rule. Announce the TRANSITION, not the STATE.
# ─────────────────────────────────────────────────────────────────────────────
def run_transition(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(env_extra or {})
    env.pop("GITHUB_OUTPUT", None)
    return subprocess.run(
        [sys.executable, str(TRANSITION), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(REPO),
        timeout=60,
    )


def decide(state: str, marker: str) -> dict:
    r = run_transition("--key", "uptime", "--state", state, "--marker", marker)
    assert r.returncode == 0, f"decision failed: {r.stdout}{r.stderr}"
    return json.loads(r.stdout)


def test_green_to_red_announces() -> None:
    """News. Nothing was wrong, now something is."""
    d = decide("failing", "absent")
    assert d["send"] is True, d
    assert d["kind"] == "opened", d


def test_red_to_red_is_silent() -> None:
    """Noise. He already knows. synthetic-live was 15/15 red = 29 msg/week here."""
    d = decide("failing", "open")
    assert d["send"] is False, (
        "a still-failing check announced itself again — this is how a channel "
        f"becomes unread: {d}"
    )


def test_red_to_green_announces() -> None:
    """Relief. This is how he stops worrying, so it must never be suppressed."""
    d = decide("ok", "open")
    assert d["send"] is True, d
    assert d["kind"] == "recovered", d


def test_green_to_green_is_silent() -> None:
    """Uptime ran 142 times in 3.6 days, all green. That must cost zero messages."""
    d = decide("ok", "absent")
    assert d["send"] is False, d


# ─────────────────────────────────────────────────────────────────────────────
# 4. IT NEVER GUESSES — an unknown previous state is not "no previous state"
# ─────────────────────────────────────────────────────────────────────────────
def test_unreadable_marker_fails_loudly_and_decides_nothing() -> None:
    """If we cannot read the previous state we cannot know it is a transition.

    Guessing "absent" makes every run look like green->red and spams; guessing
    "open" makes every run silent and goes mute. Both are the dead-guard shape:
    a confident answer to a question that was never actually asked. The only
    honest move is to refuse, loudly.
    """
    r = run_transition(
        "--key", "uptime", "--state", "failing",
        env_extra={"SLACK_TRANSITION_GH": "definitely-not-a-real-binary-xyz"},
    )
    assert r.returncode != 0, (
        f"the marker was unreadable and it produced a decision anyway: {r.stdout}"
    )
    assert '"send"' not in r.stdout, (
        "it emitted a send decision despite not knowing the previous state"
    )
    # Without this, a missing script file would satisfy the assertions above and
    # the test would be green against nothing at all.
    blob = (r.stdout + r.stderr).lower()
    assert "marker" in blob or "previous state" in blob, (
        f"it failed, but not for the reason under test — it must say it could not "
        f"read the previous state:\n{r.stdout}\n{r.stderr}"
    )


def test_drill_entrance_exists_and_passes() -> None:
    """The guard must be able to prove itself red on demand, from a laptop."""
    r = run_transition("--drill")
    assert r.returncode == 0, f"--drill did not pass:\n{r.stdout}\n{r.stderr}"

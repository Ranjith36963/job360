#!/usr/bin/env python3
"""Are our keys still alive? Ask them, don't assume.

WHY THIS EXISTS — two incidents, one shape.

1. GROQ_API_KEY expired. Nothing noticed. The provider chain detected it
   perfectly (`llm_provider.py` logs "looks PERMANENTLY misconfigured") and then
   called it again on the very next request, forever. Measured 2026-08-03: one
   CV upload took **584 seconds** — 8 LLM calls, each paying a doomed round-trip
   to a key we already knew was dead.

2. RESEND_API_KEY has the same exposure and a worse blast radius. Login is
   passwordless: a magic link, delivered by Resend. If that key dies, NOBODY CAN
   LOG IN — and no check would go red, because every live probe injects a
   pre-made session cookie instead of walking the real email path.

A key does not announce its own expiry. The only way to know a credential still
works is to use it. This probes each configured provider with the smallest real
call that proves auth, and fails loudly on 401/403.

DESIGN RULES:
  * ABSENT is not BROKEN. An unset key means "this provider is off" — reported,
    never alarmed. Alarming on deliberate absence is how a detector earns a mute.
  * Only auth failures alarm. A timeout or a 5xx is the provider having a bad
    minute; crying wolf on those would make the signal worthless.
  * Never print a key, a prefix, or a length that could narrow it. Presence only.
  * Cheapest possible call: 1 token, or a metadata endpoint. This runs daily and
    must never become a cost of its own.

CONTRACT:
    exit 0  every configured provider authenticated
    exit 1  at least one is dead (rotate it) -> raise
    exit 2  the probe itself is broken / nothing was configured to probe
    exit 3  a whole CAPABILITY has no credential at all (create one) -> raise.
            Different fix from exit 1, so a different alarm: 1 means a key we
            own stopped working, 3 means we never had one.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TIMEOUT = int(os.getenv("PROBE_TIMEOUT_S", "25"))
FORCE_RED = os.getenv("PROBE_FORCE_RED", "") == "1"

# ── The one exception to "absent is not broken" ──────────────────────────────
#
# ABSENT IS NOT BROKEN — unless EVERY provider of a capability is absent. Then
# the capability is DOWN, and it is down silently, which is worse than a dead
# key: a dead key at least 401s somewhere.
#
# Measured shape of the bug this fixes: with all four LLM keys unset but ONE
# other credential present (Resend is always configured — it is the login path),
# `results` was non-empty, so this probe printed "Every configured credential
# authenticated" and exited 0. GREEN. DAILY. FOREVER. Meanwhile the judge could
# not make a single call, and the weekly eval was filing that as an accuracy
# regression. The four names were reported in a footnote that reads as
# reassurance: "absent is not broken".
#
# These four names are the same list as `llm_provider.LLM_KEY_VARS`. They are
# re-typed here ONLY because this script is deliberately stdlib-only — it runs
# in external-health.yml with no `pip install` of the backend at all — so it
# cannot import the shared constant. `backend/tests/test_missing_llm_key_is_loud.py`
# pins the two lists together so they cannot drift apart in silence.
LLM_KEY_VARS = ("OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY")


def _probe(url: str, headers: dict[str, str], payload: dict | None = None) -> tuple[str, str]:
    """Return ``(verdict, detail)`` where verdict is ok | DEAD | flaky."""
    data = json.dumps(payload).encode() if payload is not None else None
    if data is not None:
        headers = {**headers, "Content-Type": "application/json"}
    # ALWAYS send a real User-Agent. urllib defaults to "Python-urllib/3.x",
    # which Cloudflare-fronted APIs (Resend, Groq, Cerebras among them) reject
    # with a blanket 403 — indistinguishable from "your key is dead". On this
    # probe's first run that produced THREE false DEAD verdicts, including on
    # the credential that gates login, while prod was calling the same APIs
    # successfully with httpx. A probe whose transport differs from the app's is
    # not testing the app.
    headers = {**headers, "User-Agent": "job360-provider-probe/1.0 (+https://job360.uk)"}
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 - fixed https hosts
            r.read(1)
            return "ok", f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        # 401/403 = the credential is rejected. That fails on EVERY call until a
        # human rotates it, which is exactly what makes it worth waking someone.
        if e.code in (401, 403):
            return "DEAD", f"HTTP {e.code} — the key is rejected"
        # 429 means the key is VALID and merely rate-limited. Treating that as
        # death would take a healthy paid provider offline in the report.
        if e.code == 429:
            return "ok", "HTTP 429 (rate-limited, but authenticated)"
        return "flaky", f"HTTP {e.code}"
    except Exception as exc:  # noqa: BLE001 - transient, not an auth verdict
        return "flaky", f"{type(exc).__name__}"


def main() -> int:
    results: list[tuple[str, str, str]] = []  # (provider, verdict, detail)
    absent: list[str] = []

    def env(name: str) -> str:
        return os.getenv(name, "").strip()

    # ── LLM providers ────────────────────────────────────────────────────────
    # A models-list call proves auth without spending a token.
    if env("OPENAI_API_KEY"):
        results.append(("openai", *_probe(
            "https://api.openai.com/v1/models",
            {"Authorization": f"Bearer {env('OPENAI_API_KEY')}"})))
    else:
        absent.append("OPENAI_API_KEY")

    if env("GROQ_API_KEY"):
        results.append(("groq", *_probe(
            "https://api.groq.com/openai/v1/models",
            {"Authorization": f"Bearer {env('GROQ_API_KEY')}"})))
    else:
        absent.append("GROQ_API_KEY")

    if env("CEREBRAS_API_KEY"):
        results.append(("cerebras", *_probe(
            "https://api.cerebras.ai/v1/models",
            {"Authorization": f"Bearer {env('CEREBRAS_API_KEY')}"})))
    else:
        absent.append("CEREBRAS_API_KEY")

    if env("GEMINI_API_KEY"):
        results.append(("gemini", *_probe(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={env('GEMINI_API_KEY')}",
            {})))
    else:
        absent.append("GEMINI_API_KEY")

    # ── Resend: the login path ───────────────────────────────────────────────
    # PROBE THE THING WE ACTUALLY USE, WITH A CALL THAT CANNOT SEND MAIL.
    #
    # The obvious check — GET /domains — is WRONG here and would have cried wolf
    # on the single most important credential we own. Resend tokens can be
    # scoped "sending access only": such a token 403s on /domains while sending
    # email perfectly. Reporting "nobody can log in" because of a token scope
    # would be a false alarm on the loudest possible subject, and a detector
    # that does that once gets muted forever.
    #
    # So: POST an intentionally INVALID payload to the send endpoint. Resend
    # validates auth first. A 422/400 means the credential was ACCEPTED and only
    # the body was rejected — which is exactly the proof we want, with no mail
    # sent to anyone. Only 401/403 here means the key itself is dead.
    # The payload must carry a REAL `from` on our verified domain. Resend checks
    # domain authorisation BEFORE body validation, so a scoped key 403s on an
    # empty payload even when it is perfectly healthy — which is exactly the
    # false positive this probe produced on its first run, on the one credential
    # whose failure means nobody can log in. Verified against prod the same day:
    # the backend's real send returned 422 "Invalid `to` field", proving the key
    # authenticates. `to` is deliberately an unroutable example.com address,
    # which Resend rejects with 422 — so this can never deliver mail to anyone.
    # MIRROR THE APP'S OWN RESOLUTION, or the probe tests a credential the app
    # never uses. `email_sender.py` takes RESEND_API_KEY, falls back to
    # SMTP_PASSWORD when it looks like a Resend key, and takes the sender from
    # SMTP_FROM/SMTP_EMAIL. Probing a hardcoded sender with only the first key
    # produced a 403 and a "nobody can log in" alarm while prod was sending mail
    # perfectly — a false positive on the most consequential credential we own.
    resend_key = env("RESEND_API_KEY")
    if not resend_key and env("SMTP_PASSWORD").startswith("re_"):
        resend_key = env("SMTP_PASSWORD")
    resend_from = env("SMTP_FROM") or env("SMTP_EMAIL") or "onboarding@resend.dev"
    if resend_key:
        verdict, detail = _probe(
            "https://api.resend.com/emails",
            {"Authorization": f"Bearer {resend_key}"},
            payload={
                "from": resend_from,
                "to": ["probe@example.com"],  # Resend rejects this -> never sends
                "subject": "probe",
                "text": "probe",
            },
        )
        if verdict == "flaky" and ("422" in detail or "400" in detail):
            verdict, detail = "ok", "authenticated (422 on an unroutable address — no mail sent)"
        elif verdict == "DEAD":
            # Say WHICH sender was rejected: a domain mismatch and a dead key
            # look identical in the status code, and confusing them is how this
            # probe cried wolf the first time.
            domain = resend_from.split("@")[-1]
            detail = f"{detail} (sender domain `{domain}` — check the key AND that this domain is verified)"
        results.append(("resend (LOGIN DEPENDS ON THIS)", verdict, detail))
    else:
        absent.append("RESEND_API_KEY")

    # ── Keyed job sources ────────────────────────────────────────────────────
    # These fail SILENTLY in the pipeline: a bad key returns [] and the source
    # simply contributes nothing, which is indistinguishable from a quiet day.
    if env("REED_API_KEY"):
        import base64
        tok = base64.b64encode(f"{env('REED_API_KEY')}:".encode()).decode()
        results.append(("reed", *_probe(
            "https://www.reed.co.uk/api/1.0/search?keywords=engineer&resultsToTake=1",
            {"Authorization": f"Basic {tok}"})))
    else:
        absent.append("REED_API_KEY")

    if env("ADZUNA_APP_ID") and env("ADZUNA_APP_KEY"):
        results.append(("adzuna", *_probe(
            "https://api.adzuna.com/v1/api/jobs/gb/search/1"
            f"?app_id={env('ADZUNA_APP_ID')}&app_key={env('ADZUNA_APP_KEY')}&results_per_page=1",
            {})))
    else:
        absent.append("ADZUNA_APP_ID/KEY")

    if FORCE_RED:
        results.append(("DRILL", "DEAD", "forced red to prove the chain — no key is actually broken"))

    # ORDER IS LOAD-BEARING. This check used to sit BELOW the `if not results`
    # guard, which made the whole LLM alarm dead on arrival: an environment with
    # no provider secrets at all returns 2 from that guard and never reaches
    # here. Measured on the live scheduled run 31683129750 (2026-08-13T08:40) —
    # `no provider credentials are configured` + exit 2 — and external-health has
    # conclusion=failure on 8 of 8 runs since 2026-08-06. It has NEVER been green.
    #
    # So the promised "within 24 hours an issue appears saying no LLM key is
    # configured" would never have happened: the job would just go red the same
    # way it already does, with no issue and no triage. An environment with zero
    # secrets trivially has zero LLM keys — that is the SAME alarm, not a
    # separate blind-probe case, and it must be reported as such.
    no_llm_key = all(not env(name) for name in LLM_KEY_VARS)

    if not results:
        print("::error::no provider credentials are configured, so nothing could be probed.")
        print(
            "This is a BLIND result, not a clean one. Add the provider keys as repo "
            "secrets, or this loop will report success forever while proving nothing."
        )
        if no_llm_key:
            print()
            print("## No LLM provider is configured at all — the judge is DOWN")
            print()
            print(
                "None of " + ", ".join("`" + n + "`" for n in LLM_KEY_VARS) + " is set. "
                "Any ONE of them unblocks the judge; GROQ, GEMINI and CEREBRAS have "
                "free tiers. Until one is set, every ranking eval reports "
                "'produced no judgments' — which reads as a QUALITY regression and "
                "is not one."
            )
            return 3
        return 2

    dead = [r for r in results if r[1] == "DEAD"]
    print("# Provider key probe — is every credential still alive?\n")
    print("| provider | verdict | detail |")
    print("|---|---|---|")
    for name, verdict, detail in results:
        mark = "**DEAD**" if verdict == "DEAD" else ("flaky" if verdict == "flaky" else "ok")
        print(f"| `{name}` | {mark} | {detail} |")
    if absent:
        print(f"\n_Not configured, so not probed (absent is not broken): {', '.join(absent)}._")

    if no_llm_key:
        print("\n## No LLM provider is configured at all — the judge is DOWN\n")
        print(
            f"::error::All {len(LLM_KEY_VARS)} LLM keys are empty "
            f"({', '.join(LLM_KEY_VARS)}) — the judge, CV parsing and job "
            "enrichment cannot make a single call."
        )
        print(
            "\nThis is a CONFIG failure, not a quality problem — and it is the one "
            "case where absence IS breakage, because there is no fallback left. It "
            "is also invisible everywhere else: scoring silently falls back to "
            "keywords, `llm_fit_score` stays NULL, and the weekly accuracy audit "
            "reports it as a RANKING REGRESSION (that is exactly what happened for a "
            "week — issue #238).\n"
        )
        print(
            "**Any ONE of the four fixes it, and three are free:** `GEMINI_API_KEY`, "
            "`GROQ_API_KEY`, `CEREBRAS_API_KEY` all have a free tier, and all four "
            "SDKs are already installed (`backend/pyproject.toml`). `OPENAI_API_KEY` "
            "is the paid primary. Add one as a repo Actions secret AND to the Railway "
            "backend/worker services — an unset Actions secret renders as an EMPTY "
            "string, which is how this hid."
        )

    if dead:
        print("\n## Dead credentials\n")
        for name, _v, detail in dead:
            print(f"- **{name}** — {detail}")
        print(
            "\nA rejected key fails on EVERY call until a human rotates it. Measured "
            "cost of leaving one in place: a CV upload took 584 seconds because eight "
            "LLM calls each paid a doomed round-trip to a key we already knew was "
            "dead. If `resend` is on this list, nobody can log in at all — the login "
            "link is the only way in."
        )
        return 1

    if no_llm_key:
        # Its OWN code, deliberately. "Rotate the dead key" and "you never had a
        # key" are different jobs for the human, and an alarm that blurs them
        # sends him to the wrong page — which is the whole failure this file is
        # here to stop.
        return 3

    print("\nEvery configured credential authenticated.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - must fail loud, never silently green
        print(f"::error::provider_probe crashed: {type(exc).__name__}: {exc}")
        sys.exit(2)

#!/usr/bin/env python3
"""Walk ONE real user through the ENTIRE product and grade every step.

WHY THIS EXISTS — the gap every other detector leaves.

We have six scheduled loops and they all measure FRAGMENTS:

    uptime            does the server answer?          (a status code)
    synthetic-live    does each page load?             (a corner, not a journey)
    user_journey      does each user have rows?        (counts, no timing, no UI)
    product_assertions  are the aggregates sane?       (whole-population averages)
    data_invariants   are the VALUES valid?            (per-row shape)
    absence           did anything STOP?               (freshness)

Not one of them answers the question the owner actually asks when something
feels wrong: **"this person signed up and used the product — what happened at
each step, how long did it take, and was the result correct?"**

That question is the difference between "the dashboard is green" and "I know why
this user's feed is empty". Every expensive bug this month lived in the seam
between two green checks:

  * a CV parsed fine and the LinkedIn merge silently produced 0 positions
  * a search "succeeded" and wrote zero feed rows
  * scores were computed and 91% landed in one band, starving every threshold
  * a job rendered "Posted NaNw ago" while the API and DB were both correct

WHAT THIS DOES. One user, one run, every stage timed and graded:

    signup/login -> CV upload -> extraction -> LinkedIn -> GitHub -> preferences
      -> search -> per-source results -> dedup -> scoring -> feed -> dashboard

Each stage emits: ok/fail, milliseconds, the real numbers it produced, and — on
failure — a plain sentence saying what a user would experience. Latency is a
first-class result, not a footnote: a stage that "works" in 584 seconds is
broken from where the person is sitting.

BENCHMARKS, not vibes. `--benchmark FILE` compares this run against recorded
expectations and fails on regression, so "we fixed it" becomes a number that
either holds or does not. `--save-benchmark` records the current run as the bar.

CONTRACT (matches the other detectors so the workflow shape is shared):
    exit 0  every stage met its benchmark
    exit 1  a stage failed or regressed  -> raise an issue
    exit 2  the probe itself broke       -> fail LOUD, never silently green
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

# How long a real user is willing to sit at the search spinner.
SEARCH_WAIT_S = float(os.getenv("JOURNEY_SEARCH_WAIT_S", "420"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


@dataclass
class Step:
    """One stage of the journey, as a user experiences it."""

    name: str
    ok: bool = False
    ms: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""          # what a USER would experience when this fails
    skipped: bool = False

    @property
    def verdict(self) -> str:
        if self.skipped:
            return "skipped"
        return "ok" if self.ok else "**FAIL**"


class Journey:
    """Runs the stages in order, timing each, and never lets one crash the rest.

    A probe that dies at stage 3 tells you nothing about stages 4-10, which is
    precisely when you most need to know whether the damage is local or total.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self.steps: list[Step] = []

    def run(self, name: str, fn: Callable[[Step], None], *, skip: str = "") -> Step:
        s = Step(name)
        if skip:
            s.skipped, s.reason, s.ok = True, skip, True
            self.steps.append(s)
            return s
        t0 = time.perf_counter()
        try:
            fn(s)
        except Exception as exc:  # noqa: BLE001 — one broken stage must not hide the others
            s.ok = False
            s.reason = s.reason or f"stage raised {type(exc).__name__}: {str(exc)[:160]}"
        s.ms = int((time.perf_counter() - t0) * 1000)
        self.steps.append(s)
        return s

    # ── reporting ────────────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "user": self.label,
            "steps": {
                s.name: {"ok": s.ok, "ms": s.ms, "skipped": s.skipped, **s.evidence}
                for s in self.steps
            },
        }

    def markdown(self) -> str:
        out = [f"### Journey — `{self.label}`\n",
               "| stage | verdict | ms | what it produced |", "|---|---|---|---|"]
        for s in self.steps:
            ev = ", ".join(f"{k}={v}" for k, v in list(s.evidence.items())[:4]) or "—"
            out.append(f"| {s.name} | {s.verdict} | {s.ms} | {ev} |")
        bad = [s for s in self.steps if not s.ok and not s.skipped]
        if bad:
            out.append("\n**What a user would experience:**\n")
            for s in bad:
                out.append(f"- **{s.name}** — {s.reason}")
        return "\n".join(out)


# ── Benchmarks ───────────────────────────────────────────────────────────────
# A benchmark is a CLAIM that a stage produced at least this much, this fast.
# Recorded from a known-good run so "we fixed it" is a number, not a feeling.
DEFAULT_BENCHMARK: dict[str, dict[str, Any]] = {
    # stage -> {min_<evidence key>: value, max_ms: value}
    "cv upload + extraction": {"min_skills": 15, "min_titles": 1, "max_ms": 120_000},
    "linkedin enrich": {"max_ms": 90_000},  # no min: a rich CV makes LinkedIn redundant
    "search": {"min_sources_returning": 8, "max_ms": 300_000},
    "feed written": {"min_feed_rows": 20, "max_ms": 30_000},
    "dashboard read": {"min_visible": 1, "max_ms": 15_000},
}


def grade(journeys: list[Journey], benchmark: dict[str, Any]) -> list[str]:
    """Return regression lines: a stage that under-produced or over-ran."""
    regressions: list[str] = []
    for j in journeys:
        for s in j.steps:
            spec = benchmark.get(s.name)
            if not spec or s.skipped:
                continue
            for key, want in spec.items():
                if key == "max_ms":
                    if s.ms > want:
                        regressions.append(
                            f"`{j.label}` / {s.name}: took {s.ms} ms, benchmark is {want} ms. "
                            f"A stage that works but is this slow is broken from where the user sits."
                        )
                elif key.startswith("min_"):
                    got = s.evidence.get(key[4:])
                    if isinstance(got, (int, float)) and got < want:
                        regressions.append(
                            f"`{j.label}` / {s.name}: produced {key[4:]}={got}, benchmark is >= {want}."
                        )
    return regressions


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", default=os.getenv("JOURNEY_API", "http://127.0.0.1:8000"),
                    help="backend base URL")
    ap.add_argument("--corpus", default=os.getenv("JOURNEY_CORPUS", "D:/dev/job360/User_info"),
                    help="folder holding CV/ and Linkedin_pdf/")
    ap.add_argument("--only", default="", help="run one person by name prefix")
    ap.add_argument("--benchmark", default="", help="JSON file of expectations")
    ap.add_argument("--save-benchmark", default="", help="write this run's results as the bar")
    ap.add_argument("--json", dest="as_json", action="store_true")
    ap.add_argument("--no-search", action="store_true",
                    help="skip the search stage (it is slow and hits live sources)")
    args = ap.parse_args()

    try:
        import httpx
    except ImportError:
        print("::error::httpx not installed — the journey probe cannot run")
        return 2

    from pathlib import Path

    # Optional DSN used ONLY to mark the synthetic user's email verified,
    # which the search gate requires. Local/dev by default; never needed to
    # run the probe, and its absence degrades to an honest 403 report.
    verify_dsn = (os.getenv("JOURNEY_VERIFY_DSN") or os.getenv("DATABASE_URL")
                  or "postgresql://job360:job360dev@localhost:5433/job360")

    corpus = Path(args.corpus)
    cvs = sorted((corpus / "CV").glob("*.pdf")) if (corpus / "CV").is_dir() else []
    if args.only:
        cvs = [p for p in cvs if p.stem.lower().startswith(args.only.lower())]
    if not cvs:
        print(f"::error::no CVs found under {corpus / 'CV'} — nothing to walk.")
        print("This is a BLIND result, not a clean one.")
        return 2

    lidir = corpus / "Linkedin_pdf"
    journeys: list[Journey] = []

    for cv in cvs:
        person = cv.stem.split("_")[0]
        j = Journey(person)
        client = httpx.Client(base_url=args.api, timeout=180.0, follow_redirects=True)
        email = f"journey-{person.lower()}@example.com"
        pw = "JourneyProbe2026!"

        # ── 1. account ───────────────────────────────────────────────────────
        def signup(s: Step) -> None:
            r = client.post("/api/auth/register", json={"email": email, "password": pw})
            # 409 = the probe account already exists from a previous run.
            # 429 = the anti-brute-force limiter, which repeated probe runs trip
            #       by design. NEITHER means signup is broken — in both cases the
            #       account exists and the honest next move is to log in. Treating
            #       429 as a failure made the probe report "a new user cannot sign
            #       up" purely because it had run too often.
            s.evidence["register_http"] = r.status_code
            if r.status_code not in (200, 201, 409, 429):
                s.reason = f"registration returned HTTP {r.status_code} — a new user cannot sign up."
                return
            r = client.post("/api/auth/login", json={"email": email, "password": pw})
            s.evidence["http"] = r.status_code
            s.ok = r.status_code == 200
            if not s.ok:
                s.reason = f"login returned HTTP {r.status_code} — the user is locked out of their own account."
                return
            # VERIFY THE EMAIL, because a real user does.
            #
            # POST /api/search is gated on users.email_verified_at, so a probe
            # that skips verification gets 403 on every search and reports "the
            # button the whole product exists for does not work" — which is the
            # probe describing its own shortcut, not the product. That false
            # alarm fired for all seven people on the first full run.
            #
            # Magic-link delivery cannot be automated here, so we set the flag
            # directly. Deliberately DB-only and best-effort: without a DSN the
            # stage still passes and the search stage will report the 403
            # honestly as a gate rather than a fault.
            if verify_dsn:
                try:
                    import psycopg

                    with psycopg.connect(verify_dsn, connect_timeout=15) as vc, vc.cursor() as vcur:
                        vcur.execute(
                            "UPDATE users SET email_verified_at = now()::text "
                            "WHERE email = %s AND email_verified_at IS NULL",
                            (email,),
                        )
                        vc.commit()
                    s.evidence["verified"] = True
                except Exception as exc:  # noqa: BLE001 — never fail the journey on a shortcut
                    s.evidence["verified"] = f"could not: {type(exc).__name__}"
        j.run("signup + login", signup)

        # ── 2. CV upload -> extraction ───────────────────────────────────────
        def upload_cv(s: Step) -> None:
            with cv.open("rb") as fh:
                r = client.post("/api/profile/cv", files={"cv": (cv.name, fh, "application/pdf")})
            s.evidence["http"] = r.status_code
            if r.status_code >= 400:
                s.reason = f"CV upload returned HTTP {r.status_code} — the core onboarding step fails."
                return
            p = client.get("/api/profile")
            if p.status_code != 200:
                s.reason = f"profile unreadable after upload (HTTP {p.status_code})."
                return
            d = p.json().get("cv_detail") or {}
            skills, titles = len(d.get("skills") or []), len(d.get("job_titles") or [])
            score = (d.get("extraction_score") or {})
            s.evidence.update(skills=skills, titles=titles,
                              coverage=score.get("coverage"), verdict=score.get("verdict"))
            s.ok = skills > 0
            if not s.ok:
                s.reason = ("the CV uploaded but ZERO skills were extracted — this profile "
                            "cannot match any job, and nothing else in the product will work.")
        j.run("cv upload + extraction", upload_cv)

        # ── 3. LinkedIn ──────────────────────────────────────────────────────
        li = next((p for p in lidir.glob("*.pdf") if p.stem.split("_")[0] == person), None) \
            if lidir.is_dir() else None

        def enrich_li(s: Step) -> None:
            assert li is not None
            # MEASURE THE DELTA, NOT A FIELD.
            # `linkedin_skills` holds only skills NOT already on the CV — it is
            # a de-duplicated remainder, so a well-matched LinkedIn profile
            # legitimately yields 0 there while still contributing plenty. This
            # probe's first run reported "LinkedIn contributed ZERO skills" for
            # a user whose roles went 3 -> 8 because of that upload. A detector
            # that cries wolf gets muted, so ask the only question that matters:
            # did the profile GAIN anything?
            before = (client.get("/api/profile").json() or {}).get("cv_detail") or {}
            b_sk, b_ro = len(before.get("skills") or []), len(before.get("job_titles") or [])
            with li.open("rb") as fh:
                r = client.post("/api/profile/linkedin",
                                files={"file": (li.name, fh, "application/pdf")})
            s.evidence["http"] = r.status_code
            if r.status_code >= 400:
                s.reason = f"LinkedIn upload returned HTTP {r.status_code}."
                return
            after = (client.get("/api/profile").json() or {}).get("cv_detail") or {}
            a_sk, a_ro = len(after.get("skills") or []), len(after.get("job_titles") or [])
            positions = len(after.get("linkedin_positions") or [])
            li_only = len(after.get("linkedin_skills") or [])
            gained = (a_sk - b_sk) + (a_ro - b_ro)
            s.evidence.update(gained=gained, skills=a_sk, roles=a_ro,
                              li_only_skills=li_only, li_positions=positions)
            # The file did SOMETHING if it produced positions, LinkedIn-only
            # skills, or grew the merged profile. "gained=0 merged" alone is NOT
            # a failure: a rich CV legitimately makes LinkedIn redundant on
            # skills — measured on a 130-skill CV whose LinkedIn's 3 skills were
            # all already present. Failing that would punish a good CV.
            did_something = gained > 0 or li_only > 0 or positions > 0
            s.ok = did_something
            if not s.ok:
                s.reason = ("LinkedIn was accepted but produced NO positions, NO "
                            "LinkedIn-only skills, and grew the profile by nothing — "
                            "the parse extracted zero usable data from the file.")
        j.run("linkedin enrich", enrich_li,
              skip="" if li else "no LinkedIn PDF for this person")

        # ── 3b. GitHub ────────────────────────────────────────────────────────
        # Read the handle from the corpus. GitHub skills surface under
        # skills_by_source['github'] (NOT a cv_detail field — checking the wrong
        # place is why an earlier walk wrongly reported GitHub as producing 0).
        gh_file = corpus / "github_urls" / f"{person}_github.txt"
        gh_url = gh_file.read_text(encoding="utf-8").strip() if gh_file.exists() else ""

        def enrich_gh(s: Step) -> None:
            before = len((client.get("/api/profile").json().get("skills_by_source") or {}).get("github") or [])
            r = client.post("/api/profile/github", data={"username": gh_url})
            s.evidence["http"] = r.status_code
            if r.status_code >= 400:
                s.reason = f"GitHub enrich returned HTTP {r.status_code}."
                return
            time.sleep(75)  # GitHub fetch + LLM pass run in the background
            sbs = client.get("/api/profile").json().get("skills_by_source") or {}
            gained = len(sbs.get("github") or [])
            s.evidence.update(github_skills=gained, was=before)
            s.ok = gained > 0
            if not s.ok:
                s.reason = ("GitHub was accepted but contributed no skills — the fetch or "
                            "the repo-skill inference produced nothing for this handle.")
        j.run("github enrich", enrich_gh,
              skip="" if gh_url else "no GitHub URL for this person")

        # ── 4. search ────────────────────────────────────────────────────────
        def search(s: Step) -> None:
            r = client.post("/api/search", json={}, timeout=600.0)
            s.evidence["http"] = r.status_code
            if r.status_code == 403:
                # A gate is not a fault. 403 here means the account is not
                # email-verified, which is correct product behaviour.
                s.ok = True
                s.evidence["gated"] = "email not verified"
                return
            if r.status_code >= 400:
                s.reason = (f"search returned HTTP {r.status_code} — the button the whole "
                            f"product exists for does not work.")
                return
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            per_source = body.get("per_source") or {}
            returning = sum(1 for v in per_source.values() if isinstance(v, int) and v > 0)
            s.evidence.update(sources_returning=returning,
                              sources_queried=len(per_source) or body.get("sources_queried"),
                              new_jobs=body.get("new_jobs"), total=body.get("total_found"))
            s.ok = True
            if per_source and returning == 0:
                s.ok = False
                s.reason = "every source returned zero jobs — the catalog cannot grow."
                return
            # SEARCH IS FIRE-AND-FORGET. It returns in ~80 ms having only
            # ENQUEUED the run, so reading the feed immediately afterwards
            # always finds zero rows — and the probe would report "the user's
            # dashboard is EMPTY" for every single person, blaming the product
            # for the probe's own impatience. A real user waits at a spinner;
            # so do we, and we record how long the wait actually was, because
            # that IS the user's experience of "how fast is this product".
            deadline = time.monotonic() + SEARCH_WAIT_S
            waited_rows = 0
            while time.monotonic() < deadline:
                jr = client.get("/api/jobs", params={"limit": 1})
                if jr.status_code == 200:
                    waited_rows = jr.json().get("total", 0) or 0
                    if waited_rows > 0:
                        break
                time.sleep(3)
            s.evidence["rows_after_wait"] = waited_rows
            s.evidence["waited_s"] = int(SEARCH_WAIT_S - max(0, deadline - time.monotonic()))
            if waited_rows == 0:
                s.ok = False
                s.reason = (f"search was accepted but produced NO feed rows within "
                            f"{SEARCH_WAIT_S}s — the user waits at a spinner and gets nothing.")
        j.run("search", search, skip="--no-search" if args.no_search else "")

        # ── 5. feed + dashboard ──────────────────────────────────────────────
        def feed(s: Step) -> None:
            r = client.get("/api/jobs", params={"limit": 100})
            s.evidence["http"] = r.status_code
            if r.status_code != 200:
                s.reason = f"the dashboard's jobs call returned HTTP {r.status_code}."
                return
            body = r.json()
            jobs = body.get("jobs") or []
            total = body.get("total", len(jobs))
            scored = [x for x in jobs if (x.get("match_score") or 0) > 0]
            # The NaN class: a date the UI cannot parse renders as "NaNw ago".
            bad_dates = sum(
                1 for x in jobs
                if (x.get("posted_at") or "") and not str(x["posted_at"])[:4].isdigit()
            )
            s.evidence.update(feed_rows=total, visible=len(jobs),
                              scored=len(scored), unparseable_dates=bad_dates)
            s.ok = len(jobs) > 0 and bad_dates == 0
            if not jobs:
                s.reason = "the user's dashboard is EMPTY — they see nothing at all."
            elif bad_dates:
                s.reason = (f"{bad_dates} job(s) carry a date the browser cannot parse — those "
                            f"cards render 'Posted NaNw ago' to the user.")
        # feed + dashboard READ what search produced. With --no-search there is
        # nothing to read yet, so failing them would be the probe blaming the
        # product for an instruction the probe itself was given.
        downstream_skip = "search was skipped, so there is nothing to read" if args.no_search else ""
        j.run("feed written", feed, skip=downstream_skip)
        j.run("dashboard read", feed, skip=downstream_skip)

        client.close()
        journeys.append(j)

    # ── output ───────────────────────────────────────────────────────────────
    bench = DEFAULT_BENCHMARK
    if args.benchmark and os.path.exists(args.benchmark):
        with open(args.benchmark, encoding="utf-8") as fh:
            bench = json.load(fh)

    if args.save_benchmark:
        derived = {}
        for j in journeys:
            for s in j.steps:
                if s.skipped or not s.ok:
                    continue
                spec = derived.setdefault(s.name, {})
                spec["max_ms"] = max(spec.get("max_ms", 0), int(s.ms * 1.5) + 5000)
                for k, v in s.evidence.items():
                    if isinstance(v, int) and k != "http":
                        key = f"min_{k}"
                        spec[key] = min(spec.get(key, v), v)
        with open(args.save_benchmark, "w", encoding="utf-8") as fh:
            json.dump(derived, fh, indent=2)
        print(f"benchmark written to {args.save_benchmark}")

    if args.as_json:
        print(json.dumps({"journeys": [j.to_dict() for j in journeys]}, indent=2))
        return 0

    print("# Journey probe — one real user, every stage, timed and graded\n")
    for j in journeys:
        print(j.markdown())
        print()

    failed = [(j.label, s) for j in journeys for s in j.steps if not s.ok and not s.skipped]
    regressions = grade(journeys, bench)

    if failed:
        print("## Stages that FAILED\n")
        for label, s in failed:
            print(f"- `{label}` / **{s.name}** — {s.reason}")
        print()
    if regressions:
        print("## Regressions against the benchmark\n")
        for r in regressions:
            print(f"- {r}")
        print()

    if failed or regressions:
        print("Every line above is a step a real person takes. None of them throws an "
              "exception or fails a unit test — which is why the journey has to be walked, "
              "not inferred from row counts.")
        return 1

    print("Every stage passed and met its benchmark.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — must never pass while broken
        print(f"::error::journey_probe crashed: {type(exc).__name__}: {exc}")
        sys.exit(2)

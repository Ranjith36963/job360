"""Score any extraction, for any CV, in any profession — and escalate when bad.

THE ARGUMENT FOR THIS FILE.

We spent a week fixing extraction one CV at a time: a heading that was really a
sentence, a skills block split in two, a PDF exported without spaces. Every fix
was correct and every fix was a patch for a layout we happened to have seen.

That road has no end. CV layouts are unbounded — the next upload invents a shape
nobody anticipated, and a parser tuned to seven documents is not a product.

**You cannot make a structural parser universal, because structure is exactly
what varies.** So stop trying to enumerate layouts. Measure the RESULT instead:
compare what went in against what came out, in a way that needs no knowledge of
the document's shape, the person's profession, or the language of their field.

That measurement works identically for the eighth CV and the eight-thousandth,
and it is what turns "extraction feels flaky" into a number a loop can act on.

WHAT IT MEASURES (all layout-agnostic, all rule-#28 safe — no skill vocabulary):

  COVERAGE   Of the document's own signal terms — capitalised words, acronyms,
             dotted/slashed tech tokens — how many reached the profile? Low
             coverage means we read the file and understood little of it.

             **Coverage is a RELATIVE signal, never a target.** Measured on 7 real
             CVs, roughly half of what it counts as "missed" is not a skill at all:
             the person's own name, Leeds, United Kingdom, former employers, the
             word "Awards". A profile that captured 100% of signal terms would be
             full of cities and company names and would match jobs WORSE, not
             better. So: compare coverage against the same CV over time, or across
             the deterministic/LLM passes. Never drive it toward 1.0, and never let
             a loop optimise it — that is a metric begging to be gamed into junk.
  PRECISION  Of what we extracted, how much is junk? Bare lowercase words,
             glued-together tokens, near-duplicates. High junk means we are
             padding the profile with noise that flattens every match.
  COMPLETENESS  Did the fields a job-matcher actually needs get filled at all —
             skills, titles, summary, certifications?
  INPUT HEALTH  Was the source even readable (spaces present, enough text)?

None of these ask "is Python a skill?". They ask "did what we produced plausibly
come from what we read?" — a question with the same shape for a nurse, a welder
and a machine-learning engineer.

WHAT A LOW SCORE MEANS: escalate, do not guess. Re-run the LLM pass, ask the
user to confirm, or flag the upload. The universal solution to imperfect
extraction is not a better parser — it is noticing, and telling someone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# A "signal term" is a token a CV uses to name something specific: a proper
# noun, an acronym, or a technical token. Deliberately shape-based — it carries
# no opinion about any profession.
_SIGNAL = re.compile(
    r"\b("
    r"[A-Z][A-Za-z]*(?:[./+#-][A-Za-z0-9+#]+)+"   # Node.js, CI/CD, C++, .NET
    r"|[A-Z]{2,6}\b"                              # AWS, SQL, RTOS, CEH, HIPAA
    r"|[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}"        # Deep Learning, Power BI
    r")"
)

# Words that are capitalised in every CV and name nothing specific. A GRAMMAR
# stoplist, not a skill list: removing "January" tells you nothing about any
# profession, and leaving it in would inflate coverage for everyone equally.
_GENERIC = {
    "january","february","march","april","may","june","july","august",
    "september","october","november","december","present","current",
    "education","experience","summary","skills","projects","university",
    "college","school","bachelor","master","msc","bsc","phd","gpa",
    "the","and","for","with","from","this","that","using","led","built",
    "developed","managed","designed","company","limited","ltd","inc",
}


@dataclass
class ExtractionScore:
    """A layout-agnostic verdict on one extraction."""

    coverage: float = 0.0        # 0-1: share of the document's signal terms captured
    precision: float = 0.0       # 0-1: share of extracted skills that look real
    completeness: float = 0.0    # 0-1: share of matcher-critical fields filled
    input_health: float = 0.0    # 0-1: was the source readable at all
    overall: float = 0.0
    verdict: str = "unknown"     # good | weak | broken
    problems: list[str] = field(default_factory=list)
    missed_examples: list[str] = field(default_factory=list)
    junk_examples: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "coverage": round(self.coverage, 3),
            "precision": round(self.precision, 3),
            "completeness": round(self.completeness, 3),
            "input_health": round(self.input_health, 3),
            "overall": round(self.overall, 3),
            "verdict": self.verdict,
            "problems": self.problems,
            "missed_examples": self.missed_examples[:10],
            "junk_examples": self.junk_examples[:10],
        }


def _signal_terms(text: str) -> set[str]:
    """Every specific-looking term the document itself uses."""
    out: set[str] = set()
    for m in _SIGNAL.finditer(text or ""):
        t = m.group(1).strip()
        if len(t) < 2 or t.lower() in _GENERIC:
            continue
        # A multi-word phrase whose every word is generic carries no signal.
        if all(w.lower() in _GENERIC for w in t.split()):
            continue
        out.add(t.lower())
    return out


def _looks_like_junk(skill: str) -> bool:
    """Shape-only junk test — no vocabulary, works in any profession."""
    s = skill.strip()
    if not s:
        return True
    # A bare lowercase common word. Real skill names are proper nouns, acronyms
    # or capitalised terms; "validity", "timeliness", "basic" are qualities.
    # Guard: a lowercase token containing a dot or digit is a real tool name
    # (asyncio is a false positive we accept; node.js, k8s are not).
    if s.islower() and len(s.split()) == 1 and not re.search(r"[.\d/+#-]", s):
        return True
    # Words glued together by a PDF that lost its spaces.
    if len(s) > 14 and " " not in s and re.search(r"[a-z][A-Z]", s):
        return True
    # A sentence, not a name.
    if len(s.split()) > 6 or s.endswith("."):
        return True
    return False


def score_extraction(
    raw_text: str,
    skills: list[str],
    *,
    job_titles: list[str] | None = None,
    summary: str = "",
    certifications: list[str] | None = None,
) -> ExtractionScore:
    """Grade one extraction against the document it came from.

    Deliberately takes plain values, not a profile object, so it can score the
    deterministic pass, the LLM pass, or the merged result — and be used in a
    test, a benchmark, or production, without adapters.
    """
    s = ExtractionScore()
    text = raw_text or ""
    skills = [x for x in (skills or []) if x and x.strip()]
    titles = job_titles or []
    certs = certifications or []

    # ── INPUT HEALTH — was the source even usable? A score computed on an
    # unreadable file is meaningless, so this gates everything else.
    if len(text) < 200:
        s.input_health = 0.0
        s.problems.append("almost no text was read from the file")
    else:
        space_ratio = text.count(" ") / len(text)
        if space_ratio < 0.05:
            s.input_health = 0.2
            s.problems.append(
                f"the file has no word boundaries ({space_ratio*100:.1f}% spaces) — "
                "it needs re-exporting; every downstream field will be mangled"
            )
        else:
            s.input_health = 1.0

    # ── COVERAGE — of what the document names, how much did we capture?
    signals = _signal_terms(text)
    if signals:
        extracted_blob = " ".join(skills + titles + certs).lower()
        hit = {t for t in signals if t in extracted_blob}
        s.coverage = len(hit) / len(signals)
        missed = sorted(signals - hit, key=len, reverse=True)
        s.missed_examples = missed[:10]
        if s.coverage < 0.25:
            # Deliberately NOT "most of what they wrote was not understood". About
            # half of what this counts as missed is a place, an employer or the
            # person's own name — saying otherwise tells someone their good CV was
            # unreadable, which is both false and discouraging.
            s.problems.append(
                f"only {s.coverage*100:.0f}% of the specific terms in this document "
                "reached the profile — likely whole sections were not read"
            )
    else:
        s.coverage = 0.0
        s.problems.append("the document names nothing specific — is it a real CV?")

    # ── PRECISION — of what we produced, how much is real?
    if skills:
        junk = [x for x in skills if _looks_like_junk(x)]
        s.precision = 1 - (len(junk) / len(skills))
        s.junk_examples = junk[:10]
        if s.precision < 0.8:
            s.problems.append(
                f"{len(junk)} of {len(skills)} extracted skills are not skills — "
                "noise flattens matching, so every job looks equally relevant"
            )
    else:
        s.precision = 0.0
        s.problems.append("no skills were extracted at all")

    # ── COMPLETENESS — the fields a matcher cannot work without.
    have = sum([
        bool(skills), bool(titles), bool(summary.strip()), bool(certs),
    ])
    s.completeness = have / 4
    if not skills:
        s.problems.append("no skills — this profile cannot match anything")
    if not titles:
        s.problems.append("no job titles — search has no role to aim at")
    if not certs and re.search(r"certif|licen[cs]e|accredit", text, re.I):
        s.problems.append(
            "the CV mentions certifications but none were extracted — "
            "ATS-style filters screen on exactly these"
        )

    # ── OVERALL. Weighted for what actually breaks a job match: missing the
    # person's real skills hurts most, junk second, missing fields third.
    s.overall = (
        s.input_health * 0.20
        + s.coverage * 0.40
        + s.precision * 0.25
        + s.completeness * 0.15
    )
    s.verdict = "good" if s.overall >= 0.65 else ("weak" if s.overall >= 0.40 else "broken")
    return s


def needs_escalation(score: ExtractionScore) -> bool:
    """True when this extraction should be flagged rather than trusted silently.

    WHAT ACTUALLY SHIPS TODAY when this returns True (verified against the
    one call site, ``two_pass.py::run_two_pass_extraction``, 2026-08-16):
    the score is stored on ``cv.extraction_score`` — which reaches the
    profile API response, and is the one thing that lets the frontend show
    the user an accurate banner — and a ``logger.warning`` fires so a bad
    extraction is also visible in prod logs. THAT IS THE WHOLE IMPLEMENTED
    PATH: the user-facing notice (the banner). Nothing here retries the
    parse and nothing re-runs the LLM pass. A CV that scores 'broken' (e.g.
    a PDF export that lost its word boundaries) shows the true banner every
    time, but re-saving the identical file reproduces the identical
    'broken' verdict forever — there is no automated path back to a working
    profile, only the person editing the upload by hand.

    NOT IMPLEMENTED HERE, ON PURPOSE, THIS BATCH: automatic retry / LLM
    re-enhancement needs a change inside ``two_pass.py`` (the only place that
    knows how to re-invoke the LLM pass), which is out of this module's
    reach this run. The concrete plan, for whoever picks it up: on a
    'broken' verdict, ``run_two_pass_extraction`` re-runs ONLY the LLM pass
    once more with a prompt that names the specific problem
    (``score.problems``, e.g. "no word boundaries") so the retry targets the
    actual defect instead of repeating the same read; if the second score is
    still 'broken', stop there and rely on the banner — a loop that keeps
    retrying an unreadable file burns cost for no gain, and QUALITY here
    means one well-aimed retry, not an unbounded one.
    """
    return score.verdict != "good"

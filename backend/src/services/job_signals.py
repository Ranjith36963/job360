"""Workplace type and seniority as deterministic TEXT signals — filling the
gap where LLM enrichment says 'unknown'.

WHY. Measured on prod 2026-08-04: `job_enrichment.workplace_type` is
'unknown' for 70% of the catalog (2,181/3,119); `seniority` is 'unknown' for
63% (1,970/3,119). Meanwhile 1,311 active jobs carry under 200 characters of
description — the LLM literally has nothing to read. Both fields are
FORMULAIC: employers announce workplace policy and seniority in a small,
repeated vocabulary ("fully remote", "hybrid", "3 days in the office",
"Senior", "Graduate", "Head of Engineering"). A regex over text we already
store is free and repeatable — the same reasoning `visa_signal.py` uses,
where a deterministic scan already outperforms the LLM 42% vs 4.6%.

WHY SENIORITY READS THE TITLE FIRST. This is the whole point of building
this detector at all: the title exists even for the 1,311 no-description
jobs, and seniority is the field most likely to live there ("Senior Backend
Engineer", "Graduate Software Developer").

WHY THREE(-ish) STATES, REUSED ENUMS. Same reasoning as `visa_signal.py`: a
value the ad never states is not the same fact as a value it contradicts.
Both detectors return the SHARED `WorkplaceType` / `SeniorityLevel` enums
from `job_enrichment_schema.py` — never a parallel enum — because every
member already carries an UNKNOWN, so "the detector found nothing" and "the
LLM never ran" collapse to the same value the rest of the pipeline already
understands.

WHY ENRICHMENT WINS WHEN IT EXISTS. The LLM read the whole ad with context;
this module only pattern-matches surface text. Same precedence as
`visa_signal.detect_visa_status`: a present, non-'unknown' `enrichment_value`
short-circuits before any text is touched.

VOCABULARY LIVES IN DATA FILES (rule #28's rationale, extended past its
literal scope of profile extraction — the same brittleness applies here: a
hand-typed keyword dict means "new term = code edit").
`src/data/job_signals/workplace_terms.txt`, `workplace_location_terms.txt`
and `seniority_terms.txt` are TSV: one `<enum_value><TAB><phrase>` pair per
line; blank lines and lines starting with `#` are ignored. They live INSIDE
the package on purpose — see `_DATA` below; anywhere else and production runs
without them. Loaded lazily and cached with `lru_cache`, exactly like
`uk_gate._gazetteer()` — and, like that loader, a missing or unreadable file
degrades to an empty vocabulary (every detector call then returns UNKNOWN)
rather than raising, but LOGS AN ERROR naming the path. A forgotten data file
in a deploy must not crash the pipeline; it must also not be invisible.

WHY LOCATION GETS ITS OWN VOCABULARY AND ITS OWN RULES. Added 2026-08-07
after a manager audit found 165 prod jobs still UNKNOWN while their
LOCATION field states the arrangement outright — e.g.
`location='Remote', title='Ranger'`, or `location='Cardiff, London or
Remote (UK)', title='Lead Product Designer'`. `detect_workplace` originally
only read title + description prose.

The location field is a DELIMITED list of tokens, not a sentence — "Cardiff,
London or Remote (UK)" is offices-and-arrangement, comma/`or`/parens
separated, never a sentence about a job's DOMAIN. That is precisely the
condition that made bare "remote" unsafe in prose ("Remote Sensing
Analyst" — "remote" describing the JOB, not the arrangement): a location
field never names a job domain, so there is no sentence for a bare "remote"
token to be part of. Same word, different field, different reliability —
`workplace_terms.txt` (prose) still bans the bare token,
`workplace_location_terms.txt` (location) allows it. Do NOT collapse these
into one shared vocabulary "to simplify" — the ban exists for the prose
failure mode specifically, and the location field doesn't have it.

Location is checked LAST, strictly after prose (title + description), never
before or merged with it: prose is the MORE SPECIFIC claim. A location field
can be stale, generic, or set once at intake while the description says
"this role is now fully onsite, five days a week" — the description must
win. See `detect_workplace`'s docstring for the exact composition order.

Multi-site locations — named office cities ALONGSIDE "Remote", e.g.
"Cardiff, London or Remote (UK)" — resolve to HYBRID, not REMOTE. Reasoning:
naming real offices next to "Remote" is the employer offering a CHOICE, not
committing to fully remote; treating it as REMOTE would overclaim, and
`WorkplaceType` has no "flexible" member, so HYBRID (a mix of on-site and
remote across the role) is the closest true statement of the three enum
values available.

Some patterns stay in CODE, not data, because they are STRUCTURE, not
vocabulary — the same distinction `uk_gate.py` draws for `_POSTCODE`:
  * `_HYBRID_CADENCE` — the "N day(s) ... office" cadence pattern, N in
    1-4. 5 (or "five") is deliberately EXCLUDED and left to the ONSITE
    vocabulary instead: "5 days in the office" is UK shorthand for a full
    standard work week with no remote days at all, i.e. plain onsite, not a
    mix. That cutoff is a real fact about the UK working week, not an
    arbitrary vocabulary item, so it stays a code-level rule.
  * `_ONSITE_AMENITY` — "on-site parking" / "on-site gym" is a perk
    mention, not a workplace-arrangement statement; this is a NEGATIVE
    guard, stripped from the text before the onsite vocabulary ever runs.
  * `_REMOTE_NEGATED` — "no remote working available" / "not a remote
    role" CONTAIN the positive phrases "remote working" / "remote role" as
    plain substrings. Checked (and stripped) before the positive remote
    vocabulary runs, for the identical reason `visa_signal.py` checks
    refusal before offer: a positive-first design reads a refusal as an
    offer.
  * `_SENIORITY_NOISE` — compound phrases that contain a tier word but mean
    something else entirely ("Junior School" is an institution, not a
    junior role; "Head of Year" / "Head of House" / "Head of Sixth Form"
    are school pastoral titles, not the department-leadership sense
    "head of" means elsewhere; "lead generation" is a sales function, not
    a seniority claim). Stripped BEFORE any tier match runs, so it can
    never feed a tier either.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

from src.services.job_enrichment_schema import SeniorityLevel, WorkplaceType

logger = logging.getLogger(__name__)

# THE DATA LIVES INSIDE THE PACKAGE, AND THAT IS LOAD-BEARING (issue #260, the
# third instance of one bug).
#
# It used to sit in `backend/data/job_signals/` — one directory further up,
# `parent.parent.parent`. Production installs the app with `pip install .`, and
# `[tool.setuptools.packages.find] include = ["src*"]` copies only the `src`
# packages into site-packages. So `backend/data/` never shipped, this path
# resolved to `<site-packages>/data/job_signals`, which does not exist in the
# container, and `_load_terms()` returned {} for all three vocabularies. Both
# detectors then answered UNKNOWN for every job in production, silently: the
# `signal_backed_lookup` wrapper that fills 'unknown' enrichment at read time
# filled nothing, and `detect_seniority` — the one that reads the TITLE, which
# exists even for the 1,311 jobs with no usable description — decided nothing.
#
# Under `src/` the files are package data (`pyproject.toml`
# `[tool.setuptools.package-data]`), so the dev tree and the installed wheel
# resolve the SAME relative path. Moving them back out re-opens #260.
# Guards: `tests/test_job_signals.py::TestTheDataShipsWithTheInstalledPackage`
# and `tests/test_shipped_data.py`.
_DATA = Path(__file__).resolve().parent.parent / "data" / "job_signals"


# ---------------------------------------------------------------------------
# Data-file loading (lazy, cached — mirrors uk_gate._gazetteer())
# ---------------------------------------------------------------------------


def _load_terms(filename: str) -> dict[str, tuple[str, ...]]:
    """Parse one `value<TAB>phrase` file into ``{value: (phrase, ...)}``.

    Blank lines and `#`-prefixed comment lines are skipped. A missing or
    unreadable file returns an empty dict rather than raising — callers then
    naturally fall through to UNKNOWN, so a forgotten data file in a deploy
    degrades the feature instead of crashing the pipeline.

    But it SAYS SO. Graceful degradation is right; SILENT degradation is what
    let this ship blind — every instrument stayed green (no exception, no
    failing test, no log line) while both detectors decided nothing for every
    job. An empty vocabulary is always a deployment fault, never a normal
    state, so it is logged at ERROR with the path it actually looked at.
    """
    path = _DATA / filename
    terms: dict[str, list[str]] = {}
    if not path.exists():
        _report_empty(path, "file not found")
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        _report_empty(path, f"unreadable ({exc})")
        return {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "\t" not in line:
            continue
        value, _, phrase = line.partition("\t")
        value = value.strip()
        phrase = phrase.strip()
        if value and phrase:
            terms.setdefault(value, []).append(phrase)
    if not terms:
        # Present but useless — the nastier half. `path.exists()` is True, so
        # any existence check (including a Docker build that copied the file)
        # looks healthy while the vocabulary is still empty.
        _report_empty(path, "file present but no `value<TAB>phrase` lines parsed")
        return {}
    return {k: tuple(v) for k, v in terms.items()}


def _report_empty(path: Path, why: str) -> None:
    """Say loudly that a vocabulary is empty. See `_load_terms`."""
    logger.error(
        "JOB SIGNALS DEGRADED — vocabulary empty at %s (%s). Workplace and "
        "seniority detection will answer 'unknown' for every job: "
        "signal_backed_lookup fills nothing and detect_seniority decides "
        "nothing. This is the issue-#260 packaging shape — check that "
        "src/data/job_signals/*.txt ships with the installed package "
        "([tool.setuptools.package-data] in backend/pyproject.toml).",
        path, why,
    )


@lru_cache(maxsize=1)
def _workplace_terms() -> dict[str, tuple[str, ...]]:
    return _load_terms("workplace_terms.txt")


@lru_cache(maxsize=1)
def _seniority_terms() -> dict[str, tuple[str, ...]]:
    return _load_terms("seniority_terms.txt")


@lru_cache(maxsize=1)
def _location_terms() -> dict[str, tuple[str, ...]]:
    return _load_terms("workplace_location_terms.txt")


@cache
def _phrase_pattern(phrases: tuple[str, ...]) -> Optional[re.Pattern[str]]:
    """Compile one tier's phrases into a single word-boundary alternation.

    Longest phrase first: regex alternation is first-match not longest-match,
    so without ordering a short phrase earlier in the list could shadow a
    longer one that starts the same way.
    """
    if not phrases:
        return None
    ordered = sorted(set(phrases), key=len, reverse=True)
    body = "|".join(re.escape(p) for p in ordered)
    return re.compile(rf"\b(?:{body})\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Structural patterns (shape, not vocabulary — see module docstring)
# ---------------------------------------------------------------------------

# "3 days a week in the office" / "2-3 days per week in office" / "in the
# office 3 days a week" — the count is structure, not vocabulary. Bounded to
# 1-4: see the module docstring for why 5/"five" is excluded on purpose.
# Order-agnostic: cadence can precede or follow the office mention.
_HYBRID_CADENCE = re.compile(
    r"\b[1-4](?:\+|\s*-\s*[1-4])?\s*days?\b[^.\n]{0,30}\b(?:in|at)\s+(?:the\s+)?office\b"
    r"|\b(?:in|at)\s+(?:the\s+)?office\b[^.\n]{0,30}\b[1-4](?:\+|\s*-\s*[1-4])?\s*days?\b",
    re.IGNORECASE,
)

# "Free on-site parking", "on-site gym" — a perk mention, not a
# workplace-arrangement statement. UK job ads advertise amenities this way
# constantly; without this guard every employer with a gym would misclassify
# as ONSITE regardless of the actual work arrangement.
_ONSITE_AMENITY = re.compile(
    r"\bon[\s-]?site\s+(?:parking|gym|canteen|caf(?:e|é)|catering|"
    r"childcare|cr(?:e|è)che|kitchen|facilities|car\s*park|showers?)\b",
    re.IGNORECASE,
)

# "No remote working available" / "not a remote role" — the NEGATIVE
# statement contains the POSITIVE phrase as a literal substring ("remote
# working", "remote role"). Checked and stripped before the positive remote
# vocabulary, mirroring visa_signal.py's refusal-before-offer precedence.
_REMOTE_NEGATED = re.compile(
    r"\bno\s+remote\s+(?:working|work)\b"
    r"|\bnot\s+(?:a\s+)?remote\b"
    r"|\bno\s+remote\s+role\b"
    r"|\bremote\s+working\s+is\s+not\s+(?:available|offered|possible)\b",
    re.IGNORECASE,
)

# Splits a STRUCTURED location field into candidate tokens — structural, not
# vocabulary, because these are punctuation delimiters, not phrases.
# "Cardiff, London or Remote (UK)" -> ["Cardiff", "London", "Remote", "UK"].
_LOCATION_SPLIT = re.compile(r"[,;/|()\[\]]|\s+or\s+|\s+-\s+", re.IGNORECASE)

# Compound phrases that CONTAIN a tier word but mean something else. See the
# module docstring for the three concrete traps this exists to close.
_SENIORITY_NOISE = re.compile(
    r"\bjunior\s+schools?\b"
    r"|\bhead\s+of\s+year\b"
    r"|\bhead\s+of\s+house\b"
    r"|\bhead\s+of\s+sixth\s+form\b"
    r"|\blead\s+generation\b",
    re.IGNORECASE,
)

# Most-senior-first. A text naming two tiers picks the more senior one — see
# `_match_seniority_tier` for why that also happens to resolve the
# "graduate degree required" vs "graduate role" ambiguity for free.
_SENIORITY_RANK: tuple[SeniorityLevel, ...] = (
    SeniorityLevel.DIRECTOR,
    SeniorityLevel.PRINCIPAL,
    SeniorityLevel.STAFF,
    SeniorityLevel.SENIOR,
    SeniorityLevel.MID,
    SeniorityLevel.JUNIOR,
    SeniorityLevel.INTERN,
)


# ---------------------------------------------------------------------------
# Result types — mirror uk_gate.GateVerdict: the REASON is the point, a bare
# enum makes a wrong verdict as hard to debug as a bare boolean.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkplaceSignal:
    value: WorkplaceType
    reason: str


@dataclass(frozen=True)
class SenioritySignal:
    value: SeniorityLevel
    reason: str


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def _detect_prose_signal(title: str, description: str) -> Optional[WorkplaceSignal]:
    """Title + description prose, read as one blob. Returns None (no
    verdict) rather than UNKNOWN, so `detect_workplace` can tell "prose said
    nothing" apart from "nothing said anything" and fall through to the
    location field instead of stopping here.

    Precedence within the text is load-bearing:

    1. HYBRID first. A hybrid ad routinely also says "remote"
       ("remote-friendly hybrid role, 3 days in the office") — checking
       remote first would read that as fully remote.
    2. REMOTE second. An explicit "fully remote" beats a passing mention of
       an office ("we do have a small office in London if you'd like to
       visit") — that mention alone must not demote a clearly remote ad.
    3. ONSITE last, and only after stripping amenity mentions
       ("on-site parking/gym"), because "office" is the weakest vocabulary
       here and the easiest to catch as a stray mention rather than a
       genuine work-arrangement statement.
    """
    text = f"{title or ''}\n{description or ''}"
    if not text.strip():
        return None

    terms = _workplace_terms()

    hybrid_pattern = _phrase_pattern(terms.get("hybrid", ()))
    if (hybrid_pattern and hybrid_pattern.search(text)) or _HYBRID_CADENCE.search(text):
        return WorkplaceSignal(WorkplaceType.HYBRID, "hybrid_signal")

    # Negation guard runs before the positive check — see _REMOTE_NEGATED.
    remote_text = _REMOTE_NEGATED.sub(" ", text)
    remote_pattern = _phrase_pattern(terms.get("remote", ()))
    if remote_pattern and remote_pattern.search(remote_text):
        return WorkplaceSignal(WorkplaceType.REMOTE, "remote_term")

    cleaned = _ONSITE_AMENITY.sub(" ", text)
    onsite_pattern = _phrase_pattern(terms.get("onsite", ()))
    if onsite_pattern and onsite_pattern.search(cleaned):
        return WorkplaceSignal(WorkplaceType.ONSITE, "onsite_term")

    return None


def _location_segments(location: str) -> list[str]:
    """Split a structured location field into candidate tokens. See
    `_LOCATION_SPLIT` for why this is a structural split, not vocabulary.
    """
    return [seg.strip() for seg in _LOCATION_SPLIT.split(location or "") if seg.strip()]


def _detect_location_signal(location: str) -> Optional[WorkplaceSignal]:
    """Classify the STRUCTURED location field — a fallback source, consulted
    only after prose gives no verdict (see `detect_workplace`).

    Returns None (not UNKNOWN) for an empty or non-decisive field, so the
    caller's own UNKNOWN/"no_signal" return stays the single place that
    reason string is produced.

    Deliberately does NOT infer ONSITE from a bare place name alone
    ("London" with nothing else): hybrid and onsite jobs both routinely list
    a plain city here too, so a place name by itself isn't decisive the way
    it becomes once paired with an explicit remote/onsite token.
    """
    segments = _location_segments(location)
    if not segments:
        return None

    terms = _location_terms()
    remote_pattern = _phrase_pattern(terms.get("remote", ()))
    onsite_pattern = _phrase_pattern(terms.get("onsite", ()))
    ignore_pattern = _phrase_pattern(terms.get("ignore", ()))

    # Segments are matched WHOLE (fullmatch), not searched as a substring —
    # a location token is either exactly "Remote" or it's an office name;
    # there is no prose to accidentally match a fragment of.
    has_remote = has_onsite = has_office = False
    for seg in segments:
        if remote_pattern and remote_pattern.fullmatch(seg):
            has_remote = True
        elif onsite_pattern and onsite_pattern.fullmatch(seg):
            has_onsite = True
        elif ignore_pattern and ignore_pattern.fullmatch(seg):
            continue  # country/nation qualifier — neither an office nor a signal
        else:
            has_office = True

    # A named office ALONGSIDE remote is an offered CHOICE, not a commitment
    # to fully remote — see the module docstring's multi-site decision.
    if has_remote and (has_office or has_onsite):
        return WorkplaceSignal(WorkplaceType.HYBRID, "location_multi_site")
    if has_remote:
        return WorkplaceSignal(WorkplaceType.REMOTE, "location_remote")
    if has_onsite:
        return WorkplaceSignal(WorkplaceType.ONSITE, "location_onsite")
    return None


def detect_workplace(
    description: Optional[str],
    title: Optional[str] = "",
    *,
    location: Optional[str] = "",
    enrichment_value: Optional[str] = None,
) -> WorkplaceSignal:
    """Classify an ad's workplace arrangement: remote / hybrid / onsite.

    Composition order, most to least specific — see the module docstring's
    "WHY LOCATION GETS ITS OWN RULES" for the full reasoning:

    1. ``enrichment_value`` — the LLM read the whole ad with context; wins
       outright when present and not 'unknown'.
    2. Prose (title + description) — see `_detect_prose_signal` for the
       HYBRID > REMOTE > ONSITE precedence within it.
    3. ``location`` — the structured field, consulted ONLY when prose said
       nothing. Prose is the more specific claim: "fully onsite, five days
       a week" in the description must win over a stale or generic
       ``location='Remote'``, never the other way round.

    ``location`` is keyword-only and defaults to "" so every existing
    caller passing only ``(description, title)`` is unaffected byte-for-byte.
    """
    if enrichment_value:
        ev = enrichment_value.strip().lower()
        if ev and ev != WorkplaceType.UNKNOWN.value:
            for member in WorkplaceType:
                if ev == member.value:
                    return WorkplaceSignal(member, "enrichment")

    prose_signal = _detect_prose_signal(title or "", description or "")
    if prose_signal is not None:
        return prose_signal

    location_signal = _detect_location_signal(location or "")
    if location_signal is not None:
        return location_signal

    return WorkplaceSignal(WorkplaceType.UNKNOWN, "no_signal")


def _match_seniority_tier(
    text: str, terms: dict[str, tuple[str, ...]]
) -> Optional[SeniorityLevel]:
    """Return the highest-ranked tier whose vocabulary matches `text`, or
    None. Noise is stripped first so a compound trap phrase can't feed
    either the tier it superficially resembles or any other.
    """
    if not text.strip():
        return None
    cleaned = _SENIORITY_NOISE.sub(" ", text)
    for level in _SENIORITY_RANK:
        pattern = _phrase_pattern(terms.get(level.value, ()))
        if pattern and pattern.search(cleaned):
            return level
    return None


def detect_seniority(
    title: Optional[str] = "",
    description: Optional[str] = "",
    *,
    enrichment_value: Optional[str] = None,
) -> SenioritySignal:
    """Classify a role's seniority: intern / junior / mid / senior / staff /
    principal / director.

    ``enrichment_value`` wins outright when present and not 'unknown' — same
    precedence as `detect_workplace` / `visa_signal.detect_visa_status`.

    Reads the TITLE first, description second, as two independent passes
    (not one merged blob): seniority almost always sits in the title, and
    the title exists even for jobs with no usable description — that gap is
    the entire reason this detector exists. A description-only mention
    should never outrank a clear title signal.

    Within either pass, a text naming two tiers picks the MORE senior one
    (`_SENIORITY_RANK`, checked top-down). That ordering also resolves a
    real trap for free: "graduate" often describes an educational
    requirement ("must hold a graduate degree, 5 years' experience") rather
    than the role's level, and such ads are disproportionately senior roles
    that happen to also require a degree — checking SENIOR before JUNIOR
    reads that combination correctly.
    """
    if enrichment_value:
        ev = enrichment_value.strip().lower()
        if ev and ev != SeniorityLevel.UNKNOWN.value:
            for member in SeniorityLevel:
                if ev == member.value:
                    return SenioritySignal(member, "enrichment")

    terms = _seniority_terms()

    title_hit = _match_seniority_tier(title or "", terms)
    if title_hit is not None:
        return SenioritySignal(title_hit, "title")

    description_hit = _match_seniority_tier(description or "", terms)
    if description_hit is not None:
        return SenioritySignal(description_hit, "description")

    return SenioritySignal(SeniorityLevel.UNKNOWN, "no_signal")

# ---------------------------------------------------------------------------
# Wiring the detectors into scoring
# ---------------------------------------------------------------------------


def signal_backed_lookup(
    base_lookup: Callable[[Any], Any],
) -> Callable[[Any], Any]:
    """Wrap an ``enrichment_lookup`` so the detectors fill what the LLM left blank.

    THE SEAM. ``JobScorer(enrichment_lookup=...)`` takes a CALLABLE OF A JOB,
    which is the only place in the system where the enrichment record and the
    job's own text are both in scope. The bulk loader
    (`job_enrichment._build_enrichment_lookup`) is keyed by job_id and never
    sees the job, so it cannot do this; the scorer's own dim functions receive
    only the enrichment, so they cannot either. Wrapping here means EVERY call
    site (API read path, rescore, worker) gains the detectors at once, and no
    scoring formula changes.

    WHY THIS IS NOT INVENTING DATA. The LLM enricher returns 'unknown' whenever
    the ad gave it nothing to read — and 1,311 live jobs have under 200
    characters of description, so 'unknown' is overwhelmingly "we never looked
    at any text", not "we looked and could not tell". The detectors read the
    TITLE and the LOCATION field, which exist even for those jobs. Measured on
    the live catalog: seniority coverage 27% -> 60%, workplace 22% -> 29%.

    Precedence is one-way: an existing LLM verdict is never overwritten, only a
    literal 'unknown' is filled. That keeps the LLM authoritative wherever it
    actually spoke.
    """
    def _wrapped(job: Any) -> Any:
        enrichment = base_lookup(job)
        if enrichment is None:
            return None
        title = getattr(job, "title", "") or ""
        desc = getattr(job, "description", "") or ""
        loc = getattr(job, "location", "") or ""

        wp = getattr(enrichment, "workplace_type", None)
        if _is_unknown(wp):
            detected_wp = detect_workplace(
                desc, title, location=loc, enrichment_value=None
            )
            _apply(enrichment, "workplace_type", detected_wp)

        sn = getattr(enrichment, "seniority", None)
        if _is_unknown(sn):
            detected_sn = detect_seniority(title, desc, enrichment_value=None)
            _apply(enrichment, "seniority", detected_sn)

        return enrichment

    return _wrapped


def _is_unknown(value: Any) -> bool:
    if value is None:
        return True
    raw = getattr(value, "value", value)
    return str(raw).strip().lower() in ("", "unknown")


def _apply(enrichment: Any, field: str, detected: Any) -> None:
    """Write a detected value back, tolerating a frozen/validated model.

    ``JobEnrichment`` is a Pydantic model; some configurations forbid mutation.
    A detector result must never crash scoring, so a failed assignment is
    silently skipped — the dim scorer then sees 'unknown' and returns its
    neutral constant, exactly as before this wrapper existed.
    """
    raw = getattr(detected, "value", detected)
    raw = getattr(raw, "value", raw)
    if raw is None or str(raw).strip().lower() in ("", "unknown"):
        return
    try:
        setattr(enrichment, field, raw)
    except Exception:  # noqa: BLE001 — never let a detector break scoring
        pass


def _all_seniority_phrases() -> tuple[str, ...]:
    """Every rank phrase across every tier, as ONE tuple.

    Deliberately derived from `_seniority_terms()` rather than re-typed: this
    is the single vocabulary CLAUDE.md rule #28 (and `profile/seniority.py`'s
    "no parallel word list") insists on. Adding a rank word stays a one-line
    data edit.

    NOT `lru_cache`d, on purpose. `_seniority_terms()` already is, and
    `_phrase_pattern` is keyed on the returned tuple, so the only work repeated
    here is building an identical small tuple. A second cache layer would just
    be a second thing to forget to invalidate — which is exactly the bug
    `tests/test_job_signals.py`'s missing-file tests kept re-creating.
    """
    return tuple(p for phrases in _seniority_terms().values() for p in phrases)


# Trailing junk a strip can expose: "Director of Engineering" -> "of
# Engineering", "Senior, Data Engineer" -> ", Data Engineer". Punctuation only
# — no vocabulary.
_STRIP_EDGE = " -–—,/&:·|�"


def strip_seniority(text: str) -> str:
    """Return `text` with its RANK words removed, keeping the DOMAIN.

    "AI/ML Engineer Intern" -> "AI/ML Engineer". "Senior Data Engineer" ->
    "Data Engineer". "Intern" -> "" (nothing but a rank).

    WHY THIS EXISTS (measured live, 2026-08-13). Reed, Adzuna and Careerjet
    match a query as AND-of-terms, so one extra rank token collapses the result
    set instead of re-ranking it: "Machine Learning Engineer" returned 486 /
    806 / 856 jobs, "Machine Learning Engineer Intern" returned 0 / 1 / 1. A
    control query with a nonsense word ("… Banana") returned 0 everywhere,
    proving it is the AND, not the word. So a rank word in a QUERY is a ~99.9%
    recall loss.

    WHY IT REUSES THE JOB-SIDE VOCABULARY. `seniority_terms.txt` is already
    tuned for exactly the traps a naive intern/junior/senior list walks into:
    bare "staff" is absent (NHS "Staff Nurse" is an entry grade) and bare
    "executive" is absent ("Account Executive" is a UK entry title), so both
    survive this function untouched. `_SENIORITY_NOISE` is honoured too, so
    "Lead Generation Executive" keeps its "lead".

    Never raises; returns the input unchanged when the vocabulary is
    unavailable (see `_load_terms`'s degrade-quietly contract).
    """
    if not text:
        return text
    pattern = _phrase_pattern(_all_seniority_phrases())
    if pattern is None:
        return text
    protected = [m.span() for m in _SENIORITY_NOISE.finditer(text)]

    def _is_protected(start: int, end: int) -> bool:
        return any(ps <= start and end <= pe for ps, pe in protected)

    out: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        if _is_protected(*match.span()):
            continue
        out.append(text[cursor : match.start()])
        cursor = match.end()
    out.append(text[cursor:])
    return re.sub(r"\s+", " ", "".join(out)).strip(_STRIP_EDGE)

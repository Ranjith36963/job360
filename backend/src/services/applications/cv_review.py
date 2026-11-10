"""The second check before a CV goes without the owner (owner decision 2026-11-05): a separate reviewer.

The assistant hands a saved CV to a fresh helper that is not its writer (the Doctor's step 2 hand-off). That
helper gives, for every CV line, the profile quote and field path behind it, then ``strong`` or ``weak``; the
assistant records it as ``cv_reviewed``. Job360 runs no model (decision 28): it stores the verdict and VERIFIES
it. ``strong`` is stored only when all of these hold, else ``weak`` with closed :data:`DOWNGRADES` codes:

(a) ``artifact_id`` + ``sha256`` are this application's CURRENT CV, exactly (``not_current_cv``);
(b) every content line of that CV has an entry whose ``cv_line`` is that line (case, spacing and a leading
    bullet aside) (``line_uncovered``; an entry naming no CV line is ``line_not_in_cv``; a CV of headers only is
    ``no_content``). Exempt, and only these: the name line (the FIRST line, every word a word of the profile's
    name), a contact line (only emails, links and one phone number, with separators; a bare site only when the
    profile says it; beside them, words of the profile's own location fields; a ``Label:`` word is content), and
    a section heading (1-4 alphabetic words, one of 4+ letters: ALL CAPS, ``# Heading`` or ``Heading:``; ``&`` is
    ``and``) whose every word is a section word and a word of the profile source text. A summary line is not.
    A row for an exempt line, or for a line the profile itself holds (end of (d)), is not judged by (c) or (d);
(c) each other ``profile_quote`` is in ``cv_checks.profile_source_text`` (case and whitespace aside, or word for word
    with only outer punctuation aside: every word, number and name exact and in order) (``quote_not_in_profile``);
(d) every number and capitalised name of a ``cv_line``, its first word too (``evidence.claim_tokens``), is in
    its own quote (``claim_not_in_quote``); the quote has 3+ words and at least half the line's words of 4+
    letters (``quote_too_thin``); words are scanned one by one, no regex on caller text. The quote also says
    what it says in other words: ``&`` is ``and``, and a place of the CLOSED ``cv_checks.PLACE_ABBREVIATIONS``
    table is its other spelling ("United Kingdom" says "UK"). Two line shapes need no one quote to hold them,
    because the profile itself does, token for token (production 2026-11-10: 234 of 279 newest reviews weak):
    a list line ``[Label:] a, b, c`` whose every item is a WHOLE profile skill item under a label of the
    profile's own group words or closed generic category words, never an employer, title, project or degree
    word (:func:`list_line_holds`), and a ``|`` header line, or a comma / dash one with a date range (12 pieces,
    300 characters at most), whose every piece is a whole piece (places read through the same closed table) of ONE
    profile record: one role, one project, one education entry, one CV text line, never two
    mixed (:func:`record_line_holds`). One item or piece the profile lacks keeps the line a claim;
(e) ``cv_checks.check_text("cv")`` finds nothing for that CV (``cv_checks_found``).
After weak reviews of :data:`MAX_WEAK` other CV versions judged under the current :data:`RULES`, a review of a
new one is weak (``retry_limit``): two rewrites, each re-reviewed (three reviews in all); never a fourth try. A
weak review judged under older rules never counts (its rows were not stored, so it cannot be judged again).

The event stores codes and counts, never CV or profile text; the reply names, for a claimed-strong review stored
weak, each code and the rows / review lines it came from (:func:`weak_warning`). ``may_submit`` reads
:func:`review_state` for the exact current CV id + sha, and only with ``assistant_settings.cv_auto_approve`` on,
in auto mode. The writer avoids rewrites by copying the profile's own sentences word for word (recipes).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from src.services.applications import cv_checks, evidence
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

EVENT_TYPE = "cv_reviewed"
STRONG, WEAK, NONE = "strong", "weak", "none"
PAYLOAD_KEYS = frozenset({"artifact_id", "sha256", "confidence", "lines", "reviewer"})
LINE_KEYS = frozenset({"cv_line", "profile_quote", "field_path"})
DOWNGRADES = (
    "not_current_cv", "no_content", "line_uncovered", "line_not_in_cv", "quote_not_in_profile",
    "claim_not_in_quote", "quote_too_thin", "cv_checks_found", "retry_limit",
)
MAX_WEAK = 3  # owner 2026-11-09: two rewrites, each re-reviewed
# The version of the rules a stored review was judged under (stored as ``rules``; none = 1). Bump it with every
# change to what is stored strong: retry_limit counts only weak reviews judged under the current rules.
RULES = 3  # 2026-11-10: place abbreviations, "&" = "and", list lines, one-record header lines, own sites (2); a held
# line's row is not judged by its quote, comma / dash headers, "portfolio" headings, no dict keys as profile text (3)
MAX_LINES = 200
_CAPS = {"cv_line": 1000, "profile_quote": 2000, "field_path": 200}
REVIEWER_MAX = 40
_BULLETS = "".join(cv_checks._BULLET_STARTS)
_SEPARATORS = frozenset({"|", "•", "·", "-", "–", "—", ",", "/"})
_PHONE_CHARS = frozenset("0123456789+()-.")


from src.utils.logger import safe_log_value  # noqa: E402 - CodeQL: no caller value reaches a log raw

logger = logging.getLogger(__name__)


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def line_key(text: Any) -> str:
    """A CV line as compared: a leading bullet, case and spacing aside."""
    return _norm(str(text or "").strip().lstrip(_BULLETS + " \t"))


def _is_email(tok: str) -> bool:
    local, at, domain = tok.partition("@")
    return bool(at and local and "@" not in domain and "." in domain.strip("."))


def _is_link(tok: str) -> bool:
    return tok.casefold().startswith(("http://", "https://", "www.", "linkedin.com/", "github.com/"))


# The countries a residence code names, for the contact line only (closed; the codes PLACE_ABBREVIATIONS spells).
_ISO2_PLACES = {"GB": "United Kingdom", "US": "United States", "AE": "United Arab Emirates"}


def place_words(profile: Any) -> frozenset[str]:
    """The words of where the PERSON lives: ``cv_data.location`` and ``user_info.contact`` residence / address city
    and country (with their :data:`cv_checks.PLACE_ABBREVIATIONS` spelling). Never a job's location: "Stevenage"
    or "Remote" on a role is where the work was, not the person's place."""
    cv = getattr(profile, "cv_data", None)
    contact = getattr(getattr(profile, "user_info", None), "contact", None) or {}
    found = [getattr(cv, "location", ""), contact.get("residence_city"), contact.get("address_city")]
    for key in ("residence_country", "address_country"):
        code = contact.get(key)
        found.append(_ISO2_PLACES.get(code.upper(), "") if isinstance(code, str) else "")
    text = " ".join(f for f in found if isinstance(f, str))
    return frozenset(evidence._words(text + " " + cv_checks.place_equivalents(text)))


def _is_domain(tok: str) -> bool:
    """A bare site ("job360.uk", "ada.github.io/cv"): letters, digits, ``.-/_~``, a dotted host ending in letters."""
    host = tok.partition("/")[0]
    tld = host.rpartition(".")[2]
    return ("." in host and len(tld) >= 2 and tld.isalpha() and host[:1].isalnum()
            and all(c.isalnum() or c in ".-/_~" for c in tok))


def own_links(profile: Any) -> frozenset[str]:
    """Every word of the profile's own text and link fields: a bare site counts as a link only when it is one."""
    cv = getattr(profile, "cv_data", None)
    found = [cv_checks.profile_source_text(profile), *cv_checks._flat(getattr(cv, "links", None))]
    for blob in (getattr(cv, "github_identity", None), getattr(cv, "linkedin_contact", None)):
        found += cv_checks._flat(list(blob.values())) if isinstance(blob, dict) else []
    return frozenset(evidence._words("\n".join(found)))


def is_contact_line(line: str, places: frozenset[str] = frozenset(), links: frozenset[str] = frozenset()) -> bool:
    """Only emails, links and ONE phone number (10-15 digits, opening with ``+``, ``(`` or ``0``), with separators;
    a bare site ("job360.uk") only when the profile's own text or links say it (``links``); and, beside an email,
    link or phone, words of the profile's own location (``places``): "London, UK | +44 ...". A place word alone is
    never a contact line."""
    rest: list[str] = []
    anchor = False
    for raw in line.split():
        tok = raw.strip(",;")
        if not tok or tok in _SEPARATORS:
            continue
        if _is_email(tok) or _is_link(tok) or (_is_domain(tok) and tok.strip(".").casefold() in links):
            anchor = True
        elif tok.strip(".").casefold() in places and tok.replace(".", "").isalpha():
            continue  # "London", "UK": the profile's own location, never a digit
        else:  # a label word ("CTO:") is content, never a free pass
            rest.append(tok)
    if not rest:
        return anchor
    digits = sum(c.isdigit() for t in rest for c in t)
    return (len(rest) <= 5 and all(set(t) <= _PHONE_CHARS for t in rest) and 10 <= digits <= 15
            and rest[0][0] in "+(0")


# A heading is exempt only when every word is a section name: "SENIOR DATA ENGINEER" is a claim, never a heading.
SECTION_WORDS = frozenset((
    "about", "achievements", "and", "awards", "career", "certification", "certifications", "competencies", "contact",
    "core", "courses", "education", "employment", "experience", "history", "interests", "key", "languages", "objective",
    "other", "personal", "portfolio", "professional", "profile", "projects", "publications", "qualifications",
    "references", "relevant", "selected", "skills", "summary", "technical", "training", "volunteering", "work",
))


def is_heading(line: str) -> bool:
    s = line.strip()
    words = s.strip("#*: \t").replace("&", " ").replace("/", " ").split()
    if not words or len(words) > 4 or not all(w.isalpha() for w in words) or max(map(len, words)) < 4:
        return False  # "AWS" or "SQL" on its own line is a claim, not a heading
    return cv_checks._is_section_heading(s) or s.startswith("#") or s.endswith(":")


def _name_words(profile: Any) -> set[str]:
    contact = getattr(getattr(profile, "user_info", None), "contact", None) or {}
    names = [getattr(getattr(profile, "cv_data", None), "name", "")] + [
        contact.get(k) for k in ("legal_first_name", "legal_last_name", "preferred_name")
    ]
    return {w for n in names if isinstance(n, str) for w in evidence._words(n)}


def _and_words(words: list[str]) -> set[str]:
    """Words as a set, ``&`` read as ``and`` (heading grammar, never a fact)."""
    return {"and" if w == "&" else w for w in words}


def content_lines(text: str, profile: Any) -> list[str]:
    """The CV's non-empty lines minus the narrow exemptions (the name line, contact lines, and a heading whose
    every word the profile already says: "AWS SOLUTIONS ARCHITECT" is a claim unless the profile holds it)."""
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    names = _name_words(profile)
    known = _and_words(evidence._words(cv_checks.profile_source_text(profile)))
    places, links = place_words(profile), own_links(profile)
    out: list[str] = []
    for i, line in enumerate(lines):
        words = set(evidence._words(line))
        said = _and_words(evidence._words(line.strip("#*: \t")))  # "EDUCATION & CERTIFICATION" = "... AND ..."
        heading = is_heading(line) and bool(said) and said <= known and said <= SECTION_WORDS
        if (i == 0 and names and words and words <= names) or is_contact_line(line, places, links) or heading:
            continue
        out.append(line)
    return out


_SKILL_FIELDS = ("skills", "linkedin_skills", "github_languages", "github_frameworks")
_LABEL_MAX_WORDS = 4
_ITEM_MAX_WORDS = 4
_LABEL_CHARS = frozenset(" &/+-")
_LABEL_JOINERS = frozenset({"&", "and", "/", "+"})


def list_parts(line: str) -> Optional[tuple[list[str], list[str]]]:
    """``(label words, items)`` of a list line, ``[Label:] item, item | item`` (2+ items of at most
    :data:`_ITEM_MAX_WORDS` words; a label of at most :data:`_LABEL_MAX_WORDS` words of letters), else ``None``.
    Split with ``cv_checks._split_items`` (a character scan, no regex on caller text)."""
    s = line.strip().lstrip(_BULLETS + " \t")
    label, colon, body = s.partition(":")
    if not colon:
        label, body = "", s
    if not all(c.isalpha() or c in _LABEL_CHARS for c in label):
        return None
    words = label.replace("&", " & ").replace("/", " / ").split()
    items = cv_checks._split_items(body)
    if len(words) > _LABEL_MAX_WORDS or len(items) < 2 or any(len(i.split()) > _ITEM_MAX_WORDS for i in items):
        return None
    return [w.casefold() for w in words], items


# Closed and hard-coded: generic skill-category words a CV may label a skills line with. Never an employer, title,
# project, degree or section of the CV ("Education: Python" is no skills group).
GENERIC_LABEL_WORDS = frozenset({
    "skills", "skill", "technical", "tools", "technologies", "languages", "frameworks", "core", "key", "backend",
    "frontend", "data", "engineering", "cloud", "ops", "ai", "ml", "agents", "llms", "web", "devops", "databases",
})
_NEVER_LABEL_WORDS = frozenset({"education", "experience", "work", "employment"})


@dataclass(frozen=True)
class SkillBook:
    """What the profile says about skills: every whole item, its own labelled groups (label words -> items), and
    the words of its records (companies, titles, projects, education, certifications) a label may never use."""

    items: frozenset[str]
    groups: tuple[tuple[frozenset[str], frozenset[str]], ...]
    entities: frozenset[str]


def _label_words(words: list[str]) -> frozenset[str]:
    return frozenset("and" if w == "&" else w for w in words if w not in ("/", "+"))


def skill_book(profile: Any) -> SkillBook:
    """The profile's :class:`SkillBook`: skill lists, the items of its own LABELLED list lines ("Languages: Python,
    SQL, TypeScript"); items compared whole, case and spacing aside, never a word of an item."""
    cv = getattr(profile, "cv_data", None)
    prefs = getattr(profile, "preferences", None)
    raw = [s for n in _SKILL_FIELDS for s in cv_checks.field_strings(n, getattr(cv, n, None))]
    raw += cv_checks._flat(getattr(prefs, "additional_skills", None))
    out = {_norm(s) for s in raw} | {_norm(i) for s in raw for i in cv_checks._split_items(s)}
    groups: dict[frozenset[str], set[str]] = {}
    for ln in cv_checks.profile_source_text(profile).splitlines():
        parts = list_parts(ln)
        if parts and parts[0]:
            got = {_norm(i) for i in parts[1]}
            out |= got
            groups.setdefault(_label_words(parts[0]), set()).update(got)
    texts = [str(r.get(k) or "") for n in ("cv_positions", "linkedin_positions") for r in (getattr(cv, n, None) or [])
             if isinstance(r, dict) for k in ("company", "title")]
    texts += [str(r.get("name") or "") for n in ("cv_projects", "linkedin_projects")
              for r in (getattr(cv, n, None) or []) if isinstance(r, dict)]
    texts += [s for n in ("education", "certifications", "companies", "job_titles")
              for s in cv_checks._flat(getattr(cv, n, None))]
    entities = frozenset(evidence._words(" ".join(texts).replace("/", " ").replace("-", " ")))
    return SkillBook(frozenset(out - {""}), tuple((k, frozenset(v)) for k, v in groups.items()), entities)


def profile_items(profile: Any) -> frozenset[str]:
    """Every whole skill item the profile holds (:func:`skill_book`)."""
    return skill_book(profile).items


def list_line_holds(line: str, book: SkillBook) -> bool:
    """A skills line the profile holds item by item (production 2026-11-10, apps 848-882): EVERY item a whole
    profile item, in any order, under a label that is a skills group, never a fact. Each label word is a word of one
    of the profile's OWN group labels, or a closed :data:`GENERIC_LABEL_WORDS` word; a word of an employer, title,
    project, degree or certification is never one (unless the profile's own group label uses it), nor
    :data:`_NEVER_LABEL_WORDS`. A label made only of profile group words claims THOSE groups: its items must come
    from them ("Agents: Python, SQL" is weak when Python is not in "Agents & LLMs"). A label with a generic word no
    group uses ("Backend & Data") may take any profile item. One item the profile lacks keeps it a claim."""
    parts = list_parts(line)
    if parts is None:
        return False
    label = _label_words(parts[0]) - {"and"}
    group_words = frozenset().union(*(k for k, _ in book.groups)) if book.groups else frozenset()
    for w in label:
        named = w in group_words
        if w in _NEVER_LABEL_WORDS or not (named or w in GENERIC_LABEL_WORDS) or (w in book.entities and not named):
            return False
    got = {_norm(i) for i in parts[1]}
    if not got <= book.items:
        return False
    if label and label <= group_words:  # it names the profile's own groups: the items come from them
        named_items = frozenset().union(*(v for k, v in book.groups if k & label))
        return got <= named_items
    return True


HEADER_MAX_CHARS = 300
HEADER_MAX_PIECES = 12
RAW_RECORD_MAX_CHARS = 200  # a CV text line longer than this is a paragraph, never one record
_PIECE_ENDS = frozenset("|•·;/")
_HEADER_BARS = frozenset({"|", "•", "·"})
_DASH_TOKENS = frozenset("-–—‐‑‒―−")


def pieces(text: str) -> list[tuple[str, ...]]:
    """A line cut at a standalone ``|``, ``•``, ``;``, ``/`` or dash, after a word ending ``,`` or ``;``, and around a
    ``(...)`` group, each piece as its words (case, outer punctuation and ``**`` bold aside; ``&`` is ``and``). A dash
    after a number joins a range ("Jun 2025 – Sep 2025" is one piece, never half of one). Word by word, no regex."""
    out: list[list[str]] = []
    cur: list[str] = []
    for raw in str(text or "").split():
        raw = raw.strip("*")
        if not raw:
            continue
        dash = set(raw) <= _DASH_TOKENS
        after_number = any(c.isdigit() for c in "".join(cur[-1:]))
        if set(raw) <= _PIECE_ENDS or (dash and not after_number):
            out.append(cur)
            cur = []
            continue
        if raw.startswith("(") and cur:
            out.append(cur)
            cur = []
        cur.append("-" if dash else "and" if raw == "&" else raw)
        if raw.endswith((",", ";", ")")):
            out.append(cur)
            cur = []
    out.append(cur)
    return [p for p in (tuple(evidence._words(" ".join(c))) for c in out) if p]


_RECORD_FIELDS = ("cv_positions", "linkedin_positions", "cv_projects", "linkedin_projects")
_ONE_LINE_FIELDS = ("education", "cv_education_details", "certifications")


def profile_records(profile: Any) -> list[frozenset[tuple[str, ...]]]:
    """The profile's records, each the set of its pieces: one role (company, title, dates, location, ...), one
    project (name, stack, ...), one education or certification entry, one line of the CV / LinkedIn text."""
    cv = getattr(profile, "cv_data", None)
    groups: list[list[str]] = []
    for name in _RECORD_FIELDS:
        groups += [cv_checks._flat(list(r.values())) for r in (getattr(cv, name, None) or []) if isinstance(r, dict)]
    groups += [[s] for n in _ONE_LINE_FIELDS for s in cv_checks._flat(getattr(cv, n, None))]
    for name in ("raw_text", "linkedin_raw_text"):
        groups += [[ln] for ln in str(getattr(cv, name, "") or "").splitlines()
                   if ln.strip() and len(ln) <= RAW_RECORD_MAX_CHARS]
    # each string also in its other place spelling ("Stevenage, United Kingdom" says "Stevenage, UK"): the closed
    # cv_checks.PLACE_ABBREVIATIONS, as rule (d) reads a quote; never another record's piece
    texts = [[t for s in g for t in (s, cv_checks.place_variant(s)) if t] for g in groups]
    records = [frozenset(p for s in g for p in [*pieces(s), tuple(evidence._words(s))] if p) for g in texts]
    return [r for r in records if r]


def record_line_holds(line: str, records: list[frozenset[tuple[str, ...]]]) -> bool:
    """A header line of pieces joined by a standalone ``|`` or ``•`` ("Calnex Solutions | AI Solutions Engineer,
    R&D | Jun 2025 - Sep 2025 | Stevenage", "Support Agent | LangGraph, MCP") that ONE profile record holds piece
    for piece, whole pieces, any order
    (production 2026-11-10, app 478). Pieces of two records never combine: a title is never moved to another
    employer, and a piece the record lacks ("Senior", "2018") keeps the line a claim. A comma / dash header ("Co -
    Title, R&D Department, Jun 2025 - Sep 2025, Stevenage, UK", app 353) is one too when it has 3+ pieces and one is
    a date range (:func:`_dated_header`); a plain comma line without dates still needs a quote."""
    if len(line) > HEADER_MAX_CHARS:
        return False
    toks = line.split()
    barred = bool(_HEADER_BARS.intersection(toks))
    if not barred and not any(t.endswith(",") or set(t) <= _DASH_TOKENS for t in toks):
        return False
    got = pieces(line.strip().lstrip(_BULLETS + " \t"))
    if len(got) > HEADER_MAX_PIECES or len(set(got)) < 2:  # a header has a few pieces; never an unbounded scan
        return False
    if not barred and not _dated_header(got):
        return False
    return any(_runs_in(got, r) for r in records)


def _dated_header(got: list[tuple[str, ...]]) -> bool:
    """A comma / dash header: 3+ distinct pieces, one of them a date range ("Jun 2025 - Sep 2025")."""
    return len(set(got)) >= 3 and any(cv_checks._date_range(" ".join(p)) is not None for p in got)


def _runs_in(got: list[tuple[str, ...]], record: frozenset[tuple[str, ...]]) -> bool:
    """Every piece of ``got`` is a whole piece of ``record``, or a run of neighbouring pieces is one, joined in their
    order (a separator the CV adds: "R&D | Jun 2025 - Sep 2025" against the PDF's "R&D Jun 2025 – Sep 2025"). A run
    never grows past the record's longest piece (at most :data:`HEADER_MAX_PIECES` pieces: bounded work)."""
    longest = max((len(p) for p in record), default=0)
    ok = [True] + [False] * len(got)  # ok[i]: got[:i] splits into such runs
    for start in range(len(got)):
        if not ok[start]:
            continue
        run: list[str] = []
        for end in range(start + 1, len(got) + 1):
            run.extend(got[end - 1])
            if len(run) > longest:
                break
            if not ok[end] and tuple(run) in record:
                ok[end] = True
    return ok[-1]


def quote_in_profile(quote: str, source: str, source_words: str) -> bool:
    """(c): the quote is in the profile, case and spacing aside, or word for word with only OUTER punctuation aside
    ("in Docker at 95%" for "in Docker, at 95%"): every word, number and name stays exact and in order."""
    words = " ".join(evidence._words(quote))
    return _norm(quote) in source or bool(words and f" {words} " in source_words)


def _quote_bank(quote: str) -> str:
    """The quote plus what it says in other words: ``&`` is ``and``, and a place of the closed
    :data:`cv_checks.PLACE_ABBREVIATIONS` is its other spelling ("United Kingdom" says "UK")."""
    extra = " and" if "&" in quote.split() else ""
    return f"{quote} {cv_checks.place_equivalents(quote)}{extra}"


def missing_warning(stored: dict[str, Any]) -> Optional[str]:
    """The reply line for a review that quoted too few lines (it is still stored, weak)."""
    if "line_uncovered" not in (stored.get("downgraded") or []):
        return None
    missing = int(stored.get("content_lines") or 0) - int(stored.get("covered") or 0)
    return (f"cv_reviewed stored weak: {missing} of {stored.get('content_lines')} required lines had no row; "
            "return ONE row per line in get_application's review.lines (cv_line copied exactly)")


def shape_problems(payload: dict[str, Any]) -> list[str]:
    """Every shape problem (422); the verification of a well-formed payload never refuses, it downgrades."""
    problems: list[str] = []
    if set(payload) != PAYLOAD_KEYS:
        problems.append("cv_reviewed takes exactly {artifact_id, sha256, confidence, lines, reviewer}")
    artifact_id = payload.get("artifact_id")
    if isinstance(artifact_id, bool) or not isinstance(artifact_id, int) or not isinstance(payload.get("sha256"), str):
        problems.append("needs artifact_id (a whole number) and sha256 (text)")
    if payload.get("confidence") not in (STRONG, WEAK):
        problems.append('confidence must be "strong" or "weak"')
    reviewer = payload.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > REVIEWER_MAX:
        problems.append(f"reviewer must be a short label (1-{REVIEWER_MAX} characters)")
    lines = payload.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LINES:
        problems.append(f"lines must be a list of 1-{MAX_LINES} {{cv_line, profile_quote, field_path}}")
        return problems
    for i, row in enumerate(lines):
        if not isinstance(row, dict) or set(row) != LINE_KEYS:
            problems.append(f"lines[{i}] takes exactly {{cv_line, profile_quote, field_path}}")
            continue
        for key, cap in _CAPS.items():
            if not isinstance(row[key], str) or not row[key].strip() or len(row[key]) > cap:
                problems.append(f"lines[{i}].{key} must be text of 1-{cap} characters")
    return problems


def _label(text: str) -> str:
    return "".join(c for c in text.strip() if c.isalnum() or c in " ._-")[:REVIEWER_MAX]


def review_valid(payload: dict[str, Any], cv_id: Optional[int] = None, cv_sha: Optional[str] = None) -> bool:
    """A stored ``cv_reviewed`` that is a real review of a real CV version: a non-empty sha256 and no
    ``not_current_cv`` downgrade (the 70 legacy reviews stored weak before #1539 refused them, 2026-11-06), and,
    when ``cv_id`` is given, exactly that CV (id + sha). THE validity rule: the gate, the queue's
    ``preflight.cv_review``, get_application's ``review.state`` and ``retry_limit`` all read it."""
    sha = payload.get("sha256")
    down = payload.get("downgraded")
    if not isinstance(sha, str) or not sha or (isinstance(down, list) and "not_current_cv" in down):
        return False
    return cv_id is None or (payload.get("artifact_id") == cv_id and sha == cv_sha)


async def newest_review(
    db: JobDatabase, user_id: str, application_id: int, cv_id: Optional[int], cv_sha: str,
) -> Optional[dict[str, Any]]:
    """The newest VALID stored ``cv_reviewed`` event of exactly this CV (id + sha), or ``None``."""
    from src.services.applications import kit  # noqa: PLC0415 - kit imports this module lazily

    if cv_id is None:
        return None
    for ev in await kit._events(db, user_id, application_id, (EVENT_TYPE,)):
        if review_valid(ev["payload"], cv_id, cv_sha):
            return ev
    return None


async def states_for_apps(db: JobDatabase, user_id: str, application_ids: list[int]) -> dict[int, str]:
    """``{application_id: none | weak | strong}`` for each application's NEWEST (unhidden) CV, read as
    :func:`review_state` reads one: the newest valid stored review of exactly that id + sha. Four queries
    however many applications (the queue has ~470 rows), never N+1; scoped by ``user_id``; read-only; one
    count-only log line per call, never per row. ``strong`` is the REVIEW's verdict, not the gate's clearance:
    the CV still needs the user when ``cv_auto_approve`` is off or its save checks list unsupported claims
    (``kit.reviews_clearing`` decides that)."""
    from src.services.applications import kit  # noqa: PLC0415 - kit imports this module lazily
    from src.services.applications.hiding import hidden_artifact_ids  # noqa: PLC0415

    ids = sorted(set(application_ids))
    out = {a: NONE for a in ids}
    if not ids:
        return out
    marks = ",".join("?" for _ in ids)
    hidden = await hidden_artifact_ids(db, user_id)
    cur = await db._db.execute(
        "SELECT id, application_id FROM application_artifacts "  # noqa: S608 - placeholders only
        f"WHERE user_id = ? AND kind = 'cv' AND application_id IN ({marks}) ORDER BY version_no DESC",
        (user_id, *ids),
    )
    newest: dict[int, int] = {}
    for art_id, app_id in await cur.fetchall():
        if int(art_id) not in hidden:
            newest.setdefault(int(app_id), int(art_id))
    if not newest:
        return out
    art_ids = sorted(newest.values())
    cur = await db._db.execute(
        "SELECT id, text FROM application_artifacts "  # noqa: S608 - placeholders only
        f"WHERE user_id = ? AND id IN ({','.join('?' for _ in art_ids)})",
        (user_id, *art_ids),
    )
    shas = {int(r[0]): kit.sha256_text(r[1] or "") for r in await cur.fetchall()}
    cur = await db._db.execute(
        "SELECT application_id, payload FROM application_events "  # noqa: S608 - placeholders only
        f"WHERE user_id = ? AND event_type = ? AND application_id IN ({marks}) ORDER BY id DESC",
        (user_id, EVENT_TYPE, *ids),
    )
    seen: set[int] = set()
    invalid: set[int] = set()
    for app_id, raw in await cur.fetchall():
        app_id = int(app_id)
        cv_id = newest.get(app_id)
        if app_id in seen or cv_id is None:
            continue
        p = kit._json(raw)
        if review_valid(p, cv_id, shas.get(cv_id)):
            seen.add(app_id)
            out[app_id] = STRONG if p.get("confidence") == STRONG else WEAK
        elif p.get("artifact_id") == cv_id:
            invalid.add(app_id)  # a stored review of this CV that is not a valid one: it reads "none"
    invalid -= seen
    if invalid:  # ONE line per read, never one per row: how many newest CVs carry only an invalid review
        logger.info("cv_review_states invalid_only=%s of=%s", len(invalid), len(ids))
    return out


async def review_state(db: JobDatabase, user_id: str, application_id: int, cv_id: Optional[int], cv_sha: str) -> str:
    """``strong`` / ``weak``: the newest stored review of exactly this CV (id + sha); ``none`` without one."""
    ev = await newest_review(db, user_id, application_id, cv_id, cv_sha)
    if ev is None:
        return NONE
    return STRONG if ev["payload"].get("confidence") == STRONG else WEAK


# Owner 2026-11-06, the safety net before autonomy: a CV that went out on a strong review instead of the user's
# look is recorded once, right after an assistant's first ``applied`` is written, and listed on Sent on its own
# (services/applications/sent_alone.py). The user's Wrong fact on it is recorded too. Both SERVER-WRITTEN.
SENT_EVENT, WRONG_FACT_EVENT = "cv_sent_on_review", "cv_wrong_fact"
SERVER_ONLY_EVENTS = frozenset({SENT_EVENT, WRONG_FACT_EVENT})


async def review_clearance(
    db: JobDatabase, user_id: str, application_id: int, actor: str, *, cv_artifact_id: Optional[int] = None,
    channel: str = "",
) -> Optional[dict[str, Any]]:
    """Read BEFORE an assistant's first ``applied`` is written: the :data:`SENT_EVENT` payload to record once it
    is, when the CV the receipt freezes was cleared by a strong review alone (no ``cv_seen`` / ``submit_approved``
    of it), else ``None``. The user's own web click and a repeat ``applied`` never count."""
    from src.services.applications import kit  # noqa: PLC0415 - kit imports this module lazily

    if actor == "web" or await kit.already_applied(db, user_id, application_id):
        return None
    who, review = await kit.cv_cleared_by(db, user_id, application_id, cv_artifact_id, channel)
    if who != "review" or review is None:
        return None
    p = review["payload"]
    return {"artifact_id": p.get("artifact_id"), "sha256": p.get("sha256"), "review_event_id": int(review["id"])}


async def record_sent(
    db: JobDatabase, user_id: str, application_id: int, clearance: Optional[dict[str, Any]], actor: str,
) -> None:
    """Append the :data:`SENT_EVENT` that :func:`review_clearance` found (nothing when it found none)."""
    from datetime import datetime, timezone  # noqa: PLC0415

    from src.services.applications import spine  # noqa: PLC0415

    if clearance is None:
        return
    await spine.append_event(
        db, user_id=user_id, application_id=application_id, event_type=SENT_EVENT, payload=dict(clearance),
        occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by=actor,
    )


REVIEW_LINES_CAP = 200


async def inputs(db: JobDatabase, user_id: str, application_id: int) -> Optional[dict[str, Any]]:
    """What a review of the NEWEST cv must name: its id, the sha256 ``check`` compares and the lines it requires a
    quote for, plus ``state`` (:func:`review_state` of that CV) (``None`` when no cv is saved). Same functions
    as ``check``, so the agent never guesses."""
    from src.services.applications import kit, spine  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    cv = await spine.latest_artifact(db, user_id, application_id, "cv")
    if cv is None:
        return None
    lines = content_lines(cv["text"], load_profile(user_id) or UserProfile())
    sha = kit.sha256_text(cv["text"])
    return {"artifact_id": cv["id"], "sha256": sha, "lines": lines[:REVIEW_LINES_CAP],
            "state": await review_state(db, user_id, application_id, cv["id"], sha)}


async def check(db: JobDatabase, user: CurrentUser, application_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """The stored payload: the verified confidence, the downgrade codes and counts (never text)."""
    return (await verify(db, user, application_id, payload))[0]


def _at(where: dict[str, list[str]], code: str, place: str) -> None:
    got = where.setdefault(code, [])
    if place not in got:
        got.append(place)


async def verify(
    db: JobDatabase, user: CurrentUser, application_id: int, payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """:func:`check`'s stored payload, plus WHERE each downgrade code came from (``row N`` = the payload's
    ``lines[N-1]``; ``review line N`` = get_application's ``review.lines[N-1]``), for the reply only, never stored."""
    from src.services.applications import kit, spine  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    problems = shape_problems(payload)
    if problems:
        raise SpineError(422, "; ".join(problems))
    entries: list[dict[str, str]] = payload["lines"]
    profile = load_profile(user.id) or UserProfile()
    where: dict[str, list[str]] = {}
    cv = await spine.latest_artifact(db, user.id, application_id, "cv")
    current = bool(cv and cv["id"] == payload["artifact_id"] and kit.sha256_text(cv["text"]) == payload["sha256"])
    if not current or cv is None:
        # Never stored, never counted toward retry_limit: a review of another CV would only look like a review.
        logger.warning("cv_reviewed refused: not_current_cv application=%s artifact=%s current=%s",
                       safe_log_value(application_id), safe_log_value(payload["artifact_id"]),
                       safe_log_value(cv["id"] if cv else None))
        raise SpineError(422, "not_current_cv: this is not the application's newest CV; use get_application's "
                              "review.artifact_id, review.sha256 and review.lines (nothing was stored)")
    content = content_lines(cv["text"], profile)
    keys = {line_key(e["cv_line"]) for e in entries}
    covered = sum(1 for ln in content if line_key(ln) in keys)
    if not content:
        where["no_content"] = []
    for n, ln in enumerate(content, 1):
        if line_key(ln) not in keys:
            _at(where, "line_uncovered", f"review line {n}")
    found = cv_checks.check_text("cv", cv["text"], profile)
    if found:
        where["cv_checks_found"] = [i["code"] for i in found]
    source = _norm(cv_checks.profile_source_text(profile))
    source_words = f' {" ".join(evidence._words(source))} '
    known = set(evidence._words(cv_checks.profile_source_text(profile)))
    actual: dict[str, list[str]] = {}
    for ln in cv["text"].splitlines():  # the CV's own text decides case, never the entry's copy
        actual.setdefault(line_key(ln), []).append(ln.strip().lstrip(_BULLETS + " \t"))
    content_keys = {line_key(ln) for ln in content}
    book, records = skill_book(profile), profile_records(profile)
    held = quotes_skipped = 0
    for n, e in enumerate(entries, 1):
        row = f"row {n}"
        key = line_key(e["cv_line"])
        if key not in actual:
            _at(where, "line_not_in_cv", row)
        bodies = actual.get(key) or [e["cv_line"].strip().lstrip(_BULLETS + " \t")]
        if key in actual and key not in content_keys:
            continue  # a row for a line that needs none (the name, a contact line, a section heading) is not judged
        if key in actual:  # a line the profile itself holds needs no quote: its row's quote is not judged either
            unheld = [b for b in bodies if not (list_line_holds(b, book) or record_line_holds(b, records))]
            held += len(bodies) - len(unheld)
            if not unheld:
                quotes_skipped += not quote_in_profile(e["profile_quote"], source, source_words)
                continue
            bodies = unheld
        if not quote_in_profile(e["profile_quote"], source, source_words):  # (c), only for a line that needs it
            _at(where, "quote_not_in_profile", row)
        bank = _quote_bank(e["profile_quote"])
        quote_words = set(evidence._words(bank))
        for body in bodies:  # a row naming no CV line (line_not_in_cv) is judged whole, never held
            if evidence.unsupported_tokens(body, bank, first_word=True):
                _at(where, "claim_not_in_quote", row)
            content_words = {w for w in evidence._words(body) if len(w) >= 4}
            if content_words - quote_words - known:  # "kubernetes", "twelve": a long word the profile never says
                _at(where, "claim_not_in_quote", row)
            thin = len(set(evidence._words(e["profile_quote"]))) < 3  # the quote's OWN words, never the bank's
            if thin or 2 * len(content_words & quote_words) < len(content_words):
                _at(where, "quote_too_thin", row)
    if held:  # how often a list or record line was held by the profile itself (no CV text logged); quotes_skipped:
        # held rows whose quote is no span of the profile, each a quote_not_in_profile under rules 2
        logger.info("cv_reviewed lines_held_by_profile=%s quotes_skipped=%s application=%s", held, quotes_skipped,
                    safe_log_value(application_id))
    reviews = await kit._events(db, user.id, application_id, (EVENT_TYPE,))
    # A legacy review that named no real version (not_current_cv, empty sha) was never a try: it never counts. Nor
    # does a weak review judged under OLDER rules (no or another ``rules``): its verdict may be one these rules fix
    # (production 2026-11-10: 3 false-weak reviews had spent apps' whole budget), and its rows were never stored,
    # so it cannot be judged again. Each rules change gives an application MAX_WEAK new tries, never more.
    weak = [e for e in reviews if e["payload"].get("confidence") != STRONG]
    weak_versions = {e["payload"].get("artifact_id") for e in weak
                     if review_valid(e["payload"]) and e["payload"].get("rules") == RULES}
    if len(weak_versions) < len({e["payload"].get("artifact_id") for e in weak}):
        logger.info("cv_reviewed retry_limit ignores legacy or older-rules reviews application=%s",
                    safe_log_value(application_id))
    if len(weak_versions - {payload["artifact_id"]}) >= MAX_WEAK:  # a re-review of a judged version is no new try
        where["retry_limit"] = []
    down = set(where)
    claimed = payload["confidence"]
    if claimed == STRONG and down:  # the line that shows a false weak: which codes, never which text
        logger.info("cv_reviewed downgraded application=%s codes=%s checks=%s content=%s covered=%s",
                    safe_log_value(application_id), ",".join(c for c in DOWNGRADES if c in down),
                    ",".join(where.get("cv_checks_found") or []), len(content), covered)
    stored = {
        "artifact_id": payload["artifact_id"], "sha256": payload["sha256"], "claimed": claimed,
        "confidence": STRONG if claimed == STRONG and not down else WEAK,
        "downgraded": [c for c in DOWNGRADES if c in down], "lines": len(entries),
        "content_lines": len(content), "covered": covered, "reviewer": _label(payload["reviewer"]),
        "by": actor_for(user), "rules": RULES,
    }
    return stored, where


_WHERE_LISTED = 10


def weak_warning(stored: dict[str, Any], where: dict[str, list[str]]) -> Optional[str]:
    """The reply line when a CLAIMED strong review was STORED weak: every code and where it came from, so a run
    never reports "strong" for a weak store."""
    if stored.get("claimed") != STRONG or stored.get("confidence") == STRONG:
        return None
    parts = []
    for code in stored.get("downgraded") or []:
        at = where.get(code) or []
        more = f" (+{len(at) - _WHERE_LISTED} more)" if len(at) > _WHERE_LISTED else ""
        parts.append(f"{code}: {', '.join(at[:_WHERE_LISTED])}{more}" if at else code)
    return ("cv_reviewed claimed strong but STORED WEAK: " + "; ".join(parts) + ". Row N = your lines[N-1]; "
            "review line N = get_application's review.lines[N-1]. Report this CV as weak, never strong.")

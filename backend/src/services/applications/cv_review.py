"""The second check before a CV goes without the owner (owner decision 2026-11-05): a separate reviewer.

The assistant hands a saved CV to a fresh helper that is not its writer (the Doctor's step 2 hand-off). That
helper gives, for every CV line, the profile quote and field path behind it, then ``strong`` or ``weak``; the
assistant records it as ``cv_reviewed``. Job360 runs no model (decision 28): it stores the verdict and VERIFIES
it. ``strong`` is stored only when all of these hold, else ``weak`` with closed :data:`DOWNGRADES` codes:

(a) ``artifact_id`` + ``sha256`` are this application's CURRENT CV, exactly (``not_current_cv``);
(b) every content line of that CV has an entry whose ``cv_line`` is that line (case, spacing and a leading
    bullet aside) (``line_uncovered``; an entry naming no CV line is ``line_not_in_cv``; a CV of headers only is
    ``no_content``). Exempt, and only these: the name line (the FIRST line, every word a word of the profile's
    name), a contact line (only emails, links and one phone number, with separators; a ``Label:`` word is
    content), and a section heading (1-4 alphabetic words, one of 4+ letters: ALL CAPS, ``# Heading`` or
    ``Heading:``) whose every word is already a word of the profile source text. A summary line is not;
(c) each ``profile_quote`` is in ``cv_checks.profile_source_text`` (case and whitespace aside)
    (``quote_not_in_profile``);
(d) every number and capitalised name of a ``cv_line``, its first word too (``evidence.claim_tokens``), is in
    its own quote (``claim_not_in_quote``); the quote has 3+ words and at least half the line's words of 4+
    letters (``quote_too_thin``); words are scanned one by one, no regex on caller text;
(e) ``cv_checks.check_text("cv")`` finds nothing for that CV (``cv_checks_found``).
After weak reviews of :data:`MAX_WEAK` other CV versions on an application, a review of a new one is weak
(``retry_limit``): one rewrite, one re-review, never a third try.

The event stores codes and counts, never CV or profile text. ``may_submit`` reads :func:`review_state` for the
exact current CV id + sha, and only with ``assistant_settings.cv_auto_approve`` on, in auto mode.
"""
from __future__ import annotations

import logging
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
MAX_WEAK = 2
MAX_LINES = 200
_CAPS = {"cv_line": 1000, "profile_quote": 2000, "field_path": 200}
REVIEWER_MAX = 40
_BULLETS = "".join(cv_checks._BULLET_STARTS)
_SEPARATORS = frozenset({"|", "•", "·", "-", "–", "—", ",", "/"})
_PHONE_CHARS = frozenset("0123456789+()-.")


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


def is_contact_line(line: str) -> bool:
    """Only emails, links and ONE phone number (10-15 digits, opening with ``+``, ``(`` or ``0``), with separators."""
    rest: list[str] = []
    anchor = False
    for raw in line.split():
        tok = raw.strip(",;")
        if not tok or tok in _SEPARATORS:
            continue
        if _is_email(tok) or _is_link(tok):
            anchor = True
        else:  # a label word ("CTO:") is content, never a free pass
            rest.append(tok)
    if not rest:
        return anchor
    digits = sum(c.isdigit() for t in rest for c in t)
    return (len(rest) <= 5 and all(set(t) <= _PHONE_CHARS for t in rest) and 10 <= digits <= 15
            and rest[0][0] in "+(0")


# A heading is exempt only when every word is a section name: "SENIOR DATA ENGINEER" is a claim, never a heading.
SECTION_WORDS = frozenset((
    "about", "achievements", "and", "awards", "career", "certifications", "competencies", "contact", "core",
    "courses", "education", "employment", "experience", "history", "interests", "key", "languages", "objective",
    "other", "personal", "professional", "profile", "projects", "publications", "qualifications", "references",
    "relevant", "selected", "skills", "summary", "technical", "training", "volunteering", "work",
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


def content_lines(text: str, profile: Any) -> list[str]:
    """The CV's non-empty lines minus the narrow exemptions (the name line, contact lines, and a heading whose
    every word the profile already says: "AWS SOLUTIONS ARCHITECT" is a claim unless the profile holds it)."""
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    names = _name_words(profile)
    known = set(evidence._words(cv_checks.profile_source_text(profile)))
    out: list[str] = []
    for i, line in enumerate(lines):
        words = set(evidence._words(line))
        said = set(evidence._words(line.strip("#*: \t")))
        heading = is_heading(line) and bool(said) and said <= known and said <= SECTION_WORDS
        if (i == 0 and names and words and words <= names) or is_contact_line(line) or heading:
            continue
        out.append(line)
    return out


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


async def newest_review(
    db: JobDatabase, user_id: str, application_id: int, cv_id: Optional[int], cv_sha: str,
) -> Optional[dict[str, Any]]:
    """The newest stored ``cv_reviewed`` event of exactly this CV (id + sha), or ``None``."""
    from src.services.applications import kit  # noqa: PLC0415 - kit imports this module lazily

    if cv_id is None:
        return None
    for ev in await kit._events(db, user_id, application_id, (EVENT_TYPE,)):
        p = ev["payload"]
        if p.get("artifact_id") == cv_id and p.get("sha256") == cv_sha:
            return ev
    return None


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
    quote for (``None`` when no cv is saved). Same functions as ``check``, so the agent never guesses."""
    from src.services.applications import kit, spine  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    cv = await spine.latest_artifact(db, user_id, application_id, "cv")
    if cv is None:
        return None
    lines = content_lines(cv["text"], load_profile(user_id) or UserProfile())
    return {"artifact_id": cv["id"], "sha256": kit.sha256_text(cv["text"]), "lines": lines[:REVIEW_LINES_CAP]}


async def check(db: JobDatabase, user: CurrentUser, application_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """The stored payload: the verified confidence, the downgrade codes and counts (never text)."""
    from src.services.applications import kit, spine  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    problems = shape_problems(payload)
    if problems:
        raise SpineError(422, "; ".join(problems))
    entries: list[dict[str, str]] = payload["lines"]
    profile = load_profile(user.id) or UserProfile()
    down: set[str] = set()
    cv = await spine.latest_artifact(db, user.id, application_id, "cv")
    current = bool(cv and cv["id"] == payload["artifact_id"] and kit.sha256_text(cv["text"]) == payload["sha256"])
    content: list[str] = []
    covered = 0
    if not current or cv is None:
        # Never stored, never counted toward retry_limit: a review of another CV would only look like a review.
        logger.warning("cv_reviewed refused: not_current_cv application=%s artifact=%s current=%s",
                       application_id, payload["artifact_id"], cv["id"] if cv else None)
        raise SpineError(422, "not_current_cv: this is not the application's newest CV; use get_application's "
                              "review.artifact_id, review.sha256 and review.lines (nothing was stored)")
    else:
        content = content_lines(cv["text"], profile)
        keys = {line_key(e["cv_line"]) for e in entries}
        covered = sum(1 for ln in content if line_key(ln) in keys)
        down |= {"no_content"} if not content else {"line_uncovered"} if covered < len(content) else set()
        if keys - {line_key(ln) for ln in cv["text"].splitlines()}:
            down.add("line_not_in_cv")
        if cv_checks.check_text("cv", cv["text"], profile):
            down.add("cv_checks_found")
    source = _norm(cv_checks.profile_source_text(profile))
    known = set(evidence._words(cv_checks.profile_source_text(profile)))
    actual: dict[str, list[str]] = {}
    for ln in (cv["text"].splitlines() if cv else []):  # the CV's own text decides case, never the entry's copy
        actual.setdefault(line_key(ln), []).append(ln.strip().lstrip(_BULLETS + " \t"))
    for e in entries:
        bodies = actual.get(line_key(e["cv_line"])) or [e["cv_line"].strip().lstrip(_BULLETS + " \t")]
        if _norm(e["profile_quote"]) not in source:
            down.add("quote_not_in_profile")
        quote_words = set(evidence._words(e["profile_quote"]))
        for body in bodies:
            if evidence.unsupported_tokens(body, e["profile_quote"], first_word=True):
                down.add("claim_not_in_quote")
            content_words = {w for w in evidence._words(body) if len(w) >= 4}
            if content_words - quote_words - known:  # "kubernetes", "twelve": a long word the profile never says
                down.add("claim_not_in_quote")
            if len(quote_words) < 3 or 2 * len(content_words & quote_words) < len(content_words):
                down.add("quote_too_thin")
    reviews = await kit._events(db, user.id, application_id, (EVENT_TYPE,))
    weak_versions = {e["payload"].get("artifact_id") for e in reviews if e["payload"].get("confidence") != STRONG}
    if len(weak_versions - {payload["artifact_id"]}) >= MAX_WEAK:  # a re-review of a judged version is no new try
        down.add("retry_limit")
    claimed = payload["confidence"]
    return {
        "artifact_id": payload["artifact_id"], "sha256": payload["sha256"], "claimed": claimed,
        "confidence": STRONG if claimed == STRONG and not down else WEAK,
        "downgraded": [c for c in DOWNGRADES if c in down], "lines": len(entries),
        "content_lines": len(content), "covered": covered, "reviewer": _label(payload["reviewer"]),
        "by": actor_for(user),
    }

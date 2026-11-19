"""The second check before an outreach message goes out on its own (owner decision 2026-11-09).

Same idea as ``cv_reviewed`` (cv_review.py): a fresh reviewer that is not the writer gives, for every line of a
saved outreach draft (a LinkedIn connection note or message, an email to a person), the profile quote and field path
behind it, then ``strong`` or ``weak``; the assistant records it as ``outreach_reviewed`` with ``record_event``
(``contact_id``). Job360 runs no model (decision 28): it stores the verdict and VERIFIES it. A review that does not
name the contact's NEWEST draft, or whose ``sha256`` is not that draft's text hash, is refused (422, nothing stored).
``strong`` is stored only when all of these hold, else ``weak`` with closed :data:`DOWNGRADES` codes:

(a) every required line of the draft has an entry whose ``msg_line`` is that line (case, spacing and a leading
    bullet aside) (``line_uncovered``; an entry naming no line of the draft is ``line_not_in_message``; a draft with
    no required line is ``no_content``). Not required, and only these: a greeting first line ("Hi Sam," - a
    greeting word, then only the person's name words or "there" / "team" / "all"), a sign-off line (only sign-off
    words and the user's own name words: "Best regards," "Ana Lima") and a contact line (cv_review.is_contact_line);
(b) each ``profile_quote`` lies inside ONE profile field value or text line (``cv_review.quote_sources`` /
    ``quote_in_profile``: case, spacing and outer punctuation aside; never two fields glued together)
    (``quote_not_in_profile``) and has 3+ words (``quote_too_thin``);
(c) every hard fact of a ``msg_line`` (``review_rows.hard_facts``, the CV rules: numbers and number words, role
    words, capitalised names wherever they stand, a name opening a later sentence too) is in its own quote, the
    person's name, their company, the job's company or title, or the user's name (``claim_not_in_quote``; the
    stored ``why`` lists which :data:`WHY_CODES` rule fired, codes only); words are scanned one by one, no regex on
    caller text.
After weak reviews of :data:`MAX_WEAK` other drafts to this person, a review of a new one is weak (``retry_limit``):
two rewrites, each re-reviewed; never a fourth try.

Stored on the contact's own ledger (``contact_outreach`` entry :data:`ENTRY`, a row Job360 writes itself, not in
``settings.OUTREACH_ENTRIES``, so a cold contact has one too) as JSON of codes and counts, never message or profile
text; a contact linked to a job also gets the ``outreach_reviewed`` timeline event. ``check_outreach`` reads
:func:`review_state` for the exact draft id + sha: every AUTOMATIC send (:data:`AUTO_SEND_REASONS`) needs ``strong``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications import cv_review, evidence, review_rows
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError
from src.utils.logger import safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

EVENT_TYPE = "outreach_reviewed"
ENTRY = "reviewed"
STRONG, WEAK, NONE = cv_review.STRONG, cv_review.WEAK, cv_review.NONE
ID_KEY, LINE_FIELD = "message_id", "msg_line"
DOWNGRADES = (
    "no_content", "line_uncovered", "line_not_in_message", "quote_not_in_profile", "claim_not_in_quote",
    "quote_too_thin", "retry_limit",
)
# Closed: which hard-fact rule made a line claim_not_in_quote (stored as ``why``, like cv_reviewed's ``where``).
WHY_CODES = review_rows.HARD_FACT_CODES
MAX_WEAK = cv_review.MAX_WEAK  # owner 2026-11-09: up to two rewrites, each re-reviewed
REVIEW_LINES_CAP = cv_review.REVIEW_LINES_CAP
# check_outreach's AUTOMATIC sends: nobody pressed Send for this text. owner_clicked and connect_no_note are the
# user's own Send; auto_connect is a connection request WITHOUT a note (no text goes, so there is nothing to review).
AUTO_SEND_REASONS = ("auto_mode", "auto_after_accept", "auto_follow_up", "auto_note")
NOT_REVIEWED = "outreach_not_reviewed"
GREETINGS = frozenset({"hi", "hello", "dear", "hey"})
_GREETING_EXTRA = frozenset({"there", "team", "all"})
# Words an email subject may add to the job, company and person names and still say nothing about the user.
_SUBJECT_EXTRA = frozenset({"subject", "re", "fw", "fwd", "role", "roles", "position", "job", "opening", "vacancy",
                            "application", "applying", "for", "the", "a", "an", "your", "at", "about", "and",
                            "to", "in", "of", "-", "–", "—", "|", ":"})
SIGNOFF_WORDS = frozenset({
    "best", "regards", "kind", "warm", "warmest", "thanks", "thank", "you", "cheers", "sincerely", "many", "all",
    "the", "yours", "with",
})

logger = logging.getLogger(__name__)


ROW_KEYS = (LINE_FIELD, "profile_quote", "field_path")
_ROW_CAPS = {LINE_FIELD: 1000, "profile_quote": cv_review.QUOTE_MAX_CHARS, "field_path": 200}
_ROWS = "{msg_line, profile_quote, field_path}"


def shape_problems(payload: dict[str, Any]) -> list[str]:
    """Every shape problem (422). Its own rows: a message line has no ``n`` in a CV's review.lines, so
    cv_review's {n, verdict, evidence} (rules 5) is not this shape; the payload checks are the same."""
    problems: list[str] = []
    if set(payload) != {ID_KEY, "sha256", "confidence", "lines", "reviewer"}:
        problems.append(f"{EVENT_TYPE} takes exactly {{{ID_KEY}, sha256, confidence, lines, reviewer}}")
    msg_id = payload.get(ID_KEY)
    if isinstance(msg_id, bool) or not isinstance(msg_id, int) or not isinstance(payload.get("sha256"), str):
        problems.append(f"needs {ID_KEY} (a whole number) and sha256 (text)")
    if payload.get("confidence") not in (STRONG, WEAK):
        problems.append('confidence must be "strong" or "weak"')
    reviewer = payload.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > cv_review.REVIEWER_MAX:
        problems.append(f"reviewer must be a short label (1-{cv_review.REVIEWER_MAX} characters)")
    lines = payload.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= cv_review.MAX_LINES:
        return [*problems, f"lines must be a list of 1-{cv_review.MAX_LINES} {_ROWS}"]
    for i, row in enumerate(lines):
        if not isinstance(row, dict) or set(row) != set(ROW_KEYS):
            problems.append(f"lines[{i}] takes exactly {_ROWS}")
            continue
        for key, cap in _ROW_CAPS.items():
            if not isinstance(row[key], str) or not row[key].strip() or len(row[key]) > cap:
                problems.append(f"lines[{i}].{key} must be text of 1-{cap} characters")
    return problems


def _parse(raw: Any) -> dict[str, Any]:
    try:
        got = json.loads(str(raw or ""))
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def content_lines(text: str, profile: Any, person_name: str = "", context: str = "") -> list[str]:
    """The draft's non-empty lines a review must quote the profile for (see the module docstring).

    ``context`` (:func:`_person`'s claim words: the person, their company, the job's company and title): an email's
    leading ``Subject:`` line naming nothing beyond those words (plus :data:`_SUBJECT_EXTRA`) is exempt, and the
    greeting test then applies to the line after it (prod 2026-11-19, apps 843 / 1140: the subject on line 0 made
    the greeting and the subject required lines no profile quote can back, so every email review stored weak).
    A subject that says anything else stays a required line."""
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    own = cv_review._name_words(profile)
    person = set(evidence._words(person_name))
    first = greet_at = 0
    head = evidence._words(lines[0]) if lines else []
    if head and head[0] == "subject":
        greet_at = 1  # the greeting is the first line after any subject line
        if context and set(head[1:]) <= set(evidence._words(context)) | _SUBJECT_EXTRA:
            first = 1
    out: list[str] = []
    for i, line in enumerate(lines):
        if i < first:
            continue
        order = evidence._words(line)
        words = set(order)
        if not words or cv_review.is_contact_line(line) or words <= own | SIGNOFF_WORDS:
            continue
        if i == greet_at and order[0] in GREETINGS and set(order[1:]) <= person | _GREETING_EXTRA:
            continue
        out.append(line)
    if first:
        logger.info("outreach_subject_exempt lines=%s required=%s", len(lines), len(out))
    return out


def review_state(rows: list[dict[str, Any]], message_id: Optional[int], sha: str) -> str:
    """``strong`` / ``weak``: the newest stored review of exactly this draft (id + sha); ``none`` without one."""
    if message_id is None or not sha:
        return NONE
    for row in reversed(rows):
        if row.get("entry") != ENTRY:
            continue
        p = _parse(row.get("text"))
        if p.get(ID_KEY) == int(message_id) and p.get("sha256") == sha:
            return STRONG if p.get("confidence") == STRONG else WEAK
    return NONE


# A fact about the recipient is proven by the page it is on ("page:<url>") or by the lookup that lists it
# ("lookup:<tool>", its own text as the quote: Kits 19 Nov, "at Abound" was proven by Clay and Sprouts).
RECIPIENT_PATHS = ("page:", "page/", "recipient:", "recipient/", "lookup:", "lookup/")


def sentences(line: str) -> list[str]:
    """A draft line's sentences (split after . ! ?), stripped; scanned character by character, no regex."""
    out, cur = [], []
    for ch in str(line or ""):
        cur.append(ch)
        if ch in ".!?":
            out.append("".join(cur).strip())
            cur = []
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return [x for x in out if x]


async def _person(db: JobDatabase, user_id: str, contact: dict[str, Any]) -> tuple[str, str]:
    """The person's CURRENT name, and the words that vouch for a name in a draft (theirs, their company, the
    job's company and title): the same context ``check_outreach`` gives its name check."""
    from src.services.applications import contacts  # noqa: PLC0415 - contacts imports this package

    current = dict(contact)
    contacts._apply_current_fields(current, (await contacts._latest_edits_for_user(db, user_id)).get(
        int(contact["id"]), {}))
    return str(current.get("name") or ""), await contacts.claim_context(db, user_id, contact, current)


async def inputs(db: JobDatabase, user_id: str, contact: dict[str, Any]) -> Optional[dict[str, Any]]:
    """What a review of the person's NEWEST draft must name: ``message_id``, the ``sha256`` the check compares,
    the ``lines`` it requires a quote for, and ``state`` (:func:`review_state`); ``None`` when no draft is saved.
    The same functions as :func:`check`, so the reviewer never computes the sha itself."""
    from src.services.applications import contacts, kit  # noqa: PLC0415

    rows = await contacts._outreach_rows(db, int(contact["id"]))
    messages = [r for r in rows if r["entry"] == "message"]
    if not messages:
        return None
    draft = messages[-1]
    name, context = await _person(db, user_id, contact)
    profile = await contacts._load_profile_or_none(user_id)
    sha = kit.sha256_text(str(draft.get("text") or ""))
    lines = content_lines(str(draft.get("text") or ""), profile, name, context)
    return {"message_id": int(draft["id"]), "sha256": sha, "lines": lines[:REVIEW_LINES_CAP],
            "state": review_state(rows, int(draft["id"]), sha)}


async def check(
    db: JobDatabase, user: CurrentUser, contact: dict[str, Any], payload: dict[str, Any],
) -> dict[str, Any]:
    """The payload to store: the verified confidence, the downgrade codes and counts (never text). 422 for a bad
    shape, or a review that does not name the newest draft exactly (id + sha): nothing is stored then."""
    from src.services.applications import contacts, kit  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415

    problems = shape_problems(payload)
    if problems:
        raise SpineError(422, "; ".join(problems))
    contact_id = int(contact["id"])
    rows = await contacts._outreach_rows(db, contact_id)
    messages = [r for r in rows if r["entry"] == "message"]
    draft = messages[-1] if messages else None
    text = str(draft.get("text") or "") if draft else ""
    if draft is None or int(draft["id"]) != payload[ID_KEY] or kit.sha256_text(text) != payload["sha256"]:
        # Never stored, never counted toward retry_limit: a review of another draft would only look like one.
        logger.warning("outreach_reviewed refused: not_latest_draft contact=%s message=%s latest=%s",
                       safe_log_value(contact_id), safe_log_value(payload[ID_KEY]),
                       safe_log_value(draft["id"] if draft else None))
        raise SpineError(422, "not_latest_draft: this is not the person's newest draft (or its sha256 differs); use "
                              "list_people(contact_id) review.message_id, review.sha256 and review.lines "
                              "(nothing was stored)")
    entries: list[dict[str, str]] = payload["lines"]
    profile = await contacts._load_profile_or_none(user.id) or UserProfile()
    name, context = await _person(db, user.id, contact)
    content = content_lines(text, profile, name, context)
    down: set[str] = set()
    # Kits audit 3A (orders 23:10): every sentence is judged against the profile EXCEPT one the fresh reviewer marks
    # as about the recipient, with a row whose field_path names its page ("page:<url>") and the quote from it (3+
    # words). Such a row may name a whole line or one sentence of it. The reviewer decides; Job360 never guesses.
    page_rows = [e for e in entries if str(e.get("field_path") or "").lower().startswith(RECIPIENT_PATHS)]
    page_keys = {cv_review.line_key(e[LINE_FIELD]) for e in page_rows}
    profile_rows = [e for e in entries if e not in page_rows]
    keys = {cv_review.line_key(e[LINE_FIELD]) for e in entries}

    own_words = cv_review._name_words(profile)

    def _needs_row(sentence: str) -> bool:
        """A sentence of the user's own sign-off ("Thanks, Ada") needs no row; every other sentence does."""
        words = set(evidence._words(sentence))
        return bool(words) and not words <= own_words | SIGNOFF_WORDS

    def _covered(line: str) -> bool:
        # A whole-line row, or a row (page or profile) for every sentence that needs one (prod 2026-11-19 13:02,
        # contacts 1769 / 1770: a note reviewed sentence by sentence stored weak, covered 0).
        return cv_review.line_key(line) in keys or all(
            cv_review.line_key(x) in keys for x in sentences(line) if _needs_row(x))

    covered = sum(1 for ln in content if _covered(ln))
    down |= {"no_content"} if not content else {"line_uncovered"} if covered < len(content) else set()
    known = {cv_review.line_key(ln) for ln in text.splitlines()} | {
        cv_review.line_key(x) for ln in text.splitlines() for x in sentences(ln)}
    if keys - known:
        down.add("line_not_in_message")
    source, source_words = cv_review.quote_sources(profile)  # a quote lies inside ONE profile field or line
    own = " ".join(sorted(cv_review._name_words(profile)))
    terms = review_rows.profile_terms(profile)
    for e in page_rows:  # a recipient sentence still needs its page quote: a made-up compliment is weak too
        if len(evidence._words(e["profile_quote"])) < 3:
            down.add("quote_too_thin")
    # A row may name one sentence of a line: its quote then counts for that whole line's facts check.
    line_of = {cv_review.line_key(x): cv_review.line_key(ln) for ln in text.splitlines() for x in sentences(ln)}
    quotes: dict[str, list[str]] = {}
    for e in profile_rows:
        quote = e["profile_quote"]
        if not cv_review.quote_in_profile(quote, source, source_words):
            down.add("quote_not_in_profile")
        if len(evidence._words(quote)) < 3:
            down.add("quote_too_thin")
        row_key = cv_review.line_key(e[LINE_FIELD])
        quotes.setdefault(line_of.get(row_key, row_key), []).append(quote)
    # 2026-11-18: a LinkedIn note is ONE line naming facts from several profile fields, and each quote must sit in
    # ONE field: a line's facts are judged against all the quotes given for that line (each still inside one field).
    why: set[str] = set()  # WHERE claim_not_in_quote fired: review_rows.HARD_FACT_CODES, never the draft's words
    for ln in text.splitlines():
        key = cv_review.line_key(ln)
        if key not in quotes or key in page_keys:  # an uncovered line is line_uncovered, as before
            continue
        body = ln.strip().lstrip(cv_review._BULLETS + " \t")  # the draft's own text decides case
        judged = " ".join(x for x in sentences(body) if cv_review.line_key(x) not in page_keys)
        codes = review_rows.hard_fact_codes(judged, [*quotes.get(key, []), context, own], *terms) if judged else []
        if codes:
            down.add("claim_not_in_quote")
            why |= set(codes)
    if why:  # prod 2026-11-19 13:56: 17 reviews stored claim_not_in_quote and nothing said which rule fired
        logger.info("outreach_reviewed claim_not_in_quote contact=%s why=%s", safe_log_value(contact_id),
                    ",".join(c for c in WHY_CODES if c in why))
    weak_ids = {
        p.get(ID_KEY) for p in (_parse(r.get("text")) for r in rows if r["entry"] == ENTRY)
        if p.get("confidence") != STRONG and p.get("sha256")
    }
    if len(weak_ids - {payload[ID_KEY]}) >= MAX_WEAK:  # a re-review of a judged draft is no new try
        down.add("retry_limit")
    claimed = payload["confidence"]
    return {
        ID_KEY: payload[ID_KEY], "sha256": payload["sha256"], "claimed": claimed,
        "confidence": STRONG if claimed == STRONG and not down else WEAK,
        "downgraded": [c for c in DOWNGRADES if c in down], "why": [c for c in WHY_CODES if c in why],
        "lines": len(entries),
        "content_lines": len(content), "covered": covered, "reviewer": cv_review._label(payload["reviewer"]),
        "by": actor_for(user), "channel": str(draft.get("channel") or ""),
    }


def missing_warning(stored: dict[str, Any]) -> Optional[str]:
    """The reply line for a review that quoted too few lines (it is still stored, weak)."""
    if "line_uncovered" not in (stored.get("downgraded") or []):
        return None
    missing = int(stored.get("content_lines") or 0) - int(stored.get("covered") or 0)
    return (f"outreach_reviewed stored weak: {missing} of {stored.get('content_lines')} required lines had no row; "
            "return ONE row per line in list_people(contact_id) review.lines (msg_line copied exactly)")


async def record(
    db: JobDatabase, user: CurrentUser, contact_id: int, payload: dict[str, Any],
    application_id_hint: Optional[int] = None,
) -> dict[str, Any]:
    """Verify and store one ``outreach_reviewed``: the ledger row, plus the timeline event when the person is
    linked to a job, in ONE transaction. 404 for a contact that is not the caller's (rule #12)."""
    from src.services.applications import contacts, spine  # noqa: PLC0415

    contact = await contacts.get_owned_contact(db, user.id, contact_id)
    if contact is None:
        raise SpineError(404, "contact not found")
    application_id = contact.get("application_id")
    if application_id_hint is not None and application_id != application_id_hint:
        raise SpineError(422, "application_id does not match this contact's linked job")
    stored = await check(db, user, contact, payload)
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM contact_outreach WHERE contact_id = ? AND entry = ?", (contact_id, ENTRY)
    )
    got = await cur.fetchone()
    if got is not None and int(got[0]) >= settings.OUTREACH_ENTRIES_PER_CONTACT_MAX:
        raise SpineError(429, "too many outreach reviews for this person")
    now = datetime.now(timezone.utc).isoformat()
    actor = actor_for(user)
    event_id: Optional[int] = None
    async with db._db.transaction():
        ins = await db._db.execute(
            "INSERT INTO contact_outreach (user_id, contact_id, entry, channel, text, version_no, occurred_at, "
            "recorded_at, recorded_by, source_message_id, label) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, '', '')",
            (user.id, contact_id, ENTRY, stored["channel"] or "other", json.dumps(stored, sort_keys=True), now, now,
             actor),
        )
        outreach_id = int(ins.lastrowid or 0)
        if application_id is not None:
            event = await spine.append_event(
                db, user_id=user.id, application_id=int(application_id), event_type=EVENT_TYPE,
                payload={**stored, "contact_id": contact_id, "outreach_id": outreach_id},
                occurred_at=now, recorded_by=actor,
            )
            event_id = int(event["event_id"])
    logger.info("outreach_reviewed contact=%s message=%s confidence=%s downgraded=%s",
                safe_log_value(contact_id), safe_log_value(stored[ID_KEY]), stored["confidence"],
                ",".join(stored["downgraded"]) or "-")
    warnings = [w for w in (missing_warning(stored),) if w]
    if stored["confidence"] != STRONG:  # the caller learns the CHECKED verdict, not only the one it claimed
        warnings.append(f"outreach_reviewed stored weak ({', '.join(stored['downgraded']) or 'claimed weak'}): "
                        "check_outreach will not send it on its own")
    return {
        "event_id": event_id, "event_type": EVENT_TYPE, "occurred_at": now, "recorded_at": now, "recorded_by": actor,
        "status": "", "already_existed": False, "scheduled_at": None, "follow_up_on": None, "warnings": warnings,
    }

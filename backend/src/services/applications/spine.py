"""The application spine's DB-facing core (spec.md R1-R11).

Every write here goes through ``from src.repositories import pg as aiosqlite``
indirectly via ``db._db`` (the guarded accessor over ``db._conn`` — C5; the
same connection every route already depends on) — SQLite-shaped SQL,
``pg.py``'s ``translate()`` rewrites it for Postgres at execute time. No
route or MCP tool talks to the tables directly; they all come through this
module, so there is exactly one place that can get the event log wrong.

Append-only, for real: nothing in this file issues ``UPDATE``/``DELETE``
against ``application_events`` or ``application_artifacts`` (grep-guarded by
``tests/test_application_spine.py::test_events_are_append_only``). The only
``UPDATE`` targets are the ``applications`` CACHE SLOT (status/fit — S7: an
update of the slot is explicitly not history) and, once, a legacy
``application_receipts`` row picking up its ``application_id``.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.core import settings
from src.repositories import pg
from src.services import idempotency
from src.services.applications.status import (
    PROGRESS_RANK,
    progress_rank,
    replay_status,
    stage_for_status,
    status_for_event,
)
from src.services.auth import rate_limit
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover — type-only; avoids a runtime import of
    # database.py (rule #16-adjacent: this module stays light at import time).
    from src.repositories.database import JobDatabase


class SpineError(Exception):
    """Domain error a route turns into ``HTTPException(status_code, detail)``."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ── Pure validation helpers (no DB — hit directly by tests) ────────────────


def validate_event_type(event_type: str) -> None:
    """R7 — closed set in code. 422 naming the allowed list on a miss."""
    allowed = (
        set(settings.APPLICATION_STATUS_EVENT_TYPES)
        | set(settings.APPLICATION_NOTE_EVENT_TYPES)
        | set(settings.APPLICATION_EXTRA_EVENT_TYPES)
    )
    if event_type not in allowed:
        raise SpineError(
            422,
            f"unknown event_type {event_type!r}; allowed: {sorted(allowed)}",
        )


# Reserved payload key (weak spot #4 audit). A converted repeat-applied note carries
# ``{"confirms": "applied"}``; validate_payload refuses it from callers, so only Job360 writes it.
# proof.py counts such a note as email proof ONLY with an email source + message_id.
CONFIRMS_KEY = "confirms"
CONFIRMS_APPLIED = "applied"


def payload_bytes(payload: Mapping[str, Any]) -> int:
    """The SERIALISED size of an event payload, in bytes — what the column
    actually costs, not ``len()`` of the Python object (S5)."""
    return len(json.dumps(payload).encode("utf-8"))


def validate_payload(payload: Any, *, max_bytes: Optional[int] = None) -> dict[str, Any]:
    """S5 — payload must be a JSON OBJECT (never a list/scalar), size-capped
    on the SERIALISED form, because that is what the column costs.

    Bug fix (coordinator review, 2026-09-25) — ``follow_up_on`` is its OWN
    top-level request field precisely so ``append_event`` can route it into
    both the slot and the payload together (S7). A caller stuffing
    ``payload={"follow_up_on": ...}`` instead would silently land a date in
    history while the slot (and ``follow_up_due``) stayed untouched — the
    date reads as recorded but Job360 never actually chases it. Refuse it
    outright rather than silently drop or silently honour it."""
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise SpineError(422, "payload must be a JSON object")
    if "follow_up_on" in payload:
        raise SpineError(422, "pass follow_up_on as its own field, not inside payload")
    if CONFIRMS_KEY in payload:  # reserved: only append_event's repeat-applied conversion may write it
        raise SpineError(422, f"payload key {CONFIRMS_KEY!r} is reserved for Job360 itself")
    size = payload_bytes(payload)
    cap = settings.APPLICATION_EVENT_PAYLOAD_MAX_BYTES if max_bytes is None else max_bytes
    if size > cap:
        name = "APPLICATION_EVENT_PAYLOAD_MAX_BYTES" if max_bytes is None else "KIT_FORM_PAYLOAD_MAX_BYTES"
        raise SpineError(422, f"payload exceeds {name} ({cap} bytes)")
    return payload


# ── Outcome payloads (feedback loop slice 1) ─────────────────────────────────
# Only the NAMED optional keys are type-checked; unknown extra keys stay allowed.
OUTCOME_EVENT_TYPES = frozenset({"rejected", "ghosted", "withdrawn", "offer", "hired", "offer_declined", "post_mortem"})
REPLY_CLASSIFIED_EVENT_TYPES = frozenset({
    "replied", "rejected", "interview_requested", "interview_scheduled", "interview_done", "offer",
})
REPLY_CONFIDENCE = ("high", "medium", "low")
VERBATIM_FEEDBACK_MAX_CHARS = 2000
REASONS_MAX_ITEMS = 10
REASON_MAX_CHARS = 120
STAGE_MAX_CHARS = 60
REPLY_TYPE_MAX_CHARS = 40
EVIDENCE_MAX_ITEMS = 5
EVIDENCE_MAX_CHARS = 300
POST_MORTEM_LIST_KEYS = {  # key -> (max items, max chars each)
    "what_was_sent": (10, 300),
    "what_to_change": (10, 300),
    "proposed_lessons": (5, 300),
}


def _payload_str(payload: dict[str, Any], key: str, max_chars: int) -> None:
    if key in payload:
        value = payload[key]
        if not isinstance(value, str):
            raise SpineError(422, f"payload.{key} must be a string")
        if len(value) > max_chars:
            raise SpineError(422, f"payload.{key} exceeds {max_chars} chars")


def _payload_str_list(
    payload: dict[str, Any], key: str, max_items: int, max_chars: int, *, drop_empty: bool = False
) -> None:
    if key not in payload:
        return
    value = payload[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise SpineError(422, f"payload.{key} must be a list of strings")
    cleaned = [v.strip() for v in value]
    if drop_empty:
        cleaned = [v for v in cleaned if v]
    if len(cleaned) > max_items:
        raise SpineError(422, f"payload.{key} has more than {max_items} items")
    for item in cleaned:
        if len(item) > max_chars or (drop_empty and not item):
            raise SpineError(422, f"payload.{key} items must be 1 to {max_chars} chars")
    payload[key] = cleaned


def validate_outcome_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The optional outcome / reply-classification / post-mortem keys (422 naming the key).

    Returns the payload with ``reasons`` stripped and empties dropped. Run AFTER ``validate_payload``.
    """
    if event_type in OUTCOME_EVENT_TYPES:
        _payload_str(payload, "verbatim_feedback", VERBATIM_FEEDBACK_MAX_CHARS)
        _payload_str(payload, "stage", STAGE_MAX_CHARS)
        if "reasons" in payload:
            raw = payload["reasons"]
            if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
                raise SpineError(422, "payload.reasons must be a list of strings")
            reasons = [v.strip() for v in raw if v.strip()]
            if len(reasons) > REASONS_MAX_ITEMS:
                raise SpineError(422, f"payload.reasons has more than {REASONS_MAX_ITEMS} items")
            if any(len(v) > REASON_MAX_CHARS for v in reasons):
                raise SpineError(422, f"payload.reasons items must be 1 to {REASON_MAX_CHARS} chars")
            payload["reasons"] = reasons
    if event_type in REPLY_CLASSIFIED_EVENT_TYPES:
        _payload_str(payload, "reply_type", REPLY_TYPE_MAX_CHARS)
        _payload_str_list(payload, "evidence", EVIDENCE_MAX_ITEMS, EVIDENCE_MAX_CHARS)
        if "confidence" in payload and payload["confidence"] not in REPLY_CONFIDENCE:
            raise SpineError(422, f"payload.confidence must be one of {list(REPLY_CONFIDENCE)}")
    if event_type == "post_mortem":
        for key, (max_items, max_chars) in POST_MORTEM_LIST_KEYS.items():
            _payload_str_list(payload, key, max_items, max_chars)
        if "outcome_event_id" in payload:
            value = payload["outcome_event_id"]
            if isinstance(value, bool) or not isinstance(value, int):
                raise SpineError(422, "payload.outcome_event_id must be a whole number")
    return payload


# ── Automatic employer emails (feedback loop slice 2) ────────────────────────
AUTO_EMAIL_SENT_TYPES = frozenset({"checkin_sent", "feedback_asked", "feedback_followup_sent"})
AUTO_EMAIL_SKIPPED_TYPE = "auto_email_skipped"
AUTO_EMAIL_KINDS = ("checkin", "feedback_request", "feedback_followup")
AUTO_EMAIL_TO_KINDS = ("person", "careers_address")
AUTO_EMAIL_TO_MAX_CHARS = 320
AUTO_EMAIL_THREAD_MAX_CHARS = 256
AUTO_EMAIL_SUBJECT_MAX_CHARS = 300
AUTO_EMAIL_TEXT_MAX_CHARS = 3000
AUTO_EMAIL_REASON_MAX_CHARS = 200


def _payload_required_str(payload: dict[str, Any], key: str, max_chars: int) -> None:
    if key not in payload:
        raise SpineError(422, f"payload.{key} is required")
    _payload_str(payload, key, max_chars)
    if not payload[key].strip():
        raise SpineError(422, f"payload.{key} must not be empty")


def validate_auto_email_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The payload of the four automatic-employer-email events (422 naming the key).

    The three ``*_sent`` types need ``to``, ``text`` (the exact text sent) and ``to_kind``;
    ``thread_id`` / ``subject`` are optional. ``auto_email_skipped`` needs ``kind`` and ``reason``.
    Run AFTER ``validate_payload``."""
    if event_type in AUTO_EMAIL_SENT_TYPES:
        _payload_required_str(payload, "to", AUTO_EMAIL_TO_MAX_CHARS)
        _payload_required_str(payload, "text", AUTO_EMAIL_TEXT_MAX_CHARS)
        _payload_str(payload, "thread_id", AUTO_EMAIL_THREAD_MAX_CHARS)
        _payload_str(payload, "subject", AUTO_EMAIL_SUBJECT_MAX_CHARS)
        if "to_kind" not in payload:
            raise SpineError(422, "payload.to_kind is required")
        if payload["to_kind"] not in AUTO_EMAIL_TO_KINDS:
            raise SpineError(422, f"payload.to_kind must be one of {list(AUTO_EMAIL_TO_KINDS)}")
    elif event_type == AUTO_EMAIL_SKIPPED_TYPE:
        if "kind" not in payload:
            raise SpineError(422, "payload.kind is required")
        if payload["kind"] not in AUTO_EMAIL_KINDS:
            raise SpineError(422, f"payload.kind must be one of {list(AUTO_EMAIL_KINDS)}")
        _payload_required_str(payload, "reason", AUTO_EMAIL_REASON_MAX_CHARS)
    return payload


async def check_outcome_event_id(
    db: JobDatabase, user_id: str, application_id: int, event_type: str, payload: Mapping[str, Any]
) -> None:
    """A post_mortem's ``outcome_event_id`` must be an event on THIS application (422 otherwise)."""
    if event_type != "post_mortem" or "outcome_event_id" not in payload:
        return
    cur = await db._db.execute(
        "SELECT 1 FROM application_events WHERE id = ? AND user_id = ? AND application_id = ?",
        (payload["outcome_event_id"], user_id, application_id),
    )
    if await cur.fetchone() is None:
        raise SpineError(422, "payload.outcome_event_id must be an event on this application")


def _parse_bounded_iso(
    raw: str,
    *,
    field: str,
    max_future_seconds: int,
    max_future_setting: str,
) -> str:
    """Slice 6 — the shared guts of ``parse_occurred_at`` (S6), reused by
    ``validate_source``'s ``received_at`` and ``parse_scheduled_at``'s
    ``scheduled_at`` (S8): ISO-8601, timezone required, bounded on the future
    side only. ``field`` and ``max_future_setting`` name themselves in the
    error so a 422 always points at the right input and the right knob.

    B3 fix — normalise to the canonical +00:00 offset (pg.py:178-184's own
    convention) before storing. Every one of these columns is a TEXT column
    that some caller sorts lexically (``list_events_for_display``'s ORDER BY
    ``occurred_at`` ASC); two instants recorded under different offsets (e.g.
    "+05:30" vs "+00:00") do NOT compare correctly as strings even though they
    compare correctly as instants, so every caller's offset must collapse to
    the same one before it ever reaches SQL.
    """
    now = datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise SpineError(422, f"{field} must be ISO-8601 (e.g. 2026-09-04T12:00:00+00:00)") from None
    if parsed.tzinfo is None:
        raise SpineError(422, f"{field} must include a timezone offset")
    max_future = timedelta(seconds=max_future_seconds)
    if parsed > now + max_future:
        raise SpineError(
            422,
            f"{field} is more than {max_future_setting} ({max_future_seconds}s) in the future",
        )
    return parsed.astimezone(timezone.utc).isoformat()


def parse_occurred_at(raw: Optional[str], field: str = "occurred_at") -> str:
    """S6 — ISO-8601 with a timezone, bounded on the future side only.
    ``field`` names the input in the 422 (record_application passes ``applied_at``)."""
    if raw is None:
        return datetime.now(timezone.utc).isoformat()
    return _parse_bounded_iso(
        raw,
        field=field,
        max_future_seconds=settings.APPLICATION_EVENT_MAX_FUTURE_SECONDS,
        max_future_setting="APPLICATION_EVENT_MAX_FUTURE_SECONDS",
    )


# ── Slice 6 (docs/plans/2026-09-07-email-evidence/spec.md) — an event's
# optional email source and interview datetime ──────────────────────────────


_SOURCE_CONTROL_CHAR_FIELDS = ("message_id", "sender", "subject", "thread_id")

# S3 — what "control character" means for a source field. Unicode category
# Cc is C0 + DEL + C1; Zl/Zp are U+2028/U+2029, real line terminators in
# JSON/JS contexts, so they poison exports exactly like "\n" does; the bidi
# embeddings/overrides/isolates flip how the REST of a timeline line renders.
# Deliberately NOT all of Cf: emoji ZWJ sequences (U+200D) are legal subjects.
_SOURCE_BANNED_CATEGORIES = frozenset({"Cc", "Zl", "Zp"})
_SOURCE_BANNED_CHARS = frozenset(
    chr(cp) for cp in (
        0x202A, 0x202B, 0x202C, 0x202D, 0x202E,  # LRE RLE PDF LRO RLO
        0x2066, 0x2067, 0x2068, 0x2069,  # LRI RLI FSI PDI
    )
)


def _has_control_chars(value: str) -> bool:
    return any(
        ch in _SOURCE_BANNED_CHARS or unicodedata.category(ch) in _SOURCE_BANNED_CATEGORIES
        for ch in value
    )


def normalize_quote(value: Any) -> str:
    """Feature 9 - a verbatim email sentence: every run of whitespace (newline, tab, CR,
    U+2028/2029, NBSP: anything str.split() splits on) becomes ONE space, ends trimmed.
    Other control characters are left for the caller to REFUSE (never silently stripped)."""
    return " ".join(str(value or "").split())


def _strip_control_chars(value: str, *, keep: str = "\n\t") -> str:
    """Remove (never 422) the same control characters ``_has_control_chars``
    refuses, except the line breaks/tabs in ``keep`` — free-text notes are
    legitimately multi-line."""
    return "".join(
        ch for ch in value
        if ch in keep
        or not (ch in _SOURCE_BANNED_CHARS or unicodedata.category(ch) in _SOURCE_BANNED_CATEGORIES)
    )


def _slug(raw: str) -> str:
    """``" Company-site "`` → ``"company_site"`` — case/space/hyphen-insensitive
    spelling of a closed-set member. Never maps one word to ANOTHER word."""
    return re.sub(r"[\s\-]+", "_", raw.strip().lower())


# The spellings agents and the old docstring actually used before the closed
# set existed ("LinkedIn", "company website", "Indeed"). Mapped, not refused:
# a refused channel would lose the RECEIPT itself, the one record that must
# never go missing.
_CHANNEL_ALIASES: dict[str, str] = {
    "linkedin": "linkedin_easy_apply",
    "easy_apply": "linkedin_easy_apply",
    "company_website": "company_site",
    "website": "company_site",
    "careers_page": "company_site",
    "indeed": "job_board",
    # Applying through a company's own applicant-tracking form is the
    # company's site/portal, not a job board.
    "ats_form": "company_site",
    "ats": "company_site",
    "application_form": "company_site",
    "workday": "company_site",
    "greenhouse": "company_site",
    "lever": "company_site",
    "ashby": "company_site",
    "smartrecruiters": "company_site",
}


def _channel_member(raw: str) -> Optional[str]:
    slug = _slug(raw)
    slug = _CHANNEL_ALIASES.get(slug, slug)
    return slug if slug in settings.APPLICATION_RECEIPT_CHANNELS else None


def normalize_receipt_channel(raw: Optional[str]) -> str:
    """Owner decision 2026-10-04 — the ONE normaliser for a NEW receipt's
    ``channel`` (rich receipt route, record_application tool, legacy
    ``POST /receipts/{job_id}``). ``None``/'' → '' (not said, rule #29); a
    spelling (or known alias) of a set member is stored as the member;
    anything else is stored as ``"other"`` — never a 422, because refusing
    the channel would drop the whole receipt."""
    if raw is None or not raw.strip():
        return ""
    return _channel_member(raw) or "other"


def receipt_channel_key(raw: Optional[str]) -> Optional[str]:
    """Stats' reading of a STORED channel, new or legacy. Receipts are
    append-only history, so a pre-2026-10-04 free-text value is never
    rewritten — it is MAPPED here: empty → ``None`` (not said); a value that
    spells a set member exactly (case/space/hyphen-insensitive, e.g.
    "company site") → that member; any other legacy text → ``"other"``."""
    if raw is None or not raw.strip():
        return None
    return _channel_member(raw) or "other"


def validate_ats(kind: str, ats_score: Optional[int], ats_notes: Optional[str]) -> tuple[Optional[int], Optional[str]]:
    """Owner decision 2026-10-04 — the assistant's ATS opinion on a saved
    document. Only ``APPLICATION_ARTIFACT_ATS_KINDS`` may carry one (another
    kind → 422, the stricter choice). Score 0-100 (bool is not a number here).
    Notes: control characters removed (line breaks kept), trimmed, '' → None,
    capped at ``APPLICATION_ARTIFACT_ATS_NOTES_MAX_CHARS`` (a breach is a 422,
    never a silent clip)."""
    if ats_score is None and ats_notes is None:
        return None, None
    if kind not in settings.APPLICATION_ARTIFACT_ATS_KINDS:
        raise SpineError(
            422,
            f"ats_score/ats_notes are only allowed for kinds {settings.APPLICATION_ARTIFACT_ATS_KINDS}",
        )
    if ats_score is not None and (
        isinstance(ats_score, bool) or not isinstance(ats_score, int) or not 0 <= ats_score <= 100
    ):
        raise SpineError(422, "ats_score must be a whole number from 0 to 100")
    notes: Optional[str] = None
    if ats_notes is not None:
        notes = _strip_control_chars(ats_notes).strip() or None
        if notes is not None and len(notes) > settings.APPLICATION_ARTIFACT_ATS_NOTES_MAX_CHARS:
            raise SpineError(
                422,
                f"ats_notes exceeds APPLICATION_ARTIFACT_ATS_NOTES_MAX_CHARS "
                f"({settings.APPLICATION_ARTIFACT_ATS_NOTES_MAX_CHARS} chars)",
            )
    return ats_score, notes


def validate_source(raw: Optional[dict[str, Any]], *, allow_quote: bool = True) -> Optional[dict[str, Any]]:
    """R1/S2/S3 — normalise-and-validate an event's optional email source.

    ``quote`` is optional, whitespace-collapsed, capped, and refused when ``allow_quote`` is False (outreach).

    ``None`` in, ``None`` out (an event with no source). Otherwise: trim the
    four text fields (``thread_id`` optional, '' = none), reject an empty ``message_id`` (that is the identity
    R2 dedupes on — it cannot be blank), reject any control character (S3 —
    a subject cannot legally carry a newline), cap each field's length
    (S2, naming the setting), and check ``kind`` against the closed set.
    ``received_at`` is parsed with ``occurred_at``'s rules (S8: ISO-8601,
    timezone required, normalised to +00:00, same tight future bound — an
    email cannot arrive in the future).
    """
    if raw is None:
        return None

    kind = raw.get("kind", "email")
    message_id = str(raw.get("message_id") or "").strip()
    sender = str(raw.get("sender") or "").strip()
    subject = str(raw.get("subject") or "").strip()
    thread_id = str(raw.get("thread_id") or "").strip()
    received_at_raw = raw.get("received_at")

    if not message_id:
        raise SpineError(422, "message_id must not be empty")

    fields = {"message_id": message_id, "sender": sender, "subject": subject, "thread_id": thread_id}
    for name in _SOURCE_CONTROL_CHAR_FIELDS:
        value = fields[name]
        if _has_control_chars(value):
            raise SpineError(422, f"{name} must not contain control characters")

    caps = (
        ("message_id", message_id, settings.APPLICATION_EVENT_SOURCE_MESSAGE_ID_MAX_CHARS,
         "APPLICATION_EVENT_SOURCE_MESSAGE_ID_MAX_CHARS"),
        ("sender", sender, settings.APPLICATION_EVENT_SOURCE_SENDER_MAX_CHARS,
         "APPLICATION_EVENT_SOURCE_SENDER_MAX_CHARS"),
        ("subject", subject, settings.APPLICATION_EVENT_SOURCE_SUBJECT_MAX_CHARS,
         "APPLICATION_EVENT_SOURCE_SUBJECT_MAX_CHARS"),
        ("thread_id", thread_id, settings.APPLICATION_EVENT_SOURCE_THREAD_ID_MAX_CHARS,
         "APPLICATION_EVENT_SOURCE_THREAD_ID_MAX_CHARS"),
    )
    for name, value, cap, setting_name in caps:
        if len(value) > cap:
            raise SpineError(422, f"{name} exceeds {setting_name} ({cap} chars)")

    quote = normalize_quote(raw.get("quote"))
    if _has_control_chars(quote):
        raise SpineError(422, "quote must not contain control characters")
    quote_cap = settings.APPLICATION_EVENT_SOURCE_QUOTE_MAX_CHARS
    if len(quote) > quote_cap:
        raise SpineError(422, f"quote exceeds APPLICATION_EVENT_SOURCE_QUOTE_MAX_CHARS ({quote_cap} chars)")
    if quote and not allow_quote:
        raise SpineError(422, "source.quote is kept on application events only; record outreach without it")

    if kind not in settings.APPLICATION_EVENT_SOURCE_KINDS:
        raise SpineError(
            422,
            f"kind must be one of APPLICATION_EVENT_SOURCE_KINDS {settings.APPLICATION_EVENT_SOURCE_KINDS}",
        )

    received_at = ""
    if received_at_raw is not None:
        received_at = _parse_bounded_iso(
            str(received_at_raw),
            field="received_at",
            max_future_seconds=settings.APPLICATION_EVENT_MAX_FUTURE_SECONDS,
            max_future_setting="APPLICATION_EVENT_MAX_FUTURE_SECONDS",
        )

    return {
        "kind": kind,
        "message_id": message_id,
        "thread_id": thread_id,
        "sender": sender,
        "subject": subject,
        "received_at": received_at,
        "quote": quote,
    }


def parse_scheduled_at(raw: Optional[str], event_type: str) -> str:
    """R3/S8 — an interview datetime, allowed only on
    ``settings.APPLICATION_SCHEDULABLE_EVENT_TYPES``. Its own, longer future
    bound (an interview can be scheduled far ahead; an email cannot arrive in
    the future)."""
    if raw is None:
        return ""
    if event_type not in settings.APPLICATION_SCHEDULABLE_EVENT_TYPES:
        raise SpineError(
            422,
            f"scheduled_at is only allowed on APPLICATION_SCHEDULABLE_EVENT_TYPES "
            f"{settings.APPLICATION_SCHEDULABLE_EVENT_TYPES}; got event_type {event_type!r}",
        )
    return _parse_bounded_iso(
        raw,
        field="scheduled_at",
        max_future_seconds=settings.APPLICATION_SCHEDULED_AT_MAX_FUTURE_SECONDS,
        max_future_setting="APPLICATION_SCHEDULED_AT_MAX_FUTURE_SECONDS",
    )


# ── Follow-up dates (owner decision, 2026-09-25) ────────────────────────────

# A caller of `append_event` that never mentions `follow_up_on` at all must
# leave the slot untouched — `None` is not usable for that because `None` is
# also "clear" for the CALLER-FACING field (RecordEventRequest.follow_up_on).
# This sentinel is the third state: "don't touch it", never seen outside
# this module and the one route that reads it.
FOLLOW_UP_UNSET: Any = object()

_FOLLOW_UP_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_follow_up_on(raw: str, today: date) -> Optional[str]:
    """R — a follow-up date: ``""`` clears (returns ``None``, the column's
    unset value), ``YYYY-MM-DD`` sets it (returns the same string, normalised
    through ``date.fromisoformat`` so ``2026-2-3`` is refused, not silently
    accepted). Bounded around the CALLER's own ``today`` (``user_today``) by
    ``APPLICATION_FOLLOW_UP_MAX_PAST_DAYS`` / ``_MAX_FUTURE_DAYS`` — a little
    past-dating is normal, an implausible date is not. 422 names the field and
    the setting, same pattern as every other spine cap."""
    if raw == "":
        return None
    if not _FOLLOW_UP_DATE_RE.match(raw):
        raise SpineError(422, "follow_up_on must be YYYY-MM-DD, or '' to clear")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        raise SpineError(422, "follow_up_on must be YYYY-MM-DD, or '' to clear") from None
    earliest = today - timedelta(days=settings.APPLICATION_FOLLOW_UP_MAX_PAST_DAYS)
    latest = today + timedelta(days=settings.APPLICATION_FOLLOW_UP_MAX_FUTURE_DAYS)
    if not (earliest <= parsed <= latest):
        raise SpineError(
            422,
            f"follow_up_on must be within APPLICATION_FOLLOW_UP_MAX_PAST_DAYS "
            f"({settings.APPLICATION_FOLLOW_UP_MAX_PAST_DAYS}) days before, or "
            f"APPLICATION_FOLLOW_UP_MAX_FUTURE_DAYS "
            f"({settings.APPLICATION_FOLLOW_UP_MAX_FUTURE_DAYS}) days after, today",
        )
    return parsed.isoformat()


async def user_zone(db: JobDatabase, user_id: str) -> ZoneInfo:
    """The caller's own time zone (``users.timezone``, migration 0012 - default
    ``'UTC'``). An unset or unrecognised zone name falls back to UTC rather than
    500ing - a bad string in that column must degrade, never break every read."""
    cur = await db._db.execute("SELECT timezone FROM users WHERE id = ?", (user_id,))
    row = await cur.fetchone()
    raw_zone = (dict(row).get("timezone") if row else None) or "UTC"
    try:
        return ZoneInfo(raw_zone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


async def user_today(db: JobDatabase, user_id: str, *, now: Optional[datetime] = None) -> date:
    """The caller's own "today", in ``users.timezone`` (see ``user_zone``).
    ``now`` exists only so a test can pin the instant; real callers never pass it."""
    zone = await user_zone(db, user_id)
    when = now if now is not None else datetime.now(timezone.utc)
    return when.astimezone(zone).date()


def _follow_up_due(follow_up_on: Optional[str], status: str, today_iso: str) -> bool:
    """Shared by ``list_applications`` and ``get_application_detail`` so the
    web list and the detail page can never disagree: a date in the past or
    today, on an OPEN application (never one of
    ``APPLICATION_FOLLOW_UP_CLOSED_STATUSES``)."""
    return (
        follow_up_on is not None
        and follow_up_on <= today_iso
        and status not in settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES
    )


def _event_source(row: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """R4 — build the ``source`` object a reader emits from the seven raw
    columns; ``None`` when the event carries no source (the 0038/0041
    convention: ``source_message_id == ''`` means none, never NULL)."""
    if not row.get("source_message_id"):
        return None
    return {
        "kind": row["source_kind"],
        "message_id": row["source_message_id"],
        "thread_id": row.get("source_thread_id") or None,
        "quote": row.get("source_quote") or None,
        "sender": row["source_sender"],
        "subject": row["source_subject"],
        "received_at": row["source_received_at"] or None,
    }


# ── Ownership lookups ───────────────────────────────────────────────────────


async def get_application_by_job(db: JobDatabase, user_id: str, job_id: int) -> Optional[dict[str, Any]]:
    """The caller's application for a given job, or ``None`` if they never
    brought it — the lookup ``birth_application``'s upsert-by-read and every
    write-through helper (legacy receipts, MCP ``record_application``) use to
    find the row a raw ``job_id`` maps to."""
    cur = await db._db.execute(
        "SELECT id, status, job_country, job_remote, job_found_on, job_url, ad_posted_at, employer_published_at "
        "FROM applications "
        "WHERE user_id = ? AND job_id = ?",
        (user_id, job_id),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def get_owned_application(db: JobDatabase, user_id: str, application_id: int) -> Optional[dict[str, Any]]:
    """S2 — the only lookup every other function here builds on. A foreign or
    unknown id reads as None (the route turns that into 404, never 403)."""
    cur = await db._db.execute(
        "SELECT * FROM applications WHERE id = ? AND user_id = ?", (application_id, user_id)
    )
    row = await cur.fetchone()
    return dict(row) if row else None


# ── The event log ────────────────────────────────────────────────────────────


async def _events_for_replay(db: JobDatabase, application_id: int) -> list[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT id, event_type, corrects_event_id, recorded_at, occurred_at FROM application_events "
        "WHERE application_id = ?",
        (application_id,),
    )
    return [dict(r) for r in await cur.fetchall()]


def _display_payload(event_type: str, raw: Optional[str]) -> dict[str, Any]:
    """S5d - the payload every timeline reader emits. A ``form_filled`` event
    carries a COUNT (``answers_count``), never the typed answers; storage keeps
    the full list (the ready-to-send route reads it straight from the row)."""
    payload: dict[str, Any] = json.loads(raw or "{}")
    if event_type == "form_filled" and isinstance(payload.get("answers"), list):
        count = len(payload["answers"])
        payload = {k: v for k, v in payload.items() if k != "answers"}
        payload["answers_count"] = count
    return payload


async def _attach_contact_names(
    db: JobDatabase, user_id: Optional[str], events: list[dict[str, Any]]
) -> None:
    """Add ``contact_name`` to the payload of every event that names a
    ``contact_id`` (outreach_sent / outreach_replied / contact_added), so the
    UI can say who without a second call. Only the CALLER's own contacts are
    read (``user_id`` match); an unknown or foreign id adds nothing. Payload
    is a free-form dict, so the response model is unchanged."""
    if not user_id:
        return
    ids = sorted({
        int(e["payload"]["contact_id"]) for e in events
        if isinstance(e["payload"].get("contact_id"), int) and not isinstance(e["payload"].get("contact_id"), bool)
    })
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    cur = await db._db.execute(
        f"SELECT id, name FROM application_contacts WHERE user_id = ? AND id IN ({placeholders})",  # noqa: S608
        [user_id, *ids],
    )
    names = {int(r["id"]): r["name"] for r in await cur.fetchall()}
    for e in events:
        cid = e["payload"].get("contact_id")
        if isinstance(cid, int) and cid in names:
            e["payload"]["contact_name"] = names[cid]


async def list_events_for_display(db: JobDatabase, application_id: int) -> list[dict[str, Any]]:
    """Timeline order — ``occurred_at`` (backdated events included), NOT the
    ``recorded_at`` order the status recompute uses."""
    cur = await db._db.execute(
        "SELECT id, event_type, detail, payload, occurred_at, recorded_at, recorded_by, corrects_event_id, "
        "source_kind, source_message_id, source_thread_id, source_quote, source_sender, source_subject, "
        "source_received_at, scheduled_at "
        "FROM application_events WHERE application_id = ? ORDER BY occurred_at ASC, id ASC",
        (application_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    superseded_ids = {r["corrects_event_id"] for r in rows if r.get("corrects_event_id") is not None}
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "event_type": r["event_type"],
                "detail": r["detail"],
                "payload": _display_payload(r["event_type"], r["payload"]),
                "occurred_at": r["occurred_at"],
                "recorded_at": r["recorded_at"],
                "recorded_by": r["recorded_by"],
                "corrects_event_id": r["corrects_event_id"],
                "superseded": r["id"] in superseded_ids,
                "source": _event_source(r),
                "scheduled_at": r["scheduled_at"] or None,
            }
        )
    owner = await db._db.execute("SELECT user_id FROM applications WHERE id = ?", (application_id,))
    owner_row = await owner.fetchone()
    await _attach_contact_names(db, owner_row["user_id"] if owner_row else None, out)
    return out


async def _event_by_source_message_id(
    db: JobDatabase, application_id: int, message_id: str
) -> Optional[dict[str, Any]]:
    """R2 — the identity a duplicate `record_event` call resolves against:
    ``(application_id, source_message_id)``, backed by the partial UNIQUE
    index `migrations/0041...up.sql`."""
    cur = await db._db.execute(
        "SELECT id, event_type, detail, occurred_at, recorded_at, recorded_by, scheduled_at "
        "FROM application_events WHERE application_id = ? AND source_message_id = ?",
        (application_id, message_id),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def _duplicate_event_result(
    db: JobDatabase, application_id: int, recorded_by: str, existing: dict[str, Any],
    requested_type: Optional[str] = None,
) -> dict[str, Any]:
    """R2/S4 — the same message id has already been recorded: audit ids ONLY
    (never subject/sender/message_id — S4), write nothing, and hand back the
    EXISTING event plus the application's current status."""
    get_audit_logger().info(
        "application_event_duplicate",
        # CodeQL py/log-injection — `event_type` is closed-enum validated
        # elsewhere, but `recorded_by` (authorship.actor_for) carries an
        # attacker-supplied token/OAuth-client name that is only
        # length-truncated at its source, not control-char-stripped, so both
        # strings go through the shared sanitizer here.
        extra={
            "event": "application_event_duplicate",
            "application_id": application_id,
            "event_id": existing["id"],
            "event_type": safe_log_value(existing["event_type"]),
            "recorded_by": safe_log_value(recorded_by),
        },
    )
    cur = await db._db.execute("SELECT status, follow_up_on FROM applications WHERE id = ?", (application_id,))
    app_row = await cur.fetchone()
    warnings: list[str] = []
    if requested_type is not None and requested_type != existing["event_type"] and not _is_confirmation_of(
        requested_type, existing
    ):
        # Same message id, DIFFERENT type: the dedupe stands (nothing written),
        # but say so — otherwise the caller believes its type was recorded.
        get_audit_logger().info(
            "application_event_type_mismatch",
            extra={
                "event": "application_event_type_mismatch",
                "application_id": safe_log_value(application_id),
                "event_id": safe_log_value(existing["id"]), "event_type": safe_log_value(existing["event_type"]),
            },
        )
        warnings.append(
            f"message_id already recorded as {existing['event_type']}; to reclassify, "
            f"record_event(..., corrects_event_id={existing['id']}) without `source` (the original keeps it)"
        )
    return {
        "warnings": warnings,
        "event_id": existing["id"],
        "event_type": existing["event_type"],
        "occurred_at": existing["occurred_at"],
        "recorded_at": existing["recorded_at"],
        "recorded_by": existing["recorded_by"],
        "status": app_row["status"] if app_row else None,
        "already_existed": True,
        "scheduled_at": existing["scheduled_at"] or None,
        # Bug fix (coordinator review, 2026-09-25) — a duplicate writes
        # NOTHING, including the slot (R2), so the echo is whatever the slot
        # already held, never the value this call asked for.
        "follow_up_on": app_row["follow_up_on"] if app_row else None,
    }


async def _replay_follow_up_on(db: JobDatabase, application_id: int) -> Optional[str]:
    """Bug fix (coordinator review, 2026-09-25) — recompute the
    ``follow_up_on`` SLOT the same way ``replay_status`` recomputes status:
    read the WHOLE event log, skip anything a ``corrects_event_id`` has
    superseded, and take ``payload["follow_up_on"]`` off the latest
    non-superseded event that carries that key at all (ordered the same way
    status replay orders — ``recorded_at``, then ``id``). ``None`` when no
    surviving event ever named a date — there is nothing left in history to
    revert to. Only called when a correcting call itself leaves
    ``follow_up_on`` untouched (an explicit value on that call always wins)."""
    cur = await db._db.execute(
        "SELECT id, payload, corrects_event_id, recorded_at FROM application_events WHERE application_id = ?",
        (application_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    superseded_ids = {r["corrects_event_id"] for r in rows if r.get("corrects_event_id") is not None}
    ordered = sorted(rows, key=lambda r: (r.get("recorded_at") or "", r.get("id") or 0))
    value: Any = FOLLOW_UP_UNSET
    for r in ordered:
        if r["id"] in superseded_ids:
            continue
        row_payload = json.loads(r["payload"] or "{}")
        if "follow_up_on" in row_payload:
            value = row_payload["follow_up_on"]
    return None if value is FOLLOW_UP_UNSET else value


# Weak spot #4 (2026-10-15) — a second `applied` on an already-applied
# application (typically the confirmation email arriving after
# record_application) is not a second status event: it is stored as a note
# whose detail starts with this prefix, which proof.py already counts as email
# proof when it also carries an email source + message_id (proof.py). The text prefix alone is NOT
# proof for new rows: the structured ``confirms`` payload key marks the conversion.
SUBMISSION_CONFIRMED_PREFIX = "Submission confirmed"
CHANGE_DATE_HINT = "to change the applied date use corrects_event_id"
REPEAT_APPLIED_WARNING = (
    "this application is already applied; recorded as a submission confirmation (proof), "
    f"not a second applied event; {CHANGE_DATE_HINT}"
)
CONFIRMATION_EXISTS_WARNING = (
    "a submission confirmation without a source is already recorded for this application; nothing written; "
    f"{CHANGE_DATE_HINT}"
)


def _is_confirmation_of(requested_type: str, existing: dict[str, Any]) -> bool:
    """A repeat ``applied`` whose message id was already stored as the
    submission-confirmation note is the SAME fact, not a type mismatch."""
    return (
        requested_type == "applied"
        and existing.get("event_type") == "note"
        and str(existing.get("detail") or "").startswith(SUBMISSION_CONFIRMED_PREFIX)
    )


async def _has_surviving_applied(db: JobDatabase, application_id: int) -> bool:
    """An ``applied`` event on this application that no correction superseded."""
    cur = await db._db.execute(
        "SELECT 1 FROM application_events e WHERE e.application_id = ? AND e.event_type = 'applied' "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) LIMIT 1",
        (application_id,),
    )
    return await cur.fetchone() is not None


async def _existing_confirmation(db: JobDatabase, application_id: int) -> Optional[dict[str, Any]]:
    """The first surviving converted confirmation note (``payload.confirms == 'applied'``) on this
    application, else None. Mirrors career-ops reply-watch.mjs:180 (skip a receipt already seen)."""
    cur = await db._db.execute(
        "SELECT e.id, e.event_type, e.detail, e.occurred_at, e.recorded_at, e.recorded_by, e.scheduled_at, "
        "e.payload FROM application_events e WHERE e.application_id = ? AND e.event_type = 'note' "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) ORDER BY e.id",
        (application_id,),
    )
    for row in await cur.fetchall():
        try:
            confirms = json.loads(row["payload"] or "{}").get(CONFIRMS_KEY)
        except (ValueError, AttributeError):
            continue
        if confirms == CONFIRMS_APPLIED:
            return {k: row[k] for k in row.keys() if k != "payload"}
    return None


def _confirmation_detail(detail: str) -> str:
    text = SUBMISSION_CONFIRMED_PREFIX + (f": {detail}" if detail else "")
    return text[: settings.APPLICATION_EVENT_DETAIL_MAX_CHARS]


NO_APPLIED_WARNING = (
    "no applied event recorded for this application — ask the user when they applied, then record_application"
)


async def missing_applied_warnings(db: JobDatabase, application_id: int, event_type: str) -> list[str]:
    """Owner decision 2026-10-14 — a stage ABOVE ``applied`` (replied,
    interview_*, offer) recorded on an application with no surviving
    ``applied`` event is accepted (it is still a fact) but answered with a
    warning. Job360 never writes the missing ``applied`` itself: that would
    invent a fact (rule 4) — the agent asks the user, then records it."""
    mapped = status_for_event(event_type)
    rank = progress_rank(mapped) if mapped else None
    if rank is None or rank <= PROGRESS_RANK["applied"]:
        return []
    return [] if await _has_surviving_applied(db, application_id) else [NO_APPLIED_WARNING]


async def append_event(
    db: JobDatabase,
    *,
    user_id: str,
    application_id: int,
    event_type: str,
    occurred_at: str,
    recorded_by: str,
    detail: str = "",
    payload: Optional[dict[str, Any]] = None,
    corrects_event_id: Optional[int] = None,
    source: Optional[dict[str, Any]] = None,
    scheduled_at: str = "",
    follow_up_on: Any = FOLLOW_UP_UNSET,
    repeat_applied_as_confirmation: bool = False,
) -> dict[str, Any]:
    """R3/R4 — append one event, then recompute + write-through the status
    cache (and its legacy `stage` projection) in the SAME logical operation.

    ``follow_up_on`` (owner decision, 2026-09-25): ``FOLLOW_UP_UNSET`` (the
    default) leaves the ``applications.follow_up_on`` slot untouched; ``None``
    clears it; an ISO date string (already validated by ``parse_follow_up_on``)
    sets it. Either way it also lands in THIS event's payload under the same
    key (overriding any caller-supplied ``payload["follow_up_on"]`` — refused
    outright by ``validate_payload``, see the coordinator-review fix there),
    so the date is history too, not just a slot (S7) — like
    ``fit_axes``/``visa``.

    Two more owner/coordinator rules layer on top of "untouched", both ONLY
    when this call's own ``follow_up_on`` is ``FOLLOW_UP_UNSET`` (an explicit
    value always wins over either):

    * A status-changing event (``status_for_event(event_type)`` is not
      ``None`` — replied/interview_*/offer/rejected/withdrawn/ghosted/
      brought) auto-clears an OVERDUE follow-up (due today or earlier) —
      news just arrived, so there is nothing left to chase. A FUTURE date is
      left alone. Never fires for a note/fit/artifact event, and never for a
      ``corrects_event_id`` call (that case uses the rule below instead).
    * A ``corrects_event_id`` call re-derives the slot from history
      (``_replay_follow_up_on``) — the same "replay the log" idea
      ``replay_status`` already uses — so correcting away the event that set
      the currently active date reverts the slot, never just leaves it stuck.

    The returned dict's ``follow_up_on`` is always the REAL final value —
    set, auto-cleared, replay-derived, or simply unchanged — never a stale
    pre-call snapshot; ``record_event``'s route/MCP tool return it as-is.

    No caller of this module ever computes `status` any other way — this is
    the ONE place `applications.status`/`stage`/`last_event_at` are written.

    B5 — ``corrects_event_id``, when given, must name a real event on THIS
    SAME application; otherwise a caller could silently supersede a stranger's
    event (or a typo'd id that never existed) and ``list_events_for_display``
    would mark the wrong thing (or nothing) as superseded. 422, not 404 — this
    is a body-field validation error, not a missing resource.

    Slice 6 R2 — ``source`` (already normalised by ``validate_source``) makes
    ``(application_id, source_message_id)`` the identity: a second call
    naming a message id already on THIS application returns the existing row
    untouched (``already_existed: True``) — no insert, no replay, no status
    change. The UNIQUE index still backstops a race between two concurrent
    callers (``pg.IntegrityError``): re-read and return the existing row the
    same way ``save_artifact``'s version race does.

    ``repeat_applied_as_confirmation`` (weak spot #4; the record_event door
    only, never record_application's receipt path): an ``applied`` with no
    ``corrects_event_id`` on an application that already has a surviving
    ``applied`` is stored as a ``note`` whose detail starts
    ``SUBMISSION_CONFIRMED_PREFIX`` (source, occurred_at and follow_up_on
    kept) and answered with ``REPEAT_APPLIED_WARNING``. Checked AFTER the
    source dedupe, so a re-read message id returns the existing row either way.
    """
    requested_type = event_type
    warnings: list[str] = []
    if corrects_event_id is not None:
        cur = await db._db.execute(
            "SELECT 1 FROM application_events WHERE id = ? AND application_id = ?",
            (corrects_event_id, application_id),
        )
        if (await cur.fetchone()) is None:
            raise SpineError(
                422,
                f"corrects_event_id {corrects_event_id} does not exist on this application",
            )

    if source is not None:
        existing = await _event_by_source_message_id(db, application_id, source["message_id"])
        if existing is not None:
            return await _duplicate_event_result(db, application_id, recorded_by, existing, event_type)

    if (
        repeat_applied_as_confirmation
        and event_type == "applied"
        and corrects_event_id is None
        and await _has_surviving_applied(db, application_id)
    ):
        if source is None:
            # No source = no identity: without this a repeat would write a new note every time.
            existing = await _existing_confirmation(db, application_id)
            if existing is not None:
                dup = await _duplicate_event_result(db, application_id, recorded_by, existing, requested_type)
                dup["warnings"] = [*dup["warnings"], CONFIRMATION_EXISTS_WARNING]
                return dup
        event_type = "note"
        detail = _confirmation_detail(detail)
        payload = {**(payload or {}), CONFIRMS_KEY: CONFIRMS_APPLIED}
        warnings.append(REPEAT_APPLIED_WARNING)

    # The slot's value BEFORE this event — needed to decide the auto-clear
    # below, and to echo back an unchanged value at the end without a stale
    # pre-call snapshot living in the caller (route/MCP tool).
    cur = await db._db.execute("SELECT follow_up_on FROM applications WHERE id = ?", (application_id,))
    _row = await cur.fetchone()
    current_follow_up_on: Optional[str] = _row["follow_up_on"] if _row else None

    # Owner decision (2026-09-25) — news that MOVES THE STATUS clears an
    # OVERDUE follow-up automatically; a future one is left alone, and this
    # never fires when the caller named a follow_up_on itself (that already
    # took follow_up_on out of FOLLOW_UP_UNSET) or when correcting a past
    # event (handled by the replay-derive rule below instead).
    if (
        follow_up_on is FOLLOW_UP_UNSET
        and corrects_event_id is None
        and current_follow_up_on
        and status_for_event(event_type) is not None
    ):
        today_iso = (await user_today(db, user_id)).isoformat()
        if current_follow_up_on <= today_iso:
            follow_up_on = None

    now = datetime.now(timezone.utc).isoformat()
    event_payload = dict(payload or {})
    if follow_up_on is not FOLLOW_UP_UNSET:
        event_payload["follow_up_on"] = follow_up_on
    payload_json = json.dumps(event_payload)
    source_kind = source["kind"] if source else ""
    source_message_id = source["message_id"] if source else ""
    source_thread_id = source["thread_id"] if source else ""
    source_quote = source["quote"] if source else ""
    source_sender = source["sender"] if source else ""
    source_subject = source["subject"] if source else ""
    source_received_at = source["received_at"] if source else ""
    try:
        # Scoped like contacts.add_contact's INSERT: a bare ``rollback()`` is
        # forbidden by psycopg inside an outer ``transaction()`` block (and
        # add_contact calls append_event inside one), which would leave the
        # transaction ABORTED for the re-read below. The block rolls the
        # failed INSERT back correctly either way — SAVEPOINT when nested.
        async with db._db.transaction():
            cur = await db._db.execute(
                "INSERT INTO application_events "
                "(user_id, application_id, event_type, detail, payload, occurred_at, recorded_at, "
                " recorded_by, corrects_event_id, source_kind, source_message_id, source_thread_id, "
                " source_quote, source_sender, source_subject, source_received_at, scheduled_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id, application_id, event_type, detail, payload_json, occurred_at, now,
                    recorded_by, corrects_event_id, source_kind, source_message_id, source_thread_id,
                    source_quote, source_sender, source_subject, source_received_at, scheduled_at,
                ),
            )
    except pg.IntegrityError:
        # A race: two callers recorded the same message id at once (same
        # reasoning as save_artifact's version race — see spine.py). The
        # UNIQUE index caught it; re-read and return the row that won.
        if source is not None:
            existing = await _event_by_source_message_id(db, application_id, source["message_id"])
            if existing is not None:
                return await _duplicate_event_result(db, application_id, recorded_by, existing, requested_type)
        raise
    event_id = cur.lastrowid

    new_status = replay_status(await _events_for_replay(db, application_id))
    stage = stage_for_status(new_status)

    # Bug fix (coordinator review, 2026-09-25) — correcting the event that
    # set (or last touched) follow_up_on must revert the slot too. Only after
    # the INSERT, since the corrects_event_id link the correction just made
    # is what marks the corrected event superseded for the replay.
    if follow_up_on is FOLLOW_UP_UNSET and corrects_event_id is not None:
        follow_up_on = await _replay_follow_up_on(db, application_id)

    set_cols = ["status = ?", "last_event_at = ?", "updated_at = ?"]
    set_params: list[Any] = [new_status, now, now]
    if stage is not None:
        set_cols.append("stage = ?")
        set_params.append(stage)
    # `considering` has no legacy stage — leave the column untouched (R4).
    if follow_up_on is not FOLLOW_UP_UNSET:
        set_cols.append("follow_up_on = ?")
        set_params.append(follow_up_on)
    set_params.append(application_id)
    await db._db.execute(
        f"UPDATE applications SET {', '.join(set_cols)} WHERE id = ?",  # noqa: S608 — set_cols are constants
        set_params,
    )
    await db._db.commit()
    get_audit_logger().info(
        "application_event_recorded",
        # CodeQL py/log-injection — same reasoning as _duplicate_event_result
        # above: `event_type` is closed-enum validated, `recorded_by` is only
        # length-truncated at its source, so both go through the sanitizer.
        extra={
            "event": "application_event_recorded",
            "application_id": application_id,
            "event_type": safe_log_value(event_type),
            "recorded_by": safe_log_value(recorded_by),
        },
    )
    # The REAL final value — touched (explicit/auto-cleared/replay-derived)
    # or, when nothing above changed anything, simply unchanged.
    final_follow_up_on = follow_up_on if follow_up_on is not FOLLOW_UP_UNSET else current_follow_up_on
    return {
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "recorded_at": now,
        "recorded_by": recorded_by,
        "status": new_status,
        "already_existed": False,
        "scheduled_at": scheduled_at or None,
        "follow_up_on": final_follow_up_on,
        **({"warnings": warnings} if warnings else {}),
    }


# ── R1/R2 — birth at bring_job ───────────────────────────────────────────────


async def birth_application(
    db: JobDatabase, *, user_id: str, job_id: int, job_row: Mapping[str, Any], recorded_by: str,
    origin: Optional[str] = None,
) -> dict[str, Any]:
    """Upsert-by-read: bringing the same job twice returns the SAME
    application_id and appends NO second `brought` event. ``origin`` (ai_hunt / user_link /
    user_paste, already validated by the route) lands in that event's payload."""
    existing = await get_application_by_job(db, user_id, job_id)
    if existing is not None:
        return {"application_id": existing["id"], "status": existing["status"], "existing": True}

    now = datetime.now(timezone.utc).isoformat()
    cur = await db._db.execute(
        "INSERT INTO applications "
        "(user_id, job_id, status, stage, job_title, job_company, job_location, job_url, job_source, "
        " job_description_snapshot, snapshot_at, created_at, updated_at, last_event_at) "
        "VALUES (?, ?, 'considering', 'considering', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id, job_id,
            job_row.get("title") or "", job_row.get("company") or "", job_row.get("location") or "",
            job_row.get("apply_url") or "", job_row.get("source") or "", job_row.get("description") or "",
            now, now, now, now,
        ),
    )
    application_id = int(cur.lastrowid or 0)
    await append_event(
        db, user_id=user_id, application_id=application_id, event_type="brought",
        occurred_at=now, recorded_by=recorded_by, payload={"origin": origin} if origin else None,
    )
    get_audit_logger().info(
        "application_born",
        extra={"event": "application_born", "application_id": application_id, "user_id": user_id, "job_id": job_id},
    )
    return {"application_id": application_id, "status": "considering", "existing": False}


# ── R5 — artifacts, versioned forever ────────────────────────────────────────


async def _artifact_version_count_and_max(db: JobDatabase, application_id: int, kind: str) -> tuple[int, int]:
    cur = await db._db.execute(
        "SELECT COUNT(*), COALESCE(MAX(version_no), 0) FROM application_artifacts "
        "WHERE application_id = ? AND kind = ?",
        (application_id, kind),
    )
    row = await cur.fetchone()
    return int(row[0]), int(row[1])


async def _run_save_checks(user_id: str, kind: str, text: str) -> list[dict[str, str]]:
    """The deterministic checks (``cv_checks``) for a CV / cover letter / outreach / answers being saved.

    Never raises and never blocks a save: any failure just yields fewer checks.
    The PDF is rendered here (CV only) so what the checks read is exactly what the
    kit and the download will hand out.
    """
    if kind not in ("cv", "cover_letter", "outreach", "answers"):
        return []
    import asyncio  # noqa: PLC0415

    from src.services.applications import cv_checks  # noqa: PLC0415

    profile: Any = None
    try:
        from src.services.profile.storage import load_profile  # noqa: PLC0415 - rule #16

        profile = await asyncio.to_thread(load_profile, user_id)
    except Exception:  # noqa: BLE001 - checks are advisory; a profile read must not break a save
        # Loud, not silent: without the profile the claim checks cannot run, and check_submit's
        # cv_unsupported_claims ask reads them (broken must not look like "nothing unsupported").
        logging.getLogger("job360.spine").exception(
            "save_checks_profile_unavailable", extra={"kind": safe_log_value(kind)},
        )
        profile = None
    checks = cv_checks.check_text(kind, text, profile)
    if kind == "cv":
        try:
            from src.services.tailoring.cv_render import (  # noqa: PLC0415 - heavy, lazy
                render_document_pdf,
                style_for_profile,
            )

            pdf = await render_document_pdf(user_id, kind, text)
            pages: Optional[int] = None
            if profile is not None:
                try:
                    pages = int(style_for_profile(profile).get("page", {}).get("pages") or 0) or None
                except (TypeError, ValueError):
                    pages = None
            checks.extend(await asyncio.to_thread(cv_checks.check_pdf, pdf, text, pages))
        except Exception:  # noqa: BLE001 - a render failure must not block the save
            pass
    return checks


async def save_artifact(
    db: JobDatabase,
    *,
    user_id: str,
    application_id: int,
    kind: str,
    text: str,
    made_by: str,
    label: str = "",
    model: Optional[str] = None,
    ats_score: Optional[int] = None,
    ats_notes: Optional[str] = None,
) -> dict[str, Any]:
    """R5 — save a NEW version of an artifact (never an update): allocates
    ``version_no = MAX(version_no) + 1`` per ``(application_id, kind)`` inside
    the insert, retrying on a ``UNIQUE`` race (C4 — caught by ``pg.
    IntegrityError``, not a string sniff over a bare ``Exception``), and
    appends the ``artifact_saved`` event that names the new id/kind/version.
    """
    app_row = await get_owned_application(db, user_id, application_id)
    if app_row is None:
        raise SpineError(404, "application not found")
    if kind not in settings.APPLICATION_ARTIFACT_KINDS:
        raise SpineError(422, f"kind must be one of {settings.APPLICATION_ARTIFACT_KINDS}")
    if len(text) > settings.APPLICATION_ARTIFACT_MAX_CHARS:
        raise SpineError(
            422,
            f"text exceeds APPLICATION_ARTIFACT_MAX_CHARS ({settings.APPLICATION_ARTIFACT_MAX_CHARS} chars)",
        )
    # Owner decision 2026-10-04 — the assistant's ATS opinion rides on THIS
    # version; a re-check is a new version, never an UPDATE of an old row.
    ats_score, ats_notes = validate_ats(kind, ats_score, ats_notes)
    checks = await _run_save_checks(user_id, kind, text)
    checks_json = json.dumps(checks) if checks else None

    # Lazy: profile storage pulls the scoring stack transitively (rule #16).
    from src.services.profile.storage import current_profile_version_id  # noqa: PLC0415

    profile_version = current_profile_version_id(user_id)
    now = datetime.now(timezone.utc).isoformat()

    artifact_id: Optional[int] = None
    version_no = 0
    for _attempt in range(5):
        count, max_version = await _artifact_version_count_and_max(db, application_id, kind)
        if count >= settings.APPLICATION_ARTIFACT_MAX_VERSIONS:
            raise SpineError(
                429,
                f"too many {kind!r} versions; cap is APPLICATION_ARTIFACT_MAX_VERSIONS "
                f"({settings.APPLICATION_ARTIFACT_MAX_VERSIONS})",
            )
        version_no = max_version + 1
        try:
            # Scoped (a SAVEPOINT inside a client_key transaction, slice 25): a
            # bare rollback() is forbidden inside an outer transaction() and
            # would leave it aborted; the block rolls back only this INSERT.
            async with db._db.transaction():
                ins = await db._db.execute(
                    "INSERT INTO application_artifacts "
                    "(user_id, application_id, kind, version_no, text, made_by, model, profile_version, "
                    " label, chars, created_at, ats_score, ats_notes, checks_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id, application_id, kind, version_no, text, made_by, model,
                        profile_version, label or "", len(text), now, ats_score, ats_notes, checks_json,
                    ),
                )
        except pg.IntegrityError:
            # C4 — a real UNIQUE(application_id, kind, version_no) violation
            # (a concurrent save landed the same version_no first); retry with
            # a freshly-read MAX(version_no). Any OTHER integrity error (a
            # genuine bug, e.g. a bad foreign value) is exactly what this
            # exception type means too, so a bounded retry loop still bails
            # out via the `artifact_id is None` check below rather than
            # looping forever on something that will never succeed.
            continue
        artifact_id = int(ins.lastrowid or 0)
        break
    if artifact_id is None:
        raise SpineError(429, "could not allocate a version number under concurrent writes — retry")

    event = await append_event(
        db, user_id=user_id, application_id=application_id, event_type="artifact_saved",
        payload={"artifact_id": artifact_id, "kind": kind, "version_no": version_no},
        occurred_at=now, recorded_by=made_by,
    )
    get_audit_logger().info(
        "artifact_saved",
        # CodeQL py/log-injection — `kind` is a caller-supplied string;
        # sanitized defensively even though callers pass a closed set today.
        extra={
            "event": "artifact_saved", "artifact_id": artifact_id, "kind": safe_log_value(kind),
            "version_no": version_no, "chars": len(text),
        },
    )
    return {
        "artifact_id": artifact_id, "kind": kind, "version_no": version_no, "chars": len(text),
        "made_by": made_by, "model": model, "profile_version": profile_version, "created_at": now,
        "event_id": event["event_id"], "ats_score": ats_score, "ats_notes": ats_notes,
        "checks": checks,
    }


async def get_artifact(
    db: JobDatabase, user_id: str, application_id: int, artifact_id: int
) -> Optional[dict[str, Any]]:
    """One artifact version IN FULL (never capped/truncated) — the one-version
    read `get_application`'s byte-capped list defers to (spec R11). ``None``
    for a foreign/unknown application OR artifact id; the route turns either
    into 404 (S2)."""
    app_row = await get_owned_application(db, user_id, application_id)
    if app_row is None:
        return None
    cur = await db._db.execute(
        "SELECT id, kind, version_no, text, made_by, model, profile_version, label, chars, created_at, "
        "ats_score, ats_notes "
        "FROM application_artifacts WHERE id = ? AND application_id = ?",
        (artifact_id, application_id),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def latest_artifact(
    db: JobDatabase, user_id: str, application_id: int, kind: str
) -> Optional[dict[str, Any]]:
    """The NEWEST saved version of one kind, in full. ``None`` when the user has
    saved none of that kind yet.

    Decision 28 (slice A): ``save_artifact`` is the only door a tailored CV /
    cover letter comes through — the agent writes the text, Job360 versions it.
    This is the read the web's render/provenance/download path uses to find
    "the current one", and the same row ``_resolve_receipt_artifact`` freezes
    into a receipt when the caller names no version.
    """
    cur = await db._db.execute(
        "SELECT id, kind, version_no, text, made_by, model, profile_version, label, chars, created_at, "
        "ats_score, ats_notes "
        "FROM application_artifacts WHERE application_id = ? AND user_id = ? AND kind = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (application_id, user_id, kind),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def _list_artifacts(db: JobDatabase, application_id: int, *, with_text: bool) -> list[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT id, kind, version_no, text, made_by, model, profile_version, label, chars, created_at, "
        "ats_score, ats_notes "
        "FROM application_artifacts WHERE application_id = ? ORDER BY created_at DESC, id DESC",
        (application_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    cap_bytes = max(1, settings.EXPORT_HISTORY_MAX_BYTES // 4)
    used = 0
    out = []
    for r in rows:
        entry = {
            "id": r["id"], "kind": r["kind"], "version_no": r["version_no"], "made_by": r["made_by"],
            "model": r.get("model"), "profile_version": r.get("profile_version"), "label": r.get("label") or "",
            "chars": r["chars"], "created_at": r["created_at"], "text": None, "truncated": False,
            "ats_score": r.get("ats_score"), "ats_notes": r.get("ats_notes"),
        }
        if with_text:
            size = len((r["text"] or "").encode("utf-8"))
            if used + size <= cap_bytes:
                entry["text"] = r["text"]
                used += size
            else:
                entry["truncated"] = True
        out.append(entry)
    return out


# ── R6 — the fit verdict is stored, never computed ───────────────────────────


def validate_axes(axes: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The fit picture's axes, checked against the live settings (2026-09-20).

    Each axis is ``{"name", "role", "you"}``: the agent's own name for the
    dimension and two whole numbers 0..100 — how much the role asks on it and
    how much the seeker brings. Job360 never names an axis or puts a number
    on one (VISION rule 4); it only refuses shapes it cannot draw. ``None`` or
    ``[]`` means "no picture" and stays silent (rule #29)."""
    if not axes:
        return []
    from src.core import settings as _settings  # noqa: PLC0415

    lo, hi = _settings.APPLICATION_FIT_AXES_MIN, _settings.APPLICATION_FIT_AXES_MAX
    name_cap = _settings.APPLICATION_FIT_AXIS_NAME_MAX_CHARS
    if not (lo <= len(axes) <= hi):
        raise SpineError(422, f"axes must have between {lo} and {hi} entries")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for axis in axes:
        if not isinstance(axis, dict):
            raise SpineError(422, "each axis must be an object with name, role, you")
        name = str(axis.get("name") or "").strip()
        if not name:
            raise SpineError(422, "each axis needs a name")
        if len(name) > name_cap:
            raise SpineError(422, f"axis name exceeds APPLICATION_FIT_AXIS_NAME_MAX_CHARS ({name_cap})")
        key = name.casefold()
        if key in seen:
            raise SpineError(422, f"duplicate axis name: {name}")
        seen.add(key)
        values: dict[str, int] = {}
        for side in ("role", "you"):
            raw = axis.get(side)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or int(raw) != raw:
                raise SpineError(422, f"axis {name!r}: {side} must be a whole number 0..100")
            if not 0 <= int(raw) <= 100:
                raise SpineError(422, f"axis {name!r}: {side} must be 0..100")
            values[side] = int(raw)
        out.append({"name": name, "role": values["role"], "you": values["you"]})
    return out


async def save_fit(
    db: JobDatabase,
    *,
    user_id: str,
    application_id: int,
    recorded_by: str,
    score: Optional[int],
    verdict: Optional[str],
    gaps: Optional[list[Any]],
    reasoning: Optional[str],
    visa_signal: Optional[str] = None,
    visa_detail: Optional[str] = None,
    visa_country: Optional[str] = None,
    axes: Optional[list[dict[str, Any]]] = None,
    score_source: Optional[str] = None,
    jd_requirements: Optional[list[Any]] = None,
    archetype: Optional[str] = None,
    remote_policy: Optional[str] = None,
    team_size: Optional[int] = None,
    via: Optional[str] = None,
) -> dict[str, Any]:
    """R6 — overwrite the fit-verdict SLOT on ``applications`` (the current
    answer) and append a ``fit_judged`` event carrying the whole verdict (the
    version history) — S7's "update of the slot is not history" case. Never
    calls a scorer, matcher or judge; the verdict is always the caller's own
    (VISION rule 4). Slice 7: an optional visa reading rides along — the
    slot is set through ``visa.set_visa_signal`` (same rules as every door)
    and the event payload carries it, so the judgement is history too.
    ``axes`` (2026-09-20): the fit picture — the agent's own named dimensions
    with a role/you number on each (``validate_axes``); part of the slot, so
    a save without axes clears them. Slice 5: a score needs ``verdict`` (its
    reason) and ``score_source``; gaps carry hard/soft kinds; ``jd_requirements``
    and four extras (archetype, remote_policy, team_size, via) are part of the
    slot too - every save replaces them (``fit_record.validate_fit_record``)."""
    # Lazy: visa.py / fit_record.py import SpineError / get_owned_application from here.
    from src.services.applications import fit_record  # noqa: PLC0415
    from src.services.applications import visa as visa_service  # noqa: PLC0415

    app_row = await get_owned_application(db, user_id, application_id)
    if app_row is None:
        raise SpineError(404, "application not found")

    axes_list = validate_axes(axes)
    rec = fit_record.validate_fit_record(
        score=score, verdict=verdict, score_source=score_source, gaps=gaps,
        jd_requirements=jd_requirements, archetype=archetype, remote_policy=remote_policy,
        team_size=team_size, via=via,
    )
    now = datetime.now(timezone.utc).isoformat()
    gaps_detail = rec["gaps"]
    gaps_list = [g["text"] for g in gaps_detail]
    jd_list = rec["jd_requirements"]
    await db._db.execute(
        "UPDATE applications SET fit_score = ?, fit_verdict = ?, fit_gaps = ?, fit_reasoning = ?, "
        "fit_axes = ?, fit_recorded_by = ?, fit_recorded_at = ?, fit_score_source = ?, "
        "fit_gaps_detail = ?, fit_jd_requirements = ?, fit_archetype = ?, fit_remote_policy = ?, "
        "fit_team_size = ?, fit_via = ?, updated_at = ? WHERE id = ?",
        (
            score, verdict, json.dumps(gaps_list), reasoning, json.dumps(axes_list), recorded_by, now,
            score_source, json.dumps(gaps_detail), json.dumps(jd_list), rec["archetype"],
            rec["remote_policy"], rec["team_size"], rec["via"], now, application_id,
        ),
    )
    payload: dict[str, Any] = {
        "score": score, "verdict": verdict, "gaps": gaps_list, "reasoning": reasoning, "axes": axes_list,
        "score_source": score_source, "gaps_detail": gaps_detail, "jd_requirements": jd_list,
        "archetype": rec["archetype"], "remote_policy": rec["remote_policy"],
        "team_size": rec["team_size"], "via": rec["via"],
    }
    visa: Optional[dict[str, Any]] = None
    if visa_signal is not None or visa_detail or visa_country:
        visa = (
            await visa_service.set_visa_signal(
                db, user_id=user_id, application_id=application_id, recorded_by=recorded_by,
                signal=visa_signal, detail=visa_detail, country=visa_country,
            )
        )["visa"]
        payload["visa"] = {"signal": visa["signal"], "detail": visa["detail"], "country": visa["country"]}
    event = await append_event(
        db, user_id=user_id, application_id=application_id, event_type="fit_judged",
        payload=payload, occurred_at=now, recorded_by=recorded_by,
    )
    extras = [
        name for name, val in (
            ("archetype", rec["archetype"]), ("remote_policy", rec["remote_policy"]),
            ("team_size", rec["team_size"]), ("via", rec["via"]),
        ) if val is not None
    ]
    get_audit_logger().info(
        "fit_saved",
        extra={
            "event": "fit_saved", "application_id": safe_log_value(application_id),
            "scored": score is not None,
            "score_source": safe_log_value(score_source, max_len=40), "gaps": len(gaps_list),
            "hard_gaps": sum(1 for g in gaps_detail if g["kind"] == "hard"),
            "jd_requirements": len(jd_list), "extras": safe_log_value(extras, max_len=200),
        },
    )
    fit = fit_record.fit_view({
        "fit_recorded_at": now, "fit_score": score, "fit_verdict": verdict,
        "fit_score_source": score_source, "fit_gaps": json.dumps(gaps_list),
        "fit_gaps_detail": json.dumps(gaps_detail), "fit_jd_requirements": json.dumps(jd_list),
        "fit_reasoning": reasoning, "fit_axes": json.dumps(axes_list), "fit_archetype": rec["archetype"],
        "fit_remote_policy": rec["remote_policy"], "fit_team_size": rec["team_size"], "fit_via": rec["via"],
        "fit_recorded_by": recorded_by,
    })
    return {
        "application_id": application_id,
        "fit": fit,
        "visa": visa,
        "event_id": event["event_id"],
    }


# ── R8 — record_application, the rich receipt ────────────────────────────────


async def _resolve_receipt_artifact(
    db: JobDatabase, user_id: str, application_id: int, kind: str, artifact_id: Optional[int]
) -> Optional[dict[str, Any]]:
    if artifact_id is not None:
        cur = await db._db.execute(
            "SELECT id, version_no, text FROM application_artifacts "
            "WHERE id = ? AND application_id = ? AND user_id = ? AND kind = ?",
            (artifact_id, application_id, user_id, kind),
        )
        row = await cur.fetchone()
        if row is None:
            raise SpineError(404, f"{kind} artifact not found")
        return dict(row)
    return await latest_artifact(db, user_id, application_id, kind)


_RECEIPT_TOOL = "record_application"


def _event_answers_count(payload: dict[str, Any]) -> int:
    """How many answers a form event says were typed: its own ``answers_count``, else its list's length."""
    claimed = payload.get("answers_count")
    if isinstance(claimed, int) and not isinstance(claimed, bool) and claimed >= 0:
        return claimed
    listed = payload.get("answers")
    return len(listed) if isinstance(listed, list) else 0


async def _form_answers_source(
    db: JobDatabase, user_id: str, application_id: int
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Slice 11 - ``(the form event a receipt freezes its answers from, the host the form was filled on)``.

    Reads this application's surviving ``form_filled`` / ``form_swept`` events (one a
    ``corrects_event_id`` names is skipped) from the newest ``form_filled`` on, newest first: the
    newest one listing answers wins, else the newest one that says it typed some (``answers_missing``).
    A page's sweep (``page`` set) holds that page only, so it never stands for the whole form.
    The host is the newest ``form_filled``'s (a sweep carries none)."""
    cur = await db._db.execute(
        "SELECT e.id, e.event_type, e.payload FROM application_events e "
        "WHERE e.user_id = ? AND e.application_id = ? AND e.event_type IN ('form_filled', 'form_swept') "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) "
        "ORDER BY e.id DESC",
        (user_id, application_id),
    )
    rows: list[dict[str, Any]] = []
    for r in await cur.fetchall():
        row = dict(r)
        row["payload"] = _receipt_json(row.get("payload"), dict)
        rows.append(row)
        if row["event_type"] == "form_filled":
            break  # an older fill is an earlier attempt at the form
    whole = [r for r in rows if "page" not in r["payload"]]
    source = next((r for r in whole if isinstance(r["payload"].get("answers"), list) and r["payload"]["answers"]), None)
    if source is None:
        source = next((r for r in whole if _event_answers_count(r["payload"]) > 0), None)
    host = rows[-1]["payload"].get("host") if rows and rows[-1]["event_type"] == "form_filled" else None
    return source, host if isinstance(host, str) and host else None


def _channel_for_host(host: str) -> str:
    """The closed channel a form's host stands for: LinkedIn = ``linkedin_easy_apply``, Indeed =
    ``job_board`` (the always-ask brands), any other host = the employer's own form, ``company_site``
    (the reading the ``ats_form`` / ``greenhouse`` aliases already give)."""
    labels = host.lower().split(".")
    if "linkedin" in labels or "lnkd" in labels:
        return "linkedin_easy_apply"
    return "job_board" if "indeed" in labels else "company_site"


async def _receipt_replay(
    db: JobDatabase, user_id: str, request_id: str, body: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """The FIRST reply stored for this ``request_id`` (``already_existed: True``), or None."""
    try:
        stored = await idempotency.lookup(db._db, user_id, _RECEIPT_TOOL, request_id, body)
    except idempotency.IdempotencyError as exc:
        raise SpineError(exc.status_code, exc.detail) from None
    if stored is None:
        return None
    get_audit_logger().info(
        "receipt_replayed",
        extra={
            "event": "receipt_replayed", "user_id": safe_log_value(user_id),
            "application_id": int(body["application_id"]), "receipt_id": safe_log_value(stored.get("receipt_id")),
            "result": "ok",
        },
    )
    return {**stored, "already_existed": True}


async def record_receipt(
    db: JobDatabase,
    *,
    user_id: str,
    application_id: int,
    recorded_by: str,
    channel: str = "",
    note: str = "",
    confirmation: str = "",
    answers: Optional[list[dict[str, str]]] = None,
    fields_filled: Optional[dict[str, Any]] = None,
    cv_artifact_id: Optional[int] = None,
    cover_letter_artifact_id: Optional[int] = None,
    applied_at: Optional[str] = None,
    request_id: Optional[str] = None,
    referred_by_contact_id: Optional[int] = None,
    recipient: str = "",
    gmail_message_id: str = "",
) -> dict[str, Any]:
    """R8/S3 — the rich receipt: freeze the named CV version (the newest, if
    none named, with a warning) and the named cover letter (NONE if not named:
    the receipt records "no cover letter sent") as text, and
    append the ``applied`` event. ``profile_version`` is stamped from the
    CALLER's current profile (B6 fix — this used to be hardcoded ``None``,
    unlike the legacy ``/receipts/{job_id}`` route which has always stamped
    it via ``current_profile_version_id``).

    ``channel`` is a closed set for every NEW receipt (owner decision
    2026-10-04, ``normalize_receipt_channel``) — '' stays "not said".

    Gap roadmap slice 10 — the receipt, its ``applied`` event and the
    ``request_id`` row (``services/idempotency``) commit in ONE transaction:
    a failure between them leaves no receipt. The same ``request_id`` again
    returns the FIRST reply with ``already_existed: True`` and writes nothing;
    with a different body it is a 409. ``applied_at`` arrives already parsed
    (the route's ``parse_occurred_at``).

    Gap roadmap slice 11 — no ``answers``: the newest form event's are frozen
    (``answers_from``, :func:`_form_answers_source`); no ``channel``: the form's
    host picks one. Every answer, given or frozen, passes G023
    (``kit.redact_unremembered``). ``idem_body`` stays the CALLER's body.

    Gap roadmap slice 29 - no ``cv_artifact_id``: the CV of the newest
    ``document_uploaded`` since the last ``applied`` (the browser's upload or a kit
    link download) is frozen; no ``cover_letter_artifact_id``: the newest letter the
    BROWSER uploaded into a form is (a download alone never claims a letter). Those
    uploads are frozen as ``files`` [{kind, artifact_id, sha256, fmt, event_id}]. A
    named id that is not the uploaded one is kept, with ``cv_mismatch`` /
    ``cover_letter_mismatch``. No upload at all = the older behaviour.

    Gap roadmap slice 31 (G078) - ``recipient`` (ONE address) and ``gmail_message_id`` are stored at
    INSERT (M3). For ``channel`` email (given, or read off the newest ``email_drafted`` when neither a
    channel nor a form host is known) with no ``recipient``, the newest draft since the last ``applied``
    names it (``recipient from email_drafted#N``); a given one that differs is kept with
    ``recipient_mismatch``. The draft's attachments count as uploads AFTER the ``document_uploaded`` ones:
    they name (and freeze as ``files``) the CV / letter no upload covers - every file when nothing was
    served, the letter when it was only downloaded through a kit link. Both new params join ``idem_body``
    only when given, so a body recorded before them still replays.
    """
    idem_body: dict[str, Any] = {
        "application_id": application_id, "channel": channel, "note": note, "confirmation": confirmation,
        "answers": answers or [], "fields_filled": fields_filled or {}, "cv_artifact_id": cv_artifact_id,
        "cover_letter_artifact_id": cover_letter_artifact_id, "applied_at": applied_at,
        "referred_by_contact_id": referred_by_contact_id,
    }
    if recipient:
        idem_body["recipient"] = recipient
    if gmail_message_id:
        idem_body["gmail_message_id"] = gmail_message_id
    from src.services.applications import email_channel  # noqa: PLC0415 - email_channel imports spine

    recipient = email_channel.clean_recipient(recipient)
    gmail_message_id = email_channel.clean_message_id(gmail_message_id)
    channel_given = (channel or "").strip()
    channel = normalize_receipt_channel(channel)
    warnings: list[str] = []
    if channel_given and channel == "other" and _slug(channel_given) != "other":
        warnings.append(
            f"channel '{channel_given}' is not a known channel; stored as 'other' — allowed: "
            + ", ".join(settings.APPLICATION_RECEIPT_CHANNELS)
        )
    # A label or prose is not an application ID: keep it in the note, never refuse the receipt.
    # Decided BEFORE the INSERT (receipts are append-only, M3).
    from src.services.applications.proof import looks_like_confirmation_id  # noqa: PLC0415 - proof imports spine

    if confirmation.strip() and not looks_like_confirmation_id(confirmation):
        extra = f"Confirmation text (not an ID): {confirmation.strip()[:200]}"
        note = ((note + "\n") if note else "") + extra
        note = note[:2_000]
        confirmation = ""
        warnings.append(
            "confirmation does not look like an application ID or portal reference; kept in the note instead"
        )
    app_row = await get_owned_application(db, user_id, application_id)
    if app_row is None:
        raise SpineError(404, "application not found")
    if request_id is not None:
        replay = await _receipt_replay(db, user_id, request_id, idem_body)
        if replay is not None:
            return replay

    # The referrer must be the caller's own contact linked to THIS application. One message for
    # unknown, foreign and other-job contacts, so it is no existence oracle. (Not via contacts:
    # contacts imports spine.)
    if referred_by_contact_id is not None:
        cur = await db._db.execute(
            "SELECT application_id FROM application_contacts WHERE id = ? AND user_id = ?",
            (referred_by_contact_id, user_id),
        )
        ref = await cur.fetchone()
        if ref is None or dict(ref)["application_id"] != application_id:
            raise SpineError(422, "referred_by_contact_id must be one of your contacts linked to this application")

    # Lazy: kit imports spine.
    from src.services.applications import kit as kit_service  # noqa: PLC0415

    # Slice 11 - no answers given: freeze the ones the assistant already recorded on the form
    # events (prod receipt 12 read "0 of 11 fields" for a form filled with 11). An empty
    # channel reads the form's host. Both decided here, before the INSERT (M3).
    answers_list: list[dict[str, Any]] = [dict(a) for a in (answers or [])]
    answers_from, answers_count, form_host = "", None, None
    source: Optional[dict[str, Any]] = None
    if not answers_list or not channel_given:
        source, form_host = await _form_answers_source(db, user_id, application_id)
    if not answers_list and source is not None:
        answers_from = f"{source['event_type']}#{int(source['id'])}"
        answers_count = _event_answers_count(source["payload"])
        listed = source["payload"].get("answers")
        answers_list = [dict(a) for a in listed if isinstance(a, dict)] if isinstance(listed, list) else []
        if not answers_list:
            warnings.append(
                f"answers_missing: {answers_from} says {answers_count} answers were typed but lists none; "
                "the receipt keeps the count only"
            )
    if not channel_given and form_host:
        channel = _channel_for_host(form_host)
        warnings.append(f"channel not given: stored '{channel}' from the form's host {form_host}")
    # Slice 31 - the draft of an email application names the channel (no form, no channel given),
    # the recipient and the attached files. Decided before the INSERT (M3).
    draft = await email_channel.newest_draft(db, user_id, application_id, since_applied=True)
    if not channel_given and not form_host and draft is not None:
        channel = "email"
        warnings.append(f"channel not given: stored 'email' from email_drafted#{draft['event_id']}")
    recipient_from = "given" if recipient else ""
    if channel == "email" and draft is not None:
        if not recipient:
            recipient, recipient_from = draft["to"], "draft"
            warnings.append(f"recipient from email_drafted#{draft['event_id']}")
        elif recipient != draft["to"]:
            warnings.append(
                f"recipient_mismatch: the receipt keeps the recipient you gave ({recipient}), but the newest "
                f"email_drafted#{draft['event_id']} was to {draft['to']}"
            )
    # G023 - a sensitive answer the user did not agree to remember is hashed (given or frozen alike).
    answers_list, hashed = await kit_service.redact_unremembered(db, user_id, app_row, answers_list)

    # Slice 29 - the files Job360 itself handed out since the last receipt (``document_uploaded``,
    # server-written: the browser's upload, or a kit link download) name the versions when the
    # caller names none. No upload at all = the older flow, unchanged.
    from src.services.applications import uploads as uploads_service  # noqa: PLC0415 - uploads imports spine

    uploads = await uploads_service.uploads_since_last_receipt(db, user_id, application_id)
    if channel == "email" and draft is not None:
        # Slice 31 - attaching a file to the email IS sending it. The draft's attachments come AFTER
        # Job360's own uploads (server-written evidence wins), so they name what no upload covers: all
        # files when nothing was served, else e.g. the letter when only a kit link download (no host) was.
        uploads = [*uploads, *email_channel.receipt_uploads(draft)]
    up_cv = next((u for u in uploads if u["kind"] == "cv"), None)
    up_cl = next((u for u in uploads if u["kind"] == "cover_letter"), None)
    # Only an upload INTO a form (the browser's, host set) proves a letter was attached; a kit link
    # download alone does not (an assistant may fetch both files for a form with no letter field).
    up_cl_form = next((u for u in uploads if u["kind"] == "cover_letter" and u["host"]), None)
    files: list[dict[str, Any]] = []

    cv_artifact = await _resolve_receipt_artifact(
        db, user_id, application_id, "cv",
        up_cv["artifact_id"] if cv_artifact_id is None and up_cv is not None else cv_artifact_id,
    )
    if cv_artifact_id is None and up_cv is None and cv_artifact is not None:
        warnings.append(
            "cv_artifact_id not given: froze the newest CV "
            f"(artifact {cv_artifact['id']}, v{cv_artifact['version_no']})"
        )
    if up_cv is not None and cv_artifact is not None:
        if up_cv["artifact_id"] == cv_artifact["id"]:
            files.append(uploads_service.file_entry(up_cv))
        else:  # artifacts are append-only (M3): another id is another text, so other bytes
            warnings.append(
                f"cv_mismatch: the receipt keeps the CV you named (artifact {cv_artifact['id']}), but the CV "
                f"Job360 last sent was artifact {up_cv['artifact_id']} "
                f"({up_cv.get('source', 'document_uploaded')}#{up_cv['event_id']})"
            )
    # A receipt never guesses a letter: only a NAMED or form-uploaded letter is frozen (M3 - the
    # receipt is permanent, so a wrong "sent" claim could never be corrected; prod receipt 19).
    cl_artifact: Optional[dict[str, Any]] = None
    if cover_letter_artifact_id is not None:
        cl_artifact = await _resolve_receipt_artifact(
            db, user_id, application_id, "cover_letter", cover_letter_artifact_id
        )
        if up_cl is not None and cl_artifact is not None:
            if up_cl["artifact_id"] == cl_artifact["id"]:
                files.append(uploads_service.file_entry(up_cl))
            else:
                warnings.append(
                    f"cover_letter_mismatch: the receipt keeps the letter you named (artifact {cl_artifact['id']}), "
                    f"but the letter Job360 last sent was artifact {up_cl['artifact_id']} "
                    f"({up_cl.get('source', 'document_uploaded')}#{up_cl['event_id']})"
                )
    elif up_cl_form is not None:
        cl_artifact = await _resolve_receipt_artifact(
            db, user_id, application_id, "cover_letter", up_cl_form["artifact_id"]
        )
        files.append(uploads_service.file_entry(up_cl_form))
    elif up_cl is not None:
        warnings.append(
            f"no cover_letter_artifact_id given: a cover letter was downloaded (artifact {up_cl['artifact_id']}) "
            "but Job360 saw no upload of it, so the receipt says no cover letter was sent. "
            "If it was sent, record a correction note; receipts cannot be edited."
        )
    else:
        warnings.append(
            "no cover_letter_artifact_id given: the receipt says no cover letter was sent. "
            "If one was sent, record a correction note; receipts cannot be edited."
        )

    # Lazy: profile storage pulls the scoring stack transitively (rule #16),
    # same reasoning as save_artifact's identical import above.
    from src.services.profile.storage import current_profile_version_id  # noqa: PLC0415

    profile_version = current_profile_version_id(user_id)
    now = datetime.now(timezone.utc).isoformat()
    sent_at = applied_at or now
    answers_json = json.dumps(answers_list)
    fields_json = json.dumps(fields_filled or {})

    # S3 - flag a possible duplicate and name the kit the assistant filled from.
    # Both are set HERE, at INSERT (M3): a flag never blocks the receipt.
    dup = await kit_service.duplicate_facts(
        db, user_id, app_row, datetime.now(timezone.utc), include_self=True
    )
    possible_duplicate = dup["flag"]
    cur = await db._db.execute(
        "SELECT id, payload FROM application_events WHERE application_id = ? AND user_id = ? "
        "AND event_type = 'kit_read' ORDER BY id DESC LIMIT 1",
        (application_id, user_id),
    )
    kit_row = await cur.fetchone()
    kit_event_id: Optional[int] = None
    kit_sha256 = ""
    if kit_row is not None:
        kit_event_id = int(dict(kit_row)["id"])
        try:
            kit_sha256 = str(json.loads(dict(kit_row)["payload"]).get("kit_sha256") or "")
        except (ValueError, AttributeError):
            kit_sha256 = ""

    try:
        async with db._db.transaction():
            cur = await db._db.execute(
                "INSERT INTO application_receipts "
                "(user_id, job_id, sent_at, job_title, job_company, job_location, job_apply_url, job_source, "
                " job_description, cv_text, cv_origin, cover_letter_text, cover_letter_origin, profile_version, "
                " channel, note, created_at, application_id, cv_artifact_id, cover_letter_artifact_id, answers, "
                " fields_filled, confirmation, recorded_by, possible_duplicate, kit_event_id, kit_sha256, "
                " referred_by_contact_id, answers_from, answers_count, files, recipient, gmail_message_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?)",
                (
                    user_id, app_row["job_id"], sent_at,
                    app_row.get("job_title") or "", app_row.get("job_company") or "",
                    app_row.get("job_location") or "", app_row.get("job_url") or "",
                    app_row.get("job_source") or "", app_row.get("job_description_snapshot") or "",
                    cv_artifact["text"] if cv_artifact else None, "artifact" if cv_artifact else None,
                    cl_artifact["text"] if cl_artifact else None, "artifact" if cl_artifact else None,
                    profile_version, channel or "", note or "", now,
                    application_id, cv_artifact["id"] if cv_artifact else None,
                    cl_artifact["id"] if cl_artifact else None, answers_json, fields_json,
                    confirmation or "", recorded_by, possible_duplicate, kit_event_id, kit_sha256,
                    referred_by_contact_id, answers_from, answers_count, json.dumps(files), recipient,
                    gmail_message_id,
                ),
            )
            receipt_id = int(cur.lastrowid or 0)
            event = await append_event(
                db, user_id=user_id, application_id=application_id, event_type="applied",
                payload={"receipt_id": receipt_id, "channel": channel or "", "possible_duplicate": possible_duplicate},
                occurred_at=sent_at, recorded_by=recorded_by,
            )
            result: dict[str, Any] = {
                "receipt_id": receipt_id, "sent_at": sent_at,
                "cv_artifact_id": cv_artifact["id"] if cv_artifact else None,
                "cv_version_no": cv_artifact["version_no"] if cv_artifact else None,
                "cover_letter_artifact_id": cl_artifact["id"] if cl_artifact else None,
                "cover_letter_sent": cl_artifact is not None,
                "cover_letter_version_no": cl_artifact["version_no"] if cl_artifact else None,
                "channel": channel or "", "channel_given": channel_given, "warnings": warnings,
                "confirmation": confirmation or "",
                "url": f"/applications/{application_id}", "event_id": event["event_id"],
                "possible_duplicate": possible_duplicate, "answers_from": answers_from, "files": files,
                "recipient": recipient, "gmail_message_id": gmail_message_id,
                "already_existed": False,
                "referred_by_contact_id": referred_by_contact_id,
            }
            if request_id is not None:
                await idempotency.store(db._db, user_id, _RECEIPT_TOOL, request_id, idem_body, result)
    except pg.IntegrityError:
        # Two retries raced past the lookup: the UNIQUE key rolled this whole
        # transaction back (no receipt, no event). Return the winner's reply.
        replay = await _receipt_replay(db, user_id, request_id, idem_body) if request_id is not None else None
        if replay is None:
            raise
        return replay
    get_audit_logger().info(
        "receipt_created",
        extra={
            "event": "receipt_created", "receipt_id": receipt_id,
            "cv_artifact_id": cv_artifact["id"] if cv_artifact else None, "files_count": len(files),
            "referred_by_contact_id": referred_by_contact_id,
        },
    )
    if answers_from or hashed or (form_host and not channel_given):
        # Slice 11 - ids and counts only: never an answer, a question or the host.
        get_audit_logger().info(
            "receipt_from_form",
            extra={
                "event": "receipt_from_form", "user_id": safe_log_value(user_id),
                "application_id": int(application_id), "receipt_id": receipt_id,
                "from_event_id": int(source["id"]) if answers_from and source is not None else None,
                "answers_count": answers_count, "answers_missing": bool(answers_from) and not answers_list,
                "redacted_count": hashed, "channel_from_form": bool(form_host and not channel_given),
                "result": "ok",
            },
        )
    files_from_draft = draft is not None and any(f["event_id"] == draft["event_id"] for f in files)
    if channel == "email" or recipient or gmail_message_id:
        # Slice 31 - the recipient's DOMAIN only, never the address.
        get_audit_logger().info(
            "receipt_email",
            extra={
                "event": "receipt_email", "user_id": safe_log_value(user_id),
                "application_id": int(application_id), "receipt_id": receipt_id,
                "to_domain": safe_log_value(email_channel.recipient_domain(recipient), max_len=80),
                "recipient_from": recipient_from or "none",
                "recipient_mismatch": any(w.startswith("recipient_mismatch") for w in warnings),
                "gmail_message_id_given": bool(gmail_message_id), "files_from_draft": files_from_draft,
                "result": "ok",
            },
        )
    if possible_duplicate:
        get_audit_logger().info(
            "possible_duplicate",
            extra={
                "event": "possible_duplicate", "user_id": safe_log_value(user_id),
                "application_id": int(application_id), "receipt_id": int(receipt_id),
                "flag": safe_log_value(possible_duplicate, max_len=40),
                "actor": safe_log_value(recorded_by, max_len=80), "result": "ok",
            },
        )
    return result


async def write_through_legacy_receipt(
    db: JobDatabase, *, user_id: str, application_id: int, receipt_id: int, sent_at: str, note: str = ""
) -> dict[str, Any]:
    """The legacy `POST /receipts/{job_id}` write-through (spec R8): appends
    the `applied` event for a receipt whose `application_id` the caller
    already resolved and passed into `db.insert_receipt`'s own INSERT — never
    backfilled by an UPDATE (tests/test_receipts.py::
    test_receipts_are_append_only greps `backend/src/` for any UPDATE/DELETE
    against `application_receipts` and must stay green).
    """
    return await append_event(
        db, user_id=user_id, application_id=application_id, event_type="applied",
        detail=note or "", payload={"receipt_id": receipt_id}, occurred_at=sent_at, recorded_by="web",
    )


async def submit_override(db: JobDatabase, user_id: str, application_id: int) -> Optional[str]:
    """The per-job submit switch (owner decision 2026-10-08, S2): the payload of
    this application's NEWEST ``submit_mode_set`` event — ``"confirm"`` or
    ``"auto_when_sure"`` — or ``None`` when there is none or it says ``"inherit"``
    (use the account's ``submit_mode``). Append-only history, latest wins; no
    column. Scoped by ``user_id`` as well as the application (rule #12)."""
    cur = await db._db.execute(
        "SELECT payload FROM application_events "
        "WHERE application_id = ? AND user_id = ? AND event_type = 'submit_mode_set' "
        "ORDER BY id DESC LIMIT 1",
        (application_id, user_id),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    try:
        mode = json.loads(dict(row)["payload"]).get("submit_mode")
    except (ValueError, AttributeError):
        return None
    return mode if mode in ("confirm", "auto_when_sure") else None


async def origin_by_app(db: JobDatabase, user_id: str, app_ids: list[int]) -> dict[int, str]:
    """Where each application's job came from (owner ask 2026-10-18), for the tag on every job:
    the ``origin`` its ``brought`` event carries (ai_hunt / user_link / user_paste); without one,
    ``user`` when the web brought it, ``ai`` when an assistant did. ONE query for the page."""
    if not app_ids:
        return {}
    marks = ",".join("?" for _ in app_ids)
    cur = await db._db.execute(
        f"SELECT application_id, payload, recorded_by FROM application_events "  # noqa: S608 - placeholders only
        f"WHERE user_id = ? AND event_type = 'brought' AND application_id IN ({marks}) ORDER BY id",
        [user_id, *app_ids],
    )
    from src.services.profile import edits as profile_edits  # noqa: PLC0415

    out: dict[int, str] = {}
    for r in await cur.fetchall():
        d = dict(r)
        if int(d["application_id"]) in out:
            continue
        raw = d.get("payload")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except ValueError:
            payload = {}
        origin = payload.get("origin") if isinstance(payload, dict) else None
        if origin not in ("ai_hunt", "user_link", "user_paste"):
            origin = "user" if d.get("recorded_by") == profile_edits.WEB_ACTOR else "ai"
        out[int(d["application_id"])] = origin
    return out


async def queue_if_open(
    db: JobDatabase, user_id: str, application_id: int, *, recorded_by: str
) -> tuple[bool, Optional[str]]:
    """``bring_job queue=true`` (owner ask 2026-10-18): append a ``queued`` event when the application
    is open (``considering``, no receipt) and not on the queue yet. ``(in_batch_now, note)``."""
    if application_id in await queued_at_by_app(db, user_id, [application_id]):
        return True, "already queued"
    row = await get_owned_application(db, user_id, application_id)
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM application_receipts WHERE application_id = ? AND user_id = ?",
        (application_id, user_id),
    )
    if row is None or row["status"] != "considering" or int((await cur.fetchone())[0]) > 0:
        return False, "not open"
    await append_event(
        db, user_id=user_id, application_id=application_id, event_type="queued",
        occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by=recorded_by,
    )
    return True, None


async def queued_at_by_app(
    db: JobDatabase, user_id: str, app_ids: Optional[list[int]] = None
) -> dict[int, str]:
    """The batch queue (owner decision 2026-10-18): ``{application_id: queued_at}`` for the
    user's applications whose newest surviving ``queued`` / ``unqueued`` event (by id) is a
    ``queued``. An event a ``corrects_event_id`` event names is skipped (the same "a
    correcting event supersedes the one it names" rule the list uses), so retracting a
    ``queued`` falls back to the one before it. ``queued_at`` is that event's
    ``recorded_at``. ``app_ids`` limits the read (``[]`` reads nothing). Event history only:
    whether the application is still ``considering`` is the caller's check. Scoped by
    ``user_id`` (rule #12). One query, never N+1."""
    if app_ids is not None and not app_ids:
        return {}
    where = "e.user_id = ? AND e.event_type IN ('queued', 'unqueued')"
    params: list[Any] = [user_id]
    if app_ids is not None:
        where += f" AND e.application_id IN ({','.join('?' for _ in app_ids)})"  # noqa: S608 - placeholders, not values
        params.extend(app_ids)
    cur = await db._db.execute(
        f"SELECT e.id, e.application_id, e.event_type, e.recorded_at FROM application_events e "  # noqa: S608
        f"WHERE {where} "
        f"AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) "
        f"ORDER BY e.id ASC",
        params,
    )
    queued: dict[int, str] = {}
    for r in await cur.fetchall():
        row = dict(r)
        if row["event_type"] == "queued":
            queued[int(row["application_id"])] = str(row["recorded_at"])
        else:
            queued.pop(int(row["application_id"]), None)
    return queued


# ── R11 — get_application / list_applications ────────────────────────────────


def _receipt_json(raw: Any, expect: type) -> Any:
    """``answers`` / ``fields_filled`` come back as JSON text (``'[]'`` /
    ``'{}'`` defaults from 0037). Junk or the wrong shape reads as the
    empty value — one bad legacy row must never 500 the whole page."""
    if isinstance(raw, expect):
        return raw
    if not isinstance(raw, str) or not raw:
        return expect()
    try:
        parsed = json.loads(raw)
    except ValueError:
        return expect()
    return parsed if isinstance(parsed, expect) else expect()


async def _list_receipts_for_application(
    db: JobDatabase, user_id: str, application_id: int, *, include_text: bool = False
) -> list[dict[str, Any]]:
    """Every receipt on the application, newest first. ``answers`` and
    ``fields_filled`` are decoded here — they were stored since 0037 but
    never read back until 2026-09-11 (``tests/test_receipt_answers_readable.py``)."""
    cols = (
        "id, sent_at, channel, confirmation, cv_artifact_id, cover_letter_artifact_id, note, "
        "answers, fields_filled, (COALESCE(cover_letter_text, '') <> '') AS _letter_frozen"
    )
    if include_text:
        cols += ", cv_text, cover_letter_text"
    cur = await db._db.execute(
        f"SELECT {cols} FROM application_receipts WHERE user_id = ? AND application_id = ? "  # noqa: S608
        f"ORDER BY sent_at DESC, id DESC",
        (user_id, application_id),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        row["answers"] = _receipt_answers(row.get("answers"))
        row["fields_filled"] = _receipt_json(row.get("fields_filled"), dict)
        frozen = bool(row.pop("_letter_frozen", None))
        row["cover_letter_sent"] = frozen or row.get("cover_letter_artifact_id") is not None
        row.update(await receipt_summary_facts(db, user_id, int(row["id"])))
        row["cv_version_no"] = await _artifact_version_no(
            db, user_id, application_id, "cv", row.get("cv_artifact_id")
        )
        row["cover_letter_version_no"] = await _artifact_version_no(
            db, user_id, application_id, "cover_letter", row.get("cover_letter_artifact_id")
        )
    return rows


def _receipt_answers(raw: Any) -> list[dict[str, Any]]:
    """Only well-formed {question, answer} pairs survive — the response model
    is strict (extra="forbid"), so a malformed legacy entry would otherwise
    turn a read into a 500. ``source`` / ``key`` come back when stored (text
    only); a receipt recorded before they existed simply has neither."""
    out: list[dict[str, str]] = []
    for a in _receipt_json(raw, list):
        hashed = isinstance(a, dict) and a.get("redacted") is True and isinstance(a.get("value_sha256"), str)
        if not (isinstance(a, dict) and "question" in a and ("answer" in a or hashed)):
            continue
        # G023: a hashed answer reads back with no value - ``redacted`` + the hash, never a guess.
        item: dict[str, Any] = {"question": str(a["question"]), "answer": "" if hashed else str(a["answer"])}
        if hashed:
            item.update(redacted=True, value_sha256=a["value_sha256"])
        for extra in ("source", "key"):
            if isinstance(a.get(extra), str) and a[extra]:
                item[extra] = a[extra]
        out.append(item)
    return out


async def _artifact_version_no(
    db: JobDatabase, user_id: str, application_id: Optional[int], kind: str, artifact_id: Optional[int]
) -> Optional[int]:
    """The stored ``version_no`` of the artifact a receipt names, or None when
    the receipt names none (or the named row is not this user's)."""
    if artifact_id is None or application_id is None:
        return None
    cur = await db._db.execute(
        "SELECT version_no FROM application_artifacts "
        "WHERE id = ? AND user_id = ? AND application_id = ? AND kind = ?",
        (artifact_id, user_id, application_id, kind),
    )
    row = await cur.fetchone()
    return int(row["version_no"]) if row is not None else None


async def receipt_summary_facts(db: JobDatabase, user_id: str, receipt_id: int) -> dict[str, Any]:
    """Read-time facts a receipt shows (nothing stored twice; receipts stay append-only):
    the counts line ``"N of M fields · S skipped · G guessed"`` and the host the form was on.

    M = ``fields_count`` of the newest ``form_filled`` recorded at or before the receipt was
    created; no such event means no M and no line (never an invented number). N = fields +
    answers on the receipt, capped at M. S = the event's own ``fields_skipped`` list when the
    assistant gave one, else M - N. G = answers whose ``source`` is ``guessed``.
    ``form_host`` is the host the form was filled on, else the job link's host marked as such.
    Scoped by ``user_id``; a foreign or unknown id reads as the empty facts."""
    empty: dict[str, Any] = {
        "counts_line": "", "fields_total": None, "fields_skipped": 0, "guessed_count": 0, "form_host": "",
    }
    cur = await db._db.execute(
        "SELECT application_id, created_at, job_apply_url, answers, fields_filled, answers_count "
        "FROM application_receipts WHERE user_id = ? AND id = ?",
        (user_id, receipt_id),
    )
    row = await cur.fetchone()
    if row is None:
        return empty
    r = dict(row)
    answers = _receipt_answers(r.get("answers"))
    filled = _receipt_json(r.get("fields_filled"), dict)
    form: dict[str, Any] = {}
    if r.get("application_id") is not None:
        cur = await db._db.execute(
            "SELECT payload FROM application_events WHERE user_id = ? AND application_id = ? "
            "AND event_type = 'form_filled' AND recorded_at <= ? ORDER BY recorded_at DESC, id DESC LIMIT 1",
            (user_id, r["application_id"], r.get("created_at") or ""),
        )
        ev = await cur.fetchone()
        form = _receipt_json(dict(ev)["payload"], dict) if ev is not None else {}
    out = dict(empty)
    out["guessed_count"] = sum(1 for a in answers if a.get("source") == "guessed")
    host = form.get("host")
    if isinstance(host, str) and host:
        out["form_host"] = host
    else:
        from src.services.profile.assistant_settings import screen_form_url  # noqa: PLC0415

        job_host = screen_form_url(r.get("job_apply_url") or None)
        out["form_host"] = f"{job_host} (job link)" if job_host else ""
    total = form.get("fields_count")
    if isinstance(total, bool) or not isinstance(total, int):
        return out
    # Slice 11: a receipt whose frozen form event listed no answers keeps that event's count.
    stored_count = r.get("answers_count")
    typed = len(answers)
    if not typed and isinstance(stored_count, int) and not isinstance(stored_count, bool):
        typed = stored_count
    done = min(len(filled) + typed, total)
    skipped_list = form.get("fields_skipped")
    skipped = len(skipped_list) if isinstance(skipped_list, list) else max(total - done, 0)
    out.update(
        fields_total=total, fields_skipped=skipped,
        counts_line=f"{done} of {total} fields \u00b7 {skipped} skipped \u00b7 {out['guessed_count']} guessed",
    )
    return out


async def receipt_details(db: JobDatabase, user_id: str, receipt_id: int) -> Optional[dict[str, Any]]:
    """What the assistant recorded when the user applied, for ONE receipt —
    read off the same ``application_receipts`` row the legacy
    ``GET /receipts/{id}`` route already serves (no join: it is one table).

    Stored facts only (rule #29): an empty answers list, an empty
    fields mapping, a blank confirmation or blank actor read as empty / None,
    never a default. Scoped by ``user_id`` (rule #12): a foreign id is None.
    Read-only — receipts are append-only (M3).
    """
    cur = await db._db.execute(
        "SELECT application_id, cv_artifact_id, cover_letter_artifact_id, answers, fields_filled, "
        "confirmation, recorded_by, possible_duplicate, kit_event_id, kit_sha256, referred_by_contact_id, "
        "answers_from, files, recipient, gmail_message_id, "
        "(COALESCE(cover_letter_text, '') <> '') AS letter_frozen "
        "FROM application_receipts WHERE user_id = ? AND id = ?",
        (user_id, receipt_id),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    r = dict(row)
    application_id = r.get("application_id")
    from src.services.applications.proof import NO_PROOF, proof_for  # noqa: PLC0415

    return {
        "application_id": application_id,
        "proof": (await proof_for(db, user_id, [application_id]))[application_id] if application_id else NO_PROOF,
        "answers": _receipt_answers(r.get("answers")),
        "answers_from": r.get("answers_from") or "",
        "files": [f for f in _receipt_json(r.get("files"), list) if isinstance(f, dict)],
        "recipient": r.get("recipient") or "",
        "gmail_message_id": r.get("gmail_message_id") or "",
        "fields_filled": _receipt_json(r.get("fields_filled"), dict),
        "confirmation": r.get("confirmation") or None,
        "recorded_by": r.get("recorded_by") or None,
        "possible_duplicate": r.get("possible_duplicate") or "",
        "kit_event_id": r.get("kit_event_id"),
        "kit_sha256": r.get("kit_sha256") or "",
        "referred_by_contact_id": r.get("referred_by_contact_id"),
        "cv_version_no": await _artifact_version_no(
            db, user_id, application_id, "cv", r.get("cv_artifact_id")
        ),
        "cover_letter_version_no": await _artifact_version_no(
            db, user_id, application_id, "cover_letter", r.get("cover_letter_artifact_id")
        ),
        "cover_letter_sent": r.get("cover_letter_artifact_id") is not None or bool(r.get("letter_frozen")),
        **await receipt_summary_facts(db, user_id, receipt_id),
    }


async def receipt_letter_sent(db: JobDatabase, user_id: str, receipt_ids: list[int]) -> dict[int, bool]:
    """``cover_letter_sent`` for many receipts at once: true when the row names a
    cover letter artifact or froze a letter's text (the legacy web "I applied"
    route stores text without an id). Read-only; a foreign or unknown id is absent."""
    if not receipt_ids:
        return {}
    marks = ",".join("?" for _ in receipt_ids)
    cur = await db._db.execute(
        f"SELECT id, cover_letter_artifact_id, (COALESCE(cover_letter_text, '') <> '') AS frozen "  # noqa: S608
        f"FROM application_receipts "
        f"WHERE user_id = ? AND id IN ({marks})",
        (user_id, *receipt_ids),
    )
    return {
        int(r["id"]): r["cover_letter_artifact_id"] is not None or bool(r["frozen"]) for r in await cur.fetchall()
    }


def _visa_for(app_row: dict[str, Any], countries: list[str]) -> dict[str, Any]:
    """Slice 7 — lazy so visa.py (which imports from here) never cycles."""
    from src.services.applications.visa import visa_view  # noqa: PLC0415

    return visa_view(app_row, countries)


def _job_facts_for(app_row: Mapping[str, Any]) -> dict[str, Any]:
    """Lazy for the same reason as ``_visa_for`` — job_facts.py imports from here."""
    from src.services.applications.job_facts import job_facts_view  # noqa: PLC0415

    return job_facts_view(app_row)


async def _work_countries(db: JobDatabase, user_id: str) -> list[str]:
    from src.services.applications.visa import user_work_countries  # noqa: PLC0415

    return await user_work_countries(db, user_id)


def derive_form_state(events: list[dict[str, Any]], receipts: list[dict[str, Any]]) -> Optional[str]:
    """Weak spot #1b (c): the form's state READ off the record, never stored or updated.
    ``None`` = no ``form_filled`` yet; ``"filled"`` = a ``form_filled`` is on record;
    ``"submitted"`` = a ``form_filled`` AND a receipt. Mirrors career-ops apply.md:356-358 ("update that
    same section from `filled` to `submitted`") as a derived read model, so the append-only
    events and receipts (M3) are never rewritten. A receipt with no ``form_filled`` stays ``None``."""
    if not any(e.get("event_type") == "form_filled" for e in events):
        return None
    return "submitted" if receipts else "filled"


async def get_application_detail(
    db: JobDatabase, user_id: str, application_id: int, *, with_artifact_text: bool = False
) -> Optional[dict[str, Any]]:
    """R11's full ``GET /applications/{id}`` shape: the job snapshot (plus
    whether the catalog row still exists), the fit slot, every artifact
    version (text omitted unless ``with_artifact_text``), the whole event
    timeline, and receipts. ``None`` for a foreign/unknown id (404, S2)."""
    # Lazy: contacts.py imports FROM this module (SpineError, append_event,
    # get_owned_application, parse_occurred_at) — a top-level import here
    # would be circular. Slice 4 (docs/plans/2026-09-05-contacts-stats/
    # spec.md R3).
    from src.services.applications.asks import list_asks  # noqa: PLC0415
    from src.services.applications.contacts import list_contacts  # noqa: PLC0415
    from src.services.applications.inbox import email_senders_for  # noqa: PLC0415 - inbox imports spine
    from src.services.applications.known_people import for_application as known_people_for  # noqa: PLC0415
    from src.services.applications.proof import proof_for  # noqa: PLC0415

    app_row = await get_owned_application(db, user_id, application_id)
    if app_row is None:
        return None
    job_id = app_row["job_id"]
    cur = await db._db.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,))
    catalog_present = (await cur.fetchone()) is not None

    from src.services.applications.fit_record import fit_view  # noqa: PLC0415

    fit = fit_view(app_row)

    events = await list_events_for_display(db, application_id)
    # R4 — the newest NON-superseded event that carries a scheduled_at (a
    # rescheduled interview is a correcting event, which is what makes the
    # old value drop out — see spec §Flagged concerns). `events` is already
    # ordered oldest-first (occurred_at ASC, id ASC), so the last match IS
    # the newest one; no extra query.
    interview_at: Optional[str] = None
    for ev in events:
        if ev["scheduled_at"] and not ev["superseded"]:
            interview_at = ev["scheduled_at"]

    # Owner decision 2026-09-25 — the follow-up date, in the CALLER's own
    # timezone (users.timezone / user_today), never the server's UTC day.
    follow_up_on = app_row.get("follow_up_on")
    today_iso = (await user_today(db, user_id)).isoformat()
    follow_up_due = _follow_up_due(follow_up_on, app_row["status"], today_iso)

    detail: dict[str, Any] = {
        "id": app_row["id"],
        "job_id": job_id,
        "status": app_row["status"],
        "created_at": app_row["created_at"],
        "updated_at": app_row["updated_at"],
        "last_event_at": app_row.get("last_event_at"),
        "job": {
            "job_title": app_row.get("job_title") or "",
            "job_company": app_row.get("job_company") or "",
            "job_location": app_row.get("job_location") or "",
            "job_url": app_row.get("job_url") or "",
            "job_source": app_row.get("job_source") or "",
            "job_description_snapshot": app_row.get("job_description_snapshot") or "",
            "snapshot_at": app_row.get("snapshot_at"),
            "catalog_present": catalog_present,
            # Owner decision 2026-10-04 — country / remote / found_on, the
            # user's own facts about the job (None when unset, rule #29).
            **_job_facts_for(app_row),
            "pay_advertised": _pay_advertised_for(app_row),
        },
        "fit": fit,
        "visa": _visa_for(app_row, await _work_countries(db, user_id)),
        "artifacts": await _list_artifacts(db, application_id, with_text=with_artifact_text),
        "events": events,
        "interview_at": interview_at,
        "receipts": await _list_receipts_for_application(db, user_id, application_id),
        "contacts": await list_contacts(db, user_id, application_id),
        "known_people": await known_people_for(db, user_id, app_row),
        # "Needs you" - every ask about this job, open first (never filtered).
        "asks": await list_asks(
            db, user_id, "all", application_id=application_id, limit=settings.ASKS_MAX_OPEN_PER_USER
        ),
        "follow_up_on": follow_up_on,
        "follow_up_due": follow_up_due,
        "proof": (await proof_for(db, user_id, [application_id]))[application_id],
        # Gmail check slice 2026-10-15 — where to search and from when.
        "email_senders": email_senders_for(app_row.get("job_url") or ""),
        "inbox_checked_at": app_row.get("inbox_checked_at"),
    }
    detail["form_state"] = derive_form_state(events, detail["receipts"])
    # The same hand-off fields as Needs you: which browser, finish here, refill (blocked imports this module).
    from src.services.applications.blocked import with_hand_off  # noqa: PLC0415

    detail["asks"] = await with_hand_off(db, user_id, detail["asks"])
    # 2026-09-20 — the one line at the top: what to do next, read off the
    # stored state above (never a judgement of the job). The same value
    # reaches the web header and every agent calling get_application.
    from src.services.applications.next_step import next_step_for_detail  # noqa: PLC0415

    detail["next_step"] = next_step_for_detail(detail)
    from src.services.applications import kit as kit_service  # noqa: PLC0415 - kit imports spine

    held = (await kit_service.holds_for_apps(db, user_id, [application_id], datetime.now(timezone.utc))).get(
        application_id
    )
    detail["hold"], detail["stage"] = (held[0], held[1]) if held else (None, "")
    return detail


def _pay_advertised_for(app_row: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Pay as the stored ad states it (job_checks.pay_facts), or None. Computed on
    read: nothing is stored, nothing is annualised."""
    from src.services.applications.job_checks import pay_facts  # noqa: PLC0415

    facts = pay_facts(str(app_row.get("job_description_snapshot") or ""))
    if not facts:
        return None
    return {k: facts[k] for k in ("amount", "evidence", "period", "currency", "min", "max", "equity")}


async def _count(db: JobDatabase, table: str, col: str, value: Any) -> int:
    cur = await db._db.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (value,))  # noqa: S608 — table/col are constants
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _artifact_counts_by_kind(db: JobDatabase, application_id: int) -> dict[str, int]:
    cur = await db._db.execute(
        "SELECT kind, COUNT(*) FROM application_artifacts WHERE application_id = ? GROUP BY kind",
        (application_id,),
    )
    return {r[0]: int(r[1]) for r in await cur.fetchall()}


async def list_applications(
    db: JobDatabase,
    user_id: str,
    *,
    status: Optional[str] = None,
    updated_since: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    due: bool = False,
    quiet_days: Optional[int] = None,
    in_flight: bool = False,
    queued: bool = False,
    priority: Optional[str] = None,
    agenda: Optional[str] = None,
) -> dict[str, Any]:
    """R11's ``GET /applications`` list: summaries only (snapshot fields,
    status, per-kind counts) — no event/artifact bodies. Each row carries ``hold`` / ``stage``
    (another assistant session's live kit hold, weak spot #3); ``in_flight`` keeps only the
    applications with one. Each row also carries ``queued_at`` / ``queue_position`` (the batch
    queue, 2026-10-18: ``queued_at`` is when it was queued, ``queue_position`` its 1-based place in
    the user's WHOLE open queue; both null when it is not queued). ``queued`` keeps only the open
    queue (``considering`` and currently queued) in first-in-first-out order (``queued_at``, then
    application id) instead of newest-first; an application that left ``considering`` is never in
    the queue, whatever its events say. Newest
    ``last_event_at`` first; optionally filtered by ``status`` and/or
    ``updated_since``.

    ``due`` (owner decision, 2026-09-25) — only applications whose
    ``follow_up_on`` has arrived (in the CALLER's own timezone,
    ``user_today``) and whose status is not one of
    ``APPLICATION_FOLLOW_UP_CLOSED_STATUSES``; ordered soonest-first instead
    of newest-activity-first. ``quiet_days`` — only applications whose
    ``last_event_at`` is older than N days (setting a follow-up counts as
    activity, since it writes an event and updates the slot), same closed-
    status exclusion. Both may be combined with ``status``/``updated_since``.

    ``priority`` (owner decision 2026-10-19) - only applications whose PLACE (the job's
    country + remote/on-site, matched to the user's ``right_to_work`` records) has that
    level of the user's own job priority, or ``not_set``. Each row carries ``priority``.

    ``agenda`` (``today`` | ``tomorrow`` | ``week``) - only open applications with a live interview
    or a ``follow_up_on`` inside that range, in the CALLER's own timezone (``agenda.py``).
    """
    from src.services.profile import job_priority  # noqa: PLC0415 - profile ↔ spine cycle
    from src.services.profile.storage import load_profile  # noqa: PLC0415 - heavy (rule #16)

    if priority is not None and priority not in job_priority.FILTER_VALUES:
        raise SpineError(422, f"priority must be one of: {', '.join(job_priority.FILTER_VALUES)}")
    _profile = load_profile(user_id)
    rtw = getattr(_profile.user_info, "right_to_work", None) if _profile is not None else None
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    # Bug fix (coordinator review, 2026-09-25) — Query(ge=1, le=...) on the
    # HTTP route only runs for HTTP requests; the MCP tool calls this
    # function directly, bypassing FastAPI's request parsing entirely. Without
    # a check HERE, quiet_days=0 silently matched everything and an absurd
    # value (e.g. 10_000_000) underflowed `datetime.now() - timedelta(...)`
    # past `datetime.min` — a bare OverflowError, not a readable error, on
    # whichever surface reached this line first.
    if quiet_days is not None and not (1 <= quiet_days <= settings.APPLICATION_QUIET_DAYS_MAX):
        raise SpineError(
            422,
            f"quiet_days must be between 1 and APPLICATION_QUIET_DAYS_MAX "
            f"({settings.APPLICATION_QUIET_DAYS_MAX})",
        )
    from src.services.applications import kit as kit_service  # noqa: PLC0415 - kit imports spine
    from src.services.applications.agenda import AGENDA_RANGES  # noqa: PLC0415

    if agenda is not None and agenda not in AGENDA_RANGES:
        raise SpineError(422, f"agenda must be one of: {', '.join(AGENDA_RANGES)}")

    where = ["user_id = ?"]
    params: list[Any] = [user_id]
    if status:
        where.append("status = ?")
        params.append(status)
    if updated_since:
        where.append("updated_at >= ?")
        params.append(updated_since)

    today = await user_today(db, user_id)
    today_iso = today.isoformat()

    if due or quiet_days is not None:
        placeholders = ",".join("?" for _ in settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES)
        where.append(f"status NOT IN ({placeholders})")  # noqa: S608 — placeholders, not values
        params.extend(settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES)
    if due:
        where.append("follow_up_on IS NOT NULL AND follow_up_on <= ?")
        params.append(today_iso)
    if quiet_days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=quiet_days)).isoformat()
        where.append("(last_event_at IS NULL OR last_event_at < ?)")
        params.append(cutoff)
    if in_flight:
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(minutes=settings.KIT_HOLD_MINUTES)).isoformat()
        cand = await db._db.execute(
            "SELECT DISTINCT application_id FROM application_events WHERE user_id = ? "
            "AND event_type = 'kit_read' AND recorded_at >= ?",
            (user_id, cutoff),
        )
        held = await kit_service.holds_for_apps(db, user_id, [int(c[0]) for c in await cand.fetchall()], now)
        if held:
            where.append(f"id IN ({','.join('?' for _ in held)})")
            params.extend(held)
        else:
            where.append("1 = 0")
    if agenda is not None:
        from src.services.applications.agenda import agenda_application_ids  # noqa: PLC0415

        zone = await user_zone(db, user_id)
        in_range = await agenda_application_ids(db, user_id, agenda, today, zone)
        if in_range:
            where.append(f"id IN ({','.join('?' for _ in in_range)})")
            params.extend(in_range)
        else:
            where.append("1 = 0")
    if priority is not None:
        p_sql, p_params = job_priority.filter_sql(rtw, priority)
        where.append(p_sql)
        params.extend(p_params)
    # The batch queue: the user's WHOLE open queue (still ``considering``), first in first out.
    # Two queries for the page (events, then the status of the queued ids), never N+1.
    queue_order: list[int] = []
    queued_at: dict[int, str] = {}
    all_queued = await queued_at_by_app(db, user_id)
    if all_queued:
        qcur = await db._db.execute(
            f"SELECT id FROM applications WHERE user_id = ? AND status = 'considering' "  # noqa: S608 - placeholders, not values
            f"AND id IN ({','.join('?' for _ in all_queued)})",
            [user_id, *all_queued],
        )
        queued_at = {int(q[0]): all_queued[int(q[0])] for q in await qcur.fetchall()}
        queue_order = sorted(queued_at, key=lambda i: (queued_at[i], i))
    queue_position = {app_id: pos for pos, app_id in enumerate(queue_order, start=1)}
    if queued:
        if queue_order:
            where.append(f"id IN ({','.join('?' for _ in queue_order)})")
            params.extend(queue_order)
        else:
            where.append("1 = 0")
    where_sql = " AND ".join(where)

    cur = await db._db.execute(
        f"SELECT COUNT(*) FROM applications WHERE {where_sql}", params  # noqa: S608 — where_sql built from constants
    )
    total = int((await cur.fetchone())[0])

    order_sql = "follow_up_on ASC, id ASC" if due else "last_event_at DESC NULLS LAST, id DESC"
    if queued:
        # FIFO order lives in Python (queued_at is an event fact, not a column): the queue is
        # small, so read every queued row, order it, then page it.
        order_sql, page_sql, page_params = "id ASC", "", []
    else:
        page_sql, page_params = " LIMIT ? OFFSET ?", [limit, offset]
    cur = await db._db.execute(
        f"SELECT id, job_id, job_title, job_company, job_url, job_location, status, last_event_at, "  # noqa: S608
        f"visa_signal, visa_country, fit_recorded_at, fit_score, fit_verdict, follow_up_on, "
        f"job_country, job_remote, job_found_on, ad_posted_at, employer_published_at, inbox_checked_at "
        f"FROM applications WHERE {where_sql} ORDER BY {order_sql}{page_sql}",
        [*params, *page_params],
    )
    rows = [dict(r) for r in await cur.fetchall()]
    if queued:
        rows.sort(key=lambda r: queue_position[r["id"]])
        rows = rows[offset:offset + limit]

    # Slice 7 — fact 2 once per request, so every card can carry its badge.
    from src.services.applications import visa as visa_service  # noqa: PLC0415
    from src.services.applications.next_step import kit_flags, next_step  # noqa: PLC0415
    from src.services.profile import assistant_settings as settings_rules  # noqa: PLC0415 — profile ↔ spine cycle

    countries = await visa_service.user_work_countries(db, user_id)
    score_line = await user_score_line(user_id)

    # 2026-09-24 — the list's "Next:" line needs interview_at and has_lesson,
    # both read off application_events. One query for the whole page (never
    # N+1): the same "a correcting event supersedes the one it names" rule
    # get_application_detail's per-application read uses, just computed for
    # every application_id in this page at once.
    app_ids = [r["id"] for r in rows]
    interview_at_by_app: dict[int, str] = {}
    has_lesson_by_app: dict[int, bool] = {}
    kit_events_by_app: dict[int, list[tuple[int, str]]] = {}
    if app_ids:
        placeholders = ",".join("?" for _ in app_ids)
        ev_cur = await db._db.execute(
            f"SELECT id, application_id, event_type, scheduled_at, corrects_event_id "  # noqa: S608 — placeholders, not values
            f"FROM application_events WHERE application_id IN ({placeholders}) "
            f"AND (event_type IN ('lesson', 'form_filled', 'submit_approved', 'submit_declined') "
            f"OR scheduled_at IS NOT NULL OR corrects_event_id IS NOT NULL) "
            f"ORDER BY occurred_at ASC, id ASC",
            app_ids,
        )
        ev_rows = [dict(r) for r in await ev_cur.fetchall()]
        superseded_ids = {r["corrects_event_id"] for r in ev_rows if r.get("corrects_event_id") is not None}
        for r in ev_rows:
            if r["id"] in superseded_ids:
                continue
            app_id = r["application_id"]
            if r.get("scheduled_at"):
                interview_at_by_app[app_id] = r["scheduled_at"]  # rows are occurred_at ASC — last write wins
            if r["event_type"] == "lesson":
                has_lesson_by_app[app_id] = True
            kit_events_by_app.setdefault(app_id, []).append((int(r["id"]), r["event_type"]))

    # Owner decision, 2026-09-25 — the list row now shows a "Sent <date>" tag,
    # which needs the LATEST receipt per application. One batched query for
    # the whole page (never N+1), same shape as the interview_at query above;
    # it also replaces the old per-row COUNT so the count and the date can
    # never disagree (they come off the same row).
    receipt_count_by_app: dict[int, int] = {}
    last_receipt_at_by_app: dict[int, str] = {}
    if app_ids:
        placeholders = ",".join("?" for _ in app_ids)
        r_cur = await db._db.execute(
            f"SELECT application_id, COUNT(*), MAX(sent_at) FROM application_receipts "  # noqa: S608 — placeholders, not values
            f"WHERE application_id IN ({placeholders}) GROUP BY application_id",
            app_ids,
        )
        for row in await r_cur.fetchall():
            receipt_count_by_app[row[0]] = int(row[1])
            last_receipt_at_by_app[row[0]] = row[2]

    from src.services.applications.inbox import email_senders_for  # noqa: PLC0415 - inbox imports spine
    from src.services.applications.proof import proof_for  # noqa: PLC0415

    proofs = await proof_for(db, user_id, app_ids)
    origins = await origin_by_app(db, user_id, app_ids)
    holds = await kit_service.holds_for_apps(db, user_id, app_ids, datetime.now(timezone.utc))
    preflights: dict[int, dict[str, Any]] = {}
    browsers: dict[int, dict[str, str]] = {}
    summary: Optional[dict[str, Any]] = None
    outreach: Optional[list[dict[str, Any]]] = None
    merged: Optional[list[str]] = None
    if queued:
        # Bulk-apply Q1/Q2: derived, no table; the order above stays first in first out.
        import asyncio  # noqa: PLC0415

        from src.services.applications import queue_preflight  # noqa: PLC0415 - imports spine
        from src.services.profile.models import UserProfile  # noqa: PLC0415
        from src.services.profile.storage import load_profile_with_overlay  # noqa: PLC0415

        # One profile read, off the event loop (CodeRabbit 2026-10-18), shared with the preflight.
        loaded, overlay = await asyncio.to_thread(load_profile_with_overlay, user_id)
        profile = loaded or UserProfile()
        cfg = settings_rules.effective(profile.assistant_settings)
        preflights = await queue_preflight.preflight_rows(
            db, user_id, rows, queued_at, holds, cfg, profile, overlay
        )
        browsers = await queue_preflight.queue_browsers(db, user_id, queue_order)
        # One queue (owner 2026-10-26): the approved, unsent LinkedIn sends too, merged by when the user said yes.
        from src.services.applications import own_browser  # noqa: PLC0415 - imports spine

        outreach = await own_browser.queued_outreach(db, user_id, datetime.now(timezone.utc))
        merged = own_browser.merged_order({i: queued_at[i] for i in queue_order}, outreach)
        summary = await queue_preflight.queue_summary(db, user_id, queue_order, cfg, browsers, outreach)
    out = []
    for r in rows:
        app_id = r["id"]
        signal = r.get("visa_signal") or "unknown"
        country = r.get("visa_country") or ""
        artifact_counts = await _artifact_counts_by_kind(db, app_id)
        receipts_count = receipt_count_by_app.get(app_id, 0)
        follow_up_on = r.get("follow_up_on")
        follow_up_due = _follow_up_due(follow_up_on, r["status"], today_iso)
        out.append(
            {
                "id": app_id, "job_id": r["job_id"], "job_title": r["job_title"] or "",
                "job_company": r["job_company"] or "", "job_url": r.get("job_url") or "",
                "job_location": r.get("job_location") or "",
                "status": r["status"],
                "last_event_at": r.get("last_event_at"),
                "visa_signal": signal, "visa_country": country,
                "needs_sponsorship": visa_service.needs_sponsorship(signal, country, countries),
                **_job_facts_for(r),
                "priority": job_priority.place_priority(rtw, r.get("job_country"), r.get("job_remote")),
                "events": await _count(db, "application_events", "application_id", app_id),
                "artifacts": artifact_counts,
                "receipts": receipts_count,
                "last_receipt_at": last_receipt_at_by_app.get(app_id),
                "interview_at": interview_at_by_app.get(app_id),
                "fit_score": r.get("fit_score"),
                "fit_verdict": r.get("fit_verdict") or "",
                "line_side": settings_rules.line_side(r.get("fit_score"), score_line),
                "follow_up_on": follow_up_on,
                "follow_up_due": follow_up_due,
                "proof": proofs[app_id],
                "hold": holds[app_id][0] if app_id in holds else None,
                "stage": holds[app_id][1] if app_id in holds else "",
                # The batch queue (2026-10-18): null when the application is not on the open queue.
                "queued_at": queued_at.get(app_id),
                "queue_position": queue_position.get(app_id),
                "preflight": preflights.get(app_id),
                "browser": browsers.get(app_id),
                "origin": origins.get(app_id, ""),
                # Gmail check slice 2026-10-15 — where to search and from when.
                "email_senders": email_senders_for(r.get("job_url") or ""),
                "inbox_checked_at": r.get("inbox_checked_at"),
                # 2026-09-24 — same state machine `get_application` feeds its
                # header with, over the same stored facts (S21: real values,
                # not a schema-presence default).
                "next_step": next_step(
                    status=r["status"],
                    has_fit=bool(r.get("fit_recorded_at")),
                    cv_versions=artifact_counts.get("cv", 0),
                    receipts=receipts_count,
                    interview_at=interview_at_by_app.get(app_id),
                    has_lesson=has_lesson_by_app.get(app_id, False),
                    follow_up_due=follow_up_due,
                    form_filled=kit_flags(kit_events_by_app.get(app_id, []))[0],
                    submit_approved=kit_flags(kit_events_by_app.get(app_id, []))[1],
                ),
            }
        )
    return {
        "applications": out, "total": total, "queue_summary": summary, "outreach": outreach, "queue_order": merged,
    }


# ── R9 — whats_new ────────────────────────────────────────────────────────────


async def whats_new(
    db: JobDatabase,
    user_id: str,
    *,
    since: Optional[str] = None,
    after_id: Optional[int] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """R9 — what changed across ALL of the caller's applications since a
    cursor. Paged and ordered by ``recorded_at`` (when Job360 learned it),
    NEVER ``occurred_at`` (when it happened in the world, which may be
    backdated) — a cursor on the latter could silently skip a backdated
    event forever. ``(recorded_at, id)`` is a real keyset pair (B4 fix): the
    ``after_id`` half only fires alongside its own ``recorded_at`` boundary,
    never as an independent predicate, so a row from either side of that
    boundary is never dropped.

    Also carries open_asks, proof_missing (applied jobs past PROOF_NO_PROOF_AFTER_DAYS with no proof and no ask)
    and ``interviews`` ({upcoming, just_passed}, services/applications/agenda.py) whatever `since` says."""  # noqa: E501
    limit = max(1, min(int(limit), settings.WHATS_NEW_MAX_EVENTS))
    now = datetime.now(timezone.utc).isoformat()
    since_val = since or (
        datetime.now(timezone.utc) - timedelta(days=settings.WHATS_NEW_DEFAULT_WINDOW_DAYS)
    ).isoformat()

    # B4 fix — a REAL keyset cursor. `recorded_at >= since AND id > after_id`
    # as two INDEPENDENT predicates drops any event whose `recorded_at` is
    # <= the page boundary's but whose `id` is higher (a concurrently
    # committed write can easily land in that gap): it satisfies neither half
    # of the AND, so it is skipped on this page AND on the next ("since" moved
    # past it), forever. The correct predicate paginates on the PAIR:
    # everything strictly after (recorded_at, id) in that lexicographic order.
    where = ["user_id = ?"]
    params: list[Any] = [user_id]
    if after_id is not None:
        where.append("(recorded_at > ? OR (recorded_at = ? AND id > ?))")
        params.extend([since_val, since_val, after_id])
    else:
        where.append("recorded_at >= ?")
        params.append(since_val)
    where_sql = " AND ".join(where)

    cur = await db._db.execute(
        f"SELECT id, application_id, event_type, detail, payload, occurred_at, recorded_at, "  # noqa: S608
        f"recorded_by, corrects_event_id, source_kind, source_message_id, source_thread_id, "
        f"source_quote, source_sender, source_subject, source_received_at, scheduled_at "
        f"FROM application_events WHERE {where_sql} "
        f"ORDER BY recorded_at ASC, id ASC LIMIT ?",
        [*params, limit + 1],
    )
    rows = [dict(r) for r in await cur.fetchall()]
    truncated = len(rows) > limit
    rows = rows[:limit]

    events = []
    app_ids: list[int] = []
    seen = set()
    for r in rows:
        events.append(
            {
                "id": r["id"], "application_id": r["application_id"], "event_type": r["event_type"],
                "detail": r["detail"], "payload": _display_payload(r["event_type"], r["payload"]),
                "occurred_at": r["occurred_at"], "recorded_at": r["recorded_at"],
                "recorded_by": r["recorded_by"], "corrects_event_id": r["corrects_event_id"],
                "source": _event_source(r), "scheduled_at": r["scheduled_at"] or None,
            }
        )
        if r["application_id"] not in seen:
            seen.add(r["application_id"])
            app_ids.append(r["application_id"])

    await _attach_contact_names(db, user_id, events)

    applications: list[dict[str, Any]] = []
    if app_ids:
        placeholders = ",".join("?" for _ in app_ids)
        cur = await db._db.execute(
            f"SELECT id, job_title, job_company, status, last_event_at FROM applications "  # noqa: S608
            f"WHERE id IN ({placeholders})",
            app_ids,
        )
        applications = [dict(r) for r in await cur.fetchall()]

    next_since = rows[-1]["recorded_at"] if rows else since_val
    next_after_id = rows[-1]["id"] if rows else after_id

    # "Needs you" - open asks are ALWAYS carried, whatever `since` says: an
    # unanswered question does not expire because the cursor moved past it.
    from src.services.applications.asks import list_asks  # noqa: PLC0415

    open_asks = await list_asks(db, user_id, "open", limit=settings.ASKS_WHATS_NEW_MAX)
    # S7 - likewise carried: an applied job still without proof (the assistant asks once).
    from src.services.applications.proof import proof_missing  # noqa: PLC0415

    missing_proof = await proof_missing(db, user_id, datetime.now(timezone.utc))
    # Gap 10 - likewise carried: interviews in the next 7 days, and those of the last 3 with no outcome yet.
    from src.services.applications.agenda import interview_agenda  # noqa: PLC0415

    interviews = await interview_agenda(db, user_id)

    # Feature 10 - unmatched mail, likewise carried whatever `since` says.
    from src.services.applications.inbox_items import for_whats_new  # noqa: PLC0415

    return {
        "now": now, "since": since_val, "events": events, "applications": applications,
        "next_since": next_since, "next_after_id": next_after_id, "truncated": truncated,
        "open_asks": open_asks, "proof_missing": missing_proof, "interviews": interviews,
        "inbox_items": await for_whats_new(db, user_id),
    }


# ── R10 — export_history ──────────────────────────────────────────────────────


async def _artifact_metadata(db: JobDatabase, application_id: int, *, include_text: bool) -> list[dict[str, Any]]:
    cur = await db._db.execute(
        "SELECT id, kind, version_no, made_by, model, profile_version, label, chars, created_at, text, "
        "ats_score, ats_notes FROM application_artifacts WHERE application_id = ? ORDER BY kind, version_no",
        (application_id,),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    out = []
    meta_cols = (
        "id", "kind", "version_no", "made_by", "model", "profile_version",
        "label", "chars", "created_at", "ats_score", "ats_notes",
    )
    for r in rows:
        entry = {k: r[k] for k in meta_cols}
        if include_text:
            entry["text"] = r["text"]
        out.append(entry)
    return out


async def _list_profile_edits_for_export(
    db: JobDatabase, user_id: str
) -> tuple[list[dict[str, Any]], bool]:
    """Slice 4 R8/R10 — export_history's ``profile_edits``, oldest first,
    INCLUDING a clear (``value IS NULL``) — unlike
    ``services.profile.edits.current_overlay`` (the live, non-cleared
    overlay), this IS the history: "nothing is deleted — the clear is a
    row" (frozen test ``test_clear_reveals_extraction_and_keeps_the_
    history``).

    BOUNDED like every other blob this export carries (review finding S3):
    the query takes the NEWEST ``EXPORT_HISTORY_MAX_PROFILE_EDITS`` rows
    (``ORDER BY id DESC LIMIT ?``) and the result is reversed so the output
    stays oldest-first. Newest-wins is the right end to keep — the current
    overlay is made of the newest row per path, so an export truncated at the
    old end still explains the profile the caller is looking at. Returns
    ``(rows, truncated)``.
    """
    limit = settings.EXPORT_HISTORY_MAX_PROFILE_EDITS
    cur = await db._db.execute(
        "SELECT path, value, set_by, set_at FROM profile_edits WHERE user_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (user_id, limit + 1),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    truncated = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()  # newest N, rendered oldest-first
    return [
        {
            "path": r["path"],
            "value": json.loads(r["value"]) if r.get("value") is not None else None,
            "set_by": r["set_by"],
            "set_at": r["set_at"],
        }
        for r in rows
    ], truncated


async def _current_assistant_notes(user_id: str) -> list[str]:
    """The notes as the profile reads them now; ``[]`` with no profile (rule #29).

    ``storage.load_profile`` is the one read door (it applies the overlay) and
    it is synchronous, so it runs off the event loop.
    """
    import asyncio  # noqa: PLC0415 — stdlib, only this reader needs it

    from src.services.profile.storage import load_profile  # noqa: PLC0415 — profile ↔ spine import cycle

    profile = await asyncio.to_thread(load_profile, user_id)
    if profile is None:
        return []
    return list(profile.preferences.assistant_notes or [])


async def user_score_line(user_id: str) -> Optional[int]:
    """The user's OWN score line (``assistant_settings.apply_min_score``) as the
    profile reads it now, or ``None`` when they have not set one (no default)."""
    import asyncio  # noqa: PLC0415 — stdlib, only this reader needs it

    from src.services.profile.storage import load_profile  # noqa: PLC0415 — profile ↔ spine import cycle

    profile = await asyncio.to_thread(load_profile, user_id)
    if profile is None or profile.assistant_settings.apply_min_score is None:
        return None
    return int(profile.assistant_settings.apply_min_score)


async def export_history(
    db: JobDatabase,
    user_id: str,
    *,
    since: Optional[str] = None,
    include_text: bool = False,
    include_unlinked: bool = True,
    unlinked_after_id: Optional[int] = None,
) -> dict[str, Any]:
    """R10/S8 — bounded on applications AND bytes; rate-limited per USER.

    ``include_unlinked``/``unlinked_after_id`` (bug fix, coordinator review
    2026-09-26) page the cold (job-less) contacts INDEPENDENTLY of the
    applications cursor (``since``) — the old code inferred "first page" from
    ``since`` being empty, which silently returned no cold contacts at all on
    a caller's very first call if it happened to pass one, and had no way to
    ever page past a byte-budget cutoff. ``include_unlinked=False`` skips
    them entirely (e.g. a caller paging applications who already has every
    cold contact from an earlier call)."""
    # Lazy — see get_application_detail's identical import above.
    from src.services.applications.contacts import (  # noqa: PLC0415
        _full_contact_view,
        list_contacts,
        list_unlinked_contacts_page,
    )

    key = f"export_history:{user_id}"
    if not rate_limit.check_and_record(
        key, max_in_window=settings.EXPORT_HISTORY_MAX_PER_HOUR, window_seconds=3600
    ):
        raise SpineError(429, "export rate limit exceeded; try again in an hour")

    where = ["user_id = ?"]
    params: list[Any] = [user_id]
    if since:
        where.append("updated_at >= ?")
        params.append(since)
    where_sql = " AND ".join(where)

    cur = await db._db.execute(
        f"SELECT id, job_id, status, job_title, job_company, created_at, updated_at, last_event_at, "  # noqa: S608
        f"job_country, job_remote, job_found_on, ad_posted_at, employer_published_at "
        f"FROM applications WHERE {where_sql} ORDER BY updated_at ASC, id ASC",
        params,
    )
    all_rows = [dict(r) for r in await cur.fetchall()]

    out_apps: list[dict[str, Any]] = []
    total_bytes = 0
    truncated = False
    next_since: Optional[str] = None
    for r in all_rows:
        if len(out_apps) >= settings.EXPORT_HISTORY_MAX_APPLICATIONS:
            truncated = True
            next_since = r["updated_at"]
            break
        app_blob = {
            "id": r["id"], "job_id": r["job_id"], "status": r["status"],
            "job_title": r["job_title"] or "", "job_company": r["job_company"] or "",
            "created_at": r["created_at"], "updated_at": r["updated_at"], "last_event_at": r.get("last_event_at"),
            **_job_facts_for(r),
            "events": await list_events_for_display(db, r["id"]),
            "artifacts": await _artifact_metadata(db, r["id"], include_text=include_text),
            "receipts": await _list_receipts_for_application(db, user_id, r["id"], include_text=include_text),
            "contacts": await list_contacts(db, user_id, r["id"], include_text=include_text),
        }
        blob_size = len(json.dumps(app_blob, default=str).encode("utf-8"))
        if out_apps and total_bytes + blob_size > settings.EXPORT_HISTORY_MAX_BYTES:
            truncated = True
            next_since = r["updated_at"]
            break
        out_apps.append(app_blob)
        total_bytes += blob_size

    # S3 — the edits blob is part of the payload, so it is part of the byte
    # figure. Counting only the applications made `bytes` describe something
    # the caller never receives on its own.
    profile_edits, edits_truncated = await _list_profile_edits_for_export(db, user_id)
    total_bytes += len(json.dumps(profile_edits, default=str).encode("utf-8"))
    # The user's standing instructions to their assistant, as they read NOW
    # (base + overlay, the same door every reader uses). Their full change
    # history is already in `profile_edits` under `preferences.assistant_notes`.
    assistant_notes = await _current_assistant_notes(user_id)
    total_bytes += len(json.dumps(assistant_notes).encode("utf-8"))
    # S2 (owner decision 2026-10-08) — the queue of setting changes the user's
    # assistants asked for, waiting and decided: part of the user's own record.
    from src.services.profile import setting_requests  # noqa: PLC0415 — lazy, like current_profile_version_id

    setting_request_rows = await setting_requests.export_rows(db, user_id)
    total_bytes += len(json.dumps(setting_request_rows, default=str).encode("utf-8"))

    # Owner decision, 2026-09-25 — cold contacts (no application) have no
    # home in `applications[].contacts`; this is where the export shows them.
    # Bug fix (coordinator review, 2026-09-26) — paged by `unlinked_after_id`
    # (own cursor, independent of `since`), fetched from Postgres a page at a
    # time (never the whole table), and the byte-budget check applies to the
    # FIRST row of the page too: an oversized contact is retried once with
    # its own text stripped, and if it STILL doesn't fit it is left OUT with
    # a cursor pointing back at it — the budget is never overshot.
    unlinked_contacts: list[dict[str, Any]] = []
    unlinked_next_after_id: Optional[int] = None
    if include_unlinked:
        after_id = unlinked_after_id if unlinked_after_id is not None else 0
        page_limit = settings.EXPORT_HISTORY_UNLINKED_PAGE_SIZE
        fetched = await list_unlinked_contacts_page(db, user_id, after_id=after_id, limit=page_limit + 1)
        more_beyond_page = len(fetched) > page_limit
        candidates = fetched[:page_limit]
        for row in candidates:
            view = await _full_contact_view(db, user_id, row, include_text=include_text)
            size = len(json.dumps(view, default=str).encode("utf-8"))
            if total_bytes + size > settings.EXPORT_HISTORY_MAX_BYTES:
                if include_text:
                    view = await _full_contact_view(db, user_id, row, include_text=False)
                    size = len(json.dumps(view, default=str).encode("utf-8"))
                if total_bytes + size > settings.EXPORT_HISTORY_MAX_BYTES:
                    unlinked_next_after_id = row["id"]
                    break
            unlinked_contacts.append(view)
            total_bytes += size
        else:
            if more_beyond_page:
                unlinked_next_after_id = candidates[-1]["id"] + 1

    result: dict[str, Any] = {
        "applications": out_apps, "truncated": truncated, "bytes": total_bytes,
        "profile_edits": profile_edits, "profile_edits_truncated": edits_truncated,
        "assistant_notes": assistant_notes, "assistant_setting_requests": setting_request_rows,
        "unlinked_contacts": unlinked_contacts,
        "unlinked_contacts_truncated": unlinked_next_after_id is not None,
    }
    if unlinked_next_after_id is not None:
        result["unlinked_next_after_id"] = unlinked_next_after_id
    if truncated:
        result["next_since"] = next_since
    get_audit_logger().info(
        "history_exported",
        extra={
            "event": "history_exported", "applications": len(out_apps),
            "bytes": total_bytes, "truncated": truncated,
        },
    )
    return result

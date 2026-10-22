"""``email_drafted``: the exact email an EMAIL application is about to send (gap roadmap slice 31, G078).

Email applications used to bypass the submit gate entirely: nothing named the recipient or the files,
and the receipt had no recipient column (a hole in hard line 1). Now:

* the assistant records ``email_drafted`` {to, subject, attachments: [{artifact_id, sha256}],
  gmail_draft_id?} before it calls ``check_submit`` with ``form_url = mailto:<to>``. The body is never
  stored. ``sha256`` is :func:`kit.sha256_text` of the artifact's TEXT - the hash ``get_application_kit``
  hands out for the CV and letter, and the one the CV approval already uses (a PDF's bytes are not stable);
* the gate stops ``email_not_drafted`` without a draft (``assistant_settings.may_submit``), and the web
  Send yes is bound to the newest draft's recipient and attachments (:func:`fingerprint`,
  :func:`draft_changed`; ``kit.approval_stale`` answers ``email``);
* ``record_application`` with ``channel=email`` stores the recipient (from the draft when not given)
  and the Gmail message id, and freezes the draft's attachments as the receipt's ``files``.

Logs carry the recipient's DOMAIN only, never the address, the subject or any text.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any, Optional

from src.services.applications import spine
from src.services.applications.spine import SpineError
from src.services.profile import assistant_settings as settings_rules

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

EVENT_TYPE = "email_drafted"
REQUIRED = frozenset({"to", "subject", "attachments"})
OPTIONAL = frozenset({"gmail_draft_id"})
SUBJECT_MAX = 300
ATTACHMENTS_MAX = 5
GMAIL_ID_MAX = 200
ATTACHMENT_KEYS = frozenset({"artifact_id", "sha256"})
ATTACHMENT_KINDS = ("cv", "cover_letter")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
# A Gmail draft / message id or an RFC 5322 Message-ID: printable ASCII, no spaces.
_GMAIL_ID_RE = re.compile(r"^[\x21-\x7e]+$")


def _subject(raw: Any, problems: list[str]) -> str:
    if not isinstance(raw, str):
        problems.append("subject must be text")
        return ""
    text = " ".join(spine._strip_control_chars(raw).split())
    if not text or len(text) > SUBJECT_MAX:
        problems.append(f"subject must be 1 to {SUBJECT_MAX} characters")
    return text


def _gmail_id(raw: Any, name: str, problems: list[str]) -> str:
    text = raw.strip() if isinstance(raw, str) else ""
    if not text or len(text) > GMAIL_ID_MAX or not _GMAIL_ID_RE.match(text):
        problems.append(f"{name} must be 1 to {GMAIL_ID_MAX} printable characters with no spaces")
    return text


def _attachments(raw: Any, problems: list[str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > ATTACHMENTS_MAX:
        problems.append(f"attachments must be a list of at most {ATTACHMENTS_MAX} items ([] = none)")
        return []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != ATTACHMENT_KEYS:
            problems.append(f"attachments[{i}] needs keys artifact_id and sha256 and nothing else")
            continue
        artifact_id, sha = item["artifact_id"], item["sha256"]
        if isinstance(artifact_id, bool) or not isinstance(artifact_id, int) or artifact_id < 1:
            problems.append(f"attachments[{i}].artifact_id must be a whole number (from get_application_kit)")
            continue
        if not isinstance(sha, str) or not _SHA_RE.match(sha):
            problems.append(f"attachments[{i}].sha256 must be the 64-character hex sha256 from get_application_kit")
            continue
        if any(a["artifact_id"] == artifact_id for a in out):
            problems.append(f"attachments[{i}] names artifact {artifact_id} twice")
            continue
        out.append({"artifact_id": artifact_id, "sha256": sha.lower()})
    return out


def payload_problems(payload: dict[str, Any]) -> list[str]:
    """EVERY shape problem in an ``email_drafted`` payload (no database needed). ``[]`` = well formed.
    A ``body`` (or any other key) is refused: Job360 never stores the email's text."""
    problems: list[str] = []
    missing, unknown = sorted(REQUIRED - set(payload)), sorted(set(payload) - REQUIRED - OPTIONAL)
    if missing:
        problems.append(f"missing payload keys: {missing}")
    if unknown:
        problems.append(
            f"unknown payload keys: {unknown} (allowed: {sorted(REQUIRED | OPTIONAL)}; the body is never stored)"
        )
    if "to" in payload:
        to = payload["to"]
        if settings_rules.clean_email_address(to if isinstance(to, str) else None) is None:
            problems.append("to must be ONE email address, like jobs@company.com (no name, no list)")
    if "subject" in payload:
        _subject(payload["subject"], problems)
    if "attachments" in payload:
        _attachments(payload["attachments"], problems)
    if "gmail_draft_id" in payload:
        _gmail_id(payload["gmail_draft_id"], "gmail_draft_id", problems)
    return problems


def _text_sha(text: str) -> str:
    """The same hash as ``kit.sha256_text`` (kit imports this module, so it is not imported back)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


async def check(
    db: JobDatabase, user_id: str, application_id: int, payload: dict[str, Any], actor: str
) -> dict[str, Any]:
    """The ``email_drafted`` payload to STORE: the address lower-cased, the subject cleaned, and each
    attachment checked against THIS application's artifacts (a CV or cover letter, 404 otherwise) with
    its ``sha256`` equal to the hash of that artifact's text (409 otherwise), stored with its ``kind`` and
    ``version``. Raises ``SpineError``; the caller (``kit.check_kit_event``) has refused a bad shape."""
    problems: list[str] = []
    to = settings_rules.clean_email_address(payload.get("to") if isinstance(payload.get("to"), str) else None)
    subject = _subject(payload.get("subject"), problems)
    attachments = _attachments(payload.get("attachments"), problems)
    if problems or to is None:
        raise SpineError(422, problems[0] if problems else "to must be ONE email address")
    stored_attachments: list[dict[str, Any]] = []
    for item in attachments:
        row = await spine.get_artifact(db, user_id, application_id, int(item["artifact_id"]))
        if row is None or row["kind"] not in ATTACHMENT_KINDS:
            raise SpineError(404, f"attachment artifact {int(item['artifact_id'])} is not a CV or cover letter "
                                  "of this application")
        if _text_sha(row["text"]) != item["sha256"]:
            raise SpineError(
                409, f"attachment artifact {int(row['id'])}: sha256 is not that version's hash - "
                "read get_application_kit again and attach what it lists",
            )
        stored_attachments.append({
            "artifact_id": int(row["id"]), "sha256": item["sha256"], "kind": row["kind"],
            "version": int(row["version_no"]),
        })
    stored: dict[str, Any] = {"to": to, "subject": subject, "attachments": stored_attachments, "by": actor}
    if "gmail_draft_id" in payload:
        stored["gmail_draft_id"] = _gmail_id(payload["gmail_draft_id"], "gmail_draft_id", [])
    return stored


def recipient_domain(address: str) -> str:
    """The only part of a recipient a log line may carry."""
    return settings_rules.email_domain(address or "")


def audit_fields(stored: dict[str, Any]) -> dict[str, Any]:
    """What the ``email_drafted`` audit line carries: the attachment count and the recipient's DOMAIN."""
    to = stored.get("to")
    return {
        "attachments_count": len(stored.get("attachments") or []),
        "to_domain": recipient_domain(to) if isinstance(to, str) else "",
    }


# ── Reads: the gate, the approval fingerprint, the receipt ────────────────────


async def newest_draft(
    db: JobDatabase, user_id: str, application_id: int, *, since_applied: bool = False
) -> Optional[dict[str, Any]]:
    """This application's NEWEST ``email_drafted`` as ``{event_id, to, subject, attachments,
    gmail_draft_id}``, or ``None``. ``since_applied``: only a draft recorded after the newest ``applied``
    (the receipt; an earlier submission's draft is not this one's). An unreadable payload is skipped."""
    cur = await db._db.execute(
        "SELECT e.id, e.payload FROM application_events e WHERE e.user_id = ? AND e.application_id = ? "
        "AND e.event_type = 'email_drafted' AND (? = 0 OR e.id > COALESCE((SELECT MAX(a.id) "
        "FROM application_events a WHERE a.user_id = ? AND a.application_id = ? AND a.event_type = 'applied'), 0)) "
        "ORDER BY e.id DESC LIMIT 20",
        (user_id, application_id, 1 if since_applied else 0, user_id, application_id),
    )
    for raw in await cur.fetchall():
        row = dict(raw)
        try:
            p = json.loads(row["payload"]) if isinstance(row["payload"], str) else dict(row["payload"] or {})
        except (TypeError, ValueError):
            continue
        to = p.get("to")
        atts = p.get("attachments")
        if not isinstance(to, str) or not isinstance(atts, list):
            continue
        return {
            "event_id": int(row["id"]), "to": to, "subject": str(p.get("subject") or ""),
            "attachments": [a for a in atts if isinstance(a, dict)],
            "gmail_draft_id": str(p.get("gmail_draft_id") or ""),
        }
    return None


def _attachment_key(raw: Any) -> list[tuple[int, str]]:
    """Attachments compared as a SET of (artifact id, sha256): order never matters."""
    out: list[tuple[int, str]] = []
    for a in raw if isinstance(raw, list) else []:
        if isinstance(a, dict):
            try:
                out.append((int(a["artifact_id"]), str(a.get("sha256") or "")))
            except (KeyError, TypeError, ValueError):
                continue
    return sorted(out)


def fingerprint(draft: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The email part of what a web Send yes is bound to: the newest draft's event id, its ``to`` and its
    attachments (artifact ids + shas). All ``None`` / ``[]`` when there is no draft."""
    if draft is None:
        return {"email_drafted_event_id": None, "email_to": None, "attachments": []}
    return {
        "email_drafted_event_id": int(draft["event_id"]), "email_to": draft["to"],
        "attachments": [{"artifact_id": i, "sha256": s} for i, s in _attachment_key(draft["attachments"])],
    }


def draft_changed(stamp: dict[str, Any], now_fp: dict[str, Any]) -> bool:
    """True when the yes stamped ``stamp`` no longer covers the newest draft (``now_fp``): there is a draft
    now and the yes named none (it was given before the draft existed), or the draft's ``to`` or any
    attachment (id or sha) differs. A new draft with the same recipient and files (a subject edit) still
    holds. No draft now = nothing to compare (the email gate stops ``email_not_drafted`` by itself)."""
    if now_fp.get("email_drafted_event_id") is None:
        return False
    if stamp.get("email_drafted_event_id") is None:
        return True
    return bool(
        stamp.get("email_to") != now_fp.get("email_to")
        or _attachment_key(stamp.get("attachments")) != _attachment_key(now_fp.get("attachments"))
    )


def receipt_uploads(draft: dict[str, Any]) -> list[dict[str, Any]]:
    """A draft's attachments in the shape ``uploads.uploads_since_last_receipt`` returns, so the receipt
    names and freezes them like uploaded files: attaching a file to an email IS sending it (``host`` set).
    ``sha256`` here is of the artifact TEXT and ``fmt`` is ``""`` (the email's file format is not known)."""
    out: list[dict[str, Any]] = []
    for a in draft["attachments"]:
        if a.get("kind") not in ATTACHMENT_KINDS:
            continue
        try:
            artifact_id = int(a["artifact_id"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({
            "event_id": int(draft["event_id"]), "kind": a["kind"], "artifact_id": artifact_id,
            "sha256": str(a.get("sha256") or ""), "fmt": "", "host": "email", "source": EVENT_TYPE,
        })
    return out


def clean_recipient(raw: str) -> str:
    """``record_application``'s ``recipient``: '' (not given) or ONE address, lower-cased. 422 otherwise."""
    if not (raw or "").strip():
        return ""
    address = settings_rules.clean_email_address(raw)
    if address is None:
        raise SpineError(422, "recipient must be ONE email address, like jobs@company.com (no name, no list)")
    return address


def clean_message_id(raw: str) -> str:
    """``record_application``'s ``gmail_message_id``: '' or 1-200 printable characters, no spaces. 422 otherwise."""
    if not (raw or "").strip():
        return ""
    problems: list[str] = []
    text = _gmail_id(raw, "gmail_message_id", problems)
    if problems:
        raise SpineError(422, problems[0])
    return text

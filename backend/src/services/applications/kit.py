"""The application kit (S3, owner decisions 2026-10-08).

Everything an assistant needs to fill ONE application form, read in one call:
the CV and cover letter of THAT application (never another job's), every stored
answer with where it came from, what is still missing for this job's country,
a short-lived file link, and the human-in-the-loop state (did the user see the
CV, did they say yes to sending it, is it a duplicate, is anyone else on it).

Job360 stores; the assistant judges. This module never writes a CV, never
guesses an answer, and never stores a password: the only account memory is a
host name (``site_account`` / ``account_needed`` events).

One module, four jobs:

* :func:`build_kit` - the kit itself (``GET /applications/{id}/kit`` and MCP
  ``get_application_kit`` both reach it through the route function);
* :func:`check_kit_event` - the payload and who-may-write rules for the S3 event
  types (``record_event`` route and the web buttons share it);
* :func:`gate_facts` / :func:`submit_verdict` - the facts ``may_submit`` needs;
* :func:`live_hold` - another assistant session's hold (kit warning, gate ask);
* :func:`duplicate_facts` - the duplicate flag (kit, receipt and gate).

Logs name the user, the application and a closed result. They never carry a
token, a link, CV text or an answer value.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import secrets
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlsplit

from src.core import settings
from src.services.applications import blocked as blocked_service
from src.services.applications import email_channel, event_examples, spine
from src.services.applications.authorship import actor_for
from src.services.applications.job_checks import checks_for_application
from src.services.applications.spine import SpineError
from src.services.profile import assistant_settings as settings_rules
from src.services.profile.answer_match import fits_country
from src.services.profile.edits import WEB_ACTOR, is_assistant_actor, is_confirmed
from src.services.profile.regions import region_of
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

SERVER_ONLY_EVENTS = frozenset({"kit_read"})
KIT_EVENT_TYPES = frozenset({
    "kit_read", "cv_seen", "submit_approved", "submit_declined", "autofill_set", "duplicate_cleared",
    "form_filled", "form_swept", "hold_released", "site_account", "account_needed", "form_checked",
    "submit_attempted", "submit_resolved",
    # Gap roadmap slice 31 (G078): the exact email an EMAIL application sends (services/applications/email_channel).
    "email_drafted",
})
# `blocked` is NOT here (review 2026-10-18): a block recorded after the click cannot prove the click sent
# nothing, and the user's later `unblocked` would let the same application be submitted twice.
SUBMIT_MARK_EVENTS = ("submit_attempted", "applied", "submit_resolved")
HOLD_RELEASE_REASONS = frozenset({"done", "blocked", "stopped", "failed"})
SEEN_WHERE = frozenset({"web", "chat"})
EQUALITY_LABEL = "equality / voluntary"
NO_CV_REASON = "no CV saved for this application"
NO_LETTER_REASON = "no cover letter saved for this application"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")

KIT_INSTRUCTIONS: tuple[str, ...] = (
    "Use only the answers in this kit. Anything in `missing`: ask the user ONCE, in one message. Never guess.",
    "An answer with state 'inferred' was worked out by an assistant - confirm it with the user once, "
    "then save it again with source user_said. 'confirmed' answers are the user's: use them. "
    "'relayed' = you saved the user's chat word and they have not confirmed it on Job360 yet: usable, "
    "but say once that they can confirm it there. "
    "A 'sensitive' answer (salary, equality, sanctions, visa / right-to-work status) is shown to the user "
    "and confirmed on THIS form before it is used; never fill it silently. Ask two things separately: "
    "may I use it on this form, and may I remember it for later. Save it with remember: true only after "
    "a yes to the second; `remembered_at` set = the user agreed to that.",
    "Upload file: desktop or Claude Code - download `file.url` to a local file and upload that. "
    "Chat app - ask the user to attach the PDF once for this job. Otherwise paste `text` if the form allows, "
    "else the user uploads it by hand. An expired link: call the kit again.",
    "Show the CV to the user. When the user says it is OK in chat, record_event cv_seen with where=chat. "
    "When the user says yes to submitting, record_event submit_approved with where=chat.",
    "`autofill` deny: do not type into the form - give the user the kit answers to paste. Still record form_filled.",
    "After filling the form, record_event form_filled {form_url, fields_count}. "
    "Then re-read EVERY field on the page, fix what is wrong, and record_event form_swept "
    "{fields_checked, fixed}. Then call check_submit (it says stop until both are recorded).",
    "Before typing into a form, read every question on the LIVE form and call check_form with them; "
    "the kit's `missing` covers only country and contact facts.",
    "Pass a `session` label (same one all conversation) to get_application_kit and check_submit. "
    "`hold` set means another session is working this application: tell the user and stop unless they say "
    "continue. When you finish or hand over, record `hold_released` {reason: done|stopped}.",
    "`duplicate` flagged: warn before any work.",
    "Account site: stop. The user signs up and signs in themselves (never ask for or store a password), "
    "then record_event site_account {host} and continue. Hit a sign-in wall on any other site: "
    "record_event account_needed {host}.",
)


# ── Small pure helpers ───────────────────────────────────────────────────────


def sha256_text(text: str) -> str:
    """The hash a CV is identified by: sha256 of its TEXT (PDF bytes are not stable)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def answers_sha256(payload: dict[str, Any]) -> Optional[str]:
    """What a ``form_filled`` put on the form, as one hash: every (question, answer) pair, case and spacing
    folded, order-free. Never the ``source`` labels, the field count or the event id (owner 2026-10-26: a
    re-recorded fill with the same answers kept going stale). None when the fill lists no readable answers."""
    typed = payload.get("answers")
    if not isinstance(typed, list) or not typed:
        return None
    def fold(text: Any) -> str:
        return " ".join(str(text).split()).casefold()

    pairs = sorted(
        (fold(a["question"]), fold(a["answer"])) for a in typed if isinstance(a, dict) and "question" in a
        and "answer" in a
    )
    return sha256_text(json.dumps(pairs)) if len(pairs) == len(typed) else None


def hash_token(token: str) -> str:
    """What is stored for a file-link token (the token itself never is)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ascii_part(value: Any) -> str:
    """One name part as ASCII letters/digits only (accents folded, spaces -> ``-``)."""
    folded = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "-", folded).strip("-")


_NAMED_KINDS = {"cv": "CV", "cover_letter": "Cover_Letter"}
_STEM_MAX = 120  # characters of an uploaded file-name stem (ASCII, so also bytes)


def legal_file_stem(profile: Any, kind: str) -> Optional[str]:
    """``Ada_Lovelace_CV`` / ``Ada_Lovelace_Cover_Letter`` from the user's legal names
    (``user_info.contact``), else ``cv_data.name``; ``None`` when no name is known."""
    suffix = _NAMED_KINDS.get(kind)
    if suffix is None or profile is None:
        return None
    contact = getattr(getattr(profile, "user_info", None), "contact", None) or {}
    first = _ascii_part(contact.get("legal_first_name"))
    last = _ascii_part(contact.get("legal_last_name"))
    if not (first and last):
        words = str(getattr(getattr(profile, "cv_data", None), "name", "") or "").split()
        if len(words) >= 2:
            first, last = _ascii_part(words[0]), _ascii_part("-".join(words[1:]))
        else:
            return None
    if not (first and last):
        return None
    return f"{first}_{last}_{suffix}"


async def named_stem(user_id: str, kind: str) -> Optional[str]:
    """``legal_file_stem`` for the user's saved profile; ``None`` when no name is known."""
    import asyncio  # noqa: PLC0415

    try:
        from src.services.profile.storage import load_profile  # noqa: PLC0415 - rule #16

        profile = await asyncio.to_thread(load_profile, user_id)
    except Exception:  # noqa: BLE001 - a name lookup must never break a download
        profile = None
    stem = legal_file_stem(profile, kind)
    if stem is None or len(stem) <= _STEM_MAX:
        return stem
    # An upload writes this name to disk (NAME_MAX 255): keep the start of the name, always the kind.
    suffix = _NAMED_KINDS[kind]
    return f"{stem[: -len(suffix) - 1][: _STEM_MAX - len(suffix) - 1].rstrip('_-')}_{suffix}"


async def download_stem(user_id: str, company: str, kind: str, version_no: int) -> str:
    """The one download/kit file-name stem: the user's legal name, else ``file_stem``."""
    return await named_stem(user_id, kind) or file_stem(company, kind, version_no)


def file_stem(company: str, kind: str, version_no: int) -> str:
    """``northwind-cv-v3`` - the one download file name rule (web + link)."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(company or "").lower()).strip("-")[:40]
    return "-".join(p for p in (slug, kind.replace("_", "-"), f"v{version_no}") if p)


def norm_url(url: str) -> str:
    """Lowercase scheme + host, drop the fragment, strip a trailing slash. The query is kept (two
    different ``?id=`` are two different jobs) minus tracking keys and secret-named keys such as a
    token or session id (gap-audit #18, ``job_checks.redact_url``), so a receipt stored in full still
    matches one stored redacted. An unparseable value is compared as typed."""
    text = (url or "").strip()
    if not text:
        return ""
    from src.services.applications.job_checks import redact_url  # noqa: PLC0415

    red = redact_url(text)
    if not red:
        return text
    parts = urlsplit(red)
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}{query}"


def host_needs_account(host: Optional[str], learned: set[str]) -> bool:
    """True for a seed host (``ACCOUNT_REQUIRED_HOST_SUFFIXES``, subdomains
    included, label-boundary match so ``notworkday.com`` does not hit) or a host
    this user's own ``account_needed`` events taught us."""
    if not host:
        return False
    if host in learned:
        return True
    for suffix in settings.ACCOUNT_REQUIRED_HOST_SUFFIXES:
        s = suffix.strip().lower().lstrip(".")
        if s and (host == s or host.endswith("." + s)):
            return True
    return False


def _link_on(url: Any, domain: str) -> bool:
    """True when ``url``'s host is ``domain`` or a subdomain of it (never a substring match)."""
    host = settings_rules.parse_site_host(str(url) if url else None)
    return bool(host) and (host == domain or str(host).endswith("." + domain))


def _present(value: Any) -> bool:
    """Rule #29: None / "" / [] / {} are absent. ``False`` and ``0`` are answers."""
    if value is None:
        return False
    if isinstance(value, (str, list, dict, tuple)):
        return len(value) > 0
    return True


def _json(raw: Any) -> dict[str, Any]:
    try:
        out = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


# ── Event reads ──────────────────────────────────────────────────────────────


async def _events(
    db: JobDatabase, user_id: str, application_id: int, types: tuple[str, ...]
) -> list[dict[str, Any]]:
    """This application's events of the given types, NEWEST first, payload parsed."""
    marks = ",".join("?" for _ in types)
    cur = await db._db.execute(
        "SELECT id, event_type, payload, recorded_at, recorded_by FROM application_events "  # noqa: S608
        f"WHERE application_id = ? AND user_id = ? AND event_type IN ({marks}) ORDER BY id DESC",
        (application_id, user_id, *types),
    )
    rows = []
    for r in await cur.fetchall():
        d = dict(r)
        d["payload"] = _json(d.get("payload"))
        rows.append(d)
    return rows


def _who(event: dict[str, Any]) -> dict[str, Any]:
    p = event["payload"]
    by = p.get("by") or event.get("recorded_by") or ""
    return {"by": by, "where": p.get("where") or "", "at": event["recorded_at"]}


# ── Duplicates ───────────────────────────────────────────────────────────────


async def duplicate_facts(
    db: JobDatabase, user_id: str, app_row: dict[str, Any], now: datetime, *, include_self: bool = False
) -> dict[str, Any]:
    """The duplicate picture for one application. ``same_job``: ANOTHER
    application of the user's (never this one) whose receipt has the same job id
    or the same normalised URL. ``include_self`` is only for the moment a receipt is
    about to be written: an existing receipt on this very application then means a
    second receipt, a real duplicate. Every read leaves it False. ``same_role`` (bulk-apply D3):
    ANOTHER application of the user's with a surviving ``applied`` event inside
    ``DUPLICATE_ROLE_WINDOW_DAYS`` at the same ``normalized_company`` AND the same title
    (:func:`normalized_title`, exact equality only: a repost on another board has a new URL).
    ``same_company_30d``: OTHER
    applications of the user's with an ``applied`` event inside the window at the
    same company (the rule-1 ``normalized_company`` key; blank / ``unknown`` skipped).
    Flag: same_job beats same_role beats same_company beats ''. Never blocks anything."""
    application_id = int(app_row["id"])
    cur = await db._db.execute(
        "SELECT r.application_id, r.job_id, r.job_apply_url, r.sent_at, a.status "
        "FROM application_receipts r LEFT JOIN applications a ON a.id = r.application_id "
        "WHERE r.user_id = ? ORDER BY r.id DESC LIMIT 1000",
        (user_id,),
    )
    mine = norm_url(str(app_row.get("job_url") or ""))
    same_job: Optional[dict[str, Any]] = None
    for r in (dict(x) for x in await cur.fetchall()):
        if r["application_id"] == application_id and not include_self:
            continue  # an application is never a duplicate of itself
        hit = r["job_id"] == app_row.get("job_id") or (
            mine and norm_url(str(r["job_apply_url"] or "")) == mine
        )
        if hit:
            same_job = {
                "application_id": r["application_id"], "status": r["status"] or "", "applied_at": r["sent_at"],
            }
            break
    company = ""
    cur = await db._db.execute("SELECT normalized_company FROM jobs WHERE id = ?", (app_row.get("job_id"),))
    row = await cur.fetchone()
    if row is not None:
        company = str(dict(row).get("normalized_company") or "")
    same_role = await _same_role(db, user_id, application_id, company, now)
    same_company = 0
    if company and company != "unknown":
        cutoff = (now - timedelta(days=settings.DUPLICATE_COMPANY_WINDOW_DAYS)).isoformat()
        cur = await db._db.execute(
            "SELECT COUNT(DISTINCT e.application_id) FROM application_events e "
            "JOIN applications a ON a.id = e.application_id JOIN jobs j ON j.id = a.job_id "
            "WHERE e.user_id = ? AND e.event_type = 'applied' AND e.occurred_at >= ? "
            "AND e.application_id <> ? AND j.normalized_company = ?",
            (user_id, cutoff, application_id, company),
        )
        got = await cur.fetchone()
        same_company = int(got[0]) if got else 0
    flag = "same_job" if same_job else ("same_role" if same_role else ("same_company" if same_company else ""))
    return {"same_job": same_job, "same_role": same_role, "same_company_30d": same_company, "flag": flag}


_TITLE_PUNCT_RE = re.compile(r"[^\w]+", re.UNICODE)


def normalized_title(title: str) -> str:
    """A job title for the ``same_role`` match: casefolded, punctuation removed, spaces collapsed.
    Exact equality of this string is the whole rule (no fuzzy match, no seniority stripping)."""
    return " ".join(_TITLE_PUNCT_RE.sub(" ", unicodedata.normalize("NFKC", title or "").casefold()).split())


async def _same_role(
    db: JobDatabase, user_id: str, application_id: int, company: str, now: datetime
) -> Optional[dict[str, Any]]:
    """The newest OTHER application of this user with an ``applied`` event inside
    ``DUPLICATE_ROLE_WINDOW_DAYS`` (by the date it says OR the day it was recorded) at the same
    normalized company and the same normalized title. Corrected ``applied`` rows still count:
    this only ASKS, so back-dating or retracting cannot hide a repeat (security review 2026-10-18)."""
    if not company or company == "unknown":
        return None
    cur = await db._db.execute(
        "SELECT job_title FROM applications WHERE id = ? AND user_id = ?", (application_id, user_id)
    )
    mine_row = await cur.fetchone()
    mine = normalized_title(str(dict(mine_row).get("job_title") or "")) if mine_row else ""
    if not mine:
        return None
    cutoff = (now - timedelta(days=settings.DUPLICATE_ROLE_WINDOW_DAYS)).isoformat()
    cur = await db._db.execute(
        "SELECT e.application_id, a.job_title, a.status, e.occurred_at FROM application_events e "
        "JOIN applications a ON a.id = e.application_id JOIN jobs j ON j.id = a.job_id "
        "WHERE e.user_id = ? AND a.user_id = ? AND e.event_type = 'applied' "
        "AND (e.occurred_at >= ? OR e.recorded_at >= ?) "
        "AND e.application_id <> ? AND j.normalized_company = ? ORDER BY e.id DESC LIMIT 200",
        (user_id, user_id, cutoff, cutoff, application_id, company),
    )
    for r in (dict(x) for x in await cur.fetchall()):
        if normalized_title(str(r.get("job_title") or "")) == mine:
            return {
                "application_id": r["application_id"], "status": r["status"] or "",
                "applied_at": str(r["occurred_at"]),
            }
    return None


# ── The decision state (what the web buttons show, what the gate reads) ──────


def cv_mark_matches(ev: dict[str, Any], cv_id: Optional[int], cv_sha: Optional[str]) -> bool:
    """A ``cv_seen`` / ``submit_approved`` event marks the CV ``cv_id`` (and its text, ``cv_sha``; None =
    no CV to compare). Where it was given (chat or web) never matters: either counts."""
    p = ev["payload"]
    return ev["event_type"] in ("cv_seen", "submit_approved") and p.get("artifact_id") == cv_id and (
        cv_sha is None or p.get("sha256") == cv_sha
    )


async def controls_state(
    db: JobDatabase, user_id: str, app_row: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """Every human-in-the-loop decision on this application, each with who /
    where / when: the latest CV's seen + approved marks, a decline, autofill,
    the duplicate warning (with whether the user cleared it) and an open
    ``blocked`` record (S6)."""
    application_id = int(app_row["id"])
    cv = await spine.latest_artifact(db, user_id, application_id, "cv")
    events = await _events(
        db, user_id, application_id,
        ("cv_seen", "submit_approved", "submit_declined", "autofill_set", "duplicate_cleared"),
    )
    seen = approved = None
    stale: Optional[str] = None
    cv_id = cv["id"] if cv else None
    cv_sha = sha256_text(cv["text"]) if cv else ""
    for ev in events:
        if cv_mark_matches(ev, cv_id, cv_sha if cv else None):
            if seen is None:
                seen = _who(ev)
            if approved is None and stale is None and ev["event_type"] == "submit_approved":
                stale = await approval_stale(db, user_id, app_row, ev, now)
                approved = None if stale else _who(ev)
    newest_approval = next((e for e in events if e["event_type"] == "submit_approved"), None)
    newest_decline = next((e for e in events if e["event_type"] == "submit_declined"), None)
    declined = (
        _who(newest_decline)
        if newest_decline and (newest_approval is None or newest_decline["id"] > newest_approval["id"])
        else None
    )
    autofill_ev = next((e for e in events if e["event_type"] == "autofill_set"), None)
    mode = autofill_ev["payload"].get("mode") if autofill_ev else None
    cleared_ev = next((e for e in events if e["event_type"] == "duplicate_cleared"), None)
    dup = await duplicate_facts(db, user_id, app_row, now)
    return {
        "cv": (
            {
                "artifact_id": cv["id"], "version": cv["version_no"], "sha256": cv_sha,
                "seen": seen, "approved": approved,
            }
            if cv else None
        ),
        "declined": declined,
        "autofill": {
            "mode": mode if mode in ("allow", "deny") else "unset",
            **(_who(autofill_ev) if autofill_ev else {}),
        },
        "duplicate": {**dup, "cleared": _who(cleared_ev) if cleared_ev else None},
        "blocked": await blocked_service.open_block(db, user_id, application_id),
        # Slice 8 / 31: the part of the newest yes for this CV that no longer holds (None = it holds,
        # or there is none). Read by gate_facts; the web response model does not carry it.
        "approval_stale": stale,
    }


# ── The yes is bound to what the user saw (gap roadmap slice 8) ──────────────


async def _assistant_edit_id(db: JobDatabase, user_id: str) -> int:
    """The newest ``profile_edits`` id an ASSISTANT wrote for this user (0 = none). The user's own
    web edits are left out: an answer the user edited does not cancel their yes (owner decision
    2026-10-19, the ``type_first`` answers)."""
    cur = await db._db.execute(
        "SELECT MAX(id) FROM profile_edits WHERE user_id = ? AND set_by <> ?", (user_id, WEB_ACTOR)
    )
    got = await cur.fetchone()
    return int(got[0]) if got is not None and got[0] is not None else 0


async def approval_fingerprint(db: JobDatabase, user_id: str, application_id: int) -> dict[str, Any]:
    """What a web Send yes is bound to besides the CV id + hash: the cover letter, the newest
    ``form_filled``, the form page (:func:`latest_form_url`) and the newest assistant profile edit.
    Slice 31 (G078) adds the newest ``email_drafted`` (event id, ``email_to``, ``attachments``). The
    approve route stores it on ``submit_approved``; :func:`approval_stale` compares it again."""
    letter = await spine.latest_artifact(db, user_id, application_id, "cover_letter")
    fills = await _events(db, user_id, application_id, ("form_filled",))
    return {
        **email_channel.fingerprint(await email_channel.newest_draft(db, user_id, application_id)),
        "cover_letter_id": letter["id"] if letter else None,
        "cover_letter_sha256": sha256_text(letter["text"]) if letter else None,
        "form_filled_event_id": int(fills[0]["id"]) if fills else None,
        "form_answers_sha256": answers_sha256(fills[0]["payload"]) if fills else None,
        "form_url": await latest_form_url(db, user_id, application_id),
        "profile_edit_id": await _assistant_edit_id(db, user_id),
    }


def _approval_age_over(recorded_at: Any, now: datetime) -> bool:
    at = recorded_at if isinstance(recorded_at, datetime) else datetime.fromisoformat(str(recorded_at))
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return now - at > timedelta(minutes=settings.SUBMIT_APPROVAL_TTL_MINUTES)


async def approval_stale(
    db: JobDatabase, user_id: str, app_row: dict[str, Any], event: dict[str, Any], now: datetime
) -> Optional[str]:
    """``None`` while a ``submit_approved`` (already matched to the latest CV) still holds, else the
    part that no longer does: ``expired`` (older than ``SUBMIT_APPROVAL_TTL_MINUTES``),
    ``cover_letter``, ``form_fill``, ``form_page``, ``profile`` or (slice 31) ``email``: the newest
    ``email_drafted`` has another recipient or
    attachment than the draft the yes was given on. A yes without a ``fingerprint``
    (a chat yes, or one from before slice 8) is bound to the CV and the time only. A part that was
    unknown at the yes (no fill, no page yet) is not compared. A newer fill still holds when it differs
    only by answers that are now the user's own words (:func:`ready.refill_keeps_answers`)."""
    if _approval_age_over(event["recorded_at"], now):
        return "expired"
    stamp = event["payload"].get("fingerprint")
    if not isinstance(stamp, dict):
        return None
    application_id = int(app_row["id"])
    now_fp = await approval_fingerprint(db, user_id, application_id)
    for key in ("cover_letter_id", "cover_letter_sha256"):
        if stamp.get(key) != now_fp[key]:
            return "cover_letter"
    was_fill, fill = stamp.get("form_filled_event_id"), now_fp["form_filled_event_id"]
    # The content decides: a newer fill with the very same answers (a re-record, a refill) keeps the yes.
    was_sum = stamp.get("form_answers_sha256")
    same_answers = isinstance(was_sum, str) and was_sum == now_fp["form_answers_sha256"]
    if isinstance(was_fill, int) and fill != was_fill and not same_answers:
        from src.services.applications import ready  # noqa: PLC0415 - ready imports this module

        if fill is None or not await ready.refill_keeps_answers(db, user_id, app_row, was_fill, fill):
            return "form_fill"
    was_page, page = stamp.get("form_url"), now_fp["form_url"]
    if isinstance(was_page, str) and was_page and not (page and settings_rules._same_form_page(page, was_page)):
        return "form_page"
    if now_fp["profile_edit_id"] > int(stamp.get("profile_edit_id") or 0):
        return "profile"
    if email_channel.draft_changed(stamp, now_fp):
        return "email"
    return None


async def _filled_with_latest_cv(db: JobDatabase, user_id: str, application_id: int) -> bool:
    """A ``form_filled`` is on record AND it is newer than the latest CV saved for
    this application. A fill from an earlier attempt (before a new CV version)
    does not count: the record must describe the form being sent now."""
    events = await _events(db, user_id, application_id, ("form_filled", "artifact_saved"))
    fills = [e["id"] for e in events if e["event_type"] == "form_filled"]
    if not fills:
        return False
    cvs = [e["id"] for e in events if e["event_type"] == "artifact_saved" and e["payload"].get("kind") == "cv"]
    return not cvs or max(fills) > max(cvs)


async def _swept_after_fill(db: JobDatabase, user_id: str, application_id: int) -> bool:
    """A ``form_swept`` is on record AND it is newer than the latest ``form_filled``:
    the pre-submit re-read must describe the form being sent now."""
    events = await _events(db, user_id, application_id, ("form_filled", "form_swept"))
    fills = [e["id"] for e in events if e["event_type"] == "form_filled"]
    sweeps = [e["id"] for e in events if e["event_type"] == "form_swept"]
    return bool(fills and sweeps and max(sweeps) > max(fills))


def _page_of(event: dict[str, Any]) -> Optional[int]:
    page = event["payload"].get("page")
    return page if isinstance(page, int) and not isinstance(page, bool) else None


def _current_checks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest-first events with every UNPAGED one older than the newest PAGED one dropped; when the newest
    event has no page, only it is left (owner, 2026-10-23: newest form check wins). Paged events are kept."""
    if not events:
        return []
    if _page_of(events[0]) is None:
        return events[:1]
    newest_paged = max(e["id"] for e in events if _page_of(e) is not None)
    return [e for e in events if _page_of(e) is not None or e["id"] > newest_paged]


async def _sweep_shortfalls(db: JobDatabase, user_id: str, application_id: int) -> tuple[bool, bool, bool]:
    """``(count_short, unreadable, pages_incomplete)`` of :func:`_sweep_report`."""
    report = await _sweep_report(db, user_id, application_id)
    return report.short, report.unreadable, report.incomplete


@dataclass(frozen=True)
class SweepReport:
    short: bool = False
    unreadable: bool = False
    incomplete: bool = False
    # For the ``form_count_mismatch`` message: questions the sweep accounted for (``fields_checked`` plus the
    # newest ``form_filled``'s ``fields_skipped``) of how many check_form listed. None on a paged shortfall.
    counted: Optional[int] = None
    listed: Optional[int] = None


def _flat_count(
    check: Optional[dict[str, Any]], sweep: dict[str, Any], skipped: int
) -> tuple[bool, Optional[int], Optional[int]]:
    """``(short, counted, listed)`` of one unpaged sweep against one unpaged check. The blank optional fields
    the fill named in ``fields_skipped`` count as checked (owner, 2026-10-23: Job360 counts them itself)."""
    want = (check or {"payload": {}})["payload"].get("questions_count")
    got = sweep["payload"].get("fields_checked")
    if not (isinstance(want, int) and isinstance(got, int)):
        return False, None, None
    return got + skipped < want, got + skipped, want


async def _sweep_report(db: JobDatabase, user_id: str, application_id: int) -> SweepReport:
    """The NEWEST ``form_checked`` / ``form_swept`` judged. ``short``: the sweep read fewer fields than the
    form check listed questions; the ``fields_skipped`` of the newest ``form_filled`` count as read.
    NOT IN REFERENCE (career-ops apply.md:315-321 asserts each required control holds a value, it never
    compares an earlier count). No ``form_checked`` on record = no shortfall (older applications).
    A multi-page form (bulk-apply F2: ``page`` / ``pages_total`` on the newest check or sweep) is judged page
    by page in :func:`_paged_shortfalls`; an application with no ``page`` anywhere is judged exactly as before."""
    events = await _events(db, user_id, application_id, ("form_checked", "form_swept"))
    fills = await _events(db, user_id, application_id, ("form_filled",))
    skipped_raw = fills[0]["payload"].get("fields_skipped") if fills else None
    skipped = len(skipped_raw) if isinstance(skipped_raw, list) else 0
    checked = next((e for e in events if e["event_type"] == "form_checked"), None)
    swept = next((e for e in events if e["event_type"] == "form_swept"), None)
    # Once ANY event carries a page the form is judged page by page (security review 2026-10-18: dropping
    # back to unpaged events must not switch the per-page rule off).
    paged_ids = [e["id"] for e in events if _page_of(e) is not None]
    if paged_ids:
        filled = fills[0]["payload"].get("fields_count") if fills else None
        # A RE-fill (a second form_filled) reopens every page swept before it (CodeRabbit 2026-10-18); the
        # first form_filled, recorded once after the last page, does not.
        refill_id = fills[0]["id"] if len(fills) > 1 else None
        short, unreadable, incomplete = _paged_shortfalls(
            events, filled if isinstance(filled, int) else None, refill_id
        )
        # A mix of paged and unpaged events must never switch the unpaged checks OFF (review 2026-10-18):
        # the newest UNPAGED sweep's unreadable list counts, and so does its count against the newest unpaged
        # check (a whole-form re-check that found more questions) - but only unpaged events NEWER than the
        # newest paged one (owner, 2026-10-23: an old whole-form check must not outvote newer page checks).
        flat = [e for e in events if _page_of(e) is None and e["id"] > max(paged_ids)]
        flat_sweep = next((e for e in flat if e["event_type"] == "form_swept"), None)
        flat_check = next((e for e in flat if e["event_type"] == "form_checked"), None)
        if flat_sweep is not None:
            unreadable = unreadable or bool(flat_sweep["payload"].get("unreadable"))
            flat_short, counted, listed = _flat_count(flat_check, flat_sweep, skipped)
            if flat_short and not short:
                return SweepReport(True, unreadable, incomplete, counted, listed)
        return SweepReport(short, unreadable, incomplete)
    if swept is None:
        return SweepReport()
    unreadable = bool(swept["payload"].get("unreadable"))
    if checked is None:
        return SweepReport(unreadable=unreadable)
    short, counted, listed = _flat_count(checked, swept, skipped)
    return SweepReport(short, unreadable, False, counted, listed)


def _paged_shortfalls(
    events: list[dict[str, Any]], filled: Optional[int], refill_id: Optional[int] = None
) -> tuple[bool, bool, bool]:
    """Bulk-apply F2, newest-first ``events`` of a multi-page form. Only events carrying a ``page`` count.
    N = ``pages_total`` of the newest paged event. For every page 1..N: the newest ``form_swept`` of that page
    must exist and be NEWER than the newest ``form_checked`` of that page (a page checked again after its sweep
    is open again; a page that was never swept is incomplete). The count is judged per page (its newest sweep
    read at least as many fields as its newest check listed), the unreadable list over every page's newest
    sweep, and the pages' sweeps together must reach the newest ``form_filled`` ``fields_count``. A page swept
    before ``refill_id`` (the newest of two or more ``form_filled``) is open again.
    NOT IN REFERENCE as a gate: career-ops apply.md:315-342 repeats its sweep before every Next by prompt only."""
    paged = [e for e in events if _page_of(e) is not None]
    # The LARGEST pages_total ever declared (security review 2026-10-18): a later, smaller number cannot
    # shrink the form to skip its unswept pages.
    total = max(
        (
            e["payload"]["pages_total"] for e in paged
            if isinstance(e["payload"].get("pages_total"), int) and not isinstance(e["payload"]["pages_total"], bool)
        ),
        default=0,
    )
    newest_check: dict[int, dict[str, Any]] = {}
    newest_sweep: dict[int, dict[str, Any]] = {}
    for e in paged:  # newest first: the first seen per page wins
        page = int(_page_of(e) or 0)
        bucket = newest_check if e["event_type"] == "form_checked" else newest_sweep
        bucket.setdefault(page, e)
    incomplete = short = unreadable = False
    swept_total = 0
    for page in range(1, total + 1):
        sweep, check = newest_sweep.get(page), newest_check.get(page)
        if (
            sweep is None
            or (check is not None and check["id"] > sweep["id"])
            or (refill_id is not None and sweep["id"] < refill_id)
        ):
            incomplete = True
            continue
        got = sweep["payload"].get("fields_checked")
        want = check["payload"].get("questions_count") if check is not None else None
        if isinstance(got, int):
            swept_total += got
            if isinstance(want, int) and got < want:
                short = True
        unreadable = unreadable or bool(sweep["payload"].get("unreadable"))
    if filled is not None and not incomplete and swept_total < filled:
        short = True
    return short, unreadable, incomplete


# Weak spot #1b (c): applications CREATED on or after this day must have a ``form_checked`` on record
# before check_submit allows a submit (``form_check_missing``). Older in-flight applications keep
# working without one. Set to the day this gate shipped (2026-10-15); never move it later.
# NOT IN REFERENCE: career-ops apply.md:61-62 scans the form for knock-outs by prompt only, with no gate.
FORM_CHECK_REQUIRED_FROM = datetime(2026, 10, 15, tzinfo=timezone.utc)


def form_check_required(created_at: Any) -> bool:
    """True when an application created at ``created_at`` is under the ``form_checked`` rule.
    An unreadable timestamp is treated as an old application (fail open, never a surprise stop)."""
    if not isinstance(created_at, str) or not created_at:
        return False
    try:
        made = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if made.tzinfo is None:
        made = made.replace(tzinfo=timezone.utc)
    return made >= FORM_CHECK_REQUIRED_FROM


async def _form_check_missing(db: JobDatabase, user_id: str, app_row: dict[str, Any]) -> bool:
    """A new application (see :data:`FORM_CHECK_REQUIRED_FROM`) with no ``form_checked`` event."""
    if not form_check_required(app_row.get("created_at")):
        return False
    return not await _events(db, user_id, int(app_row["id"]), ("form_checked",))


# Owner rule 2026-10-20 (strict): an autofill answers every REQUIRED question from the user's saved facts;
# only optional questions may stay blank. Applications created on or after this day cannot be submitted
# while the newest ``form_checked`` of any page still lists a required question with no answer.
REQUIRED_ANSWERS_FROM = datetime(2026, 10, 20, tzinfo=timezone.utc)


def required_answers_enforced(created_at: Any) -> bool:
    """True when an application created at ``created_at`` is under the required-answers rule (it follows
    the ``form_checked`` cut-off too, so a test that moves that cut-off moves this one). Fail open on an
    unreadable timestamp, like :func:`form_check_required`."""
    if not form_check_required(created_at):
        return False
    made = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    if made.tzinfo is None:
        made = made.replace(tzinfo=timezone.utc)
    return made >= REQUIRED_ANSWERS_FROM


async def _required_unanswered(db: JobDatabase, user_id: str, app_row: dict[str, Any]) -> bool:
    """The newest ``form_checked`` of a page (or of the unpaged form) still lists a required question with
    no answer. ``missing_required`` is the count check_form returned; a check that reports ``missing``
    questions without saying how many are required counts as unanswered too (it must say). A later
    ``form_checked`` of the same page, recorded after the user answered, clears it.
    Newest check wins (owner, 2026-10-23): when the newest ``form_checked`` has no ``page`` only it counts;
    otherwise every unpaged check older than the newest paged one is ignored (an old whole-form check
    must not outvote a newer page check)."""
    if not required_answers_enforced(app_row.get("created_at")):
        return False
    newest: dict[Optional[int], dict[str, Any]] = {}
    for e in _current_checks(await _events(db, user_id, int(app_row["id"]), ("form_checked",))):
        newest.setdefault(_page_of(e), e)
    for e in newest.values():
        required = e["payload"].get("missing_required")
        if isinstance(required, int) and not isinstance(required, bool):
            if required > 0:
                return True
        elif (e["payload"].get("missing") or 0) > 0:
            return True
    return False


async def _newest_form_host(db: JobDatabase, user_id: str, application_id: int) -> Optional[str]:
    """The host stored on the NEWEST ``form_filled`` (``None`` when there is none)."""
    fills = await _events(db, user_id, application_id, ("form_filled",))
    host = fills[0]["payload"].get("host") if fills else None
    return host if isinstance(host, str) and host else None


async def last_fill(db: JobDatabase, user_id: str, application_id: int) -> list[dict[str, Any]]:
    """The answers of the NEWEST ``form_filled`` as stored, [] when there is none: what the assistant
    retypes in one browser ``fill`` when the browser closed before the submit (owner decision 2026-10-23).
    A G023-hashed answer comes back with ``answer`` "" and ``redacted`` true: its value was never kept."""
    fills = await _events(db, user_id, application_id, ("form_filled",))
    raw = fills[0]["payload"].get("answers") if fills else None
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not item.get("question"):
            continue
        row: dict[str, Any] = {
            "question": str(item["question"]), "source": str(item.get("source") or ""),
            "answer": "" if item.get("redacted") else str(item.get("answer") or ""),
        }
        if isinstance(item.get("key"), str) and item["key"]:
            row["key"] = item["key"]
        if item.get("redacted"):
            row["redacted"] = True
        out.append(row)
    return out


async def latest_form_url(db: JobDatabase, user_id: str, application_id: int) -> Optional[str]:
    """The form PAGE the newest ``form_filled`` / ``form_swept`` that carries one was recorded on
    (stored cleaned by ``settings_rules.clean_form_url``), or ``None`` (none recorded, or only
    events from before slice 7). check_submit's ``form_page_mismatch`` and the approval fingerprint
    read it here, and only here."""
    for ev in await _events(db, user_id, application_id, ("form_filled", "form_swept")):
        url = ev["payload"].get("form_url")
        if isinstance(url, str) and url:
            return url
    return None


async def gate_facts(
    db: JobDatabase, user_id: str, app_row: dict[str, Any], *, email_verified: bool,
    other_session_hold: Optional[tuple[str, str, str]] = None,
) -> settings_rules.SubmitFacts:
    """The facts :func:`settings_rules.may_submit` reads for this application.
    ``email_verified`` comes from the caller's :class:`CurrentUser`
    (``auth_deps.email_verification_satisfied``) - it is about the account, not
    the application. ``other_session_hold`` is :func:`hold_fact` of the caller's
    :func:`live_hold` (it depends on who asks, so the caller computes it)."""
    application_id = int(app_row["id"])
    cur = await db._db.execute(
        "SELECT 1 FROM application_receipts WHERE application_id = ? AND user_id = ? LIMIT 1",
        (application_id, user_id),
    )
    has_receipt = await cur.fetchone() is not None
    now = datetime.now(timezone.utc)
    state = await controls_state(db, user_id, app_row, now)
    shortfalls = await _sweep_report(db, user_id, application_id)
    cv = state["cv"]
    stale = state["approval_stale"]
    approved = cv["approved"] is not None if cv else stale is None and await _approved_without_cv(
        db, user_id, application_id
    )
    dup = state["duplicate"]
    form_host = await _newest_form_host(db, user_id, application_id)
    draft = await email_channel.newest_draft(db, user_id, application_id)
    return settings_rules.SubmitFacts(
        status=str(app_row["status"]),
        has_receipt=has_receipt,
        submit_override=await spine.submit_override(db, user_id, application_id),
        duplicate_job=bool(dup["same_job"]) and dup["cleared"] is None,
        duplicate_role=bool(dup.get("same_role")) and dup["cleared"] is None,
        company_avoided=company_avoided(user_id, app_row),
        submit_unconfirmed=await submit_unconfirmed(db, user_id, application_id),
        new_site=await _new_site(db, user_id, application_id, form_host),
        cv_seen=bool(cv and cv["seen"] is not None),
        approved=bool(approved),
        approval_stale=stale,
        declined=state["declined"] is not None,
        blocked=state["blocked"] is not None,
        form_filled=await _filled_with_latest_cv(db, user_id, application_id),
        form_swept=await _swept_after_fill(db, user_id, application_id),
        form_count_short=shortfalls.short,
        form_count_counted=shortfalls.counted,
        form_count_listed=shortfalls.listed,
        unreadable_fields=shortfalls.unreadable,
        form_pages_incomplete=shortfalls.incomplete,
        form_check_missing=await _form_check_missing(db, user_id, app_row),
        required_unanswered=await _required_unanswered(db, user_id, app_row),
        # The latest saved fit (the slot save_fit overwrites); None = no fit yet.
        fit_score=int(app_row["fit_score"]) if app_row.get("fit_score") is not None else None,
        form_host=form_host,
        form_url=await latest_form_url(db, user_id, application_id),
        email_verified=email_verified,
        other_session_hold=other_session_hold,
        cv_unsupported=await _cv_unsupported(db, user_id, cv["artifact_id"]) if cv else False,
        has_cv=cv is not None,
        guessed_unverified=await _guessed_unverified(db, user_id, app_row),
        sensitive_unconfirmed=await _sensitive_unconfirmed(db, user_id, app_row),
        written_unapproved=await _written_unapproved(db, user_id, app_row),
        blank_unasked=await _blank_unasked(db, user_id, app_row),
        application_id=application_id,
        email_to=draft["to"] if draft else None,
    )


async def _guessed_unverified(db: JobDatabase, user_id: str, app_row: dict[str, Any]) -> bool:
    """The newest form holds a guessed answer the user has not verified (``ready`` owns the reading)."""
    from src.services.applications import ready  # noqa: PLC0415 - ready imports this module

    return await ready.has_unverified_guess(db, user_id, app_row)


async def _written_unapproved(db: JobDatabase, user_id: str, app_row: dict[str, Any]) -> bool:
    """The newest form holds a written answer the user has not approved (``ready`` owns the reading)."""
    from src.services.applications import ready  # noqa: PLC0415 - ready imports this module

    return await ready.has_unapproved_written(db, user_id, app_row)


async def _blank_unasked(db: JobDatabase, user_id: str, app_row: dict[str, Any]) -> tuple[str, ...]:
    """The newest form's blank fields nobody asked the user about (``ready`` reads them)."""
    from src.services.applications import ready  # noqa: PLC0415 - ready imports this module

    return tuple(await ready.blank_unasked(db, user_id, app_row))


async def _sensitive_unconfirmed(db: JobDatabase, user_id: str, app_row: dict[str, Any]) -> bool:
    """The newest form holds an answer to a sensitive question the user has not confirmed (``ready`` reads it)."""
    from src.services.applications import ready  # noqa: PLC0415 - ready imports this module

    return await ready.has_unconfirmed_sensitive(db, user_id, app_row)


def company_avoided(user_id: str, app_row: dict[str, Any], entries: Optional[list[Any]] = None) -> bool:
    """Bulk-apply D2: the job's company is on the user's OWN ``preferences.avoid_companies`` list.
    Exact match of :func:`normalized_company_key` on both sides (no fuzzy match); a blank or
    ``unknown`` key never matches. The user's stop list, never a recommendation."""
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    mine = normalized_company_key(str(app_row.get("job_company") or ""))
    if not mine or mine == "unknown":
        return False
    if entries is None:  # callers that loop pass the list once (queue preflight, review 2026-10-18)
        entries = (load_profile(user_id) or UserProfile()).preferences.avoid_companies
    return any(isinstance(e, str) and normalized_company_key(e) == mine for e in entries or [])


async def submit_unconfirmed(db: JobDatabase, user_id: str, application_id: int) -> bool:
    """Bulk-apply D1: the newest of ``submit_attempted`` / ``applied`` /
    ``submit_resolved`` (web only) is a ``submit_attempted`` (a submit was started and never confirmed).
    Corrections are deliberately ignored: nothing an assistant retracts can clear this."""
    rows = await _events(db, user_id, application_id, SUBMIT_MARK_EVENTS)
    # An ``applied`` that a later event corrects (retracts) resolves nothing (security review
    # 2026-10-18): otherwise applied + a retracting note would clear the stop. A corrected
    # ``submit_attempted`` still counts, so a retraction can never clear it either.
    cur = await db._db.execute(
        "SELECT corrects_event_id FROM application_events WHERE application_id = ? AND user_id = ? "
        "AND corrects_event_id IS NOT NULL",
        (application_id, user_id),
    )
    retracted = {int(dict(r)["corrects_event_id"]) for r in await cur.fetchall()}
    rows = [r for r in rows if not (r["event_type"] == "applied" and int(r["id"]) in retracted)]
    return bool(rows) and rows[0]["event_type"] == "submit_attempted"


SUBMIT_CLAIM_REFUSALS = {
    "unconfirmed": "submit_already_started: a submit was already started on this application and has no outcome "
    "yet. Do not click Submit: read the page; if nothing confirms it went, record blocked and tell the user.",
    "receipt": "submit_already_started: this application already has a receipt (it was sent). Do not click Submit.",
    "status": "submit_already_started: this application is no longer considering. Do not click Submit.",
}


@contextlib.asynccontextmanager
async def submit_claim(db: JobDatabase, user: CurrentUser, application_id: int) -> AsyncIterator[None]:
    """G338: recording ``submit_attempted`` is a compare-and-set CLAIM. The application row is
    locked, then the claim is refused (409) while a submit is unconfirmed (:func:`submit_unconfirmed`,
    the same rule check_submit stops on), once a receipt exists, or when the status is not
    ``considering``. The caller appends the event inside this block, so two sessions that both
    passed check_submit queue on the lock and only the first may click."""
    async with db._db.transaction():
        cur = await db._db.execute(
            "SELECT status FROM applications WHERE id = ? AND user_id = ? FOR UPDATE", (application_id, user.id)
        )
        row = await cur.fetchone()
        if row is None:
            raise SpineError(404, "application not found")
        cur = await db._db.execute(
            "SELECT 1 FROM application_receipts WHERE application_id = ? AND user_id = ? LIMIT 1",
            (application_id, user.id),
        )
        has_receipt = await cur.fetchone() is not None
        reason = ""
        if await submit_unconfirmed(db, user.id, application_id):
            reason = "unconfirmed"
        elif has_receipt:
            reason = "receipt"
        elif dict(row)["status"] != "considering":
            reason = "status"
        if reason:
            get_audit_logger().warning(
                "submit_claim_refused",
                extra={
                    "event": "submit_claim_refused", "user_id": safe_log_value(user.id),
                    "actor": safe_log_value(actor_for(user)), "application_id": int(application_id),
                    "reason": reason, "status": 409, "result": "refused",
                },
            )
            raise SpineError(409, SUBMIT_CLAIM_REFUSALS[reason])
        yield


async def _new_site(db: JobDatabase, user_id: str, application_id: int, host: Optional[str]) -> bool:
    """Bulk-apply R2: ``host`` (the newest ``form_filled``'s) has no earlier application of this
    user with a RECEIPT (``record_application``; a bare ``applied`` event is not enough) that is not
    marked ``simulation`` (at any position) and has a ``form_filled`` on that same host (security
    review 2026-10-18). ``False`` when there is no host (``unknown_site`` handles that)."""
    if not host:
        return False
    cur = await db._db.execute(
        "SELECT f.payload FROM application_events f WHERE f.user_id = ? AND f.event_type = 'form_filled' "
        "AND f.application_id <> ? AND EXISTS (SELECT 1 FROM application_receipts r WHERE "
        "r.application_id = f.application_id AND r.user_id = f.user_id) "
        "AND NOT EXISTS (SELECT 1 FROM application_events s WHERE s.application_id = f.application_id "
        "AND s.user_id = f.user_id AND s.event_type = ? AND s.id NOT IN "
        "(SELECT corrects_event_id FROM application_events WHERE user_id = ? AND corrects_event_id IS NOT NULL)) "
        "ORDER BY f.id DESC LIMIT 2000",
        (user_id, application_id, settings.SIMULATION_EVENT_TYPE, user_id),
    )
    return not any(_json(dict(r).get("payload")).get("host") == host for r in await cur.fetchall())


_UNSUPPORTED_CODES = frozenset({"unsupported_numbers", "unsupported_terms"})


async def _cv_unsupported(db: JobDatabase, user_id: str, artifact_id: int) -> bool:
    """The CV version's stored save-time checks name numbers or terms not in the profile."""
    cur = await db._db.execute(
        "SELECT checks_json FROM application_artifacts WHERE id = ? AND user_id = ?", (artifact_id, user_id)
    )
    got = await cur.fetchone()
    return any(c.get("code") in _UNSUPPORTED_CODES for c in _stored_checks(got[0] if got else None))


async def _approved_without_cv(db: JobDatabase, user_id: str, application_id: int) -> Optional[dict[str, Any]]:
    """No CV saved: the newest yes recorded with ``artifact_id`` null (it still counts), or ``None``."""
    for ev in await _events(db, user_id, application_id, ("submit_approved",)):
        if ev["payload"].get("artifact_id") is None:
            return ev
    return None


async def submit_verdict(
    db: JobDatabase, user_id: str, app_row: dict[str, Any], form_url: str, now: datetime,
    *, email_verified: bool, assume_filled: bool = False,
    other_session_hold: Optional[tuple[str, str, str]] = None,
) -> settings_rules.SubmitDecision:
    """The ONE gate, evaluated for one application (the submit-check route and
    the kit's ``settings.submit_preview`` both call this). The kit is read BEFORE
    the form is filled, so its preview passes ``assume_filled=True``: it shows what
    the gate will say once ``form_filled`` is recorded, not a "stop" the assistant
    could misread as "do not apply"."""
    from dataclasses import replace  # noqa: PLC0415

    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    cfg = settings_rules.effective((load_profile(user_id) or UserProfile()).assistant_settings)
    counts = await settings_rules.load_submit_counts(
        db, user_id, now, int(app_row["id"]), window_days=cfg.company_cap_days
    )
    facts = await gate_facts(
        db, user_id, app_row, email_verified=email_verified, other_session_hold=other_session_hold
    )
    if assume_filled:
        # The preview imagines the form filled ON this address, so it never
        # shows a form_host_mismatch for a form that is not filled yet.
        facts = replace(
            facts, form_filled=True, form_swept=True, form_count_short=False, unreadable_fields=False,
            form_check_missing=False, form_pages_incomplete=False, required_unanswered=False,
            form_host=settings_rules.screen_form_url(form_url),
            form_url=settings_rules.clean_form_url(form_url),
            # The answers of an OLD fill say nothing about the form about to be filled.
            guessed_unverified=False, sensitive_unconfirmed=False, written_unapproved=False, blank_unasked=(),
        )
    return settings_rules.may_submit(cfg, facts, form_url, counts, now=now)


# ── Who may write which S3 event ─────────────────────────────────────────────


def _closed_payload(event_type: str, payload: dict[str, Any], keys: frozenset[str] | set[str]) -> None:
    if set(payload) != keys:
        raise SpineError(422, f"{event_type} needs payload keys {sorted(keys)} and nothing else")


def _where(user: CurrentUser, event_type: str, payload: dict[str, Any]) -> str:
    where = payload.get("where")
    if where not in SEEN_WHERE:
        raise SpineError(422, f"{event_type} where must be 'web' or 'chat'")
    web = actor_for(user) == "web"
    if web != (where == "web"):
        _refuse(user, event_type, 403)
        raise SpineError(
            403,
            "where=web needs the user's own click on the Job360 website; an assistant records where=chat, "
            "only after the user said so in chat",
        )
    return str(where)


# The closed payload keys of each kit event - the validator below reads these, and a test pins the
# record_event tool description against them so the two can never drift apart (2026-10-13).
EVENT_PAYLOAD_KEYS: dict[str, frozenset[str]] = {
    "cv_seen": frozenset({"artifact_id", "sha256", "where"}),
    "submit_approved": frozenset({"artifact_id", "sha256", "where"}),
    "submit_declined": frozenset({"where"}),
    "autofill_set": frozenset({"mode"}),
    "form_swept": frozenset({
        "fields_checked", "fixed", "notes", "unreadable", "page", "pages_total", "answers", "form_url",
    }),
    "hold_released": frozenset({"reason"}),
    "site_account": frozenset({"host"}),
    "account_needed": frozenset({"host"}),
    "email_drafted": email_channel.REQUIRED | email_channel.OPTIONAL,
}
FORM_FILLED_REQUIRED = frozenset({"form_url", "fields_count"})
FORM_FILLED_OPTIONAL = frozenset({"answers", "fields_skipped"})
FORM_SWEPT_REQUIRED = frozenset({"fields_checked", "fixed"})
FORM_SWEPT_NOTES_MAX = 300
FORM_SWEPT_UNREADABLE_MAX = 50
# ``form_checked`` (weak spot #1b): what check_form found, COUNTS and wording only - never an answer value.
FORM_CHECKED_REQUIRED = frozenset({"questions_count", "answered", "missing"})
FORM_CHECKED_OPTIONAL = frozenset({"answers", "page", "pages_total", "missing_required"})
FORM_PAGES_MAX = 20  # bulk-apply F2: the most pages one form may declare
FORM_CHECKED_ANSWER_KEYS = frozenset({"question", "key", "source", "needs_confirmation"})
ANSWER_SOURCES = ("memory", "profile", "approved", "written", "guessed")
ANSWER_QUESTION_MAX = 300
ANSWER_KEY_MAX = 120
# Owner 2026-10-26 (AppZen on production): a form answer is the EXACT text typed into the field, so the
# user can read what went out.
# A short answer that only DESCRIBES the text ("drafted from profile facts", "see CV", "metrics from
# CV") is refused: it is a note about the answer, not the answer. Only checked under this length -
# a real paragraph may mention its CV.
_DESCRIPTION_MAX_CHARS = 120
_DESCRIBES_ITSELF = re.compile(
    r"\b(drafted|placeholder|as typed|see (the |my )?(cv|profile|typed text|above|form)"
    r"|(from|per|in) (the |my )?(cv|profile)( facts)?)\b",
    re.IGNORECASE,
)


def _clean_answer(item: Any) -> dict[str, str]:
    """One answer, validated and cleaned; raises ``SpineError`` 422 on the first problem."""
    if not isinstance(item, dict) or not {"question", "answer", "source"} <= set(item) <= {
        "question", "answer", "source", "key",
    }:
        raise SpineError(422, "each answer needs question, answer and source (and optionally key), nothing else")
    if item["source"] not in ANSWER_SOURCES:
        raise SpineError(422, "answer source must be one of: " + ", ".join(ANSWER_SOURCES))
    texts = {k: item[k] for k in ("question", "answer", "key") if k in item}
    if not all(isinstance(v, str) for v in texts.values()):
        raise SpineError(422, "answer question, answer and key must be text")
    # A line break in a question becomes a space (never glues two words); an
    # answer keeps its line breaks - a written answer may be paragraphs.
    question = " ".join(spine._strip_control_chars(texts["question"]).split())
    answer = spine._strip_control_chars(texts["answer"]).strip()
    if not question or len(question) > ANSWER_QUESTION_MAX:
        raise SpineError(422, f"answer question must be 1 to {ANSWER_QUESTION_MAX} characters")
    if len(answer) > settings.USER_INFO_ANSWER_MAX_CHARS:
        raise SpineError(422, f"an answer is over {settings.USER_INFO_ANSWER_MAX_CHARS} characters")
    _refuse_description(question, answer, str(item["source"]))
    clean = {"question": question, "answer": answer, "source": str(item["source"])}
    if "key" in texts:
        key = spine._strip_control_chars(texts["key"], keep="").strip()
        if not key or len(key) > ANSWER_KEY_MAX:
            raise SpineError(422, f"answer key must be 1 to {ANSWER_KEY_MAX} characters")
        clean["key"] = key
    return clean


def _refuse_description(question: str, answer: str, source: str) -> None:
    """422 when a written or guessed answer is a note ABOUT the text, not the text itself."""
    if source not in ("written", "guessed") or not answer:
        return
    if len(answer) <= _DESCRIPTION_MAX_CHARS and _DESCRIBES_ITSELF.search(answer):
        raise SpineError(422, (
            f'answer for "{question[:80]}" describes the text instead of giving it ("{answer[:60]}"): '
            "send the exact text you typed into the field, word for word, so the user can read it"
        ))


def _answers_list(raw: Any) -> list[Any]:
    if not isinstance(raw, list) or len(raw) > settings.KIT_FORM_FIELDS_MAX:
        raise SpineError(422, f"answers must be a list of at most {settings.KIT_FORM_FIELDS_MAX} items")
    return raw


def clean_answers(raw: Any) -> list[dict[str, str]]:
    """S5d: the answers an assistant typed into a form, validated. A list of at
    most ``KIT_FORM_FIELDS_MAX`` of ``{question, answer, source[, key]}`` - closed
    keys, control characters removed, sizes capped. ``source`` says where the
    answer came from (a guess is ``guessed``); ``key`` is the kit key for a
    memory / profile / approved answer. An answer may be blank (the page flags it).
    A receipt's answers take the same ``source`` list and ``key`` (both optional there)."""
    return [_clean_answer(item) for item in _answers_list(raw)]


def clean_fields_skipped(raw: Any) -> list[str]:
    """The field labels the assistant left blank on the form (``form_filled``'s optional
    ``fields_skipped``): a list of short texts, at most ``KIT_FORM_FIELDS_MAX``. 422 otherwise."""
    if not isinstance(raw, list) or len(raw) > settings.KIT_FORM_FIELDS_MAX:
        raise SpineError(422, f"fields_skipped must be a list of at most {settings.KIT_FORM_FIELDS_MAX} field names")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise SpineError(422, "each fields_skipped entry must be text")
        label = " ".join(spine._strip_control_chars(item).split())
        if not label or len(label) > ANSWER_QUESTION_MAX:
            raise SpineError(422, f"each fields_skipped entry must be 1 to {ANSWER_QUESTION_MAX} characters")
        out.append(label)
    return out


def answer_problems(raw: Any) -> list[str]:
    """Every problem in an ``answers`` value (same rules as :func:`clean_answers`), not just the first."""
    problems: list[str] = []
    items = event_examples.collect(problems, _answers_list, raw)
    for i, item in enumerate(items or []):
        event_examples.collect(problems, _clean_answer, item, prefix=f"answers[{i}]: ")
    return problems


# ── G023 (retired 2026-10-26): sensitive answers were hashed, never copied. The owner chose to keep every
# typed answer as typed so the card can show it; rows hashed before stay hashed (M3: append-only).


def _sensitive_value(item: Any) -> bool:
    """A non-blank answer to a sensitive question (:func:`_question_is_sensitive`, the same test
    ``check_form`` flags ``needs_confirmation`` with). A blank or already-hashed answer has no value to hide."""
    if not isinstance(item, dict) or item.get("redacted") or not str(item.get("answer") or "").strip():
        return False
    key = item.get("key")
    return _question_is_sensitive(str(item.get("question") or ""), key if isinstance(key, str) else None)


async def redact_unremembered(
    db: JobDatabase, user_id: str, app_row: dict[str, Any], answers: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """``(answers to store, how many were hashed)``. Owner decision 2026-10-26 ("yes, keep them"): every
    answer typed into a form is kept as typed - salary, equality and consent included - so the card shows
    it and the user can edit it; nothing is hashed any more. Rows hashed before stay hashed (question,
    ``value_sha256``, ``redacted: true``); the ready card offers the user's own saved answer as a draft."""
    return answers, 0


async def _stored_answers(
    db: JobDatabase, user: CurrentUser, application_id: int, event_type: str, raw: Any
) -> list[dict[str, Any]]:
    """A form event's ``answers`` as stored: cleaned, then G023-hashed (:func:`redact_unremembered`)."""
    cleaned: list[dict[str, Any]] = list(clean_answers(raw))
    if not any(_sensitive_value(a) for a in cleaned):
        return cleaned
    app_row = await spine.get_owned_application(db, user.id, application_id) or {}
    stored, hashed = await redact_unremembered(db, user.id, app_row, cleaned)
    if hashed:
        get_audit_logger().info("answers_redacted", extra={
            "event": "answers_redacted", "user_id": safe_log_value(user.id), "application_id": int(application_id),
            "event_type": event_type, "redacted_count": hashed, "result": "ok",
        })
    return stored


def _refuse(user: CurrentUser, event_type: str, status: int) -> None:
    get_audit_logger().warning(
        f"{event_type}_refused",
        extra={
            "event": f"{event_type}_refused", "user_id": safe_log_value(user.id),
            "actor": safe_log_value(actor_for(user)), "status": status, "result": "refused",
        },
    )


def _host(event_type: str, value: Any) -> str:
    host = settings_rules.parse_site_host(value if isinstance(value, str) else None)
    if host is None:
        raise SpineError(422, f"{event_type} host could not be read")
    return host


def _keys_problem(
    payload: dict[str, Any], required: frozenset[str] | set[str], allowed: frozenset[str] | set[str]
) -> list[str]:
    missing, unknown = sorted(set(required) - set(payload)), sorted(set(payload) - set(allowed))
    out: list[str] = []
    if missing:
        out.append(f"missing payload keys: {missing}")
    if unknown:
        out.append(f"unknown payload keys: {unknown} (allowed: {sorted(allowed)})")
    return out


def _count_problem(name: str, value: Any, problems: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= settings.KIT_FORM_FIELDS_MAX:
        problems.append(f"{name} must be a whole number from 0 to {settings.KIT_FORM_FIELDS_MAX}")


def _pages_problems(payload: dict[str, Any], problems: list[str]) -> None:
    """Bulk-apply F2: ``page`` and ``pages_total`` come together (whole numbers, 1..FORM_PAGES_MAX), page <= total."""
    if "page" not in payload and "pages_total" not in payload:
        return
    if "page" not in payload or "pages_total" not in payload:
        problems.append("page and pages_total go together (send both, or neither for a one-page form)")
        return
    ok = True
    for name in ("page", "pages_total"):
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= FORM_PAGES_MAX:
            problems.append(f"{name} must be a whole number from 1 to {FORM_PAGES_MAX}")
            ok = False
    if ok and payload["page"] > payload["pages_total"]:
        problems.append("page cannot be more than pages_total")


def _clean_text(value: Any, name: str, limit: int, problems: list[str]) -> str:
    if not isinstance(value, str):
        problems.append(f"{name} must be text")
        return ""
    text = " ".join(spine._strip_control_chars(value).split())
    if len(text) > limit:
        problems.append(f"{name} must be at most {limit} characters")
    return text


def _checked_answers(raw: Any, problems: list[str]) -> list[dict[str, Any]]:
    """``form_checked.answers``: closed ``{question, key?, source, needs_confirmation?}`` rows. A key named
    ``value`` or ``answer`` is refused as unknown, so a saved answer can never be copied into this event."""
    if not isinstance(raw, list) or len(raw) > settings.KIT_FORM_FIELDS_MAX:
        problems.append(f"answers must be a list of at most {settings.KIT_FORM_FIELDS_MAX} items")
        return []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not {"question", "source"} <= set(item) <= FORM_CHECKED_ANSWER_KEYS:
            problems.append(f"answers[{i}] needs keys question, source (and key, needs_confirmation) and nothing else")
            continue
        row: dict[str, Any] = {
            "question": _clean_text(item["question"], f"answers[{i}].question", 500, problems),
            "source": _clean_text(item["source"], f"answers[{i}].source", 40, problems),
        }
        if item.get("key") is not None:
            row["key"] = _clean_text(item["key"], f"answers[{i}].key", 300, problems)
        if "needs_confirmation" in item:
            if not isinstance(item["needs_confirmation"], bool):
                problems.append(f"answers[{i}].needs_confirmation must be true or false")
            else:
                row["needs_confirmation"] = item["needs_confirmation"]
        out.append(row)
    return out


def _unreadable_list(raw: Any, problems: list[str]) -> list[str]:
    if not isinstance(raw, list) or len(raw) > FORM_SWEPT_UNREADABLE_MAX:
        problems.append(f"unreadable must be a list of at most {FORM_SWEPT_UNREADABLE_MAX} field names")
        return []
    return [t for t in (_clean_text(x, "unreadable item", 200, problems) for x in raw) if t]


def payload_problems(event_type: str, payload: dict[str, Any]) -> list[str]:
    """EVERY problem in a kit event's payload that needs no database and no
    caller identity (shape, types, ranges, closed sets) - the same rules the
    validators in :func:`check_kit_event` apply, reported together instead of one
    per call. Empty list = the payload is well formed (who may write it, and the
    rules that read the application's history, are checked after)."""
    problems: list[str] = []
    if event_type in ("cv_seen", "submit_approved"):
        problems += _keys_problem(payload, EVENT_PAYLOAD_KEYS[event_type], EVENT_PAYLOAD_KEYS[event_type])
        if payload.get("where") not in SEEN_WHERE:
            problems.append("where must be 'web' or 'chat'")
        artifact_id, sha = payload.get("artifact_id"), payload.get("sha256")
        no_cv = event_type == "submit_approved" and artifact_id is None and sha in ("", None)
        bad_id = isinstance(artifact_id, bool) or not isinstance(artifact_id, int)
        if not no_cv and (bad_id or not isinstance(sha, str)):
            problems.append("needs artifact_id (a whole number) and sha256 (text)")
    elif event_type == "submit_declined":
        problems += _keys_problem(payload, EVENT_PAYLOAD_KEYS[event_type], EVENT_PAYLOAD_KEYS[event_type])
        if payload.get("where") not in SEEN_WHERE:
            problems.append("where must be 'web' or 'chat'")
    elif event_type == "autofill_set":
        problems += _keys_problem(payload, EVENT_PAYLOAD_KEYS[event_type], EVENT_PAYLOAD_KEYS[event_type])
        if payload.get("mode") not in ("allow", "deny"):
            problems.append('mode must be "allow" or "deny"')
    elif event_type == "submit_attempted":
        problems += _keys_problem(payload, frozenset(), frozenset())
    elif event_type == "submit_resolved":
        problems += _keys_problem(payload, frozenset({"outcome"}), frozenset({"outcome"}))
        if payload.get("outcome") != "not_sent":
            problems.append('outcome must be "not_sent" (a submit that WAS sent is recorded with record_application)')
    elif event_type == "hold_released":
        problems += _keys_problem(payload, EVENT_PAYLOAD_KEYS[event_type], EVENT_PAYLOAD_KEYS[event_type])
        if payload.get("reason") not in HOLD_RELEASE_REASONS:
            problems.append("reason must be one of: " + ", ".join(sorted(HOLD_RELEASE_REASONS)))
    elif event_type in ("site_account", "account_needed"):
        problems += _keys_problem(payload, EVENT_PAYLOAD_KEYS[event_type], EVENT_PAYLOAD_KEYS[event_type])
        host = payload.get("host")
        if settings_rules.parse_site_host(host if isinstance(host, str) else None) is None:
            problems.append("host could not be read (a host name such as jobs.example.com)")
    elif event_type == "form_filled":
        problems += _keys_problem(payload, FORM_FILLED_REQUIRED, FORM_FILLED_REQUIRED | FORM_FILLED_OPTIONAL)
        count = payload.get("fields_count")
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= settings.KIT_FORM_FIELDS_MAX:
            problems.append(f"fields_count must be a whole number from 0 to {settings.KIT_FORM_FIELDS_MAX}")
        url = payload.get("form_url")
        if settings_rules.parse_site_host(url if isinstance(url, str) else None) is None:
            problems.append("form_url host could not be read (the full address of the application form)")
        if "answers" in payload:
            problems += answer_problems(payload["answers"])
        if "fields_skipped" in payload:
            event_examples.collect(problems, clean_fields_skipped, payload["fields_skipped"])
    elif event_type == "form_swept":
        problems += _keys_problem(payload, FORM_SWEPT_REQUIRED, EVENT_PAYLOAD_KEYS[event_type])
        ints: dict[str, int] = {}
        for name in ("fields_checked", "fixed"):
            value = payload.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= settings.KIT_FORM_FIELDS_MAX:
                problems.append(f"{name} must be a whole number from 0 to {settings.KIT_FORM_FIELDS_MAX}")
            else:
                ints[name] = value
        if len(ints) == 2 and ints["fixed"] > ints["fields_checked"]:
            problems.append("fixed cannot be more than fields_checked")
        if "answers" in payload:
            problems += answer_problems(payload["answers"])
        if "notes" in payload:
            notes = payload["notes"]
            if not isinstance(notes, str):
                problems.append("notes must be text")
            elif len(" ".join(spine._strip_control_chars(notes).split())) > FORM_SWEPT_NOTES_MAX:
                problems.append(f"notes must be at most {FORM_SWEPT_NOTES_MAX} characters")
        if "unreadable" in payload:
            _unreadable_list(payload["unreadable"], problems)
        if "form_url" in payload:
            url = payload["form_url"]
            if settings_rules.clean_form_url(url if isinstance(url, str) else None) is None:
                problems.append(
                    "form_url must be one plain http(s) address of the form page: no user@ part, "
                    "no % escapes or backslashes before the path, and an ASCII host name"
                )
        _pages_problems(payload, problems)
    elif event_type == "form_checked":
        problems += _keys_problem(payload, FORM_CHECKED_REQUIRED, FORM_CHECKED_REQUIRED | FORM_CHECKED_OPTIONAL)
        _pages_problems(payload, problems)
        for name in ("questions_count", "answered", "missing", "missing_required"):
            if name in payload:
                _count_problem(name, payload[name], problems)
        missing_required, missing = payload.get("missing_required"), payload.get("missing")
        if (
            isinstance(missing_required, int) and isinstance(missing, int)
            and not isinstance(missing_required, bool) and not isinstance(missing, bool) and missing_required > missing
        ):
            problems.append("missing_required cannot be more than missing")
        if "answers" in payload:
            _checked_answers(payload["answers"], problems)
    elif event_type == email_channel.EVENT_TYPE:
        problems += email_channel.payload_problems(payload)
    return problems


async def check_kit_event(
    db: JobDatabase, user: CurrentUser, application_id: int, event_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Validate an S3 event's payload and who may write it; return the payload to
    STORE (server adds ``by``, and ``version`` where a CV is named). Raises
    ``SpineError``. Lives here, called by the ``record_event`` ROUTE, so the MCP
    tool inherits it. A malformed payload is refused ONCE with every problem and a
    valid example (:func:`payload_problems`); the rules below only ever see a
    well-formed one."""
    if event_type not in SERVER_ONLY_EVENTS:
        problems = payload_problems(event_type, payload)
        if problems:
            raise event_examples.refusal(event_type, problems)
    try:
        return await _check_kit_event(db, user, application_id, event_type, payload)
    except SpineError as exc:
        raise event_examples.with_example(event_type, exc) from None


async def _check_kit_event(
    db: JobDatabase, user: CurrentUser, application_id: int, event_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    actor = actor_for(user)
    if event_type in SERVER_ONLY_EVENTS:
        raise SpineError(422, f"{event_type} is written by Job360 itself (get_application_kit), not by a caller")
    if event_type in ("cv_seen", "submit_approved"):
        _closed_payload(event_type, payload, EVENT_PAYLOAD_KEYS[event_type])
        where = _where(user, event_type, payload)
        kinds = ("cv", "cover_letter") if event_type == "cv_seen" else ("cv",)
        artifact_id, sha = payload.get("artifact_id"), payload.get("sha256")
        if artifact_id is None and event_type == "submit_approved" and sha in ("", None):
            if await spine.latest_artifact(db, user.id, application_id, "cv") is not None:
                raise SpineError(409, "this CV changed - show the new version")
            return {"artifact_id": None, "sha256": "", "where": where, "version": None, "by": actor}
        if isinstance(artifact_id, bool) or not isinstance(artifact_id, int) or not isinstance(sha, str):
            raise SpineError(422, f"{event_type} needs artifact_id (a whole number) and sha256 (text)")
        row = await spine.get_artifact(db, user.id, application_id, artifact_id)
        if row is None or row["kind"] not in kinds:
            raise SpineError(404, "artifact not found")
        latest = await spine.latest_artifact(db, user.id, application_id, row["kind"])
        if latest is None or latest["id"] != row["id"] or sha != sha256_text(row["text"]):
            raise SpineError(409, "this CV changed - show the new version")
        return {"artifact_id": row["id"], "sha256": sha, "where": where, "version": row["version_no"], "by": actor}
    if event_type == "submit_declined":
        _closed_payload(event_type, payload, EVENT_PAYLOAD_KEYS[event_type])
        return {"where": _where(user, event_type, payload), "by": actor}
    if event_type == "autofill_set":
        _closed_payload(event_type, payload, EVENT_PAYLOAD_KEYS[event_type])
        mode = payload.get("mode")
        if mode not in ("allow", "deny"):
            raise SpineError(422, 'autofill_set needs payload {"mode": "allow" | "deny"} and nothing else')
        if actor != "web" and mode == "allow":
            _refuse(user, event_type, 403)
            raise SpineError(403, "this needs your click on the Job360 website")
        # ``where`` is server-filled (the client sends only ``mode``): the page shows
        # "on the website" vs "in chat", so an assistant's deny must never read as web.
        return {"mode": mode, "where": "web" if actor == "web" else "chat", "by": actor}
    if event_type == "duplicate_cleared":
        _closed_payload(event_type, payload, {"where"})
        if actor != "web" or payload.get("where") != "web":
            _refuse(user, event_type, 403)
            raise SpineError(403, "this needs your click on the Job360 website")
        return {"where": "web", "by": actor}
    if event_type == "submit_attempted":
        _closed_payload(event_type, payload, frozenset())
        return {"by": actor}
    if event_type == "submit_resolved":
        _closed_payload(event_type, payload, {"outcome"})
        if actor != "web" or payload.get("outcome") != "not_sent":
            _refuse(user, event_type, 403)
            raise SpineError(403, "this needs your click on the Job360 website")
        if not await submit_unconfirmed(db, user.id, application_id):
            raise SpineError(409, "no unconfirmed submit to resolve")
        return {"outcome": "not_sent", "where": "web", "by": actor}
    if event_type == "form_filled":
        if not FORM_FILLED_REQUIRED <= set(payload) <= FORM_FILLED_REQUIRED | FORM_FILLED_OPTIONAL:
            raise SpineError(
                422, "form_filled needs payload keys ['fields_count', 'form_url'] (and 'answers', 'fields_skipped')"
            )
        count = payload.get("fields_count")
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= settings.KIT_FORM_FIELDS_MAX:
            raise SpineError(422, f"fields_count must be a whole number from 0 to {settings.KIT_FORM_FIELDS_MAX}")
        # The STRICT reading (the same one check_submit uses): a user-info "@",
        # "%" or backslash in the authority, a non-ASCII / IDNA host or a non-http(s)
        # scheme is refused, so a spoofed address can never become the recorded host.
        host = settings_rules.screen_form_url(
            payload.get("form_url") if isinstance(payload.get("form_url"), str) else None
        )
        if host is None:
            get_audit_logger().warning(
                "form_filled_refused",
                extra={
                    "event": "form_filled_refused", "user_id": safe_log_value(user.id),
                    "actor": safe_log_value(actor), "application_id": safe_log_value(application_id),
                    "reason": "form_url_unreadable", "status": 422, "result": "refused",
                },
            )
            raise SpineError(
                422,
                "form_filled form_url must be one plain http(s) address: no user@ part, no % escapes "
                "or backslashes before the path, and an ASCII host name",
            )
        stored: dict[str, Any] = {"host": host, "fields_count": count, "by": actor}
        page = settings_rules.clean_form_url(payload["form_url"])  # slice 7: the page, never a token
        if page:
            stored["form_url"] = page
        if "answers" in payload:
            stored["answers"] = await _stored_answers(db, user, application_id, event_type, payload["answers"])
        if "fields_skipped" in payload:
            stored["fields_skipped"] = clean_fields_skipped(payload["fields_skipped"])
        return stored
    if event_type == "form_swept":
        keys = EVENT_PAYLOAD_KEYS[event_type]
        if not FORM_SWEPT_REQUIRED <= set(payload) <= keys:
            raise SpineError(422, "form_swept needs payload keys ['fields_checked', 'fixed'] (and 'notes')")
        counts: dict[str, int] = {}
        for name in ("fields_checked", "fixed"):
            value = payload.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= settings.KIT_FORM_FIELDS_MAX:
                raise SpineError(422, f"{name} must be a whole number from 0 to {settings.KIT_FORM_FIELDS_MAX}")
            counts[name] = value
        checked, fixed = counts["fields_checked"], counts["fixed"]
        if fixed > checked:
            raise SpineError(422, "fixed cannot be more than fields_checked")
        fills = await _events(db, user.id, application_id, ("form_filled",))
        filled = fills[0]["payload"].get("fields_count") if fills else None
        paged = "page" in payload  # bulk-apply F2: one page's sweep is judged against its own check, not the form total
        if not paged and isinstance(filled, int) and not isinstance(filled, bool) and checked < filled:
            raise SpineError(
                422,
                f"fields_checked must be at least {filled} (the fields_count of the latest form_filled): "
                "re-read every field on the page, then record the sweep",
            )
        swept: dict[str, Any] = {"fields_checked": checked, "fixed": fixed, "by": actor}
        if "answers" in payload:
            # Owner decision 2026-10-19: what the form holds after the sweep. The proof that the
            # answers the user changed on the web (check_submit `type_first`) are now typed in.
            swept["answers"] = await _stored_answers(db, user, application_id, event_type, payload["answers"])
        if paged:
            swept["page"], swept["pages_total"] = payload["page"], payload["pages_total"]
        if "form_url" in payload:  # slice 7: the page swept, cleaned (never a token or session id)
            raw_url = payload["form_url"]
            swept_page = settings_rules.clean_form_url(raw_url if isinstance(raw_url, str) else None)
            if swept_page is None:
                raise SpineError(422, "form_swept form_url must be one plain http(s) address of the form page")
            swept["form_url"] = swept_page
        if "notes" in payload:
            raw_notes = payload["notes"]
            if not isinstance(raw_notes, str):
                raise SpineError(422, "notes must be text")
            notes = " ".join(spine._strip_control_chars(raw_notes).split())
            if len(notes) > FORM_SWEPT_NOTES_MAX:
                raise SpineError(422, f"notes must be at most {FORM_SWEPT_NOTES_MAX} characters")
            if notes:
                swept["notes"] = notes
        if "unreadable" in payload:
            bad: list[str] = []
            names = _unreadable_list(payload["unreadable"], bad)
            if bad:
                raise SpineError(422, bad[0])
            if names:
                swept["unreadable"] = names
        return swept
    if event_type == "form_checked":
        # Recorded by the assistant after check_form and the user's answers. Counts + wording only.
        found = payload_problems(event_type, payload)
        if found:
            raise SpineError(422, found[0])
        stored_checked: dict[str, Any] = {
            k: payload[k] for k in ("questions_count", "answered", "missing", "missing_required") if k in payload
        }
        if "answers" in payload:
            stored_checked["answers"] = _checked_answers(payload["answers"], [])
        if "page" in payload:
            stored_checked["page"], stored_checked["pages_total"] = payload["page"], payload["pages_total"]
        stored_checked["by"] = actor
        return stored_checked
    if event_type == "hold_released":
        _closed_payload(event_type, payload, EVENT_PAYLOAD_KEYS[event_type])
        if payload.get("reason") not in HOLD_RELEASE_REASONS:
            raise SpineError(422, "hold_released reason must be one of: " + ", ".join(sorted(HOLD_RELEASE_REASONS)))
        return {"reason": payload["reason"], "by": actor}
    if event_type in ("site_account", "account_needed"):
        _closed_payload(event_type, payload, EVENT_PAYLOAD_KEYS[event_type])  # a password key can never fit here
        return {"host": _host(event_type, payload.get("host")), "by": actor}
    if event_type == email_channel.EVENT_TYPE:
        # Slice 31: recipient, subject and the attached versions (never the body); attachments must be
        # this application's CV / letter with the kit's text hash.
        return await email_channel.check(db, user.id, application_id, payload, actor)
    raise SpineError(422, f"{event_type} is not a kit event")  # pragma: no cover


def log_kit_event(
    user: CurrentUser, application_id: int, event_type: str, stored: dict[str, Any], event_id: int
) -> None:
    """The audit line for a recorded S3 event: who, what, where, result - never CV text."""
    extra: dict[str, Any] = {
        "event": event_type, "user_id": safe_log_value(user.id), "actor": safe_log_value(actor_for(user)),
        "application_id": application_id, "event_id": event_id, "result": "ok",
    }
    for key in ("where", "version", "mode", "host", "reason", "fields_count"):
        if stored.get(key) is not None:
            extra[key] = safe_log_value(stored[key], max_len=80)
    if isinstance(stored.get("answers"), list):
        extra["answers_count"] = len(stored["answers"])  # a count, never the answers
    if event_type == email_channel.EVENT_TYPE:
        # Slice 31: the recipient's domain and the attachment count - never the address or the subject.
        audit = email_channel.audit_fields(stored)
        extra["to_domain"] = safe_log_value(audit["to_domain"], max_len=80)
        extra["attachments_count"] = int(audit["attachments_count"])
    get_audit_logger().info(event_type, extra=extra)


async def record_decision(
    db: JobDatabase, user: CurrentUser, application_id: int, event_type: str, payload: dict[str, Any],
    *, once_per_version: bool = False, fingerprint: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Validate + append one S3 event (the web buttons and the download wiring use
    this; ``record_event`` runs :func:`check_kit_event` and its own append).
    ``once_per_version``: an identical event for the same artifact + hash + where
    is returned instead of written again. ``fingerprint`` (:func:`approval_fingerprint`,
    server-computed by the web Send route only) is stored beside the checked payload.
    The caller has proven the application is the user's."""
    stored = await check_kit_event(db, user, application_id, event_type, payload)
    if fingerprint is not None:
        stored = {**stored, "fingerprint": fingerprint}
    if once_per_version:
        for ev in await _events(db, user.id, application_id, (event_type,)):
            p = ev["payload"]
            if all(p.get(k) == stored.get(k) for k in ("artifact_id", "sha256", "where")):
                return {"event_id": ev["id"], "already_existed": True}
    result = await spine.append_event(
        db, user_id=user.id, application_id=application_id, event_type=event_type,
        payload=stored, occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by=actor_for(user),
    )
    log_kit_event(user, application_id, event_type, stored, int(result["event_id"]))
    return {"event_id": result["event_id"], "already_existed": False}


# ── Answers ──────────────────────────────────────────────────────────────────


def _item(key: str, value: Any, source: str, saved_at: Optional[str], **extra: Any) -> dict[str, Any]:
    return {"key": key, "value": value, "source": source, "saved_at": saved_at, **extra}


# Every kit answer carries one of these (answer memory: who stands behind it).
ANSWER_STATES = ("confirmed", "relayed", "inferred", "sensitive")
# The profile paths a "profile" item is read from (an assistant may have written them).
_PROFILE_PATHS = {
    "contact.name": "cv_data.name", "contact.location": "cv_data.location",
    "contact.linkedin_url": "cv_data.links", "contact.github_url": "cv_data.links",
}


_SENSITIVE_RTW_SUFFIXES = (".sanctions_country_citizen", ".needs_sponsorship", ".work_authorization")


def _is_sensitive(block: str, key: str) -> bool:
    """Salary, equality, sanctions and visa status: shown to the user and confirmed on each form before use.

    Mirrors neonwatty/job-apply-plugin skills/answer-memory/SKILL.md L126-133 (the state table:
    "sensitive - Salary, authorization, visa, demographic, disability ... Never; ask before every use")
    and L142-149 (two separate decisions: use on this form vs remember); states from
    scripts/job-apply-store.py L26.
    """
    if block in ("equality", "salary") or key == "logistics.current_salary":
        return True
    return block == "right_to_work" and key.endswith(_SENSITIVE_RTW_SUFFIXES)


# The overlay path each sensitive kit block is read from (one whole-value row per path).
_SENSITIVE_PATH = {
    "equality": "user_info.equality", "salary": "preferences.salary_by_country",
    "right_to_work": "user_info.right_to_work", "logistics": "user_info.logistics",
}


def edit_is_sensitive(path: str, value: Any) -> bool:
    """True when saving ``value`` at overlay ``path`` stores a sensitive answer (equality,
    expected salary, current salary, sanctions or visa status). An empty value is the
    ``"value": null`` placeholder of SKILL.md L151 and is never sensitive."""
    if not _present(value):
        return False
    if path in ("user_info.equality", "preferences.salary_by_country"):
        return True
    if path == "user_info.logistics":
        return isinstance(value, dict) and _present(value.get("current_salary"))
    if path != "user_info.right_to_work" or not isinstance(value, dict):
        return False
    keys = [f"right_to_work.{k}" for k, v in value.items() if k != "countries" and _present(v)]
    for rec in _records(value.get("countries")):
        cc = str(rec.get("country") or "").upper()
        keys += [f"right_to_work.{cc}.{k}" for k, v in rec.items() if k != "country" and _present(v)]
    return any(_is_sensitive("right_to_work", k) for k in keys)


def answers_summary(answers: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    """How many kit answers are in each ``state``."""
    counts = dict.fromkeys(ANSWER_STATES, 0)
    for block in answers.values():
        for item in block:
            counts[str(item.get("state"))] = counts.get(str(item.get("state")), 0) + 1
    return counts


def _records(value: Any) -> list[dict[str, Any]]:
    return [r for r in (value or []) if isinstance(r, dict)]


def normalized_company_key(company: str) -> str:
    """The rule-1 company key of a free-text company name (``Job.normalized_key``; never re-implemented)."""
    from src.models import Job  # noqa: PLC0415

    return Job(title="", company=company, apply_url="", source="", date_found="").normalized_key()[0]


def _mark_sensitive(out: dict[str, list[dict[str, Any]]]) -> None:
    """``state`` = sensitive for equality, sanctions and visa items the user has NOT confirmed
    (``confirmed: false`` stays where it is, for older readers; ``confirmed_at`` is dropped). Owner
    2026-10-26: an answer the user confirmed on Memory or the profile is filled like any other fact -
    only an inferred or relayed one waits for the user on the form."""
    for block, items in out.items():
        for item in items:
            if _is_sensitive(block, str(item["key"])) and item.get("state") != "confirmed":
                item["state"] = "sensitive"
                item.pop("confirmed_at", None)


def compute_answers(
    profile: Any, overlay: list[dict[str, Any]], country: Optional[str], company_key: Optional[str] = None
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    """The answer blocks (every item tagged with its source) and the ``missing``
    list for ``country`` (the JOB's country; ``None`` = unknown). Empty / null =
    absent, not an item (rule #29); ``False`` and ``0`` are real answers.
    An approved answer with a ``company`` rides only in that company's kit: it is
    skipped when ``company_key`` differs from the company's normalized key.
    A row an assistant saved as the user's chat word (``user_said``) reads ``relayed`` until the user
    approves THAT edit on the web (profile-memory Keep writes a newer ``web`` row, so it is no longer
    the assistant's and reads ``confirmed``). No other action confirms it."""
    saved = {str(r["path"]): r.get("set_at") for r in overlay}
    # Feature 9: a path whose newest row an assistant wrote WITHOUT the user's word is an inference.
    unconfirmed = {
        str(r["path"]) for r in overlay
        if is_assistant_actor(str(r.get("set_by") or ""))
        and not is_confirmed(str(r.get("set_by") or ""), str(r.get("source") or ""))
    }
    # Relayed: an assistant wrote it as the user's chat word and the newest row is still the assistant's.
    relayed_paths = {
        str(r["path"]) for r in overlay
        if is_assistant_actor(str(r.get("set_by") or "")) and str(r.get("source") or "") == "user_said"
    }
    # Review of #1261: an assistant can save an answer with approved: true itself. A private question is
    # filled without the user's click only when the USER wrote the answers list last (web), never the
    # assistant - approved_text items carry ``by_user`` for that check.
    answers_by_user = not any(
        str(r["path"]) == "user_info.answers" and is_assistant_actor(str(r.get("set_by") or "")) for r in overlay
    )
    current = [""]

    def when(path: str) -> Optional[str]:
        current[0] = path
        return saved.get(path)

    def _mk(key: str, value: Any, source: str, saved_at: Optional[str], **extra: Any) -> dict[str, Any]:
        item = _item(key, value, source, saved_at, **extra)
        if source == "memory" and current[0] in unconfirmed and "confirmed" not in item:
            item["confirmed"] = False  # inferred by the assistant - confirm with the user before use
        if source == "approved_text":
            # approved: true = the user agreed this wording, whoever wrote the row.
            item.pop("confirmed", None)
            item["state"] = "confirmed"
        elif item.get("confirmed") is False or (source == "profile" and _PROFILE_PATHS.get(key) in unconfirmed):
            item["state"] = "inferred"
        elif current[0] in relayed_paths or (source == "profile" and _PROFILE_PATHS.get(key) in relayed_paths):
            item["state"] = "relayed"
        else:
            item["state"] = "confirmed"
        if item["state"] == "confirmed" and saved_at:
            item["confirmed_at"] = saved_at  # the reference's confirmedAt: when the user's word was saved
        return item

    info, prefs, cvd = profile.user_info, profile.preferences, profile.cv_data
    out: dict[str, list[dict[str, Any]]] = {
        "contact": [], "right_to_work": [], "logistics": [], "salary": [], "languages": [],
        "equality": [], "approved_text": [],
    }
    at = when("user_info.contact")
    for k, v in (info.contact or {}).items():
        if _present(v):
            out["contact"].append(_mk(f"contact.{k}", v, "memory", at))
    for key, value in (
        ("contact.name", cvd.name), ("contact.location", cvd.location),
        ("contact.linkedin_url", next((u for u in cvd.links or [] if _link_on(u, "linkedin.com")), "")),
        ("contact.github_url", next((u for u in cvd.links or [] if _link_on(u, "github.com")), "")),
    ):
        # A saved `contact.linkedin_url` (memory) wins over the one read off the profile links.
        if _present(value) and not any(i.get("key") == key for i in out["contact"]):
            out["contact"].append(_mk(key, value, "profile", None))

    at = when("user_info.right_to_work")
    rtw = info.right_to_work or {}
    for rec in _records(rtw.get("countries")):
        cc = str(rec.get("country") or "").upper()
        for k, v in rec.items():
            if k != "country" and _present(v) and cc:
                out["right_to_work"].append(_mk(f"right_to_work.{cc}.{k}", v, "memory", at))
    for k in ("citizenship", "sanctions_country_citizen"):
        if _present(rtw.get(k)):
            out["right_to_work"].append(_mk(f"right_to_work.{k}", rtw[k], "memory", at))

    at = when("user_info.logistics")
    lg = info.logistics or {}
    for k in ("notice_period", "earliest_start", "current_salary"):
        if _present(lg.get(k)):
            out["logistics"].append(_mk(f"logistics.{k}", lg[k], "memory", at))
    for rec in _records(lg.get("countries")):
        cc = str(rec.get("country") or "").upper()
        for k, v in rec.items():
            if k != "country" and _present(v) and cc:
                out["logistics"].append(_mk(f"logistics.{cc}.{k}", v, "memory", at))

    at = when("preferences.salary_by_country")
    for rec in _records(prefs.salary_by_country):
        cc = str(rec.get("country") or "").upper()
        # A record saved before ranges has one `amount`: it reads as min = max.
        low = rec.get("min") if _present(rec.get("min")) else rec.get("amount")
        high = rec.get("max") if _present(rec.get("max")) else low
        if cc and _present(low):
            out["salary"].append(_mk(
                f"salary.{cc}",
                {"min": low, "max": high, "currency": rec.get("currency"), "period": rec.get("period")},
                "memory", at,
            ))

    at = when("user_info.languages")
    for rec in _records(info.languages):
        if _present(rec.get("language")) and _present(rec.get("level")):
            out["languages"].append(_mk(f"languages.{rec['language']}", rec["level"], "memory", at))

    at = when("user_info.equality")
    for k, v in (info.equality or {}).items():
        if _present(v):
            out["equality"].append(_mk(f"equality.{k}", v, "memory", at, label=EQUALITY_LABEL))

    for rec in _records(info.answers):
        if rec.get("approved") is True and _present(rec.get("answer")) and _present(rec.get("question")):
            scoped = str(rec.get("company") or "").strip()
            if scoped and normalized_company_key(scoped) != company_key:
                continue  # given for ONE employer; this kit is for another (or an unknown) one
            if not fits_country(rec, country):
                continue  # a sponsorship / right-to-work answer for another country (owner, 2026-10-26)
            out["approved_text"].append(
                _mk(str(rec["question"]), rec["answer"], "approved_text", rec.get("recorded_at"),
                    by_user=answers_by_user,
                    **({"company": scoped} if scoped else {}),
                    **({"topic": rec["topic"]} if _present(rec.get("topic")) else {}))
            )

    # Remember consent (job-apply-store.py L195-199: "sensitive answer record has no remember
    # consent marker"): a sensitive value an assistant saved with no marker is not offered; it is
    # listed in `missing` so the assistant asks the user. A web write is the user: always trusted.
    consented = {
        str(r["path"]): (r.get("remember_consent_at") or (r.get("set_at") if not is_assistant_actor(
            str(r.get("set_by") or "")) else None))
        for r in overlay
    }
    dropped: list[str] = []
    for block, items in out.items():
        path = _SENSITIVE_PATH.get(block)
        if path is None or consented.get(path) or path not in saved:
            continue
        keep = [i for i in items if not _is_sensitive(block, str(i["key"]))]
        if len(keep) != len(items):
            out[block] = keep
            dropped.append(path)
    have = {i["key"] for block in out.values() for i in block}
    _mark_sensitive(out)
    # No record for the job's country: its region's record (owner decision 2026-10-26), else the
    # user's "ANY" record (every other country), answers it, so the assistant is not asked again.
    # Its facts ride along as right_to_work.<country>.<field> items; a region's salary as salary.<country>.
    if country:
        region = region_of(country)
        for via in ([region] if region else []) + ["ANY"]:
            via_items = [i for i in out["right_to_work"] if i["key"].startswith(f"right_to_work.{via}.")]
            if via_items and not any(k.startswith(f"right_to_work.{country}.") for k in have):
                for item in via_items:
                    field = item["key"].rsplit(".", 1)[1]
                    out["right_to_work"].append(
                        _mk(f"right_to_work.{country}.{field}", item["value"], "memory", item["saved_at"],
                              applies_via=via, **({"confirmed": False} if item.get("confirmed") is False else {}))
                    )
                have = {i["key"] for block in out.values() for i in block}
        if region and f"salary.{country}" not in have:
            for item in [i for i in out["salary"] if i["key"] == f"salary.{region}"]:
                out["salary"].append(
                    _mk(f"salary.{country}", item["value"], "memory", item["saved_at"],
                          applies_via=region, **({"confirmed": False} if item.get("confirmed") is False else {}))
                )
            have = {i["key"] for block in out.values() for i in block}
        _mark_sensitive(out)
    for block, items in out.items():
        for item in items:
            if _is_sensitive(block, str(item["key"])) and consented.get(_SENSITIVE_PATH.get(block, "")):
                item["remembered_at"] = consented[_SENSITIVE_PATH[block]]  # the user's OK to remember
    missing: list[dict[str, str]] = []
    for path in dropped:
        missing.append({
            "key": path,
            "why": "saved without the user's OK to remember it: ask the user, then save again with remember: true",
        })
    if country:
        for key, why in (
            (f"right_to_work.{country}.work_authorization", f"right to work in {country} is not saved"),
            (f"right_to_work.{country}.needs_sponsorship", f"sponsorship need for {country} is not saved"),
            (f"salary.{country}", f"expected salary for {country} is not saved"),
        ):
            if key not in have:
                missing.append({"key": key, "why": why})
    else:
        missing.append({"key": "job_country", "why": "the job's country is not set"})
        for key in ("right_to_work.work_authorization", "right_to_work.needs_sponsorship", "salary"):
            missing.append({"key": key, "why": "the job's country is unknown, so this cannot be checked"})
    for key in ("email", "phone", "legal_first_name", "legal_last_name"):
        if f"contact.{key}" not in have:
            missing.append({"key": f"contact.{key}", "why": "not saved yet"})
    return out, missing


# ── The kit ──────────────────────────────────────────────────────────────────


def _stored_checks(raw: Any) -> list[dict[str, Any]]:
    """``application_artifacts.checks_json`` as a list; ``[]`` when NULL or unreadable."""
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def pay_warnings(profile: Any, country: Optional[str]) -> list[dict[str, str]]:
    """Pay sanity (2026-10-13): the hiring country's expected range tops out BELOW what
    the user earns now, in the SAME currency and period. Different currency or period:
    nothing - Job360 never converts. Plain fact for the assistant to check, never a block."""
    current = (profile.user_info.logistics or {}).get("current_salary")
    if not country or not isinstance(current, dict):
        return []
    amount = current.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return []
    for rec in _records(profile.preferences.salary_by_country):
        if str(rec.get("country") or "").upper() != country:
            continue
        high = next((rec[k] for k in ("max", "min", "amount") if _present(rec.get(k))), None)
        if isinstance(high, bool) or not isinstance(high, (int, float)):
            continue
        same = (
            str(rec.get("currency") or "").upper() == str(current.get("currency") or "").upper()
            and str(rec.get("period") or "").lower() == str(current.get("period") or "").lower()
            and _present(rec.get("currency")) and _present(rec.get("period"))
        )
        if same and high < amount:
            return [{
                "code": "expected_below_current",
                "detail": (
                    f"Expected max {_num(high)} is below current {_num(amount)} "
                    "(same currency) - check with the user"
                ),
            }]
    return []


async def _document(
    db: JobDatabase, user: CurrentUser, app_row: dict[str, Any], kind: str,
    events: list[dict[str, Any]], mint: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    row = await spine.latest_artifact(db, user.id, int(app_row["id"]), kind)
    if row is None:
        return None
    from src.services.tailoring.cv_render import render_document_pdf  # noqa: PLC0415 - heavy, lazy (rule #16)

    sha = sha256_text(row["text"])
    size = len(await render_document_pdf(user.id, kind, row["text"]))
    stem = await download_stem(
        user.id, str(app_row.get("job_company") or ""), kind, int(row["version_no"])
    )
    cur = await db._db.execute(
        "SELECT checks_json FROM application_artifacts WHERE id = ? AND user_id = ?", (row["id"], user.id)
    )
    got = await cur.fetchone()
    seen = approved = None
    for ev in events:
        p = ev["payload"]
        if p.get("artifact_id") == row["id"] and p.get("sha256") == sha:
            if seen is None and ev["event_type"] in ("cv_seen", "submit_approved"):
                seen = _who(ev)
            if approved is None and ev["event_type"] == "submit_approved":
                approved = _who(ev)
    doc = {
        "artifact_id": row["id"], "version": row["version_no"], "label": row.get("label") or "",
        "text": row["text"], "sha256": sha, "chars": len(row["text"]),
        "seen": seen, "approved": approved,
        # Plain facts found when this version was saved (cv_checks.py); [] = none / not stored.
        "checks": _stored_checks(got[0] if got else None),
        "file": {
            "url": "", "expires_at": "", "downloads_left": settings.KIT_LINK_MAX_DOWNLOADS,
            "filename": f"{stem}.pdf",
            "mime": "application/pdf", "size": size,
        },
    }
    mint.append(doc)
    return doc


async def _mint_link(
    db: JobDatabase, user: CurrentUser, application_id: int, doc: dict[str, Any], now: datetime
) -> None:
    """A fresh random token per document per kit call; only its hash is stored."""
    token = secrets.token_urlsafe(32)
    expires = (now + timedelta(minutes=settings.KIT_LINK_TTL_MINUTES)).isoformat()
    await db._db.execute(
        "INSERT INTO artifact_links (token_hash, user_id, application_id, artifact_id, version_no, fmt, "
        "expires_at, downloads_left, created_by, created_at) VALUES (?, ?, ?, ?, ?, 'pdf', ?, ?, ?, ?)",
        (
            hash_token(token), user.id, application_id, doc["artifact_id"], doc["version"], expires,
            settings.KIT_LINK_MAX_DOWNLOADS, actor_for(user), now.isoformat(),
        ),
    )
    doc["file"]["url"] = f"{settings.SITE_BASE_URL}/api/files/{token}"
    doc["file"]["expires_at"] = expires
    get_audit_logger().info(
        "file_link_created",
        extra={
            "event": "file_link_created", "user_id": safe_log_value(user.id),
            "actor": safe_log_value(actor_for(user)), "application_id": application_id,
            "artifact_id": doc["artifact_id"], "version": doc["version"], "result": "ok",
        },
    )


SESSION_MAX_CHARS = 60
SESSION_RE = re.compile(r"^[A-Za-z0-9 ._:-]*$")
UNSURE_HOLD_DETAIL = (
    "another session of this assistant may be working on this application; pass `session` to tell sessions apart"
)


def clean_session(raw: Any) -> str:
    """The assistant session's own label (weak spot #3, 2026-10-15): stripped, at most
    ``SESSION_MAX_CHARS``, only letters, digits, space and ``. _ : -``. ``""`` = not
    given. Raises ``SpineError`` 422 otherwise. It is DATA: never markup, never logged raw."""
    text = (raw if isinstance(raw, str) else "").strip()
    if len(text) > SESSION_MAX_CHARS or not SESSION_RE.match(text):
        raise SpineError(
            422,
            f"session must be a short label of at most {SESSION_MAX_CHARS} characters using only letters, "
            "digits, spaces and . _ : - (for example \"desktop-apply\"); pick one per conversation",
        )
    return text


async def live_hold(
    db: JobDatabase, user: CurrentUser, application_id: int, now: datetime, session: str = ""
) -> Optional[dict[str, Any]]:
    """Another session's live hold: the newest kit read inside ``KIT_HOLD_MINUTES`` whose
    ``(recorded_by, session)`` differs from the caller's ``(actor, session)``, with no later
    applied / withdrawn / rejected / hold_released by ANY actor. A kit read with no
    ``session`` key (before 2026-10-15) counts as session ``""``; the same actor with both
    sessions ``""`` is the same holder. An UNLABELLED caller after a labelled read by its
    own actor gets ``unsure: true`` (it may be another session of the same assistant).
    Warns in the kit; check_submit asks ``held_by_other_session`` on a sure hold."""
    actor = actor_for(user)
    cutoff = (now - timedelta(minutes=settings.KIT_HOLD_MINUTES)).isoformat()
    cur = await db._db.execute(
        "SELECT id, recorded_at, recorded_by, payload FROM application_events WHERE application_id = ? "
        "AND user_id = ? AND event_type = 'kit_read' AND recorded_at >= ? ORDER BY id DESC LIMIT 200",
        (application_id, user.id, cutoff),
    )
    reads = [dict(r) for r in await cur.fetchall()]
    if not reads:
        return None
    cur = await db._db.execute(
        "SELECT MAX(id) FROM application_events WHERE application_id = ? AND user_id = ? "
        "AND event_type IN ('applied', 'withdrawn', 'rejected', 'hold_released')",
        (application_id, user.id),
    )
    got = await cur.fetchone()
    cleared_upto = int(got[0]) if got is not None and got[0] is not None else 0
    return _pick_hold(reads, cleared_upto, actor, session)


def _pick_hold(
    reads: list[dict[str, Any]], cleared_upto: int, actor: str, session: str
) -> Optional[dict[str, Any]]:
    """The hold shape of :func:`live_hold` from kit reads (newest first) and the id of the
    newest releasing event. Shared with :func:`holds_for_apps` so the two can never disagree."""
    sure: Optional[dict[str, Any]] = None
    unsure: Optional[dict[str, Any]] = None
    for r in reads:  # newest first
        if int(r["id"]) <= cleared_upto:
            break  # this read and every older one were released
        their = str(_json(r.get("payload")).get("session") or "")
        if r["recorded_by"] != actor or (session and their != session):
            sure = sure or {**r, "session": their}
        elif not session and their:
            unsure = unsure or {**r, "session": their}
    found = sure or unsure
    if found is None:
        return None
    since = datetime.fromisoformat(str(found["recorded_at"]))
    hold: dict[str, Any] = {
        "held_by": found["recorded_by"], "session": found["session"], "since": found["recorded_at"],
        "until": (since + timedelta(minutes=settings.KIT_HOLD_MINUTES)).isoformat(), "unsure": sure is None,
    }
    if sure is None:
        hold["detail"] = UNSURE_HOLD_DETAIL
    return hold


# The headline stage of a held application. Verb + "..." while the assistant works, a plain
# state once it waits. Job360's own wording (NOT IN REFERENCE: Tsenta's static dashboard shows
# "Tailoring résumé" / "Reading job description…" but not how the string is derived).
STAGE_LABELS = {
    "kit_read": "Reading the kit…", "form_filled": "Form filled", "form_swept": "Ready to submit",
    "blocked": "Needs you",
}
_RELEASING = ("applied", "withdrawn", "rejected", "hold_released")


async def holds_for_apps(
    db: JobDatabase, user_id: str, application_ids: list[int], now: datetime
) -> dict[int, tuple[dict[str, Any], str]]:
    """``{application_id: (hold, stage)}`` for the applications with a live hold, in TWO queries
    for the whole page. The web user is the viewer: it holds no session of its own, so every
    kit read in the window counts (``unsure`` holds included, shown softer). ``stage`` is
    :data:`STAGE_LABELS` of the newest kit_read / form_filled / form_swept / blocked after the
    last release. Applications without a live hold are absent."""
    if not application_ids:
        return {}
    marks = ",".join("?" for _ in application_ids)
    cutoff = (now - timedelta(minutes=settings.KIT_HOLD_MINUTES)).isoformat()
    cur = await db._db.execute(
        f"SELECT id, application_id, event_type, recorded_at, recorded_by, payload FROM application_events "  # noqa: S608 - placeholders only
        f"WHERE user_id = ? AND application_id IN ({marks}) AND recorded_at >= ? "
        f"AND event_type IN ('kit_read', 'form_filled', 'form_swept', 'blocked') ORDER BY id DESC",
        [user_id, *application_ids, cutoff],
    )
    events: dict[int, list[dict[str, Any]]] = {}
    for r in await cur.fetchall():
        row = dict(r)
        events.setdefault(int(row["application_id"]), []).append(row)
    if not events:
        return {}
    held_ids = list(events)
    marks = ",".join("?" for _ in held_ids)
    rel = ",".join("?" for _ in _RELEASING)
    cur = await db._db.execute(
        f"SELECT application_id, MAX(id) FROM application_events WHERE user_id = ? "  # noqa: S608 - placeholders only
        f"AND application_id IN ({marks}) AND event_type IN ({rel}) GROUP BY application_id",
        [user_id, *held_ids, *_RELEASING],
    )
    cleared = {int(r[0]): int(r[1]) for r in await cur.fetchall()}
    out: dict[int, tuple[dict[str, Any], str]] = {}
    for app_id, rows in events.items():
        upto = cleared.get(app_id, 0)
        reads = [r for r in rows if r["event_type"] == "kit_read"]
        hold = _pick_hold(reads, upto, "", "")
        if hold is None:
            continue
        newest = next((r for r in rows if int(r["id"]) > upto), None)  # rows are newest first
        out[app_id] = (hold, STAGE_LABELS.get(str(newest["event_type"]), "") if newest else "")
    return out


def hold_fact(hold: Optional[dict[str, Any]]) -> Optional[tuple[str, str, str]]:
    """The gate's view of a hold: ``(held_by, session, since)`` for a SURE hold, else ``None``."""
    if not hold or hold.get("unsure"):
        return None
    return (str(hold["held_by"]), str(hold.get("session") or ""), str(hold["since"]))


async def _job_company_key(db: JobDatabase, app_row: dict[str, Any]) -> str:
    cur = await db._db.execute("SELECT normalized_company FROM jobs WHERE id = ?", (app_row.get("job_id"),))
    job_row = await cur.fetchone()
    return str(dict(job_row).get("normalized_company") or "") if job_row is not None else ""


async def _application_answers(
    db: JobDatabase, user: CurrentUser, app_row: dict[str, Any]
) -> tuple[Any, Optional[str], dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    """The profile, the job's country and :func:`compute_answers` for ONE application - the single
    place the kit and the form check load them (same profile, overlay, country and company key)."""
    return await _answers_for(db, user.id, app_row)


async def _answers_for(
    db: JobDatabase, user_id: str, app_row: dict[str, Any]
) -> tuple[Any, Optional[str], dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    """:func:`_application_answers` by user id (the receipt has no ``CurrentUser``)."""
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile_with_overlay  # noqa: PLC0415

    loaded, overlay = load_profile_with_overlay(user_id)
    profile = loaded or UserProfile()
    country = (str(app_row.get("job_country") or "").strip().upper()) or None
    job_company_key = await _job_company_key(db, app_row)
    answers, missing = compute_answers(profile, overlay, country, job_company_key or None)
    return profile, country, answers, missing


def kit_missing(missing: list[dict[str, str]]) -> list[dict[str, str]]:
    """The kit's ``missing`` without salary (owner, 2026-10-23): a form that never asks about pay made it
    noise, and check_form reports salary when a live form does ask. Every other entry stays."""
    return [
        m for m in missing
        if m["key"] not in ("salary", _SENSITIVE_PATH["salary"]) and not m["key"].startswith("salary.")
    ]


async def build_kit(
    db: JobDatabase, user: CurrentUser, application_id: int, *, now: datetime, include_text: bool = True,
    session: str = "",
) -> dict[str, Any]:
    """Build the kit for one application and record that it was read.
    ``session`` is the caller's own label (:func:`clean_session`, already cleaned):
    it is stored on the ``kit_read`` and tells two sessions of one assistant apart.

    404 for an application that is not the caller's (no link row is written);
    429 over ``KIT_READS_MAX_PER_HOUR``. Writes one ``kit_read`` event (the
    timeline shows who read it and when) and one ``artifact_links`` row per
    document. The token appears only in this return value."""
    from src.api.auth_deps import email_verification_satisfied  # noqa: PLC0415 - the one verification rule
    from src.api.routes.profile import settings_view  # noqa: PLC0415 - the one read model of the settings

    actor = actor_for(user)
    log = get_audit_logger()
    app_row = await spine.get_owned_application(db, user.id, application_id)
    if app_row is None:
        log.warning("kit_read_refused", extra={
            "event": "kit_read_refused", "user_id": safe_log_value(user.id), "actor": safe_log_value(actor),
            "application_id": application_id, "status": 404, "result": "refused",
        })
        raise SpineError(404, "application not found")
    hour_ago = (now - timedelta(hours=1)).isoformat()
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM application_events WHERE user_id = ? AND event_type = 'kit_read' AND recorded_at >= ?",
        (user.id, hour_ago),
    )
    got = await cur.fetchone()
    if got and int(got[0]) >= settings.KIT_READS_MAX_PER_HOUR:
        log.warning("kit_read_refused", extra={
            "event": "kit_read_refused", "user_id": safe_log_value(user.id), "actor": safe_log_value(actor),
            "application_id": application_id, "status": 429, "result": "refused",
        })
        raise SpineError(
            429, f"too many kit reads this hour (KIT_READS_MAX_PER_HOUR = {settings.KIT_READS_MAX_PER_HOUR})"
        )

    profile, country, answers, missing = await _application_answers(db, user, app_row)
    missing = kit_missing(missing)
    answers_omitted = 0
    approved = answers["approved_text"]
    if len(approved) > settings.KIT_APPROVED_TEXT_MAX:
        # Keep the newest whole items (original order preserved); the rest stay in the profile.
        newest = sorted(range(len(approved)), key=lambda i: (str(approved[i].get("saved_at") or ""), i))
        keep = set(newest[-settings.KIT_APPROVED_TEXT_MAX:])
        answers_omitted = len(approved) - len(keep)
        answers["approved_text"] = [it for i, it in enumerate(approved) if i in keep]
    hold = await live_hold(db, user, application_id, now, session)
    state = await controls_state(db, user.id, app_row, now)
    from src.services.applications import known_people as known_people_service  # noqa: PLC0415

    people_here = await known_people_service.for_application(db, user.id, app_row)
    events = await _events(db, user.id, application_id, ("cv_seen", "submit_approved"))

    docs: list[dict[str, Any]] = []
    cv = await _document(db, user, app_row, "cv", events, docs)
    letter = await _document(db, user, app_row, "cover_letter", events, docs)
    for doc in docs:
        await _mint_link(db, user, application_id, doc, now)

    apply_url = str(app_row.get("job_url") or "")
    remote = app_row.get("job_remote")
    # Plus the facts from this user's own records (posting_history.py); never raises.
    from src.services.applications.posting_history import history_checks  # noqa: PLC0415

    base_checks = checks_for_application(app_row)
    base_checks += await history_checks(db, user.id, app_row, list(base_checks))
    job = {
        "job_id": app_row.get("job_id"), "title": app_row.get("job_title") or "",
        "company": app_row.get("job_company") or "", "location": app_row.get("job_location") or "",
        "country": country, "remote": None if remote is None else bool(remote),
        "apply_url": apply_url, "found_on": app_row.get("job_found_on"),
        "ad_posted_at": app_row.get("ad_posted_at") or None,
        "employer_published_at": app_row.get("employer_published_at") or None,
        # Plain facts about this ad (job_checks.py) - never a verdict, never a sort key.
        "checks": base_checks,
    }
    from src.services.applications import contacts as contacts_service  # noqa: PLC0415 - contacts imports spine

    application = {
        "id": application_id, "status": app_row["status"], "follow_up_on": app_row.get("follow_up_on"),
        "submit_override": await spine.submit_override(db, user.id, application_id),
        "referred_by": await contacts_service.referred_by(db, user.id, application_id),
    }
    host = settings_rules.parse_site_host(apply_url)
    learned = {
        str(e["payload"]["host"])
        for e in await _user_events(db, user.id, ("account_needed",))
        if e["payload"].get("host")
    }
    accounts = await _user_events(db, user.id, ("site_account",))
    known = next((e for e in accounts if host and e["payload"].get("host") == host), None)
    account_site = {
        "host": host, "likely_needs_account": host_needs_account(host, learned),
        "known_account": {"recorded_by": known["recorded_by"], "recorded_at": known["recorded_at"]} if known else None,
    }
    digest_body = {
        "job": job, "application": application,
        "cv": _strip_file(cv), "cover_letter": _strip_file(letter), "answers": answers, "missing": missing,
    }
    kit_sha = hashlib.sha256(
        json.dumps(digest_body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()

    settings_block = settings_view(profile, user.id)
    verdict = await submit_verdict(
        db, user.id, app_row, apply_url, now,
        email_verified=email_verification_satisfied(user), assume_filled=True, other_session_hold=hold_fact(hold),
    )
    preview = {"decision": verdict.decision, "reason": verdict.reason, "detail": verdict.detail}
    settings_block = {**settings_block, "submit_preview": preview}

    event = await spine.append_event(
        db, user_id=user.id, application_id=application_id, event_type="kit_read",
        payload={
            "kit_sha256": kit_sha, "cv_artifact_id": cv["artifact_id"] if cv else None,
            "cover_letter_artifact_id": letter["artifact_id"] if letter else None,
            "links": [
                {"artifact_id": d["artifact_id"], "version": d["version"], "expires_at": d["file"]["expires_at"]}
                for d in docs
            ],
            "by": actor, "session": session,
        },
        occurred_at=now.isoformat(), recorded_by=actor,
    )
    dup = state["duplicate"]
    log.info("kit_read", extra={
        "event": "kit_read", "user_id": safe_log_value(user.id), "actor": safe_log_value(actor),
        "application_id": application_id, "kit_event_id": event["event_id"], "missing_count": len(missing),
        "hold": bool(hold), "session": safe_log_value(session, max_len=SESSION_MAX_CHARS),
        "duplicate": dup["flag"], "surface": "mcp" if user.auth_via != "session" else "web",
        "result": "ok", "known_people_count": people_here["count"],
    })
    out: dict[str, Any] = {
        "kit": {"id": event["event_id"], "sha256": kit_sha, "generated_at": now.isoformat()},
        "job": job, "application": application,
        "cv": _cap_text(cv, include_text), "cover_letter": _cap_text(letter, include_text),
        "answers": answers, "answers_summary": answers_summary(answers), "missing": missing,
        "settings": settings_block,
        "duplicate": dup,
        "hold": hold, "account_site": account_site,
        "autofill": state["autofill"]["mode"],
        "warnings": pay_warnings(profile, country),
        "known_people": people_here,
        "last_fill": await last_fill(db, user.id, application_id),
        "instructions": list(KIT_INSTRUCTIONS),
    }
    if answers_omitted:
        out["answers_omitted"] = answers_omitted
        out["instructions"].append(
            "More approved answers: get_profile fields['user_info.answers']"
        )
    if people_here["count"]:
        out["instructions"].append(
            f"known_people: {people_here['count']} people you already stored at this company "
            "(list_people(company=...) lists them all). Read them before finding someone new; "
            "whether to reach out is the user's call."
        )
    if cv is None:
        out["cv_none_reason"] = NO_CV_REASON
    if letter is None:
        out["cover_letter_none_reason"] = NO_LETTER_REASON
    return out


# ── Form check: which questions on the LIVE form already have a saved answer ──

FORM_NEXT_ASK = (
    "Ask the user ONCE, in one message, for every question in `missing`; "
    "save reusable answers with update_profile (user_info.answers); never guess."
)
FORM_NEXT_FILL = "Every question has a saved answer. Fill the form with these values."
FORM_NEXT_CONFIRM = (
    " Confirm every `needs_confirmation` answer with the user in that same message, and raise each one with "
    "ask_user so it shows on Needs you: the user checks it there before anything is sent."
)
FORM_NEXT_KNOCKOUT = " `knockouts` is not empty: tell the user BEFORE drafting or typing anything (`stop_for_user`)."
_NON_ALNUM_RE = re.compile(r"[\W_]+")  # a run of anything that is not a letter or digit
# NOT IN REFERENCE (kept small, deterministic): which kit groups / which wording count as sensitive or a knock-out.
# The wording follows career-ops modes/apply.md:266 (needs_candidate_confirmation) and :65-68 (knock-out areas).
_SENSITIVE_GROUPS = frozenset({"right_to_work", "salary", "equality"})
_SENSITIVE_RE = re.compile(
    r"visa|sponsor|work authori[sz]ation|right to work|eligible to work|relocat|salary|compensation|"
    r"disabilit|veteran|gender|ethnic|\brace\b|sexual|religio|criminal|convict|background check|"
    r"self.?identif|demographic|"
    # UK wording and legal terms (gap audit #14). The text is only casefolded, so SC / DV count only next to
    # "clear..." ("SC or DV security clearance", "DV cleared") - never "B. Sc." or "Charleston, SC".
    r"authori[sz]ed to work|entitled to work|permission to work|security clearance|"
    r"\b(?:sc|dv)\b.{0,30}\bclear|\bclearance\b.{0,30}\b(?:sc|dv)\b|\bbpss\b|security vetting|"
    r"vetting (?:check|process|level)|(?:undergo|pass|hold) (?:\w+ )?vetting|"
    r"day.?rate|expected (?:pay|salary)|notice period|\bitar\b|export controls?|non.?compete|"
    r"interview record(?:ing)?|record(?:ing)? (?:of )?(?:the |this )?interview|"
    r"previously (?:been )?employed (?:by|at|with)|"
    # Any country, any job (owner, 2026-10-22): identity, health, money and legal history are the user's to
    # answer, never typed from a guess. "health" and "age" alone are too broad ("healthcare", "page"), so
    # only the asking phrases count.
    r"nationality|citizen(?:ship)?\b|immigration status|national origin|(?:place|country) of birth|"
    r"passport|national insurance|social security|\bssn\b|tax (?:id|number)|"
    r"date of birth|\bdob\b|\bhow old\b|marital|pregnan|"
    r"medical (?:condition|history|exam)|health (?:condition|issue|problem|status)|mental health|"
    r"drug (?:test|screen)|vaccinat|transgender|\blgbt|political|trade union|union member|\bcaste\b|"
    r"bankrupt|credit (?:check|score|history)|financial (?:history|status)|salary history|"
    r"(?:current|previous) (?:salary|pay)\b|arrest|felony|misdemeanou?r|military|armed forces"
)
_KNOCKOUT_GROUPS = frozenset({"right_to_work", "salary"})
_KNOCKOUT_RE = re.compile(
    r"\byears?\b.{0,30}\bexperience|\bexperience\b.{0,30}\byears?\b|\bdegree\b|bachelor|master'?s|"
    r"sponsor|\bvisa\b|work authori[sz]ation|right to work|salary|compensation expectation|"
    r"authori[sz]ed to work|entitled to work|driving licen[cs]e|"
    # Age, only as an age: "over 18?", "aged 18 or over" - not "over 180 countries" or "over 18 engineers".
    r"\b(?:over|at least) (?:the age of )?18\b(?!\s*(?:months|weeks|days|people|engineers|countries|staff|members))|"
    r"\b18 (?:years )?(?:of age )?or (?:over|older)\b"
)
_OPTION_FIELDS = frozenset({"select", "radio", "checkbox"})
# A FILE question that asks for the CV or the cover letter: Job360's browser tool uploads the saved artifact.
_CV_FILE_RE = re.compile(r"\bcv\b|\bresume\b|résumé|curriculum vitae")
_LETTER_FILE_RE = re.compile(r"cover(?:ing)? letter|motivation(?:al)? letter|letter of motivation")
_DOCUMENT_LABELS = {"cv": "CV", "cover_letter": "Cover letter"}


def _norm_question(text: Any) -> str:
    """Casefold, every non-alphanumeric run -> one space, stripped. No fuzzy or semantic matching."""
    return _NON_ALNUM_RE.sub(" ", str(text or "").casefold()).strip()


def _question_is_sensitive(text: str, key: Optional[str]) -> bool:
    group = (key or "").split(".", 1)[0]
    return group in _SENSITIVE_GROUPS or bool(_SENSITIVE_RE.search(text.casefold()))


def _is_knockout(text: str, key: Optional[str]) -> bool:
    group = (key or "").split(".", 1)[0]
    return group in _KNOCKOUT_GROUPS or bool(_KNOCKOUT_RE.search(text.casefold()))


def _over_limit(value: Any, limit: Any, unit: str) -> bool:
    """career-ops apply.md:273 - the limit is the live control's own; Job360 only compares, never cuts."""
    if not isinstance(limit, int) or not isinstance(value, str):
        return False
    return (len(value.split()) if unit == "words" else len(value)) > limit


def _record_item(rec: dict[str, Any], by_user: bool = False) -> dict[str, Any]:
    # approved: true = the user stood behind this wording (the kit's approved_text reads "confirmed" too);
    # ``by_user`` = the user, not an assistant, wrote the answers list last.
    return {"key": str(rec["question"]), "value": rec["answer"], "source": "approved_text", "state": "confirmed",
            "by_user": by_user}


def _file_kind(text: str) -> Optional[str]:
    """The artifact kind a file question asks for, or None (portfolio, transcript, ID, "CV or cover letter")."""
    low = text.casefold()
    is_cv, is_letter = bool(_CV_FILE_RE.search(low)), bool(_LETTER_FILE_RE.search(low))
    return "cv" if is_cv and not is_letter else "cover_letter" if is_letter and not is_cv else None


def form_check(
    answers: dict[str, list[dict[str, Any]]], questions: list[dict[str, Any]],
    records: Any = None, company_key: Optional[str] = None, documents: Optional[dict[str, dict[str, Any]]] = None,
    country: Optional[str] = None,
) -> dict[str, Any]:
    """Pure: for each form question, a saved answer from ``answers`` (the output of
    :func:`compute_answers`) or a gap. A question with a ``key`` must name an item that
    really is in the kit (an ``approved_text`` item is also matched by normalized
    question); else the key may be a saved ``topic``. Without a key the question is matched
    against the approved ``records`` (``user_info.answers``) by
    :func:`answer_match.resolve_answer`: topic, then wording or alias, the company's own answer
    before the general one; two answers that disagree are a gap (fail closed), never a guess.
    Also reads the control facts the assistant passed: a saved value that is
    not one of a select/radio/checkbox's ``options`` is a gap, one longer than the
    control's ``limit`` is flagged ``over_limit`` (never cut), a sensitive question is
    flagged ``needs_confirmation`` and a knock-out question is listed in ``knockouts``.
    A ``file`` question for the CV or the cover letter is answered by the application's newest saved
    artifact of that kind (``documents``: kind -> artifact row), source ``artifact``; none saved = a gap.
    Never logs, never writes, never guesses."""
    from src.services.profile.answer_match import NEVER_REUSED_TOPIC_PREFIXES, resolve_answer  # noqa: PLC0415

    items = [i for block in answers.values() for i in block]
    by_key = {str(i["key"]): i for i in items}
    answers_by_user = any(i.get("by_user") for i in answers.get("approved_text", []))
    approved = {
        _norm_question(i["key"]): i for i in answers.get("approved_text", [])
        if not str(i.get("topic") or "").casefold().startswith(NEVER_REUSED_TOPIC_PREFIXES)
    }
    answered: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    knockouts: list[dict[str, Any]] = []
    confirm: list[str] = []
    for q in questions:
        text = str(q["question"])
        required = bool(q.get("required", True))
        key = (q.get("key") or "").strip() or None
        ftype = str(q.get("field_type") or "unknown")
        sensitive = _question_is_sensitive(text, key)
        item: Optional[dict[str, Any]] = None
        matched_by = "key"
        why = ""
        if key is not None:
            item = by_key.get(key)
            if item is None:
                item = approved.get(_norm_question(key)) if _norm_question(key) else None
            if item is None:
                rec, note = resolve_answer(records, topic=key, company_key=company_key, country=country)
                if note:
                    why = note
                elif rec is not None:
                    item = _record_item(rec, answers_by_user)
                    matched_by = "topic"
            if item is None and not why:
                why = f"key '{key}' is not in this application's kit"
        else:
            rec, note = resolve_answer(records, question=text, company_key=company_key, country=country)
            matched_by = "wording"
            if note:
                why = note
            elif rec is not None:
                item = _record_item(rec, answers_by_user)
            else:
                item = approved.get(_norm_question(text)) if _norm_question(text) else None
            if item is None and not why:
                why = "no saved answer for this question"
        kind = _file_kind(text) if ftype == "file" and item is None else None
        if kind is not None:
            label = _DOCUMENT_LABELS[kind]
            doc = (documents or {}).get(kind)
            if doc is None:
                why = f"no {label} saved for this application yet: save the {label} first (save_artifact kind {kind})"
            else:
                why = ""
                item = {
                    "key": f"artifact.{kind}", "source": "artifact",
                    "value": f"{label} v{doc['version_no']} (artifact {doc['id']})",
                }
                matched_by = "key" if key is not None else "wording"
        options = [str(o) for o in (q.get("options") or [])]
        if item is not None and ftype in _OPTION_FIELDS and options and isinstance(item["value"], str) \
                and item["value"].casefold() not in {o.casefold() for o in options}:
            why = "saved answer is not one of the form's options"
            item = None
        # A knock-out is spotted from the question and key alone - never from a gap in the profile or CV
        # (career-ops apply.md:72: a missing field is not evidence the candidate lacks the credential).
        if _is_knockout(text, key):
            knockouts.append({"question": text, "key": key, **({"value": item["value"]} if item is not None else {})})
        if item is None:
            row_m: dict[str, Any] = {"question": text, "required": required, "why": why}
            if ftype != "unknown":
                row_m["field_type"] = ftype
            if sensitive:
                row_m["needs_confirmation"] = True
                confirm.append(text)
            missing.append(row_m)
            continue
        row: dict[str, Any] = {
            "question": text, "required": required, "key": item["key"], "value": item["value"],
            "source": item["source"], "matched_by": matched_by,
        }
        if ftype != "unknown":
            row["field_type"] = ftype
        if item.get("confirmed") is False:
            row["confirmed"] = False
        if _over_limit(item["value"], q.get("limit"), str(q.get("limit_unit") or "chars")):
            row["over_limit"] = True
        # A private question answered with the user's own confirmed answer is filled as it is (owner,
        # 2026-10-26); only an answer the user has not stood behind waits for them on this form.
        owned = item.get("state") == "confirmed" and (item.get("source") != "approved_text" or item.get("by_user"))
        if (sensitive and not owned) or item.get("confirmed") is False:
            row["needs_confirmation"] = True
            confirm.append(text)
        answered.append(row)
    missing_required = sum(1 for m in missing if m["required"])
    nxt = FORM_NEXT_ASK if missing else FORM_NEXT_FILL
    if confirm:
        nxt += FORM_NEXT_CONFIRM
    if knockouts:
        nxt += FORM_NEXT_KNOCKOUT
    out: dict[str, Any] = {
        "questions_count": len(questions), "answered": answered, "missing": missing,
        "counts": {"answered": len(answered), "missing": len(missing), "missing_required": missing_required},
        "needs_confirmation": confirm, "knockouts": knockouts, "next": nxt,
    }
    if knockouts:
        out["stop_for_user"] = True
    return out


async def check_form(
    db: JobDatabase, user: CurrentUser, application_id: int, *,
    questions: list[dict[str, Any]], form_url: str = "", now: datetime,
) -> dict[str, Any]:
    """Read-only: look up a saved answer for every question on the live form. Loads the
    answers exactly as :func:`build_kit` does (same profile, overlay, country, company
    key). 404 for an application that is not the caller's. Writes NO event. Logs counts
    only - never a question or an answer value."""
    log = get_audit_logger()
    app_row = await spine.get_owned_application(db, user.id, application_id)
    if app_row is None:
        log.warning("form_check", extra={
            "event": "form_check", "user_id": safe_log_value(user.id), "application_id": application_id,
            "questions_count": len(questions), "answered": 0, "missing": 0, "missing_required": 0,
            "result": "refused",
        })
        raise SpineError(404, "application not found")
    profile, country, answers, _missing = await _application_answers(db, user, app_row)
    documents = {}
    for kind in _DOCUMENT_LABELS:
        row = await spine.latest_artifact(db, user.id, application_id, kind)
        if row is not None:
            documents[kind] = row
    out = form_check(
        answers, questions, profile.user_info.answers, await _job_company_key(db, app_row) or None, documents,
        country=country,
    )
    log.info("form_check", extra={
        "event": "form_check", "user_id": safe_log_value(user.id), "application_id": application_id,
        "questions_count": out["questions_count"], "answered": out["counts"]["answered"],
        "missing": out["counts"]["missing"], "missing_required": out["counts"]["missing_required"],
        "result": "ok",
    })
    return {"application_id": application_id, **out}


TEXT_MORE = "get_application(application_id, with_artifact_text=true)"


def _cap_text(doc: Optional[dict[str, Any]], include_text: bool) -> Optional[dict[str, Any]]:
    """Output copy of a document with ``text`` cut to ``KIT_DOC_TEXT_MAX_CHARS``. The sha256 / chars
    stay those of the FULL text (cv_seen / submit_approved compare them); the kit sha is already
    computed from the full text. ``include_text=False`` sends no text, only the pointer."""
    if doc is None:
        return None
    text = str(doc.get("text") or "")
    cap = settings.KIT_DOC_TEXT_MAX_CHARS
    if include_text and len(text) <= cap:
        return doc
    return {**doc, "text": text[:cap] if include_text else "", "text_truncated": True, "text_more": TEXT_MORE}


def _strip_file(doc: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    return None if doc is None else {k: v for k, v in doc.items() if k != "file"}


async def _user_events(db: JobDatabase, user_id: str, types: tuple[str, ...]) -> list[dict[str, Any]]:
    """The user's events of these types across ALL their applications, newest first."""
    marks = ",".join("?" for _ in types)
    cur = await db._db.execute(
        "SELECT id, event_type, payload, recorded_at, recorded_by FROM application_events "  # noqa: S608
        f"WHERE user_id = ? AND event_type IN ({marks}) ORDER BY id DESC LIMIT 500",
        (user_id, *types),
    )
    rows = []
    for r in await cur.fetchall():
        d = dict(r)
        d["payload"] = _json(d.get("payload"))
        rows.append(d)
    return rows

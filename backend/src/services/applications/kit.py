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
* :func:`duplicate_facts` - the duplicate flag (kit, receipt and gate).

Logs name the user, the application and a closed result. They never carry a
token, a link, CV text or an answer value.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlsplit

from src.core import settings
from src.services.applications import blocked as blocked_service
from src.services.applications import spine
from src.services.applications.authorship import actor_for
from src.services.applications.spine import SpineError
from src.services.profile import assistant_settings as settings_rules
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

SERVER_ONLY_EVENTS = frozenset({"kit_read"})
KIT_EVENT_TYPES = frozenset({
    "kit_read", "cv_seen", "submit_approved", "submit_declined", "autofill_set", "duplicate_cleared",
    "form_filled", "hold_released", "site_account", "account_needed",
})
HOLD_RELEASE_REASONS = frozenset({"done", "blocked", "stopped", "failed"})
SEEN_WHERE = frozenset({"web", "chat"})
EQUALITY_LABEL = "equality / voluntary"
NO_CV_REASON = "no CV saved for this application"
NO_LETTER_REASON = "no cover letter saved for this application"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")

KIT_INSTRUCTIONS: tuple[str, ...] = (
    "Use only the answers in this kit. Anything in `missing`: ask the user ONCE, in one message. Never guess.",
    "Upload file: desktop or Claude Code - download `file.url` to a local file and upload that. "
    "Chat app - ask the user to attach the PDF once for this job. Otherwise paste `text` if the form allows, "
    "else the user uploads it by hand. An expired link: call the kit again.",
    "Show the CV to the user. When the user says it is OK in chat, record_event cv_seen with where=chat. "
    "When the user says yes to submitting, record_event submit_approved with where=chat.",
    "`autofill` deny: do not type into the form - give the user the kit answers to paste. Still record form_filled.",
    "After filling the form, record_event form_filled {form_url, fields_count}. Then call check_submit.",
    "`hold` set: another assistant is on this application - tell the user. `duplicate` flagged: warn before any work.",
    "Account site: stop. The user signs up and signs in themselves (never ask for or store a password), "
    "then record_event site_account {host} and continue. Hit a sign-in wall on any other site: "
    "record_event account_needed {host}.",
)


# ── Small pure helpers ───────────────────────────────────────────────────────


def sha256_text(text: str) -> str:
    """The hash a CV is identified by: sha256 of its TEXT (PDF bytes are not stable)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def hash_token(token: str) -> str:
    """What is stored for a file-link token (the token itself never is)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def file_stem(company: str, kind: str, version_no: int) -> str:
    """``northwind-cv-v3`` - the one download file name rule (web + link)."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(company or "").lower()).strip("-")[:40]
    return "-".join(p for p in (slug, kind.replace("_", "-"), f"v{version_no}") if p)


def norm_url(url: str) -> str:
    """Lowercase scheme + host, drop the fragment, strip a trailing slash;
    the query is kept (two different ``?id=`` are two different jobs)."""
    text = (url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}{query}"


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
    db: JobDatabase, user_id: str, app_row: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """The duplicate picture for one application. ``same_job``: this application
    already has a receipt, or another application of the user's whose receipt has
    the same job id or the same normalised URL. ``same_company_30d``: OTHER
    applications of the user's with an ``applied`` event inside the window at the
    same company (the rule-1 ``normalized_company`` key; blank / ``unknown`` skipped).
    Flag: same_job beats same_company beats ''. Never blocks anything."""
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
        hit = (
            r["application_id"] == application_id
            or r["job_id"] == app_row.get("job_id")
            or (mine and norm_url(str(r["job_apply_url"] or "")) == mine)
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
    flag = "same_job" if same_job else ("same_company" if same_company else "")
    return {"same_job": same_job, "same_company_30d": same_company, "flag": flag}


# ── The decision state (what the web buttons show, what the gate reads) ──────


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
    cv_id = cv["id"] if cv else None
    cv_sha = sha256_text(cv["text"]) if cv else ""
    for ev in events:
        p = ev["payload"]
        if ev["event_type"] in ("cv_seen", "submit_approved") and p.get("artifact_id") == cv_id and (
            cv is None or p.get("sha256") == cv_sha
        ):
            if seen is None:
                seen = _who(ev)
            if approved is None and ev["event_type"] == "submit_approved":
                approved = _who(ev)
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
    }


async def gate_facts(db: JobDatabase, user_id: str, app_row: dict[str, Any]) -> settings_rules.SubmitFacts:
    """The facts :func:`settings_rules.may_submit` reads for this application."""
    application_id = int(app_row["id"])
    cur = await db._db.execute(
        "SELECT 1 FROM application_receipts WHERE application_id = ? AND user_id = ? LIMIT 1",
        (application_id, user_id),
    )
    has_receipt = await cur.fetchone() is not None
    now = datetime.now(timezone.utc)
    state = await controls_state(db, user_id, app_row, now)
    cv = state["cv"]
    approved = state["cv"]["approved"] is not None if cv else await _approved_without_cv(db, user_id, application_id)
    dup = state["duplicate"]
    return settings_rules.SubmitFacts(
        status=str(app_row["status"]),
        has_receipt=has_receipt,
        submit_override=await spine.submit_override(db, user_id, application_id),
        duplicate_job=bool(dup["same_job"]) and dup["cleared"] is None,
        cv_seen=bool(cv and cv["seen"] is not None),
        approved=bool(approved),
        declined=state["declined"] is not None,
        blocked=state["blocked"] is not None,
    )


async def _approved_without_cv(db: JobDatabase, user_id: str, application_id: int) -> bool:
    """No CV saved: a yes recorded with ``artifact_id`` null still counts."""
    for ev in await _events(db, user_id, application_id, ("submit_approved",)):
        if ev["payload"].get("artifact_id") is None:
            return True
    return False


async def submit_verdict(
    db: JobDatabase, user_id: str, app_row: dict[str, Any], form_url: str, now: datetime
) -> settings_rules.SubmitDecision:
    """The ONE gate, evaluated for one application (the submit-check route and
    the kit's ``settings.submit_preview`` both call this)."""
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    cfg = settings_rules.effective((load_profile(user_id) or UserProfile()).assistant_settings)
    counts = await settings_rules.load_submit_counts(db, user_id, now)
    facts = await gate_facts(db, user_id, app_row)
    return settings_rules.may_submit(cfg, facts, form_url, counts, now=now)


# ── Who may write which S3 event ─────────────────────────────────────────────


def _closed_payload(event_type: str, payload: dict[str, Any], keys: set[str]) -> None:
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


ANSWER_SOURCES = ("memory", "profile", "approved", "written", "guessed")
ANSWER_QUESTION_MAX = 300
ANSWER_KEY_MAX = 120


def clean_answers(raw: Any) -> list[dict[str, str]]:
    """S5d: the answers an assistant typed into a form, validated. A list of at
    most ``KIT_FORM_FIELDS_MAX`` of ``{question, answer, source[, key]}`` - closed
    keys, control characters removed, sizes capped. ``source`` says where the
    answer came from (a guess is ``guessed``); ``key`` is the kit key for a
    memory / profile / approved answer. An answer may be blank (the page flags it)."""
    if not isinstance(raw, list) or len(raw) > settings.KIT_FORM_FIELDS_MAX:
        raise SpineError(422, f"answers must be a list of at most {settings.KIT_FORM_FIELDS_MAX} items")
    out: list[dict[str, str]] = []
    for item in raw:
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
        clean = {"question": question, "answer": answer, "source": str(item["source"])}
        if "key" in texts:
            key = spine._strip_control_chars(texts["key"], keep="").strip()
            if not key or len(key) > ANSWER_KEY_MAX:
                raise SpineError(422, f"answer key must be 1 to {ANSWER_KEY_MAX} characters")
            clean["key"] = key
        out.append(clean)
    return out


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


async def check_kit_event(
    db: JobDatabase, user: CurrentUser, application_id: int, event_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Validate an S3 event's payload and who may write it; return the payload to
    STORE (server adds ``by``, and ``version`` where a CV is named). Raises
    ``SpineError``. Lives here, called by the ``record_event`` ROUTE, so the MCP
    tool inherits it."""
    actor = actor_for(user)
    if event_type in SERVER_ONLY_EVENTS:
        raise SpineError(422, f"{event_type} is written by Job360 itself (get_application_kit), not by a caller")
    if event_type in ("cv_seen", "submit_approved"):
        _closed_payload(event_type, payload, {"artifact_id", "sha256", "where"})
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
        _closed_payload(event_type, payload, {"where"})
        return {"where": _where(user, event_type, payload), "by": actor}
    if event_type == "autofill_set":
        _closed_payload(event_type, payload, {"mode"})
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
    if event_type == "form_filled":
        if not {"form_url", "fields_count"} <= set(payload) <= {"form_url", "fields_count", "answers"}:
            raise SpineError(422, "form_filled needs payload keys ['fields_count', 'form_url'] (and 'answers')")
        count = payload.get("fields_count")
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= settings.KIT_FORM_FIELDS_MAX:
            raise SpineError(422, f"fields_count must be a whole number from 0 to {settings.KIT_FORM_FIELDS_MAX}")
        host = _host(event_type, payload.get("form_url"))
        stored: dict[str, Any] = {"host": host, "fields_count": count, "by": actor}
        if "answers" in payload:
            stored["answers"] = clean_answers(payload["answers"])
        return stored
    if event_type == "hold_released":
        _closed_payload(event_type, payload, {"reason"})
        if payload.get("reason") not in HOLD_RELEASE_REASONS:
            raise SpineError(422, "hold_released reason must be one of: " + ", ".join(sorted(HOLD_RELEASE_REASONS)))
        return {"reason": payload["reason"], "by": actor}
    if event_type in ("site_account", "account_needed"):
        _closed_payload(event_type, payload, {"host"})  # a password key can never fit here
        return {"host": _host(event_type, payload.get("host")), "by": actor}
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
    get_audit_logger().info(event_type, extra=extra)


async def record_decision(
    db: JobDatabase, user: CurrentUser, application_id: int, event_type: str, payload: dict[str, Any],
    *, once_per_version: bool = False,
) -> dict[str, Any]:
    """Validate + append one S3 event (the web buttons and the download wiring use
    this; ``record_event`` runs :func:`check_kit_event` and its own append).
    ``once_per_version``: an identical event for the same artifact + hash + where
    is returned instead of written again. The caller has proven the application
    is the user's."""
    stored = await check_kit_event(db, user, application_id, event_type, payload)
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


def _records(value: Any) -> list[dict[str, Any]]:
    return [r for r in (value or []) if isinstance(r, dict)]


def compute_answers(
    profile: Any, overlay: list[dict[str, Any]], country: Optional[str]
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    """The answer blocks (every item tagged with its source) and the ``missing``
    list for ``country`` (the JOB's country; ``None`` = unknown). Empty / null =
    absent, not an item (rule #29); ``False`` and ``0`` are real answers."""
    saved = {str(r["path"]): r.get("set_at") for r in overlay}

    def when(path: str) -> Optional[str]:
        return saved.get(path)

    info, prefs, cvd = profile.user_info, profile.preferences, profile.cv_data
    out: dict[str, list[dict[str, Any]]] = {
        "contact": [], "right_to_work": [], "logistics": [], "salary": [], "languages": [],
        "equality": [], "approved_text": [],
    }
    at = when("user_info.contact")
    for k, v in (info.contact or {}).items():
        if _present(v):
            out["contact"].append(_item(f"contact.{k}", v, "memory", at))
    for key, value in (
        ("contact.name", cvd.name), ("contact.location", cvd.location),
        ("contact.linkedin_url", next((u for u in cvd.links or [] if _link_on(u, "linkedin.com")), "")),
        ("contact.github_url", next((u for u in cvd.links or [] if _link_on(u, "github.com")), "")),
    ):
        if _present(value):
            out["contact"].append(_item(key, value, "profile", None))

    at = when("user_info.right_to_work")
    rtw = info.right_to_work or {}
    for rec in _records(rtw.get("countries")):
        cc = str(rec.get("country") or "").upper()
        for k, v in rec.items():
            if k != "country" and _present(v) and cc:
                out["right_to_work"].append(_item(f"right_to_work.{cc}.{k}", v, "memory", at))
    for k in ("citizenship", "sanctions_country_citizen"):
        if _present(rtw.get(k)):
            out["right_to_work"].append(_item(f"right_to_work.{k}", rtw[k], "memory", at))

    at = when("user_info.logistics")
    lg = info.logistics or {}
    for k in ("notice_period", "earliest_start"):
        if _present(lg.get(k)):
            out["logistics"].append(_item(f"logistics.{k}", lg[k], "memory", at))
    for rec in _records(lg.get("countries")):
        cc = str(rec.get("country") or "").upper()
        for k, v in rec.items():
            if k != "country" and _present(v) and cc:
                out["logistics"].append(_item(f"logistics.{cc}.{k}", v, "memory", at))

    at = when("preferences.salary_by_country")
    for rec in _records(prefs.salary_by_country):
        cc = str(rec.get("country") or "").upper()
        if cc and _present(rec.get("amount")):
            out["salary"].append(_item(
                f"salary.{cc}",
                {"amount": rec.get("amount"), "currency": rec.get("currency"), "period": rec.get("period")},
                "memory", at,
            ))

    at = when("user_info.languages")
    for rec in _records(info.languages):
        if _present(rec.get("language")) and _present(rec.get("level")):
            out["languages"].append(_item(f"languages.{rec['language']}", rec["level"], "memory", at))

    at = when("user_info.equality")
    for k, v in (info.equality or {}).items():
        if _present(v):
            out["equality"].append(_item(f"equality.{k}", v, "memory", at, label=EQUALITY_LABEL))

    for rec in _records(info.answers):
        if rec.get("approved") is True and _present(rec.get("answer")) and _present(rec.get("question")):
            out["approved_text"].append(
                _item(str(rec["question"]), rec["answer"], "approved_text", rec.get("recorded_at"))
            )

    have = {i["key"] for block in out.values() for i in block}
    missing: list[dict[str, str]] = []
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


async def _document(
    db: JobDatabase, user: CurrentUser, app_row: dict[str, Any], kind: str,
    events: list[dict[str, Any]], mint: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    row = await spine.latest_artifact(db, user.id, int(app_row["id"]), kind)
    if row is None:
        return None
    from src.services.tailoring.pdf import render_pdf  # noqa: PLC0415 - heavy, lazy (rule #16)

    sha = sha256_text(row["text"])
    title = "Curriculum Vitae" if kind == "cv" else "Cover Letter"
    size = len(await asyncio.to_thread(render_pdf, row["text"], title=title))
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
        "file": {
            "url": "", "expires_at": "", "downloads_left": settings.KIT_LINK_MAX_DOWNLOADS,
            "filename": f"{file_stem(str(app_row.get('job_company') or ''), kind, int(row['version_no']))}.pdf",
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


async def _hold(db: JobDatabase, user: CurrentUser, application_id: int, now: datetime) -> Optional[dict[str, Any]]:
    """Another actor's live hold: its newest kit read inside ``KIT_HOLD_MINUTES``,
    with no later applied / withdrawn / rejected / hold_released. Warns only."""
    actor = actor_for(user)
    cutoff = (now - timedelta(minutes=settings.KIT_HOLD_MINUTES)).isoformat()
    cur = await db._db.execute(
        "SELECT id, recorded_at, recorded_by FROM application_events WHERE application_id = ? AND user_id = ? "
        "AND event_type = 'kit_read' AND recorded_by <> ? AND recorded_at >= ? ORDER BY id DESC LIMIT 1",
        (application_id, user.id, actor, cutoff),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    r = dict(row)
    cur = await db._db.execute(
        "SELECT 1 FROM application_events WHERE application_id = ? AND user_id = ? AND id > ? "
        "AND event_type IN ('applied', 'withdrawn', 'rejected', 'hold_released') LIMIT 1",
        (application_id, user.id, r["id"]),
    )
    if await cur.fetchone() is not None:
        return None
    since = datetime.fromisoformat(str(r["recorded_at"]))
    return {
        "held_by": r["recorded_by"], "since": r["recorded_at"],
        "until": (since + timedelta(minutes=settings.KIT_HOLD_MINUTES)).isoformat(),
    }


async def build_kit(db: JobDatabase, user: CurrentUser, application_id: int, *, now: datetime) -> dict[str, Any]:
    """Build the kit for one application and record that it was read.

    404 for an application that is not the caller's (no link row is written);
    429 over ``KIT_READS_MAX_PER_HOUR``. Writes one ``kit_read`` event (the
    timeline shows who read it and when) and one ``artifact_links`` row per
    document. The token appears only in this return value."""
    from src.api.routes.profile import settings_view  # noqa: PLC0415 - the one read model of the settings
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile_with_overlay  # noqa: PLC0415

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

    loaded, overlay = load_profile_with_overlay(user.id)
    profile = loaded or UserProfile()
    country = (str(app_row.get("job_country") or "").strip().upper()) or None
    answers, missing = compute_answers(profile, overlay, country)
    hold = await _hold(db, user, application_id, now)
    state = await controls_state(db, user.id, app_row, now)
    events = await _events(db, user.id, application_id, ("cv_seen", "submit_approved"))

    docs: list[dict[str, Any]] = []
    cv = await _document(db, user, app_row, "cv", events, docs)
    letter = await _document(db, user, app_row, "cover_letter", events, docs)
    for doc in docs:
        await _mint_link(db, user, application_id, doc, now)

    apply_url = str(app_row.get("job_url") or "")
    remote = app_row.get("job_remote")
    job = {
        "job_id": app_row.get("job_id"), "title": app_row.get("job_title") or "",
        "company": app_row.get("job_company") or "", "location": app_row.get("job_location") or "",
        "country": country, "remote": None if remote is None else bool(remote),
        "apply_url": apply_url, "found_on": app_row.get("job_found_on"),
    }
    application = {
        "id": application_id, "status": app_row["status"], "follow_up_on": app_row.get("follow_up_on"),
        "submit_override": await spine.submit_override(db, user.id, application_id),
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
    verdict = await submit_verdict(db, user.id, app_row, apply_url, now)
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
            "by": actor,
        },
        occurred_at=now.isoformat(), recorded_by=actor,
    )
    dup = state["duplicate"]
    log.info("kit_read", extra={
        "event": "kit_read", "user_id": safe_log_value(user.id), "actor": safe_log_value(actor),
        "application_id": application_id, "kit_event_id": event["event_id"], "missing_count": len(missing),
        "hold": bool(hold), "duplicate": dup["flag"], "surface": "mcp" if user.auth_via != "session" else "web",
        "result": "ok",
    })
    out: dict[str, Any] = {
        "kit": {"id": event["event_id"], "sha256": kit_sha, "generated_at": now.isoformat()},
        "job": job, "application": application,
        "cv": cv, "cover_letter": letter,
        "answers": answers, "missing": missing,
        "settings": settings_block,
        "duplicate": dup,
        "hold": hold, "account_site": account_site,
        "autofill": state["autofill"]["mode"],
        "instructions": list(KIT_INSTRUCTIONS),
    }
    if cv is None:
        out["cv_none_reason"] = NO_CV_REASON
    if letter is None:
        out["cover_letter_none_reason"] = NO_LETTER_REASON
    return out


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

"""A one-time code a job site emails, read from the user's own Gmail (owner 2026-11-07), only with the setting on.

``assistant_settings.use_email_codes`` (default off; on is riskier, so only the user turns it on). With it on, the
assistant may type a code the job site emailed into that site's code box, and records ``email_code_used`` first.
Job360 never sees, keeps or logs the code: the event is refused unless ALL of these hold (:func:`problems`):

* ``to`` is the user's own email (the account email or the profile's ``contact.email``);
* ``form_host`` is a site this application already names (its job link, a ``form_filled`` host, a ``blocked``
  ``page_host``) and the sender's domain is that host, shares its registrable domain, or is that host's applicant
  system (``sites.SITES``), or the sender's registrable domain is the EMPLOYER's own (:func:`employer_domain`: the
  application's job link when it is the employer's own site; unknown = not accepted). Exact host and
  label-boundary compares only, never a substring;
* neither the sender nor the form is an account-security, mail or payment provider (:data:`FORBIDDEN_DOMAINS`),
  and the subject has no password / reset / bank / payment word (:data:`FORBIDDEN_WORDS`, word by word);
* ``purpose`` is ``apply`` or ``verify_email``; or, only with ``assistant_settings.site_sign_in`` on (owner
  2026-11-19, SITE SIGN-IN: the assistant signs in to / signs up on a JOB SITE with the user's own email),
  ``sign_in``, ``create_account`` or ``reset_password`` (:data:`SIGN_IN_PURPOSES`), never on Indeed
  (:data:`SIGN_IN_FORBIDDEN`). A ``reset_password`` email may name a password reset: it is THIS job site's (the
  sender rules above); a bank or payment word still refuses it;
* ``asked_at`` <= ``received_at`` <= ``asked_at`` + :data:`WINDOW`, and ``received_at`` is at most
  :data:`WINDOW` old now; at most :data:`MAX_PER_HOUR` codes per application an hour.
Stored: ``{host, sender_host, received_at, purpose}``. Never the code, the subject or the address.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.services.applications.spine import SpineError
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

EVENT_TYPE = "email_code_used"
PAYLOAD_KEYS = frozenset({"to", "sender", "subject", "purpose", "form_host", "asked_at", "received_at"})
PURPOSES = frozenset({"apply", "verify_email"})
# Owner 2026-11-19 (SITE SIGN-IN): accepted only while ``assistant_settings.site_sign_in`` is on.
SIGN_IN_PURPOSES = frozenset({"sign_in", "create_account", "reset_password"})
# Job boards whose account is the user's own beyond one job site: the assistant never signs in or signs up there
# (registrable-domain first label, so every country domain counts: uk.indeed.com, indeed.co.uk).
SIGN_IN_FORBIDDEN = frozenset({"indeed"})
# The words a job site's own password-reset email carries: allowed for ``reset_password`` only.
RESET_WORDS = frozenset({"password", "passwort", "reset", "recover", "recovery", "unlock"})
WINDOW = timedelta(minutes=15)
CLOCK_SLACK = timedelta(minutes=1)
MAX_PER_HOUR = 3
# Owner decision 2 (suggested, F8): the second ``otp_needed`` block on one job with no accepted code between them
# hands the job back to the owner (the only exit from an assistant-owned loop).
OTP_ESCALATE_AT = 2
# Account security, mail and payment providers: never a job site's application code, whatever the form says.
FORBIDDEN_DOMAINS = (
    "google.com", "gmail.com", "googlemail.com", "youtube.com", "microsoft.com", "microsoftonline.com",
    "live.com", "outlook.com", "hotmail.com", "office.com", "apple.com", "icloud.com", "me.com", "linkedin.com",
    "facebook.com", "meta.com", "amazon.com", "paypal.com", "stripe.com", "wise.com", "revolut.com", "monzo.com",
)
FORBIDDEN_WORDS = frozenset({
    "password", "passwort", "reset", "recover", "recovery", "unlock", "bank", "banking", "payment", "payments",
    "card", "billing", "invoice", "transaction",
})
# Second-level labels under a two-letter country code (acme.co.uk): the registrable domain is three labels there.
_SECOND_LEVEL = frozenset({"co", "com", "ac", "org", "net", "gov", "edu", "ltd", "plc", "nhs", "sch"})
# Hosting platforms where every customer gets a subdomain (acme.github.io): one more label, so only that site matches.
_PLATFORM_SUFFIXES = frozenset({
    "github.io", "herokuapp.com", "netlify.app", "vercel.app", "azurewebsites.net", "wixsite.com", "pages.dev",
    "web.app", "firebaseapp.com", "onrender.com", "cloudfront.net", "appspot.com",
})
_HOST_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-.")


def _host(text: Any) -> str:
    """A plain lower-case ASCII host, "" when it is anything else (a URL, a port, IDNA-mapped text)."""
    h = str(text or "").strip().lower().rstrip(".")
    h = h[4:] if h.startswith("www.") else h
    if not h or set(h) - _HOST_CHARS or ".." in h or "." not in h or h.startswith((".", "-")):
        return ""
    return h


def sender_domain(sender: Any) -> str:
    """The domain of ``Name <a@b.c>`` or ``a@b.c``: the text after the LAST ``@``, up to ``>``."""
    text = str(sender or "").strip()
    _, at, domain = text.rpartition("@")
    return _host(domain.split(">", 1)[0]) if at else ""


def _within(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def registrable(host: str) -> str:
    """``careers.acme.co.uk`` -> ``acme.co.uk``; ``boards.greenhouse.io`` -> ``greenhouse.io``; "" for a suffix."""
    labels = host.split(".")
    n = 3 if len(labels[-1]) == 2 and len(labels) >= 2 and labels[-2] in _SECOND_LEVEL else 2
    if ".".join(labels[-n:]) in _PLATFORM_SUFFIXES:
        n += 1
    return ".".join(labels[-n:]) if len(labels) >= n else ""


def _ats_domains(form_host: str) -> tuple[str, ...]:
    from src.services.browser_agent.routing import site_of  # noqa: PLC0415 - lazy (browser package)
    from src.services.browser_agent.sites import SITES  # noqa: PLC0415

    kind, key, _ = site_of(f"https://{form_host}/")
    return next((s.hosts for s in SITES if kind == "ats" and s.name == key), ())


def sender_matches(sender_host: str, form_host: str) -> bool:
    """The sender is the form's own site (same host or registrable domain) or the form's applicant system."""
    if not sender_host or not form_host:
        return False
    reg = registrable(form_host)
    if sender_host == form_host or (reg and registrable(sender_host) == reg):
        return True
    return any(_within(sender_host, d) for d in _ats_domains(form_host))


def employer_domain(job_url: str) -> str:
    """The registrable domain of the job's OWN site (``www.wsp.com`` -> ``wsp.com``), "" when the link is an
    applicant system, a job board or a shared host (many employers' jobs live there, so it names none of them) or
    no http(s) link at all. Only data Job360 stores (the application's job link); "" = unknown, never a guess."""
    from src.core import settings  # noqa: PLC0415
    from src.services.applications.job_checks import apply_host, classify_host  # noqa: PLC0415

    host = _host(apply_host(job_url or ""))
    if not host or classify_host(host) != "unknown" or host in settings.SHARED_ATS_HOSTS:
        return ""
    if any(_within(host, d) for d in settings.ATS_EMAIL_ONLY_HOSTS):  # an applicant system: many employers live there
        return ""
    return registrable(host)


def sender_match(sender_host: str, form_host: str, employer: str = "") -> str:
    """Why the sender may send this code: ``form_site`` (:func:`sender_matches`), ``employer_site`` (its
    registrable domain IS the employer's own, exact compare; owner 2026-11-18: a WSP form on an applicant-system host
    whose code came from wsp.com) or ``none`` (a third party)."""
    if sender_matches(sender_host, form_host):
        return "form_site"
    if sender_host and form_host and employer and registrable(sender_host) == employer:
        return "employer_site"
    return "none"


def forbidden(host: str) -> bool:
    from src.services.fetch.guard import is_linkedin_host  # noqa: PLC0415

    return is_linkedin_host(host) or any(_within(host, d) for d in FORBIDDEN_DOMAINS)


def sign_in_site(host: Any) -> bool:
    """A JOB SITE the assistant may sign in to or sign up on (SITE SIGN-IN): a readable host that is no account,
    mail or payment provider (:func:`forbidden`) and not Indeed (:data:`SIGN_IN_FORBIDDEN`). Exact labels only."""
    h = _host(host)
    if not h or forbidden(h):
        return False
    reg = registrable(h)
    return bool(reg) and reg.split(".", 1)[0] not in SIGN_IN_FORBIDDEN


def _words(text: str) -> set[str]:
    out = "".join(ch if ch.isalnum() else " " for ch in text.casefold())
    return set(out.split())


def _when(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def problems(
    payload: Any, *, own_emails: set[str], known_hosts: set[str], now: datetime, used_last_hour: int = 0,
    employer_domain: str = "", sign_in: bool = False,
) -> list[str]:
    """Every reason this code may NOT be used (empty = it may). Pure: no DB, no clock of its own."""
    if not isinstance(payload, dict):
        return ["payload must be an object"]
    if set(payload) != PAYLOAD_KEYS:
        return [f"payload keys must be exactly {', '.join(sorted(PAYLOAD_KEYS))}"]
    not_text = sorted(k for k in PAYLOAD_KEYS if not isinstance(payload[k], str))
    if not_text:
        return [f"{k} must be a string" for k in not_text]
    out: list[str] = []
    if str(payload["to"] or "").strip().casefold() not in own_emails:
        out.append("not_to_user")
    form, sender = _host(payload["form_host"]), sender_domain(payload["sender"])
    if not form or form not in known_hosts:
        out.append("form_host_unknown")
    if not sender or sender_match(sender, form, employer_domain) == "none":
        out.append("sender_not_site")
    if (sender and forbidden(sender)) or (form and forbidden(form)):
        out.append("sender_forbidden")
    purpose = payload["purpose"]
    banned = FORBIDDEN_WORDS - RESET_WORDS if (sign_in and purpose == "reset_password") else FORBIDDEN_WORDS
    if _words(str(payload["subject"] or "")) & banned:
        out.append("not_an_application_code")
    if purpose not in PURPOSES and not (sign_in and purpose in SIGN_IN_PURPOSES):
        out.append("purpose_not_allowed")
    if purpose in SIGN_IN_PURPOSES and form and not sign_in_site(form):
        out.append("not_a_job_site")
    asked, got = _when(payload["asked_at"]), _when(payload["received_at"])
    if asked is None or got is None:
        out.append("time_unreadable")
    elif not (asked <= got <= asked + WINDOW) or got > now + CLOCK_SLACK or now - got > WINDOW:
        out.append("outside_window")
    if used_last_hour >= MAX_PER_HOUR:
        out.append("too_many_codes")
    return out


async def _known_hosts(db: JobDatabase, user_id: str, application_id: int, app_row: dict[str, Any]) -> set[str]:
    from urllib.parse import urlsplit  # noqa: PLC0415

    from src.services.applications import kit  # noqa: PLC0415 - kit imports the spine

    hosts = {_host(urlsplit(str(app_row.get("job_url") or "")).hostname or "")}
    for e in await kit._events(db, user_id, application_id, ("form_filled", "blocked")):
        p = e["payload"] or {}
        hosts.add(_host(p.get("host") or p.get("page_host") or urlsplit(str(p.get("form_url") or "")).hostname))
    return hosts - {""}


def codes_on(profile: Any) -> bool:
    """The ONE reader of the setting (stored value, effective): every place that decides who owns a code box
    and :func:`check` call this, so they cannot drift apart. No profile = off."""
    from src.services.profile import assistant_settings as settings_rules  # noqa: PLC0415

    return profile is not None and settings_rules.effective(profile.assistant_settings).use_email_codes is True


def signin_on(profile: Any) -> bool:
    """The ONE reader of ``site_sign_in`` (stored value, effective). No profile = off."""
    from src.services.profile import assistant_settings as settings_rules  # noqa: PLC0415

    return profile is not None and settings_rules.effective(profile.assistant_settings).site_sign_in is True


async def assistant_reasons(user_id: str) -> frozenset[str]:
    """The block reasons this user's settings make the assistant's, read live (turning a setting off hands the
    step back at once): ``otp_needed`` with ``use_email_codes`` on, the sign-in / sign-up walls with
    ``site_sign_in`` on (``blocked.SIGN_IN_WALLS``). One profile read."""
    from src.services.applications import derive_memo  # noqa: PLC0415

    profile = await derive_memo.profile_or_load(user_id)
    out: set[str] = set()
    if codes_on(profile):
        out.add("otp_needed")
    if signin_on(profile):
        out |= {"login_needed", "account_creation"}
    return frozenset(out)


async def codes_are_assistants(user_id: str) -> bool:
    """True when this user's setting makes a code box the assistant's. Read live every time (turning it off
    hands the code back at once), never baked into a stored block."""
    from src.services.applications import derive_memo  # noqa: PLC0415

    return codes_on(await derive_memo.profile_or_load(user_id))


async def refused_tries(
    db: JobDatabase, user_id: str, application_id: int, reasons: frozenset[str] = frozenset({"otp_needed"}),
) -> int:
    """Blocks with one of ``reasons`` (default ``otp_needed``) on this job since the last accepted
    ``email_code_used`` (or, for the sign-in walls, the last ``site_account``) or the last time the user cleared or
    skipped it (the assistant's own ``unblocked retried`` does not reset the count). Read-only."""
    import json  # noqa: PLC0415

    cur = await db._db.execute(
        "SELECT event_type, payload FROM application_events WHERE application_id = ? AND user_id = ? "
        "AND event_type IN ('blocked', 'unblocked', 'email_code_used', 'site_account') ORDER BY id DESC",
        (application_id, user_id),
    )
    n = 0
    for row in await cur.fetchall():
        kind, raw = row[0], row[1]
        if kind == EVENT_TYPE or (kind == "site_account" and "otp_needed" not in reasons):
            break
        try:
            p = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            p = {}
        p = p if isinstance(p, dict) else {}
        if kind == "unblocked" and p.get("resolution") in ("user_did_it", "skipped"):
            break
        if kind == "blocked" and p.get("reason") in reasons:
            n += 1
    return n


async def check(
    db: JobDatabase, user: CurrentUser, application_id: int, payload: Any, *, now: Optional[datetime] = None,
) -> dict[str, Any]:
    """The stored payload, or a 422 naming every failed rule (403 while the setting is off: ``use_email_codes`` for
    ``apply`` / ``verify_email``, ``site_sign_in`` for the SITE SIGN-IN purposes). Never the code or link."""
    from src.services.applications import kit, spine  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    when = now or datetime.now(timezone.utc)
    profile = load_profile(user.id)
    audit = {"event": "email_code_check", "user_id": safe_log_value(user.id), "application_id": int(application_id)}
    purpose = payload.get("purpose") if isinstance(payload, dict) else None
    if isinstance(purpose, str) and purpose in SIGN_IN_PURPOSES:
        # Owner 2026-11-19 (SITE SIGN-IN): a sign-in / sign-up / reset email, only with site_sign_in on.
        audit["purpose"] = purpose
        sign_in = signin_on(profile)
        if profile is None or not sign_in:
            get_audit_logger().info("email_code_check", extra={**audit, "result": "setting_off",
                                                               "reasons": "site_sign_in_off"})
            raise SpineError(403, "assistant_settings.site_sign_in is off: the user signs in themselves "
                             "(login_needed / account_creation)")
    elif profile is None or not codes_on(profile):
        get_audit_logger().info("email_code_check", extra={**audit, "result": "setting_off"})
        raise SpineError(403, "assistant_settings.use_email_codes is off: the user enters the code (otp_needed)")
    else:
        sign_in = signin_on(profile)
    app_row = await spine.get_owned_application(db, user.id, application_id) or {}
    own = {str(e).strip().casefold() for e in (user.email, profile.user_info.contact.get("email")) if e}
    recent = [e for e in await kit._events(db, user.id, application_id, (EVENT_TYPE,))
              if (t := _when(e["recorded_at"])) is not None and when - t < timedelta(hours=1)]
    employer = employer_domain(str(app_row.get("job_url") or ""))
    found = problems(payload, own_emails=own, known_hosts=await _known_hosts(db, user.id, application_id, app_row),
                     now=when, used_last_hour=len(recent), employer_domain=employer, sign_in=sign_in)
    form = _host(payload.get("form_host")) if isinstance(payload, dict) else ""
    sender = sender_domain(payload.get("sender")) if isinstance(payload, dict) else ""
    get_audit_logger().info("email_code_check", extra={
        **audit, "form_host": safe_log_value(form, max_len=100), "sender_host": safe_log_value(sender, max_len=100),
        "result": "refused" if found else "ok", "reasons": ",".join(found),
        # which rule let the sender in (form_site | employer_site | none) and whether the employer's domain was known
        "sender_match": sender_match(sender, form, employer), "employer_domain_known": bool(employer),
    })
    if found:
        raise SpineError(422, "email code refused: " + "; ".join(found) + ". Record blocked otp_needed instead.")
    return {"host": form, "sender_host": sender, "received_at": str(payload["received_at"]),
            "purpose": payload["purpose"]}

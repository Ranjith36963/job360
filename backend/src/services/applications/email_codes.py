"""A one-time code a job site emails, read from the user's own Gmail (owner 2026-11-07), only with the setting on.

``assistant_settings.use_email_codes`` (default off; on is riskier, so only the user turns it on). With it on, the
assistant may type a code the job site emailed into that site's code box, and records ``email_code_used`` first.
Job360 never sees, keeps or logs the code: the event is refused unless ALL of these hold (:func:`problems`):

* ``to`` is the user's own email (the account email or the profile's ``contact.email``);
* ``form_host`` is a site this application already names (its job link, a ``form_filled`` host, a ``blocked``
  ``page_host``) and the sender's domain is that host, shares its registrable domain, or is that host's applicant
  system (``sites.SITES``). Exact host and label-boundary compares only, never a substring;
* neither the sender nor the form is an account-security, mail or payment provider (:data:`FORBIDDEN_DOMAINS`),
  and the subject has no password / reset / bank / payment word (:data:`FORBIDDEN_WORDS`, word by word);
* ``purpose`` is ``apply`` or ``verify_email``;
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
WINDOW = timedelta(minutes=15)
CLOCK_SLACK = timedelta(minutes=1)
MAX_PER_HOUR = 3
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


def forbidden(host: str) -> bool:
    from src.services.fetch.guard import is_linkedin_host  # noqa: PLC0415

    return is_linkedin_host(host) or any(_within(host, d) for d in FORBIDDEN_DOMAINS)


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
    if not sender or not sender_matches(sender, form):
        out.append("sender_not_site")
    if (sender and forbidden(sender)) or (form and forbidden(form)):
        out.append("sender_forbidden")
    if _words(str(payload["subject"] or "")) & FORBIDDEN_WORDS:
        out.append("not_an_application_code")
    if payload["purpose"] not in PURPOSES:
        out.append("purpose_not_allowed")
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


async def check(
    db: JobDatabase, user: CurrentUser, application_id: int, payload: Any, *, now: Optional[datetime] = None,
) -> dict[str, Any]:
    """The stored payload, or a 422 naming every failed rule (403 while the setting is off). Never the code."""
    from src.services.applications import kit, spine  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    when = now or datetime.now(timezone.utc)
    profile = load_profile(user.id)
    audit = {"event": "email_code_check", "user_id": safe_log_value(user.id), "application_id": int(application_id)}
    if profile is None or profile.assistant_settings.use_email_codes is not True:
        get_audit_logger().info("email_code_check", extra={**audit, "result": "setting_off"})
        raise SpineError(403, "assistant_settings.use_email_codes is off: the user enters the code (otp_needed)")
    app_row = await spine.get_owned_application(db, user.id, application_id) or {}
    own = {str(e).strip().casefold() for e in (user.email, profile.user_info.contact.get("email")) if e}
    recent = [e for e in await kit._events(db, user.id, application_id, (EVENT_TYPE,))
              if (t := _when(e["recorded_at"])) is not None and when - t < timedelta(hours=1)]
    found = problems(payload, own_emails=own, known_hosts=await _known_hosts(db, user.id, application_id, app_row),
                     now=when, used_last_hour=len(recent))
    form = _host(payload.get("form_host")) if isinstance(payload, dict) else ""
    sender = sender_domain(payload.get("sender")) if isinstance(payload, dict) else ""
    get_audit_logger().info("email_code_check", extra={
        **audit, "form_host": safe_log_value(form, max_len=100), "sender_host": safe_log_value(sender, max_len=100),
        "result": "refused" if found else "ok", "reasons": ",".join(found),
    })
    if found:
        raise SpineError(422, "email code refused: " + "; ".join(found) + ". Record blocked otp_needed instead.")
    return {"host": form, "sender_host": sender, "received_at": str(payload["received_at"]),
            "purpose": payload["purpose"]}

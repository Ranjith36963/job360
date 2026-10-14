"""Assistant settings — how much the user's assistant may do on its own.

Owner decision 2026-10-08 (S2). Job360 never applies to anything itself; these
are the user's standing rules that the CONNECTED assistant reads and obeys:

* ``apply_min_score`` — the user's OWN score line, 0-100. NO default: unset
  (``None``) means "not set" and the assistant must ask for it (owner decision
  2026-10-10).
* ``above_line`` / ``below_line`` — ``ask`` (default) | ``auto``: two SEPARATE
  per-user switches, one for jobs at or above the line and one for jobs below it.
  ``apply_mode`` is RETIRED (the field stays so old stored rows still load).
* ``submit_mode`` — ``confirm`` (default) | ``auto_when_sure``
* ``daily_cap`` — most submits per day; ``None`` = no cap (owner: no cap by default)
* ``paused_until`` — ``""`` | ``"until_resumed"`` | an ISO-8601 time in the future
* ``pause_reason`` — one short line for the user's own memory

``""`` / ``None`` mean "not chosen": the SAFE default applies (rule #29: an empty
shelf stays silent, it is never an invented value). Storage is the S1 pattern:
the base lives in ``user_profiles.assistant_settings`` (one writer,
``storage.save_assistant_settings``), the values ride the ``profile_edits``
overlay on ``assistant_settings.*`` paths.

Three things live here, all PURE except the two loaders at the bottom:

1. :func:`validate_setting` — the per-path value rules (called by
   ``edits.validate_edit``). Errors name the PATH, never echo the value.
2. :func:`classify_change` — "riskier" (more freedom for the assistant) or
   "safer". An assistant's riskier change is NEVER applied; it becomes a
   waiting request the user confirms on the website
   (``setting_requests.py``). The user's own web click applies at once.
3. :func:`may_submit` — THE one gate every "may I press submit?" question goes
   through (``GET /applications/{id}/submit-check`` and the MCP ``check_submit``
   tool both call it). One function, one order of rules, closed reason codes.

Logs never carry ``pause_reason`` or notes text (they are free text the user
or an assistant typed); the callers log paths, actors, ids and closed values.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal, Optional
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.core import settings
from src.repositories import pgsync
from src.services.profile import edits as profile_edits
from src.services.profile.models import (
    VALID_DAILY_CHECK_VALUES,
    VALID_SUBMIT_MODES,
    AssistantSettings,
    UserProfile,
)

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

HEAD = "assistant_settings"
APPLY_MODE_PATH = f"{HEAD}.apply_mode"  # RETIRED 2026-10-10: kept only to name it in the refusal
APPLY_MODE_RETIRED_MESSAGE = (
    "assistant_settings.apply_mode is retired: use assistant_settings.above_line "
    "and assistant_settings.below_line (ask|auto)"
)
APPLY_MIN_SCORE_PATH = f"{HEAD}.apply_min_score"
ABOVE_LINE_PATH = f"{HEAD}.above_line"
BELOW_LINE_PATH = f"{HEAD}.below_line"
SUBMIT_MODE_PATH = f"{HEAD}.submit_mode"
DAILY_CAP_PATH = f"{HEAD}.daily_cap"
PAUSED_UNTIL_PATH = f"{HEAD}.paused_until"
PAUSE_REASON_PATH = f"{HEAD}.pause_reason"
SETTING_PATHS: tuple[str, ...] = (
    APPLY_MIN_SCORE_PATH, ABOVE_LINE_PATH, BELOW_LINE_PATH, SUBMIT_MODE_PATH,
    DAILY_CAP_PATH, PAUSED_UNTIL_PATH, PAUSE_REASON_PATH,
)
VALID_LINE_SWITCHES: frozenset[str] = frozenset({"ask", "auto"})
# The inbox/Gmail mode stays at its old path (an ALIAS, not a move: assistants
# cached instructions that write it). "auto" there is a RISKIER change too
# (owner answer 1, 2026-10-08), so it goes through the same request flow.
INBOX_MODE_PATH = "preferences.daily_check"
# S4 - how far the six-round setup got. NOT in SETTING_PATHS / GATED_PATHS: it is
# progress the assistant reports, never a freedom, so it is never "riskier".
SETUP_PROGRESS_PATH = f"{HEAD}.setup_progress"
SETUP_ROUNDS: tuple[str, ...] = ("targets", "you", "visa", "logistics", "equality", "settings")
SETUP_PROGRESS_FUTURE_SLACK = timedelta(minutes=5)
GATED_PATHS: tuple[str, ...] = SETTING_PATHS + (INBOX_MODE_PATH,)

UNTIL_RESUMED = "until_resumed"
DEFAULT_LINE_SWITCH = "ask"
DEFAULT_SUBMIT_MODE = "confirm"
AUTO_SUBMIT_MODE = "auto_when_sure"
PAUSE_MAX_DAYS = 365
# Closed override values on the `submit_mode_set` event payload.
VALID_OVERRIDE_VALUES: frozenset[str] = frozenset(VALID_SUBMIT_MODES | {"inherit"})

Risk = Literal["safer", "riskier"]


# ── Validation ───────────────────────────────────────────────────────────────


def _bad(path: str, rule: str) -> profile_edits.ProfileEditError:
    """A 422 that names the PATH and the rule, never the submitted value."""
    return profile_edits.ProfileEditError(422, f"{path} {rule}")


def _closed_set(path: str, value: Any, allowed: frozenset[str]) -> str:
    if not isinstance(value, str):
        raise _bad(path, f"must be a string, not {type(value).__name__}")
    text = value.strip().lower()
    if text and text not in allowed:
        raise _bad(path, f"must be one of: {', '.join(sorted(allowed))} (or empty to reset)")
    return text


def _int_in_range(path: str, value: Any, low: int, high: int) -> int:
    # bool is a subclass of int; True must never pass as a number.
    if isinstance(value, bool) or not isinstance(value, int):
        raise _bad(path, f"must be a whole number from {low} to {high}")
    if value < low or value > high:
        raise _bad(path, f"must be a whole number from {low} to {high}")
    return value


def parse_iso_with_offset(text: str) -> datetime:
    """An ISO-8601 time that CARRIES an offset (``Z`` or ``+01:00``), as UTC.
    ``ValueError`` when it is not one."""
    raw = text.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError("no offset")
    try:
        return parsed.astimezone(timezone.utc)
    except OverflowError:  # year 1 / 9999 shifted past the calendar edge
        raise ValueError("out of range") from None


def _validate_paused_until(path: str, value: Any, now: datetime) -> str:
    if not isinstance(value, str):
        raise _bad(path, f"must be a string, not {type(value).__name__}")
    text = value.strip()
    if text == "" or text == UNTIL_RESUMED:
        return text
    try:
        when = parse_iso_with_offset(text)
    except ValueError:
        raise _bad(
            path,
            f'must be "" (not paused), "{UNTIL_RESUMED}", '
            "or an ISO-8601 time with an offset like 2026-10-20T09:00:00+01:00",
        ) from None
    if when <= now:
        raise _bad(path, "must be a time in the future")
    if when > now + timedelta(days=PAUSE_MAX_DAYS):
        raise _bad(path, f"must be at most {PAUSE_MAX_DAYS} days ahead")
    return when.isoformat()


def _validate_pause_reason(path: str, value: Any) -> str:
    if not isinstance(value, str):
        raise _bad(path, f"must be a string, not {type(value).__name__}")
    text = value.strip()
    if len(text) > settings.PROFILE_EDIT_MAX_ITEM_CHARS:
        raise _bad(
            path,
            f"must be one line of at most {settings.PROFILE_EDIT_MAX_ITEM_CHARS} "
            "characters (PROFILE_EDIT_MAX_ITEM_CHARS)",
        )
    # Refuse, never strip: a pause note with a line break or control character is
    # a smuggling shape, and silently rewriting it would store something the
    # caller did not send. "\n" is a control character (Cc), so it is refused too.
    import unicodedata  # noqa: PLC0415 — only this path needs it

    for ch in text:
        if ch in profile_edits._BANNED_CHARS or unicodedata.category(ch) in profile_edits._BANNED_CATEGORIES:
            raise _bad(path, "must be one plain line (no line breaks or control characters)")
    return text


def validate_setup_progress(path: str, value: Any, *, now: Optional[datetime] = None) -> dict[str, dict[str, str]]:
    """``{round: {"done_at": ISO-with-offset}}`` -> the same in UTC, in round order.

    Keys must be setup rounds; each value is exactly ``{"done_at"}`` holding a
    time that carries an offset and is not more than five minutes ahead. ``{}``
    is fine (nothing done). Refusals name the PATH, never the submitted value.
    """
    when = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(value, dict):
        raise _bad(path, f"must be an object of rounds, not {type(value).__name__}")
    if any(k not in SETUP_ROUNDS for k in value):
        raise _bad(path, f"may only hold these rounds: {', '.join(SETUP_ROUNDS)}")
    out: dict[str, dict[str, str]] = {}
    for name in SETUP_ROUNDS:
        if name not in value:
            continue
        entry = value[name]
        if not isinstance(entry, dict) or set(entry) != {"done_at"} or not isinstance(entry["done_at"], str):
            raise _bad(path, f"round {name} must be exactly done_at: an ISO-8601 time with an offset")
        try:
            done = parse_iso_with_offset(entry["done_at"])
        except ValueError:
            raise _bad(path, f"round {name}: done_at must be an ISO-8601 time with an offset") from None
        if done > when + SETUP_PROGRESS_FUTURE_SLACK:
            raise _bad(path, f"round {name}: done_at must not be in the future")
        out[name] = {"done_at": done.isoformat()}
    return out


def validate_setting(path: str, value: Any, *, now: Optional[datetime] = None) -> Any:
    """Normalise ``value`` for one ``assistant_settings.*`` path, or raise
    ``ProfileEditError(422)`` naming the PATH. ``None`` (a clear) is handled by
    the caller before this runs, so ``daily_cap: null`` never reaches here."""
    when = now if now is not None else datetime.now(timezone.utc)
    if path in (ABOVE_LINE_PATH, BELOW_LINE_PATH):
        return _closed_set(path, value, VALID_LINE_SWITCHES)
    if path == SUBMIT_MODE_PATH:
        return _closed_set(path, value, VALID_SUBMIT_MODES)
    if path == APPLY_MIN_SCORE_PATH:
        return _int_in_range(path, value, 0, 100)
    if path == DAILY_CAP_PATH:
        return _int_in_range(path, value, 1, settings.ASSISTANT_DAILY_CAP_MAX)
    if path == PAUSED_UNTIL_PATH:
        return _validate_paused_until(path, value, when)
    if path == PAUSE_REASON_PATH:
        return _validate_pause_reason(path, value)
    raise _bad(path, "is not an assistant setting")  # pragma: no cover — callers pass SETTING_PATHS


# ── Effective values ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EffectiveSettings:
    """The settings with every default filled in — what the gate reads."""

    apply_min_score: Optional[int]  # the user's own line; None = not set (NO default)
    above_line: str
    below_line: str
    submit_mode: str
    daily_cap: Optional[int]
    paused_until: str
    pause_reason: str


def effective(s: AssistantSettings) -> EffectiveSettings:
    """Fill the safe defaults: no score line / ask / ask / confirm / no cap / not paused."""
    return EffectiveSettings(
        apply_min_score=None if s.apply_min_score is None else int(s.apply_min_score),
        above_line=s.above_line or DEFAULT_LINE_SWITCH,
        below_line=s.below_line or DEFAULT_LINE_SWITCH,
        submit_mode=s.submit_mode or DEFAULT_SUBMIT_MODE,
        daily_cap=None if s.daily_cap is None else int(s.daily_cap),
        paused_until=s.paused_until or "",
        pause_reason=s.pause_reason or "",
    )


def is_paused(paused_until: str, now: datetime) -> bool:
    """Paused until resumed, or until a future time. An unparseable stored value
    reads as NOT paused (it can only be a historical row; validation refuses it
    on the way in)."""
    if paused_until == UNTIL_RESUMED:
        return True
    if not paused_until:
        return False
    try:
        return parse_iso_with_offset(paused_until) > now
    except ValueError:
        return False


def effective_path_value(path: str, raw: Any, now: Optional[datetime] = None) -> Any:
    """The EFFECTIVE value of one gated path from its raw stored value.

    An expired ``paused_until`` reads as ``""`` so a stale pause never makes the
    next change look like "moving it earlier"."""
    when = now if now is not None else datetime.now(timezone.utc)
    if path in (ABOVE_LINE_PATH, BELOW_LINE_PATH):
        return raw or DEFAULT_LINE_SWITCH
    if path == APPLY_MIN_SCORE_PATH:
        return raw  # None stays None: the line has no default
    if path == SUBMIT_MODE_PATH:
        return raw or DEFAULT_SUBMIT_MODE
    if path == DAILY_CAP_PATH:
        return raw
    if path == PAUSED_UNTIL_PATH:
        return (raw or "") if is_paused(raw or "", when) else ""
    if path == PAUSE_REASON_PATH:
        return raw or ""
    if path == INBOX_MODE_PATH:
        return raw or ""
    raise KeyError(path)


# ── Riskier vs safer ─────────────────────────────────────────────────────────

def _pause_rank(value: str) -> float:
    """How long a pause lasts: not paused = 0, a time = its epoch seconds,
    until resumed = infinity. A LOWER rank means the assistant is freer."""
    if not value:
        return 0.0
    if value == UNTIL_RESUMED:
        return float("inf")
    try:
        return parse_iso_with_offset(value).timestamp()
    except ValueError:
        return 0.0


def _is_auto_inbox(value: Any) -> bool:
    """``scheduled`` is the older name for ``auto`` (models.VALID_DAILY_CHECK_VALUES)."""
    return str(value or "") in ("auto", "scheduled")


def classify_change(
    path: str, before: Any, after: Any, *, switches: tuple[str, str] = (DEFAULT_LINE_SWITCH, DEFAULT_LINE_SWITCH)
) -> Risk:
    """``"riskier"`` when the change gives the assistant MORE freedom, else
    ``"safer"``. Pure; ``before`` / ``after`` are EFFECTIVE values (defaults
    filled). An unchanged value is ``"safer"`` (a no-op write).

    ``switches`` is ``(above_line, below_line)``, the EFFECTIVE values at the time
    of the change; only ``apply_min_score`` reads it (a moved line is riskier only
    when it moves jobs from an ``ask`` side to an ``auto`` side).

    ===================  =============================================  =========================
    path                 riskier                                        safer
    ===================  =============================================  =========================
    above_line /         ask -> auto                                    auto -> ask
    below_line
    apply_min_score      not set -> a number while either switch is     a number -> not set;
                         auto; a LOWER number when above=auto and       equal; a lower number
                         below=ask; a HIGHER number when below=auto     otherwise; a higher number
                         and above=ask                                  otherwise
    submit_mode          confirm -> auto_when_sure                      -> confirm
    daily_cap            raised, or a number -> none (cap removed)      none -> a number, lowered
    paused_until         cleared or moved earlier (resume)              set or extended
    pause_reason         never                                          always
    preferences.         anything -> auto (the assistant sends mail     every other value
    daily_check          on its own; "scheduled" counts as auto)
    ===================  =============================================  =========================
    """
    if path in (ABOVE_LINE_PATH, BELOW_LINE_PATH):
        return "riskier" if (after == "auto" and before != "auto") else "safer"
    if path == APPLY_MIN_SCORE_PATH:
        above_auto, below_auto = switches[0] == "auto", switches[1] == "auto"
        if after is None or before == after:
            return "safer"
        if before is None:
            return "riskier" if (above_auto or below_auto) else "safer"
        if after < before:
            return "riskier" if (above_auto and not below_auto) else "safer"
        return "riskier" if (below_auto and not above_auto) else "safer"
    if path == SUBMIT_MODE_PATH:
        return "riskier" if (after == AUTO_SUBMIT_MODE and before != AUTO_SUBMIT_MODE) else "safer"
    if path == DAILY_CAP_PATH:
        before_rank = float("inf") if before is None else before
        after_rank = float("inf") if after is None else after
        return "riskier" if after_rank > before_rank else "safer"
    if path == PAUSED_UNTIL_PATH:
        return "riskier" if _pause_rank(after) < _pause_rank(before) else "safer"
    if path == PAUSE_REASON_PATH:
        return "safer"
    if path == INBOX_MODE_PATH:
        return "riskier" if (_is_auto_inbox(after) and not _is_auto_inbox(before)) else "safer"
    raise ValueError(f"{path!r} is not a gated setting path")


LineSide = Literal["above", "below", "unknown"]


def line_block(fit_score: Optional[int], cfg: EffectiveSettings) -> dict[str, Any]:
    """The ``line`` block ``save_fit`` answers with: where this fit lands against
    the user's own score line, the switch that side uses, and ONE plain sentence
    telling the assistant what to do next. Pure."""
    line = cfg.apply_min_score
    side = line_side(fit_score, line)
    if line is None:
        return {
            "score_line": None, "side": "unknown", "switch": None,
            "next": "No score line set - ask the user for their score line "
                    "(update_profile assistant_settings.apply_min_score).",
        }
    if side == "unknown":
        return {
            "score_line": line, "side": "unknown", "switch": None,
            "next": f"No fit score saved, so this job cannot be placed against your line ({line}) - "
                    "save one with save_fit score.",
        }
    switch = cfg.above_line if side == "above" else cfg.below_line
    rule = (
        "ask the user before applying." if switch == "ask"
        else "you may apply on your own (still run check_submit before the final submit)."
    )
    return {
        "score_line": line, "side": side, "switch": switch,
        "next": f"{'Above' if side == 'above' else 'Below'} your line ({line}). Your rule: {switch} - {rule}",
    }


def line_side(fit_score: Optional[int], line: Optional[int]) -> LineSide:
    """Which side of the user's score line a fit score is on. ``above`` when the
    score is at or over the line; ``unknown`` when there is no score or no line.
    Pure; the ONE place the line is compared."""
    if fit_score is None or line is None:
        return "unknown"
    return "above" if fit_score >= line else "below"


def effective_inbox_values() -> frozenset[str]:  # pragma: no cover — documentation helper
    """The values ``preferences.daily_check`` may hold (kept for readers)."""
    return VALID_DAILY_CHECK_VALUES


# ── THE gate ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SubmitCounts:
    """What the gate needs counted: submits today, and how many applications
    were recorded since auto-submit was FIRST turned on (the practice run)."""

    submitted_today: int
    applied_since_auto_on: int


@dataclass(frozen=True)
class SubmitFacts:
    """The application's own facts: its status, whether a receipt exists, the
    per-job override (``confirm`` | ``auto_when_sure`` | ``None``), and the S3
    kit facts (``kit.gate_facts`` loads them):

    * ``duplicate_job`` - the same job was already applied to (and the user has
      not said "not a duplicate, go ahead");
    * ``cv_seen`` - the user saw the latest CV (web click / download, or an OK
      in chat);
    * ``approved`` - a stored ``submit_approved`` matches the latest CV
      (artifact id + hash); a CV edit makes it stop matching;
    * ``declined`` - the user's newest word on this application is "don't send";
    * ``blocked`` - the assistant recorded ``blocked`` with no later ``unblocked`` (S6);
    * ``form_filled`` - at least one ``form_filled`` event is recorded (the store
      must hold what was typed before any submit/ask answer).
    """

    status: str
    has_receipt: bool
    submit_override: Optional[str]
    duplicate_job: bool = False
    cv_seen: bool = False
    approved: bool = False
    declined: bool = False
    blocked: bool = False
    form_filled: bool = False
    # ``form_swept`` - a ``form_swept`` event is newer than the latest ``form_filled``
    # (the assistant re-read every field right before submit; 2026-10-13).
    form_swept: bool = False
    # The latest saved fit score for this application (``None`` = no fit yet).
    # Compared with the user's score line by :func:`line_side` (rule 5c).
    fit_score: Optional[int] = None
    # The host stored on the NEWEST ``form_filled`` (``None`` = none / unreadable).
    # check_submit's ``form_url`` must name the same host (``form_host_mismatch``).
    form_host: Optional[str] = None
    # The account's email is confirmed (or REQUIRE_EMAIL_VERIFICATION is off).
    # ``kit.gate_facts`` always sets it from the caller; ``False`` asks
    # ``email_not_verified`` and a yes never clears it (2026-10-14).
    email_verified: bool = True


@dataclass(frozen=True)
class SubmitDecision:
    """``submit`` — go ahead; ``ask`` — fill the form, stop before submit, ask
    the user yes for this one; ``stop`` — do not submit. ``reason`` is a closed
    code, ``detail`` one plain sentence for the user."""

    decision: Literal["submit", "ask", "stop"]
    reason: str
    detail: str


REASONS: tuple[str, ...] = (
    "paused", "already_applied", "user_declined", "daily_cap_reached", "duplicate_job", "unknown_site",
    "ask_always_site", "job_override_confirm", "submit_mode_confirm", "cv_not_seen", "practice_run",
    "user_approved", "auto_when_sure", "blocked", "score_line_unknown", "line_ask_above", "line_ask_below",
    "form_not_recorded", "form_not_swept", "form_host_mismatch", "email_not_verified",
)


_SCHEME_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_HOST_PORT_RE = re.compile(r"^[^:/?#@]+:[0-9]+(?:[/?#]|$)")


def _malformed_scheme(text: str) -> bool:
    """True for a scheme-like prefix that is not ``scheme://`` and not a bare
    ``host:port`` - e.g. the one-slash ``http:/www.linkedin.com/jobs/1``. Read
    as a bare host it would be the host ``http`` (a "company" site), skipping
    the LinkedIn / Indeed ask; it is unreadable instead (2026-10-14)."""
    if "://" in text or not _SCHEME_PREFIX_RE.match(text):
        return False
    return _HOST_PORT_RE.match(text) is None


def parse_site_host(raw: Optional[str]) -> Optional[str]:
    """The lowercase host of a URL or bare host (port and trailing dot stripped),
    or ``None`` when there is none or it cannot be read (a one-slash scheme like
    ``http:/host``, or a single-label host like ``localhost``)."""
    text = (raw or "").strip()
    if not text or any(ch.isspace() or ord(ch) < 32 for ch in text):
        return None
    if _malformed_scheme(text):
        return None
    # A browser reads "\" as "/" in an http(s) URL, Python's urlsplit does not:
    # "https://www.linkedin.com\@evil.example" is LinkedIn to the browser but
    # "evil.example" here. Two parsers disagreeing = unknown site (ask).
    if "\\" in text:
        return None
    try:
        parts = urlsplit(text if "://" in text else f"//{text}")
        host = parts.hostname
    except ValueError:
        return None
    if not host:
        return None
    # A browser percent-decodes a host ("linkedin%2Ecom") and maps full-width
    # letters and the IDNA dot look-alikes (U+3002, U+FF0E, U+FF61) to ASCII,
    # so "ｌｉｎｋｅｄｉｎ。com" IS linkedin.com to it. Refuse "%" (ask) and
    # NFKC-fold the rest so a look-alike of a brand still reads as the brand.
    if "%" in host:
        return None
    if not host.isascii():
        import unicodedata  # noqa: PLC0415 — only a non-ASCII host needs it

        host = unicodedata.normalize("NFKC", host)
        for dot in ("。", "．", "｡"):
            host = host.replace(dot, ".")
    host = host.rstrip(".").lower()
    if len([label for label in host.split(".") if label]) < 2:
        return None  # a single label ("http", "localhost") is not a site
    return host


_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def screen_form_url(raw: Optional[str]) -> Optional[str]:
    """The STRICT reading of a form address (check_submit's ``form_url`` and
    ``form_filled``'s): the lowercase ASCII host, or ``None`` when the address is
    not plainly one http(s) site. Parsed with ``urllib.parse`` only, never by
    splitting strings. ``None`` for: a scheme other than http/https; any ``@``
    (user-info), ``%`` or backslash in the authority - a browser and Python can
    disagree about which host those name ("https://boards.greenhouse.io%2F@evil.com"
    is evil.com); any non-ASCII character, an IDNA (``xn--``) label, an IP-literal
    in brackets, a label that is not letters/digits/hyphens, a single-label
    host, or a one-slash scheme (``http:/host``). A bare host
    (``boards.greenhouse.io``) is read as ``//host``. A ``None`` here can never
    be cleared by a stored yes (``unknown_site``)."""
    text = (raw or "").strip()
    if not text or len(text) > 2048 or not text.isascii() or "\\" in text:
        return None
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return None
    if _malformed_scheme(text):
        return None  # "http:/linkedin.com" would read as the host "http"
    has_scheme = _SCHEME_RE.match(text) is not None
    try:
        parts = urlsplit(text if has_scheme else f"//{text}")
        _ = parts.port  # a non-numeric / out-of-range port raises ValueError
    except ValueError:
        return None
    if has_scheme and parts.scheme.lower() not in ("http", "https"):
        return None
    netloc = parts.netloc
    if not netloc or any(ch in netloc for ch in "@%[]"):
        return None
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        return None
    labels = host.split(".")
    if len(labels) < 2:
        return None  # a single-label host ("http", "localhost") is not a site
    if any(not _HOST_LABEL_RE.match(label) or label.startswith("xn--") for label in labels):
        return None
    return host


def host_kind(host: Optional[str]) -> Literal["brand", "company", "invalid"]:
    """``brand`` when any whole label of ``host`` is an always-ask brand
    (``uk.indeed.com``, ``indeed.co.uk``, ``lnkd.in``); ``company`` for any other
    readable host (``notindeed.com``); ``invalid`` for none. Over-blocking only
    asks, so this is deliberately loose in the safe direction."""
    if host is None:
        return "invalid"
    brands = {b.strip().lower() for b in settings.SUBMIT_ASK_ALWAYS_BRANDS if b.strip()}
    return "brand" if any(label in brands for label in host.split(".")) else "company"


def may_submit(
    cfg: EffectiveSettings,
    application: SubmitFacts,
    site_host: Optional[str],
    counts: SubmitCounts,
    *,
    now: datetime,
) -> SubmitDecision:
    """May the assistant press the final submit? First matching rule wins.

    1. paused -> stop ``paused``
    2. receipt exists, or status is not ``considering`` -> stop ``already_applied``
    3. the user said "don't send" (newer than any yes) -> stop ``user_declined``
    4. daily cap reached -> stop ``daily_cap_reached``
    4b. no ``form_filled`` event recorded -> stop ``form_not_recorded`` (a stored
        yes does NOT clear it: the record of what was typed is needed either way)
    4c. no ``form_swept`` newer than the latest ``form_filled`` -> stop
        ``form_not_swept`` (the pre-submit re-read; a yes does not clear it)
    4d. the account's email is not confirmed -> ask ``email_not_verified`` (a
        yes does NOT clear it; after the stops so a stop is never softened to an ask)
    5. same job already applied to -> ``duplicate_job``: STOP when the mode is
       auto (nobody is watching), else ask
    5b. a ``blocked`` with no later ``unblocked`` -> ask ``blocked`` (S6)
    5d. ``form_url`` fails :func:`screen_form_url` (user-info ``@``, ``%`` or a
        backslash in the authority, non-ASCII / IDNA host, not http(s)) -> ask
        ``unknown_site`` (a yes does NOT clear it)
    5e. ``form_url``'s host is not the host of the newest ``form_filled`` -> ask
        ``form_host_mismatch`` (a yes does NOT clear it)
    5c. the fit score vs the user's score line (``line_side``): no line or no fit
        yet -> ask ``score_line_unknown``; the switch for that side
        (``above_line`` / ``below_line``) is ``ask`` -> ask ``line_ask_above`` /
        ``line_ask_below``. Checked inside ``_first_ask`` right after the
        duplicate ask, so a stored yes still clears it.
    6. site unknown -> ask ``unknown_site``
    7. Indeed / LinkedIn style site -> ask ``ask_always_site``
    8. the mode (the job's override, else ``submit_mode``) is ``confirm`` -> ask
    9. auto, but the user has not seen the latest CV -> ask ``cv_not_seen``
    10. first application since auto-submit was turned on -> ask ``practice_run``
    11. otherwise -> submit ``auto_when_sure``

    A stored yes for THIS CV (``approved``) clears every ASK from 5 to 10 (5c too)(even
    Indeed / LinkedIn and the practice run) and answers ``submit`` /
    ``user_approved``. Rules 1-4c, the auto-mode duplicate stop, the ``blocked``
    ask (5b), the form-address checks (5d, 5e) and ``email_not_verified`` (4d)
    are NEVER cleared by a yes.
    """
    if is_paused(cfg.paused_until, now):
        return SubmitDecision("stop", "paused", "Applications are paused. Do not submit anything.")
    if application.has_receipt or application.status != "considering":
        return SubmitDecision("stop", "already_applied", "This application is already applied or closed.")
    if application.declined:
        return SubmitDecision(
            "stop", "user_declined", "The user said not to send this application. Do not submit it."
        )
    if cfg.daily_cap is not None and counts.submitted_today >= cfg.daily_cap:
        return SubmitDecision("stop", "daily_cap_reached", "The daily limit of applications is reached. Try tomorrow.")
    if not application.form_filled:
        return SubmitDecision(
            "stop", "form_not_recorded",
            "Fill the form first, then record_event form_filled with the answers you typed, "
            "then call check_submit again. Do not submit yet.",
        )
    if not application.form_swept:
        return SubmitDecision(
            "stop", "form_not_swept",
            "Re-read every field on the page right before submit, fix what is wrong, "
            "then record_event form_swept and call check_submit again.",
        )
    if not application.email_verified:
        return SubmitDecision(
            "ask", "email_not_verified",
            "The user has not confirmed their email address yet. Do not submit; "
            "the user confirms it on Job360 first, or submits this one themselves.",
        )
    override = application.submit_override if application.submit_override in VALID_SUBMIT_MODES else None
    mode = override or cfg.submit_mode
    auto = mode == AUTO_SUBMIT_MODE
    if application.duplicate_job and auto:
        return SubmitDecision(
            "stop", "duplicate_job",
            "Needs you: possible duplicate - this job was already applied to. Do not submit; the user decides.",
        )
    if application.blocked:
        return SubmitDecision(
            "ask", "blocked",
            "Your assistant got stuck on this application. Do not submit; the user resolves it first.",
        )
    screened = screen_form_url(site_host)
    if screened is None:
        return SubmitDecision(
            "ask", "unknown_site",
            "The form address could not be read as one plain http(s) site. "
            "Fill the form, stop before submit and ask yes.",
        )
    if screened != application.form_host:
        return SubmitDecision(
            "ask", "form_host_mismatch",
            "This address is not the site where the form was filled (form_filled). "
            "Do not submit; check the page, and ask the user.",
        )
    ask = _first_ask(cfg, application, screened, counts, override=override, auto=auto)
    if ask is None:
        return SubmitDecision("submit", "auto_when_sure", "Auto-submit is on and this site allows it. You may submit.")
    if application.approved:
        return SubmitDecision(
            "submit", "user_approved", "The user said yes to sending this application with this CV. You may submit."
        )
    return ask


def _first_ask(
    cfg: EffectiveSettings,
    application: SubmitFacts,
    site_host: Optional[str],
    counts: SubmitCounts,
    *,
    override: Optional[str],
    auto: bool,
) -> Optional[SubmitDecision]:
    """The first reason to ASK, in rule order, or ``None`` when nothing asks."""
    if application.duplicate_job:
        return SubmitDecision(
            "ask", "duplicate_job",
            "Possible duplicate: this job was already applied to. Fill the form, stop before submit and ask yes.",
        )
    side = line_side(application.fit_score, cfg.apply_min_score)
    if side == "unknown":
        return SubmitDecision(
            "ask", "score_line_unknown",
            "No score line or no fit score yet. Fill the form, stop before submit and ask yes.",
        )
    if (cfg.above_line if side == "above" else cfg.below_line) == "ask":
        return SubmitDecision(
            "ask", f"line_ask_{side}",
            f"The user asked to be asked for jobs {side} their score line. "
            "Fill the form, stop before submit and ask yes.",
        )
    kind = host_kind(parse_site_host(site_host))
    if kind == "invalid":
        return SubmitDecision(
            "ask", "unknown_site", "The site could not be read. Fill the form, stop before submit and ask yes."
        )
    if kind == "brand":
        return SubmitDecision(
            "ask", "ask_always_site",
            "This site does not allow automatic applying. Fill the form, stop before submit and ask yes.",
        )
    if not auto:
        if override is not None:
            return SubmitDecision(
                "ask", "job_override_confirm",
                "This job is set to confirm. Fill the form, stop before submit and ask yes.",
            )
        return SubmitDecision(
            "ask", "submit_mode_confirm", "Submit mode is confirm. Fill the form, stop before submit and ask yes."
        )
    if not (application.cv_seen or application.approved):
        return SubmitDecision(
            "ask", "cv_not_seen",
            "The user has not seen the latest CV. Show it, get the user's OK, then fill the form and ask yes.",
        )
    if counts.applied_since_auto_on == 0:
        return SubmitDecision(
            "ask", "practice_run",
            "First application since auto-submit was turned on: a practice run. "
            "Fill the form, stop before submit and let the user check it.",
        )
    return None


# ── Loaders (the only I/O here) ──────────────────────────────────────────────


def practice_info(user_id: str) -> tuple[Optional[str], int]:
    """``(auto_on_since, applied_since_auto_on)`` — DERIVED, never stored.

    ``auto_on_since`` is when auto-submit was FIRST turned on for this account:
    the earliest ``profile_edits`` row for ``assistant_settings.submit_mode``
    holding ``auto_when_sure`` (once per account — owner answer 3, 2026-10-08).
    An account that only ever used the per-job override has no such row, so the
    earliest ``submit_mode_set`` event carrying ``auto_when_sure`` stands in.
    ``None`` when neither exists. ``applied_since_auto_on`` counts this user's
    ``applied`` events recorded after it (0 when there is no start).
    """
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        row = conn.execute(
            "SELECT MIN(set_at) FROM profile_edits WHERE user_id = ? AND path = ? AND value = ?",
            (user_id, SUBMIT_MODE_PATH, json.dumps(AUTO_SUBMIT_MODE)),
        ).fetchone()
        since: Optional[str] = row[0] if row and row[0] else None
        if since is None:
            ev = conn.execute(
                "SELECT MIN(recorded_at) FROM application_events "
                "WHERE user_id = ? AND event_type = 'submit_mode_set' AND payload LIKE ?",
                (user_id, f'%"{AUTO_SUBMIT_MODE}"%'),
            ).fetchone()
            since = ev[0] if ev and ev[0] else None
        if since is None:
            return None, 0
        cnt = conn.execute(
            "SELECT COUNT(*) FROM application_events "
            "WHERE user_id = ? AND event_type = 'applied' AND recorded_at > ?",
            (user_id, since),
        ).fetchone()
        return since, int(cnt[0]) if cnt else 0


async def load_submit_counts(db: JobDatabase, user_id: str, now: datetime) -> SubmitCounts:
    """Count what :func:`may_submit` needs for this user: submits today (in the
    user's OWN timezone, ``spine.user_today``) and the practice-run counter."""
    from src.services.applications import spine  # noqa: PLC0415 — spine imports the profile package

    today = await spine.user_today(db, user_id, now=now)
    cur = await db._db.execute("SELECT timezone FROM users WHERE id = ?", (user_id,))
    row = await cur.fetchone()
    raw_zone = (dict(row).get("timezone") if row else None) or "UTC"
    try:
        zone: ZoneInfo = ZoneInfo(raw_zone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    # Only the last 48h can fall on the user's today, whatever their offset.
    floor = (now - timedelta(hours=48)).isoformat()
    cur = await db._db.execute(
        "SELECT recorded_at FROM application_events "
        "WHERE user_id = ? AND event_type = 'applied' AND recorded_at >= ?",
        (user_id, floor),
    )
    submitted = 0
    for r in await cur.fetchall():
        try:
            at = datetime.fromisoformat(str(dict(r)["recorded_at"]))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at.astimezone(zone).date() == today:
            submitted += 1
    _since, applied_since = practice_info(user_id)
    return SubmitCounts(submitted_today=submitted, applied_since_auto_on=applied_since)


# ── The read model ───────────────────────────────────────────────────────────


def setup_round_missing(round_name: str, profile: UserProfile) -> list[str]:
    """The PATHS that must hold an answer before ``round_name`` may be marked
    done (owner decision 2026-10-10): ``targets`` needs target job titles, the
    user's score line AND (2026-10-12) at least one place to search - a
    ``user_info.right_to_work`` record with ``remote_ok`` or ``onsite``, so the
    hunt never falls back to where the user lives; ``settings`` needs the inbox
    mode. Pure; ``[]`` for every other round. An empty answer is missing (rule
    #29 stays: we never invent one)."""
    from src.services.profile.search_brief import RIGHT_TO_WORK_PATH, build_search_brief  # noqa: PLC0415

    missing: list[str] = []
    if round_name == "targets":
        if not profile.preferences.target_job_titles:
            missing.append("preferences.target_job_titles")
        if profile.assistant_settings.apply_min_score is None:
            missing.append(APPLY_MIN_SCORE_PATH)
        if not build_search_brief(profile.user_info.right_to_work)["rows"]:
            missing.append(RIGHT_TO_WORK_PATH)
    elif round_name == "settings" and not profile.preferences.daily_check:
        missing.append(INBOX_MODE_PATH)
    return missing


def setup_round_refusal(round_name: str, missing: list[str]) -> str:
    """The 422 message for a round marked done before its answers exist."""
    return (
        f"setup round {round_name} can't be marked done yet: missing {', '.join(missing)} "
        "- ask the user, then save them in the same call"
    )


def setup_round_waiting_refusal(
    round_name: str, parked: list[str], site_base_url: str, held_now: list[str] | None = None
) -> str:
    """The 422 message for a round marked done while a value it needs is not
    saved (owner decision 2026-10-14).

    ``parked`` — paths with a request ALREADY waiting for the user's Confirm.
    ``held_now`` — paths THIS call would have parked: no request exists yet (the
    whole call is refused), so they must never be described as waiting."""
    held = list(held_now or [])
    parts: list[str] = []
    if held:
        parts.append(
            f"{', '.join(held)} needs the user's OK: resend this call WITHOUT setup_progress "
            "(the change will be parked for the user's Confirm), then mark the round done after they Confirm"
        )
    if parked:
        parts.append(
            f"{', '.join(parked)} is waiting for the user's Confirm and is NOT saved - the user must tap "
            f"Confirm on Needs you at {site_base_url}/needs-you, then mark the round done"
        )
    return f"setup round {round_name} can't be marked done yet: " + "; ".join(parts)


def parked_line(path: str, site_base_url: str) -> str:
    """The plain line ``update_profile`` returns for each parked (waiting) path."""
    return f"{path}: NOT saved yet — the user must tap Confirm on Needs you at {site_base_url}/needs-you"


def setup_progress_view(progress: Any) -> dict[str, Any]:
    """``{rounds, done, total, next}`` from the stored progress. ``next`` is the
    first unfinished round in order, ``""`` when all six are done. A malformed
    stored value reads as nothing done (it can only be a historical row)."""
    rounds = progress if isinstance(progress, dict) else {}
    done_rounds = {
        name: {"done_at": str(rounds[name].get("done_at", ""))}
        for name in SETUP_ROUNDS
        if isinstance(rounds.get(name), dict)
    }
    pending = [name for name in SETUP_ROUNDS if name not in done_rounds]
    return {
        "rounds": done_rounds,
        "done": len(done_rounds),
        "total": len(SETUP_ROUNDS),
        "next": pending[0] if pending else "",
    }


def build_settings_view(
    profile: UserProfile,
    waiting: list[dict[str, Any]],
    practice: tuple[Optional[str], int],
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """The one read model of the settings, used by ``GET /api/profile`` (top-level
    ``assistant_settings``), ``GET /api/assistant-settings`` and MCP ``get_profile``
    (top-level ``settings``): each field raw + effective, ``paused``, the three
    ALIASED preference values (``inbox_mode``, ``check_every``, ``notes``), the
    practice-run state and the waiting requests. ``waiting`` rows come from
    ``setting_requests.list_waiting`` (already non-expired)."""
    when = now if now is not None else datetime.now(timezone.utc)
    raw = profile.assistant_settings
    eff = effective(raw)
    since, applied_since = practice
    return {
        "apply_min_score": {"value": raw.apply_min_score, "effective": eff.apply_min_score},
        "above_line": {"value": raw.above_line, "effective": eff.above_line},
        "below_line": {"value": raw.below_line, "effective": eff.below_line},
        "submit_mode": {"value": raw.submit_mode, "effective": eff.submit_mode},
        "daily_cap": {"value": raw.daily_cap, "effective": eff.daily_cap},
        "paused_until": {
            "value": raw.paused_until,
            "effective": effective_path_value(PAUSED_UNTIL_PATH, raw.paused_until, when),
        },
        "pause_reason": {"value": raw.pause_reason, "effective": eff.pause_reason},
        "paused": is_paused(eff.paused_until, when),
        "inbox_mode": profile.preferences.daily_check,
        "check_every": profile.preferences.check_every,
        "notes": list(profile.preferences.assistant_notes or []),
        "practice_run": {
            "needed": since is not None and applied_since == 0,
            "auto_on_since": since,
        },
        "waiting": waiting,
        "setup_progress": setup_progress_view(raw.setup_progress),
    }

"""Assistant settings — how much the user's assistant may do on its own.

Owner decision 2026-10-08 (S2). Job360 never applies to anything itself; these
are the user's standing rules that the CONNECTED assistant reads and obeys:

* ``apply_min_score`` — the user's OWN score line, 0-100. NO default: unset
  (``None``) means "not set" and the assistant must ask for it (owner decision
  2026-10-10).
* ``above_line`` / ``below_line`` — ``ask`` (default) | ``auto``: two SEPARATE
  per-user switches, one for jobs at or above the line and one for jobs below it.
  ``below_line`` also takes ``skip`` (owner 2026-10-19: "no more below score line:
  drop that job") - a job below the line is never applied to (``below_score_line``).
  ``apply_mode`` is RETIRED (the field stays so old stored rows still load).
* ``submit_mode`` — ``confirm`` (default) | ``auto_when_sure``
* ``daily_cap`` — most submits per day; ``None`` = no cap (owner: no cap by default)
* ``company_cap`` / ``company_cap_days`` — most applications to ONE company inside a
  window of days (owner 2026-10-18); ``None`` = no limit, and no days = the operator
  default window (``ASSISTANT_COMPANY_CAP_DEFAULT_DAYS``)
* ``weekly_cap`` — most submits per Monday-Sunday week in the user's own timezone (bulk-apply C2)
* ``min_gap_minutes`` / ``apply_hours`` — the user's own FIXED pace: a minimum gap between submits
  and a daily window ``{"from": "HH:MM", "to": "HH:MM"}`` in their timezone (bulk-apply C1; never randomized)
* ``auto_grant_count`` / ``auto_grant_until`` — "auto for my next N until <time>" (bulk-apply R1): while
  active the mode resolves to auto, after that it is the user's own ``submit_mode``
* ``paused_until`` — ``""`` | ``"until_resumed"`` | an ISO-8601 time in the future
* ``pause_reason`` — one short line for the user's own memory
* ``outreach_send`` — ``draft_only`` (default) | ``auto``: may the assistant press Send on an outreach
  email (feature 10 gate). Its own consent, never implied by the Gmail mode
  ``preferences.daily_check`` (owner 2026-10-20, G087).
* ``hunt_every_hours`` — hunt for jobs every N hours, 1-168; ``None`` / ``0`` = off (owner 2026-10-23).
  The user's own scheduled task reads it through MCP ``whats_due``; Job360 never runs a hunt itself.
* ``people_hunt`` — ``True`` | ``False`` (default): may the user's scheduled task find people to reach
  for new applications (``whats_due`` duty ``reach``). It never sends (owner 2026-10-24).
* ``pc_on_hours`` — ``{"days": [...], "from": "HH:MM", "to": "HH:MM"}`` in the user's timezone: when their
  computer is usually on. Tasks Job360's own browser could not finish wait for it (the own-browser queue,
  owner 2026-10-25); ``None`` = any time. Always a "safer" change: every send still needs the user's click.

``""`` / ``None`` mean "not chosen": the SAFE default applies (rule #29: an empty
shelf stays silent, it is never an invented value). Storage is the S1 pattern:
the base lives in ``user_profiles.assistant_settings`` (one writer,
``storage.save_assistant_settings``), the values ride the ``profile_edits``
overlay on ``assistant_settings.*`` paths.

Four things live here, all PURE except the loaders at the bottom:

1. :func:`validate_setting` — the per-path value rules (called by
   ``edits.validate_edit``). Errors name the PATH, never echo the value.
2. :func:`classify_change` — "riskier" (more freedom for the assistant) or
   "safer". An assistant's riskier change is NEVER applied; it becomes a
   waiting request the user confirms on the website
   (``setting_requests.py``). The user's own web click applies at once.
3. :func:`may_submit` — THE one gate every "may I press submit?" question goes
   through (``GET /applications/{id}/submit-check`` and the MCP ``check_submit``
   tool both call it). One function, one order of rules, closed reason codes.
4. :func:`may_send_outreach` — the same idea for "may I press Send on this
   outreach email?" (``GET /contacts/{id}/outreach-check`` and the MCP
   ``check_outreach`` tool, feature 10).

Logs never carry ``pause_reason`` or notes text (they are free text the user
or an assistant typed); the callers log paths, actors, ids and closed values.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal, Optional, Union
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.core import settings
from src.repositories import pgsync
from src.services.applications.job_checks import _JOB_ID_KEYS, canonical_url
from src.services.profile import edits as profile_edits
from src.services.profile.models import (
    VALID_DAILY_CHECK_VALUES,
    VALID_OUTREACH_SEND_VALUES,
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
    "(ask|auto) and assistant_settings.below_line (ask|auto|skip)"
)
APPLY_MIN_SCORE_PATH = f"{HEAD}.apply_min_score"
ABOVE_LINE_PATH = f"{HEAD}.above_line"
BELOW_LINE_PATH = f"{HEAD}.below_line"
SUBMIT_MODE_PATH = f"{HEAD}.submit_mode"
DAILY_CAP_PATH = f"{HEAD}.daily_cap"
COMPANY_CAP_PATH = f"{HEAD}.company_cap"
COMPANY_CAP_DAYS_PATH = f"{HEAD}.company_cap_days"
COMPANY_CAP_DAYS_MAX = 365
WEEKLY_CAP_PATH = f"{HEAD}.weekly_cap"
MIN_GAP_PATH = f"{HEAD}.min_gap_minutes"
MIN_GAP_MAX = 240
APPLY_HOURS_PATH = f"{HEAD}.apply_hours"
AUTO_GRANT_COUNT_PATH = f"{HEAD}.auto_grant_count"
AUTO_GRANT_UNTIL_PATH = f"{HEAD}.auto_grant_until"
PAUSED_UNTIL_PATH = f"{HEAD}.paused_until"
PAUSE_REASON_PATH = f"{HEAD}.pause_reason"
AUTO_EMPLOYER_EMAILS_PATH = f"{HEAD}.auto_employer_emails"
OUTREACH_SEND_PATH = f"{HEAD}.outreach_send"
DEFAULT_OUTREACH_SEND = "draft_only"
OUTREACH_SEND_AUTO = "auto"
HUNT_EVERY_PATH = f"{HEAD}.hunt_every_hours"
HUNT_EVERY_MAX = 168  # one week
PEOPLE_HUNT_PATH = f"{HEAD}.people_hunt"
PC_ON_HOURS_PATH = f"{HEAD}.pc_on_hours"
PC_ON_DAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SETTING_PATHS: tuple[str, ...] = (
    APPLY_MIN_SCORE_PATH, ABOVE_LINE_PATH, BELOW_LINE_PATH, SUBMIT_MODE_PATH,
    DAILY_CAP_PATH, COMPANY_CAP_PATH, COMPANY_CAP_DAYS_PATH, PAUSED_UNTIL_PATH, PAUSE_REASON_PATH,
    AUTO_EMPLOYER_EMAILS_PATH, WEEKLY_CAP_PATH, MIN_GAP_PATH, APPLY_HOURS_PATH,
    AUTO_GRANT_COUNT_PATH, AUTO_GRANT_UNTIL_PATH, OUTREACH_SEND_PATH, HUNT_EVERY_PATH,
    PEOPLE_HUNT_PATH, PC_ON_HOURS_PATH,
)
VALID_LINE_SWITCHES: frozenset[str] = frozenset({"ask", "auto"})
# Below the line only: ``skip`` = drop the job, never apply (owner 2026-10-19).
LINE_SKIP = "skip"
VALID_BELOW_SWITCHES: frozenset[str] = VALID_LINE_SWITCHES | {LINE_SKIP}
# How free each below-line switch leaves the assistant (higher = freer).
_BELOW_RANK = {LINE_SKIP: 0, "ask": 1, "auto": 2}
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


def _gap_minutes(path: str, value: Any) -> Union[int, float]:
    """``min_gap_minutes``: 0-240 minutes in WHOLE SECONDS (owner ask 2026-10-18: "I should also get
    seconds"), so 1.5 = 1 min 30 s. Kept as a whole number when it is one; bool is never a number."""
    msg = f"must be a number of minutes from 0 to {MIN_GAP_MAX}, in whole seconds (1.5 = 1 min 30 s)"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _bad(path, msg)
    if not 0 <= value <= MIN_GAP_MAX:
        raise _bad(path, msg)
    secs = value * 60
    if abs(secs - round(secs)) > 0.01:
        raise _bad(path, msg)
    whole = round(secs)
    return whole // 60 if whole % 60 == 0 else round(whole / 60, 4)


def gap_words(minutes: Union[int, float]) -> str:
    """``95`` -> "95 minutes", ``1.5`` -> "1 min 30 s", ``0.25`` -> "15 seconds"."""
    secs = round(float(minutes) * 60)
    m, sec = divmod(secs, 60)
    if sec == 0:
        return f"{m} minute{'s' if m != 1 else ''}"
    return f"{m} min {sec} s" if m else f"{sec} seconds"


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


_HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")


def _hhmm_minutes(text: str) -> int:
    """Minutes since midnight of a validated ``HH:MM``."""
    return int(text[:2]) * 60 + int(text[3:])


def _validate_apply_hours(path: str, value: Any) -> dict[str, str]:
    """``{"from": "HH:MM", "to": "HH:MM"}`` (24-hour, the user's own timezone), the two different.
    ``from`` later than ``to`` is an overnight window (22:00 to 06:00)."""
    if not isinstance(value, dict) or set(value) != {"from", "to"}:
        raise _bad(path, 'must be an object with exactly "from" and "to", each "HH:MM" (24-hour, your timezone)')
    out: dict[str, str] = {}
    for key in ("from", "to"):
        raw = value[key]
        if not isinstance(raw, str) or not _HHMM_RE.match(raw.strip()):
            raise _bad(path, f'"{key}" must be a time like 09:00 (24-hour HH:MM)')
        out[key] = raw.strip()
    if out["from"] == out["to"]:
        raise _bad(path, '"from" and "to" must be different times')
    return out


def _validate_pc_on_hours(path: str, value: Any) -> dict[str, Any]:
    """``{"days": ["mon", ...], "from": "HH:MM", "to": "HH:MM"}`` (24-hour, the user's own timezone): the days
    are unique, non-empty and from mon..sun (stored in week order); ``from`` and ``to`` differ. ``from`` later
    than ``to`` is an overnight window that belongs to its start day."""
    if not isinstance(value, dict) or set(value) != {"days", "from", "to"}:
        raise _bad(path, 'must be an object with exactly "days", "from" and "to"')
    days = value["days"]
    if not isinstance(days, list) or not days or any(not isinstance(d, str) for d in days):
        raise _bad(path, '"days" must be a non-empty list of mon, tue, wed, thu, fri, sat, sun')
    if any(d not in PC_ON_DAYS for d in days) or len(set(days)) != len(days):
        raise _bad(path, '"days" must be unique and from mon, tue, wed, thu, fri, sat, sun')
    times = _validate_apply_hours(path, {"from": value["from"], "to": value["to"]})
    return {"days": [d for d in PC_ON_DAYS if d in days], **times}


def _validate_grant_until(path: str, value: Any, now: datetime) -> str:
    """The end of a counted auto grant: an ISO-8601 time WITH an offset, in the future, at most
    ``ASSISTANT_AUTO_GRANT_MAX_DAYS`` ahead. No "until_resumed": a grant always ends."""
    if not isinstance(value, str):
        raise _bad(path, f"must be a string, not {type(value).__name__}")
    text = value.strip()
    try:
        when = parse_iso_with_offset(text)
    except ValueError:
        raise _bad(path, "must be an ISO-8601 time with an offset like 2026-10-20T18:00:00+01:00") from None
    if when <= now:
        raise _bad(path, "must be a time in the future")
    if when > now + timedelta(days=settings.ASSISTANT_AUTO_GRANT_MAX_DAYS):
        raise _bad(path, f"must be at most {settings.ASSISTANT_AUTO_GRANT_MAX_DAYS} days ahead")
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
    if path == ABOVE_LINE_PATH:
        return _closed_set(path, value, VALID_LINE_SWITCHES)
    if path == BELOW_LINE_PATH:
        return _closed_set(path, value, VALID_BELOW_SWITCHES)
    if path == SUBMIT_MODE_PATH:
        return _closed_set(path, value, VALID_SUBMIT_MODES)
    if path == APPLY_MIN_SCORE_PATH:
        return _int_in_range(path, value, 0, 100)
    if path == DAILY_CAP_PATH:
        return _int_in_range(path, value, 1, settings.ASSISTANT_DAILY_CAP_MAX)
    if path == COMPANY_CAP_PATH:
        return _int_in_range(path, value, 1, settings.ASSISTANT_COMPANY_CAP_MAX)
    if path == COMPANY_CAP_DAYS_PATH:
        return _int_in_range(path, value, 0, COMPANY_CAP_DAYS_MAX)  # 0 = all time (owner ask 2026-10-18)
    if path == WEEKLY_CAP_PATH:
        return _int_in_range(path, value, 1, settings.ASSISTANT_WEEKLY_CAP_MAX)
    if path == MIN_GAP_PATH:
        return _gap_minutes(path, value)
    if path == APPLY_HOURS_PATH:
        return _validate_apply_hours(path, value)
    if path == PC_ON_HOURS_PATH:
        return _validate_pc_on_hours(path, value)
    if path == AUTO_GRANT_COUNT_PATH:
        return _int_in_range(path, value, 1, settings.ASSISTANT_AUTO_GRANT_MAX)
    if path == AUTO_GRANT_UNTIL_PATH:
        return _validate_grant_until(path, value, when)
    if path == PAUSED_UNTIL_PATH:
        return _validate_paused_until(path, value, when)
    if path == PAUSE_REASON_PATH:
        return _validate_pause_reason(path, value)
    if path in (AUTO_EMPLOYER_EMAILS_PATH, PEOPLE_HUNT_PATH):
        if not isinstance(value, bool):
            raise _bad(path, f"must be true or false, not {type(value).__name__}")
        return value
    if path == OUTREACH_SEND_PATH:
        return _closed_set(path, value, VALID_OUTREACH_SEND_VALUES)
    if path == HUNT_EVERY_PATH:
        return _int_in_range(path, value, 0, HUNT_EVERY_MAX)  # 0 = off, like null
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
    auto_employer_emails: bool = False
    # Owner decision 2026-10-18: None = no per-company limit; the window is always a number.
    company_cap: Optional[int] = None
    company_cap_days: int = settings.ASSISTANT_COMPANY_CAP_DEFAULT_DAYS
    # Bulk-apply C1 / C2 / R1 (None / "" = not set).
    weekly_cap: Optional[int] = None
    min_gap_minutes: Optional[float] = None
    apply_hours: Optional[tuple[str, str]] = None
    auto_grant_count: Optional[int] = None
    auto_grant_until: str = ""
    outreach_send: str = DEFAULT_OUTREACH_SEND
    hunt_every_hours: Optional[int] = None  # None = no scheduled hunt
    people_hunt: bool = False
    pc_on_hours: Optional[dict[str, Any]] = None  # None = any time


def _hunt_every(raw: Any) -> Optional[int]:
    """A stored hunt interval as a whole number of hours; ``None`` / ``0`` / anything unreadable = off."""
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= HUNT_EVERY_MAX:
        return None
    return raw


def _pc_on_hours(raw: Any) -> Optional[dict[str, Any]]:
    """The stored PC-on window as ``{"days", "from", "to"}``; anything unreadable (a historical row) = any time."""
    if not isinstance(raw, dict) or not isinstance(raw.get("days"), list):
        return None
    days = raw["days"]
    if not days or any(d not in PC_ON_DAYS for d in days) or len(set(days)) != len(days):
        return None
    times = _hours_tuple({"from": raw.get("from"), "to": raw.get("to")})
    if times is None:
        return None
    return {"days": [d for d in PC_ON_DAYS if d in days], "from": times[0], "to": times[1]}


def pc_on_state(hours: Optional[dict[str, Any]], now_local: datetime) -> tuple[bool, Optional[datetime]]:
    """Is the user's computer expected to be on at ``now_local`` (a time in THEIR timezone)? Returns
    ``(open, opens_at)``: ``opens_at`` is the next opening (in the same timezone) when closed, else ``None``.
    No window (``None``) = any time = open. An overnight window belongs to its START day: ``fri 22:00-06:00``
    is open Saturday 05:00 and closed Saturday 22:00. Pure."""
    window = _pc_on_hours(hours)
    if window is None:
        return True, None
    start, end = _hhmm_minutes(window["from"]), _hhmm_minutes(window["to"])
    days = {PC_ON_DAYS.index(d) for d in window["days"]}
    minute = now_local.hour * 60 + now_local.minute
    today = now_local.weekday()
    if start < end:
        if today in days and start <= minute < end:
            return True, None
    else:  # overnight
        if today in days and minute >= start:
            return True, None
        if (today - 1) % 7 in days and minute < end:
            return True, None
    for ahead in range(8):
        day = now_local.date() + timedelta(days=ahead)
        if day.weekday() not in days:
            continue
        opening = datetime.combine(day, time(start // 60, start % 60), tzinfo=now_local.tzinfo)
        if opening > now_local:
            return False, opening
    return False, None  # unreachable: a non-empty day list always opens within a week


def _hours_tuple(raw: Any) -> Optional[tuple[str, str]]:
    """The stored ``{"from","to"}`` as a tuple; anything unreadable (a historical row) reads as no window."""
    if isinstance(raw, dict) and isinstance(raw.get("from"), str) and isinstance(raw.get("to"), str):
        if _HHMM_RE.match(raw["from"]) and _HHMM_RE.match(raw["to"]) and raw["from"] != raw["to"]:
            return raw["from"], raw["to"]
    return None


def _outreach_send(raw: Any) -> str:
    """Only a stored ``auto`` sends; "" / anything else (a historical row) reads as ``draft_only`` (G087)."""
    return OUTREACH_SEND_AUTO if raw == OUTREACH_SEND_AUTO else DEFAULT_OUTREACH_SEND


def effective(s: AssistantSettings) -> EffectiveSettings:
    """Fill the safe defaults: no score line / ask / ask / confirm / no cap / not paused /
    no company limit (and the default window when the user set a limit without days) / draft only."""
    return EffectiveSettings(
        apply_min_score=None if s.apply_min_score is None else int(s.apply_min_score),
        above_line=s.above_line or DEFAULT_LINE_SWITCH,
        below_line=s.below_line or DEFAULT_LINE_SWITCH,
        submit_mode=s.submit_mode or DEFAULT_SUBMIT_MODE,
        daily_cap=None if s.daily_cap is None else int(s.daily_cap),
        paused_until=s.paused_until or "",
        pause_reason=s.pause_reason or "",
        auto_employer_emails=bool(s.auto_employer_emails),
        company_cap=None if s.company_cap is None else int(s.company_cap),
        company_cap_days=(
            settings.ASSISTANT_COMPANY_CAP_DEFAULT_DAYS if s.company_cap_days is None else int(s.company_cap_days)
        ),
        weekly_cap=None if s.weekly_cap is None else int(s.weekly_cap),
        min_gap_minutes=None if not s.min_gap_minutes else s.min_gap_minutes,
        apply_hours=_hours_tuple(s.apply_hours),
        auto_grant_count=None if s.auto_grant_count is None else int(s.auto_grant_count),
        auto_grant_until=s.auto_grant_until or "",
        outreach_send=_outreach_send(s.outreach_send),
        hunt_every_hours=_hunt_every(s.hunt_every_hours),
        people_hunt=bool(s.people_hunt),
        pc_on_hours=_pc_on_hours(s.pc_on_hours),
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
    if path in (DAILY_CAP_PATH, COMPANY_CAP_PATH, WEEKLY_CAP_PATH, AUTO_GRANT_COUNT_PATH):
        return raw
    if path == MIN_GAP_PATH:
        return raw or None  # 0 and null both mean "no gap"
    if path == APPLY_HOURS_PATH:
        hours = _hours_tuple(raw)
        return None if hours is None else {"from": hours[0], "to": hours[1]}
    if path == AUTO_GRANT_UNTIL_PATH:
        return (raw or "") if _grant_not_over(raw or "", when) else ""
    if path == COMPANY_CAP_DAYS_PATH:
        return settings.ASSISTANT_COMPANY_CAP_DEFAULT_DAYS if raw is None else raw
    if path == PAUSED_UNTIL_PATH:
        return (raw or "") if is_paused(raw or "", when) else ""
    if path == PAUSE_REASON_PATH:
        return raw or ""
    if path == INBOX_MODE_PATH:
        return raw or ""
    if path in (AUTO_EMPLOYER_EMAILS_PATH, PEOPLE_HUNT_PATH):
        return bool(raw)
    if path == OUTREACH_SEND_PATH:
        return _outreach_send(raw)
    if path == HUNT_EVERY_PATH:
        return _hunt_every(raw)
    if path == PC_ON_HOURS_PATH:
        return _pc_on_hours(raw)
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


def _grant_not_over(until: str, now: datetime) -> bool:
    """A stored grant end that is still in the future. Unreadable / empty = over."""
    if not until:
        return False
    try:
        return parse_iso_with_offset(until) > now
    except ValueError:
        return False


def _grant_until_rank(value: str) -> float:
    """How long a grant lasts: none = 0, else its epoch seconds. LOWER = the assistant is less free."""
    if not value:
        return 0.0
    try:
        return parse_iso_with_offset(value).timestamp()
    except ValueError:
        return 0.0


def _hours_minutes(value: Any) -> frozenset[int]:
    """The minutes of the day (0-1439) an ``apply_hours`` window allows; no window = all of them."""
    hours = _hours_tuple(value)
    if hours is None:
        return frozenset(range(1440))
    start, end = _hhmm_minutes(hours[0]), _hhmm_minutes(hours[1])
    if start < end:
        return frozenset(range(start, end))
    return frozenset(range(start, 1440)) | frozenset(range(0, end))


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
    above_line           ask -> auto                                    auto -> ask
    below_line           freer: skip -> ask -> auto                     toward skip
    apply_min_score      not set -> a number while either switch is     a number -> not set
                         auto; a LOWER number when above=auto and       (unless below=skip);
                         below=ask; a LOWER number or a number ->       equal; a lower number
                         not set when below=skip; a HIGHER number       otherwise; a higher number
                         when below=auto and above=ask                  otherwise
    submit_mode          confirm -> auto_when_sure                      -> confirm
    daily_cap            raised, or a number -> none (cap removed)      none -> a number, lowered
    company_cap          raised, or a number -> none (limit removed)    none -> a number, lowered
    company_cap_days     LOWERED (a shorter window frees the           raised
                         assistant)
    weekly_cap           raised, or a number -> none (cap removed)      none -> a number, lowered
    min_gap_minutes      lowered, or removed (null / 0)                 raised, or set from none
    apply_hours          any minute of the day newly allowed (wider,    narrowed to a subset;
                         moved, or removed)                             none -> a window
    auto_grant_count     raised, or none -> a number                    lowered, or cleared
    auto_grant_until     a LATER end, or none -> a time                 an earlier end, or cleared
    paused_until         cleared or moved earlier (resume)              set or extended
    pause_reason         never                                          always
    auto_employer_       off -> on (the assistant emails employers     on -> off (the stop
    emails               in the owner's name)                          switch; applies at once)
    outreach_send        draft_only -> auto (the assistant sends       auto -> draft_only
                         outreach in the user's name)                  (applies at once)
    hunt_every_hours     off -> a number, or a SMALLER number (the      turned off, or a larger
                         assistant hunts on its own more often)         number
    pc_on_hours          never (a window frees nothing: every send       always
                         still needs the user's own click)
    preferences.         anything -> auto (the assistant reads Gmail    every other value
    daily_check          on its own; "scheduled" counts as auto)
    ===================  =============================================  =========================
    """
    if path == ABOVE_LINE_PATH:
        return "riskier" if (after == "auto" and before != "auto") else "safer"
    if path == BELOW_LINE_PATH:
        # An unknown stored value ranks as `ask` (the default it reads as).
        return "riskier" if _BELOW_RANK.get(after, 1) > _BELOW_RANK.get(before, 1) else "safer"
    if path == APPLY_MIN_SCORE_PATH:
        above_auto, below_auto = switches[0] == "auto", switches[1] == "auto"
        # below=skip is a protection: lowering or clearing the line brings jobs the user
        # chose to drop back into play (as "ask" or "unknown"), so it waits for the user.
        below_skip = switches[1] == "skip"
        if before == after:
            return "safer"
        if after is None:
            return "riskier" if (below_skip and before is not None) else "safer"
        if before is None:
            return "riskier" if (above_auto or below_auto) else "safer"
        if after < before:
            return "riskier" if ((above_auto and not below_auto) or below_skip) else "safer"
        return "riskier" if (below_auto and not above_auto) else "safer"
    if path == SUBMIT_MODE_PATH:
        return "riskier" if (after == AUTO_SUBMIT_MODE and before != AUTO_SUBMIT_MODE) else "safer"
    if path in (DAILY_CAP_PATH, COMPANY_CAP_PATH, WEEKLY_CAP_PATH):
        before_rank = float("inf") if before is None else before
        after_rank = float("inf") if after is None else after
        return "riskier" if after_rank > before_rank else "safer"
    if path == MIN_GAP_PATH:
        return "riskier" if (after or 0) < (before or 0) else "safer"
    if path == APPLY_HOURS_PATH:
        return "riskier" if (_hours_minutes(after) - _hours_minutes(before)) else "safer"
    if path == AUTO_GRANT_COUNT_PATH:
        return "riskier" if (after or 0) > (before or 0) else "safer"
    if path == AUTO_GRANT_UNTIL_PATH:
        return "riskier" if _grant_until_rank(after or "") > _grant_until_rank(before or "") else "safer"
    if path == COMPANY_CAP_DAYS_PATH:
        # 0 = all time: the longest window there is.
        def days_rank(d: Any) -> float:
            return float("inf") if d == 0 else float(d)

        return "riskier" if days_rank(after) < days_rank(before) else "safer"
    if path == PAUSED_UNTIL_PATH:
        return "riskier" if _pause_rank(after) < _pause_rank(before) else "safer"
    if path == PAUSE_REASON_PATH:
        return "safer"
    if path == INBOX_MODE_PATH:
        return "riskier" if (_is_auto_inbox(after) and not _is_auto_inbox(before)) else "safer"
    if path in (AUTO_EMPLOYER_EMAILS_PATH, PEOPLE_HUNT_PATH):
        return "riskier" if (bool(after) and not bool(before)) else "safer"
    if path == OUTREACH_SEND_PATH:
        return "riskier" if (after == OUTREACH_SEND_AUTO and before != OUTREACH_SEND_AUTO) else "safer"
    if path == HUNT_EVERY_PATH:
        # Off is the longest interval there is: more often, or newly on, frees the assistant.
        before_rank = float("inf") if not before else before
        after_rank = float("inf") if not after else after
        return "riskier" if after_rank < before_rank else "safer"
    if path == PC_ON_HOURS_PATH:
        return "safer"
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
        else "do not apply - drop this job." if switch == LINE_SKIP
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
    # Owner decision 2026-10-18: OTHER applications of this user with an ``applied`` event at
    # the same company inside the user's ``company_cap_days`` (0 when no application was given
    # to :func:`load_submit_counts`, or the company is blank / unknown).
    company_applied_in_window: int = 0
    # Bulk-apply C2: submits in the user's current Monday-Sunday week.
    submitted_this_week: int = 0
    # Bulk-apply C1: when the newest real ``applied`` was recorded (``None`` = none yet) and the user's
    # IANA timezone (the gap's exact next time and the apply hours are read in it).
    last_applied_at: Optional[datetime] = None
    user_tz: str = "UTC"
    # Bulk-apply R1: ``applied`` events since the current auto grant began (see :func:`grant_info`).
    applied_since_grant: int = 0
    # Gap roadmap slice 30: what the current grant covers - ``{application_id: form host}`` snapshotted
    # from the user's queue when they confirmed it on the web (``grant_scope.py``); ``{}`` = nothing.
    # ``None`` = a grant confirmed before snapshots existed: it covers every application until it ends.
    grant_scope: Optional[dict[int, Optional[str]]] = None


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
      (artifact id + hash); a CV edit makes it stop matching; so does
      ``approval_stale`` (gap roadmap slice 8);
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
    # Weak spot #1b: the newest ``form_swept`` read fewer fields than the newest ``form_checked``
    # listed questions (``form_count_mismatch``), or named fields it could not read back
    # (``unreadable_fields``). Both default False: an application with no ``form_checked`` never stops here.
    form_count_short: bool = False
    # The numbers behind ``form_count_short`` for its message (owner, 2026-10-23): the questions the sweep
    # counted (``fields_checked`` + the fill's ``fields_skipped``) and how many check_form listed. None = unknown.
    form_count_counted: Optional[int] = None
    form_count_listed: Optional[int] = None
    unreadable_fields: bool = False
    # Weak spot #1b (c): an application created after the cut-off (``kit.FORM_CHECK_REQUIRED_FROM``)
    # has no ``form_checked`` event yet (``form_check_missing``). Older applications are never flagged.
    form_check_missing: bool = False
    # Owner rule 2026-10-20: the newest ``form_checked`` of a page still lists a REQUIRED question with no
    # saved answer (``required_unanswered``). Optional questions may stay blank; required ones never.
    required_unanswered: bool = False
    # The latest saved fit score for this application (``None`` = no fit yet).
    # Compared with the user's score line by :func:`line_side` (rule 5c).
    fit_score: Optional[int] = None
    # The host stored on the NEWEST ``form_filled`` (``None`` = none / unreadable).
    # check_submit's ``form_url`` must name the same host (``form_host_mismatch``).
    form_host: Optional[str] = None
    # Gap roadmap slice 7: the :func:`clean_form_url` page stored on the newest ``form_filled`` /
    # ``form_swept`` that carries one (``kit.latest_form_url``; ``None`` = none). check_submit's
    # ``form_url`` must name the same host + path (``form_page_mismatch``).
    form_url: Optional[str] = None
    # The account's email is confirmed (or REQUIRE_EMAIL_VERIFICATION is off).
    # ``kit.gate_facts`` always sets it from the caller; ``False`` asks
    # ``email_not_verified`` and a yes never clears it (2026-10-14).
    email_verified: bool = True
    # Another assistant session's live hold on this application (2026-10-15):
    # ``(held_by, session, since)`` of the newest kit read by a DIFFERENT
    # (actor, session) inside ``KIT_HOLD_MINUTES`` with no later applied /
    # withdrawn / rejected / hold_released (``kit.session_hold``). ``None`` =
    # nobody else is on it. Asks ``held_by_other_session``; a yes never clears it.
    other_session_hold: Optional[tuple[str, str, str]] = None
    # The newest CV's save-time checks (cv_checks.py) found numbers or terms that are
    # not in the profile (``unsupported_numbers`` / ``unsupported_terms``). Asks
    # ``cv_unsupported_claims``; a yes for THIS CV clears it (2026-10-17, career-ops
    # verify-cv-facts.mjs blocks invented metrics; here it is an ask, not a stop).
    cv_unsupported: bool = False
    # A CV is saved for this application (check_submit's ``fill`` names ``write_cv`` first when not).
    has_cv: bool = True
    # Bulk-apply D1: a ``submit_attempted`` is the newest of submit_attempted / applied /
    # submit_resolved (a later `blocked` does not clear it). STOP ``submit_unconfirmed``; a yes never clears it.
    submit_unconfirmed: bool = False
    # Bulk-apply D2: the job's company is on the user's own ``preferences.avoid_companies`` list
    # (exact normalized match). STOP ``company_avoided``; a yes never clears it.
    company_avoided: bool = False
    # Bulk-apply D3: another application of this user, applied inside DUPLICATE_ROLE_WINDOW_DAYS,
    # has the same company and the same title, and ``duplicate_cleared`` was not clicked. Asks
    # ``duplicate_role`` (auto mode asks too: titles can legitimately repeat).
    duplicate_role: bool = False
    # Bulk-apply R2: the form host has no earlier applied application of this user. Asks
    # ``new_site_first_time`` in auto mode; a yes clears it.
    new_site: bool = False
    # Bulk-apply F2: a multi-page form (``page`` / ``pages_total`` on form_checked / form_swept) has a page
    # 1..N without a sweep newer than its check. STOP ``form_pages_incomplete``; a yes never clears it.
    form_pages_incomplete: bool = False
    # Owner rule 2026-10-19: the newest ``form_filled`` holds an answer marked ``guessed`` that the
    # user has not verified (``ready.has_unverified_guess``, the card's ``guessed`` flag). Asks
    # ``answer_guessed`` in EVERY mode; a yes does NOT clear it - the user verifies or edits the answer.
    guessed_unverified: bool = False
    # Owner rule 2026-10-22 ("I draft, you approve"): the newest ``form_filled`` holds a ``written`` answer (an
    # essay) the user has not approved (``ready.has_unapproved_written``, the card's ``unapproved`` flag). Asks
    # ``answer_unapproved`` in EVERY mode; a yes does NOT clear it - the user approves the draft on Needs you.
    written_unapproved: bool = False
    # Owner rule 2026-10-22: the newest ``form_filled`` holds an answer to a private, legal or risky question
    # (any country) the user has not confirmed (``ready.has_unconfirmed_sensitive``, the card's ``sensitive``
    # flag). Asks ``sensitive_unconfirmed`` in EVERY mode; a yes does NOT clear it - the user confirms the answer.
    sensitive_unconfirmed: bool = False
    # Gap roadmap slice 8: the newest yes for this CV no longer holds (``kit.approval_stale``): the
    # part named here changed since the user said yes - ``expired`` (older than
    # SUBMIT_APPROVAL_TTL_MINUTES), ``cover_letter``, ``form_fill``, ``form_page`` or ``profile``.
    # ``approved`` is then False; the ask the gate would give is answered as ``approval_stale``.
    approval_stale: Optional[str] = None
    # Gap roadmap slice 30: the application's id, matched against the grant's scope
    # (``SubmitCounts.grant_scope``). ``None`` is never in a scope.
    application_id: Optional[int] = None
    # Gap roadmap slice 31 (G078): the ``to`` of this application's NEWEST ``email_drafted`` (``None`` = no
    # draft). An email application (check_submit ``form_url`` = ``mailto:``) stops ``email_not_drafted``
    # without one, or when the mailto address is not this recipient; a yes never clears it.
    email_to: Optional[str] = None


@dataclass(frozen=True)
class SubmitDecision:
    """``submit`` — go ahead; ``ask`` — fill the form, stop before submit, ask
    the user yes for this one; ``stop`` — do not submit. ``reason`` is a closed
    code, ``detail`` one plain sentence for the user."""

    decision: Literal["submit", "ask", "stop"]
    reason: str
    detail: str


REASONS: tuple[str, ...] = (
    "paused", "already_applied", "user_declined", "daily_cap_reached", "company_cap_reached",
    "weekly_cap_reached", "pace_wait", "outside_hours", "duplicate_job", "unknown_site",
    "ask_always_site", "job_override_confirm", "submit_mode_confirm", "cv_not_seen", "practice_run",
    "user_approved", "auto_when_sure", "blocked", "score_line_unknown", "line_ask_above", "line_ask_below",
    "form_not_recorded", "form_not_swept", "form_host_mismatch", "form_page_mismatch", "email_not_verified",
    "held_by_other_session",
    "submit_unconfirmed", "company_avoided", "duplicate_role", "new_site_first_time", "form_pages_incomplete",
    "type_first_pending", "below_score_line", "answer_guessed", "answer_unapproved", "sensitive_unconfirmed",
    "approval_stale",
    "required_unanswered", "grant_scope", "email_not_drafted",
)

# Owner 2026-10-24: check_submit tells the assistant plainly what to do next. A stop whose fix is the
# assistant's OWN next step is answered ``fill`` with ``next_action`` (never a reason to end the run);
# only these reasons end the whole run (``stop_run``). Every other stop skips just this application.
FILL_NEXT_ACTIONS: dict[str, str] = {
    "form_not_recorded": "open_and_fill_form",
    "form_not_swept": "sweep_form",
    "unreadable_fields": "sweep_form",
    "form_count_mismatch": "sweep_form",
    "form_check_missing": "check_form",
    "form_pages_incomplete": "sweep_pages",
    "email_not_drafted": "draft_email",
    "type_first_pending": "type_answers",
}
NEXT_ACTIONS: tuple[str, ...] = (
    "write_cv", "open_and_fill_form", "sweep_form", "check_form", "sweep_pages", "draft_email", "type_answers",
)
RUN_STOP_REASONS: frozenset[str] = frozenset({"paused", "daily_cap_reached", "weekly_cap_reached", "outside_hours"})


def present_verdict(verdict: SubmitDecision, *, has_cv: bool) -> dict[str, Any]:
    """What check_submit returns for ``verdict``: ``fill`` + ``next_action`` for a stop the assistant
    fixes itself (``write_cv`` when the form is not filled and no CV is saved yet), and ``stop_run``
    true only for a stop that ends the whole run. A client that knows only submit/ask/stop/pace_wait
    never reads ``fill`` as ``submit``."""
    action = FILL_NEXT_ACTIONS.get(verdict.reason) if verdict.decision == "stop" else None
    if action == "open_and_fill_form" and not has_cv:
        action = "write_cv"
    return {
        "decision": "fill" if action else verdict.decision,
        "reason": verdict.reason,
        "detail": verdict.detail,
        "next_action": action,
        "stop_run": verdict.decision == "stop" and verdict.reason in RUN_STOP_REASONS,
    }


# What each ``approval_stale`` part means, in the words the ask shows the user.
APPROVAL_STALE_WORDS: dict[str, str] = {
    "expired": "the yes is older than {ttl} minutes",
    "cover_letter": "the cover letter changed",
    "form_fill": "the form was filled again with different answers",
    "form_page": "the form is on another page",
    "profile": "the assistant changed the profile",
    "email": "the email draft has another recipient or attachment than the one the user said yes to",
}


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


def clean_form_url(raw: Optional[str]) -> Optional[str]:
    """The form PAGE address as stored on ``form_filled`` / ``form_swept`` and compared by
    check_submit (gap roadmap slice 7): ``None`` unless :func:`screen_form_url` reads it, else
    ``job_checks.canonical_url`` with only the job-id query keys kept (``_JOB_ID_KEYS``) -
    https + host (``www.`` folded) + path; no fragment, token, ``code=`` or session id. A bare
    host is read as https."""
    if screen_form_url(raw) is None:
        return None
    text = (raw or "").strip()
    cleaned = canonical_url(text if _SCHEME_RE.match(text) else f"https://{text}", only_keys=_JOB_ID_KEYS)
    return cleaned or None


def _same_form_page(a: str, b: str) -> bool:
    """Two :func:`clean_form_url` addresses are the same form when the host matches and one path
    is the other or extends it by whole segments: a multi-step form (``/apply`` then
    ``/apply/review``) stays one page, while two jobs on one ATS (``/acme/111/apply`` vs
    ``/acme/222/apply``) do not."""
    pa, pb = urlsplit(a), urlsplit(b)
    if pa.netloc != pb.netloc:
        return False
    x, y = pa.path.rstrip("/"), pb.path.rstrip("/")
    short, long_ = (x, y) if len(x) <= len(y) else (y, x)
    return long_ == short or long_.startswith(short + "/")


# Gap roadmap slice 31 (G078): ONE email address, the strict reading the email gate and the receipt use.
EMAIL_ADDRESS_MAX = 254
_EMAIL_LOCAL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
MAILTO_PREFIX = "mailto:"


def clean_email_address(raw: Optional[str]) -> Optional[str]:
    """``local@domain`` lower-cased, or ``None`` unless ``raw`` is plainly ONE address: ASCII, one ``@``,
    a dot-atom local part of at most 64 characters, and a domain :func:`screen_form_url` reads as one
    host (no IDNA, IP literal or single label). A display name (``Ada <a@b.com>``), a list (``,`` / ``;``)
    or a quoted local part is refused, never guessed at."""
    text = (raw or "").strip()
    if not text or len(text) > EMAIL_ADDRESS_MAX or not text.isascii() or text.count("@") != 1:
        return None
    local, domain = text.split("@")
    if not local or len(local) > 64 or not _EMAIL_LOCAL_RE.match(local):
        return None
    if not domain or any(ch in domain for ch in "/?#:") or screen_form_url(domain) != domain.lower().rstrip("."):
        return None
    return f"{local}@{domain.lower().rstrip('.')}".lower()


def email_domain(address: str) -> str:
    """The domain of a :func:`clean_email_address` (what logs may carry; the full address never)."""
    return address.rsplit("@", 1)[-1] if "@" in address else ""


def is_mailto(raw: Optional[str]) -> bool:
    """check_submit's ``form_url`` names an EMAIL application (``mailto:`` + the recipient)."""
    return (raw or "").strip().lower().startswith(MAILTO_PREFIX)


def mailto_address(raw: Optional[str]) -> Optional[str]:
    """The address of a ``mailto:`` form_url (query part dropped), ``""`` for a bare ``mailto:``,
    ``None`` when the address cannot be read as one address (:func:`clean_email_address`)."""
    target = (raw or "").strip()[len(MAILTO_PREFIX):].split("?", 1)[0]
    if not target.strip():
        return ""
    return clean_email_address(target)


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
    4a. ``company_cap`` applications to this company inside ``company_cap_days`` already -> stop
        ``company_cap_reached`` (a stored yes does NOT clear it; the user raises the limit)
    4a1. ``weekly_cap`` submits already in the user's Monday-Sunday week -> stop ``weekly_cap_reached``
    4a2. the company is on the user's own ``avoid_companies`` list -> stop ``company_avoided``
        (exact normalized match; a yes does NOT clear it; the user edits their list)
    4a3. outside the user's ``apply_hours`` -> stop ``outside_hours``; less than ``min_gap_minutes`` since the
        newest applied -> stop ``pace_wait`` with the exact next allowed time (fixed values, a yes never clears them)
    4a4. ``below_line`` is ``skip`` and the fit is below the user's score line -> stop
        ``below_score_line`` (owner 2026-10-19: drop the job; a yes does NOT clear it)
    4b-email. ``form_url`` is ``mailto:<to>`` (an EMAIL application, gap roadmap slice 31): no
        ``email_drafted`` recorded, or the mailto address is empty or not the newest draft's ``to`` -> stop
        ``email_not_drafted`` (a yes does NOT clear it). An email application (no ``form_filled`` recorded)
        skips 4b-4c4, 4e, 5d, 5e, 5e2 and the site rules 6, 7 and 10b (there is no form and no site).
    4b. no ``form_filled`` event recorded -> stop ``form_not_recorded`` (a stored
        yes does NOT clear it: the record of what was typed is needed either way)
    4c. no ``form_swept`` newer than the latest ``form_filled`` -> stop
        ``form_not_swept`` (the pre-submit re-read; a yes does not clear it)
    4c2. the newest ``form_swept`` lists ``unreadable`` fields -> stop ``unreadable_fields``; it read
        fewer fields than the newest ``form_checked`` listed questions -> stop ``form_count_mismatch``
    4c3. an application created after ``kit.FORM_CHECK_REQUIRED_FROM`` has no ``form_checked`` -> stop
        ``form_check_missing`` (older in-flight applications are exempt)
    4c3b. the newest ``form_checked`` of a page lists a REQUIRED question with no answer -> stop
        ``required_unanswered`` (owner rule 2026-10-20; a yes does NOT clear it; only a new ``form_checked``
        after the user answered does)
    4c4. a multi-page form has a page 1..N with no ``form_swept`` newer than its ``form_checked`` -> stop
        ``form_pages_incomplete`` (older single-page applications are unchanged; a yes does not clear it)
    4d2. a ``submit_attempted`` with no later surviving ``applied`` or web ``submit_resolved`` -> stop
        ``submit_unconfirmed`` (a submit was started and never confirmed; a yes does NOT clear it;
        the user records the outcome)
    4d. the account's email is not confirmed -> ask ``email_not_verified`` (a
        yes does NOT clear it; after the stops so a stop is never softened to an ask)
    5. same job already applied to -> ``duplicate_job``: STOP when the mode is
       auto (nobody is watching), else ask
    4e. (after the auto duplicate stop) the newest ``form_filled`` has a ``guessed`` answer the user
        has not verified, or one the user EDITED that the form still holds -> ask ``answer_guessed`` in
        every mode (owner 2026-10-19; a yes does NOT clear it - the user verifies or edits the answer)
    5-role. same company + same title applied to inside ``DUPLICATE_ROLE_WINDOW_DAYS`` -> ask
        ``duplicate_role`` (inside ``_first_ask`` after the duplicate_job ask; ``duplicate_cleared`` or a yes clears it)
    5a. another assistant session read this application's kit inside
        ``KIT_HOLD_MINUTES`` and has not released it -> ask
        ``held_by_other_session`` (a yes does NOT clear it; after every stop)
    5b. a ``blocked`` with no later ``unblocked`` -> ask ``blocked`` (S6)
    5d. ``form_url`` fails :func:`screen_form_url` (user-info ``@``, ``%`` or a
        backslash in the authority, non-ASCII / IDNA host, not http(s)) -> ask
        ``unknown_site`` (a yes does NOT clear it)
    5e. ``form_url``'s host is not the host of the newest ``form_filled`` -> ask
        ``form_host_mismatch`` (a yes does NOT clear it)
    5e2. both ``form_url`` and the stored page (``form_url`` fact) are known and are not the same
        form (host differs, or neither path extends the other) -> ask ``form_page_mismatch`` (a yes
        does NOT clear it: the yes was for the page that was filled; either side missing = no
        stop, so older assistants keep working)
    5b2. the newest CV has save-time checks for numbers or terms not in the profile -> ask
        ``cv_unsupported_claims`` (inside ``_first_ask``, right after the duplicate ask; a yes clears it)
    5c. the fit score vs the user's score line (``line_side``): no line or no fit
        yet -> ask ``score_line_unknown``; the switch for that side
        (``above_line`` / ``below_line``) is ``ask`` -> ask ``line_ask_above`` /
        ``line_ask_below``. Checked inside ``_first_ask`` right after the
        duplicate ask, so a stored yes still clears it.
    6. site unknown -> ask ``unknown_site``
    7. Indeed / LinkedIn style site -> ask ``ask_always_site``
    8. the mode (the job's override, else ``submit_mode`` - or auto while a counted ``auto_grant`` is active:
       now before ``auto_grant_until`` and fewer than ``auto_grant_count`` applied since it began) is ``confirm`` -> ask
       (``grant_scope`` when a grant is active but does not cover this application: it was not in the user's
       queue when they confirmed the grant, or its form host is not the one snapshotted then - slice 30)
    9. auto, but the user has not seen the latest CV -> ask ``cv_not_seen``
    10. first application since auto-submit was turned on -> ask ``practice_run``
    10b. auto mode and the form host has no earlier applied application -> ask ``new_site_first_time``
    11. otherwise -> submit ``auto_when_sure``

    A stored yes for THIS CV (``approved``) clears every ASK from 5 to 10 (5c too)(even
    Indeed / LinkedIn and the practice run) and answers ``submit`` /
    ``user_approved``. A yes that no longer holds (``approval_stale``: expired, or the letter, fill,
    page, profile or email draft changed since) clears nothing; the ask it would have cleared answers
    ``approval_stale`` naming the part.
    Rules 1-4c (4a-4a3, 4b-email, 4c4 and 4d2 included), the auto-mode duplicate stop, the other
    session's hold (5a), the ``blocked`` ask (5b), the form-address checks (5d, 5e, 5e2), ``email_not_verified`` (4d),
    ``below_score_line`` (4a4), ``answer_guessed``, ``answer_unapproved`` and
    ``sensitive_unconfirmed`` (4e) are NEVER cleared by a yes.
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
    if cfg.company_cap is not None and counts.company_applied_in_window >= cfg.company_cap:
        return SubmitDecision(
            "stop", "company_cap_reached",
            f"You set a limit of {cfg.company_cap} applications per company "
            f"{'in all time' if cfg.company_cap_days == 0 else f'in {cfg.company_cap_days} days'}, "
            "and it is reached for this company. Do not submit; the user can raise the limit.",
        )
    if cfg.weekly_cap is not None and counts.submitted_this_week >= cfg.weekly_cap:
        return SubmitDecision(
            "stop", "weekly_cap_reached",
            f"You set a limit of {cfg.weekly_cap} applications per week (Monday to Sunday) and it is reached. "
            "Do not submit; the user can raise the limit.",
        )
    if application.company_avoided:
        return SubmitDecision(
            "stop", "company_avoided",
            "This company is on your own never-apply list. Do not submit; the user removes it from the list "
            "to allow it.",
        )
    pace = _pace_stop(cfg, counts, now)
    if pace is not None:
        return pace
    if cfg.below_line == LINE_SKIP and line_side(application.fit_score, cfg.apply_min_score) == "below":
        return SubmitDecision(
            "stop", "below_score_line",
            f"This job is below your score line ({cfg.apply_min_score}) and you chose to drop those. "
            "Do not apply; leave this job.",
        )
    # An email application has NO recorded form: a form application that passes ``mailto:`` keeps every form rule.
    email = is_mailto(site_host) and not application.form_filled
    if email:
        stop = _email_stop(application, site_host)
        if stop is not None:
            return stop
    else:
        form_stop = _form_stop(application)
        if form_stop is not None:
            return form_stop
    if application.submit_unconfirmed:
        return SubmitDecision(
            "stop", "submit_unconfirmed",
            "A submit was started and never confirmed. Check the employer's email or site; the user "
            "records the outcome. Do not submit again.",
        )
    if not application.email_verified:
        return SubmitDecision(
            "ask", "email_not_verified",
            "The user has not confirmed their email address yet. Do not submit; "
            "the user confirms it on Job360 first, or submits this one themselves.",
        )
    override = application.submit_override if application.submit_override in VALID_SUBMIT_MODES else None
    granted = grant_active(cfg, counts, now)
    # Slice 30: a grant makes only the applications it covers auto; any other one keeps the user's own mode.
    outside_grant = granted and not grant_covers(counts.grant_scope, application)
    base_mode = AUTO_SUBMIT_MODE if granted and not outside_grant else cfg.submit_mode
    mode = override or base_mode
    auto = mode == AUTO_SUBMIT_MODE
    if application.duplicate_job and auto:
        return SubmitDecision(
            "stop", "duplicate_job",
            "Needs you: possible duplicate - this job was already applied to. Do not submit; the user decides.",
        )
    # 4e, after every stop (the auto duplicate one included) so a stop is never softened to this ask.
    # An email has no form answers, so an email application skips 4e.
    if application.guessed_unverified and not email:
        return SubmitDecision(
            "ask", "answer_guessed",
            "An answer on this form was guessed and the user has not verified it, or the user changed it "
            "and the form still holds the guess. Do not submit; ask the user, type their answer, record a "
            "new form_filled (they can Verify or Edit it on Needs you).",
        )
    if application.written_unapproved and not email:
        return SubmitDecision(
            "ask", "answer_unapproved",
            "A written answer on this form is not one the user approved. Do not submit; show the draft with "
            "ask_user, type the approved text, record a new form_filled (the user can Verify or Edit it on "
            "Needs you).",
        )
    if application.sensitive_unconfirmed and not email:
        return SubmitDecision(
            "ask", "sensitive_unconfirmed",
            "This form asks a private, legal or risky question (identity, health, money, right to work, "
            "criminal record...) and the user has not confirmed the answer. Do not submit; raise it with "
            "ask_user, the user checks it on Needs you, then record a new form_filled.",
        )
    if application.other_session_hold is not None:
        held_by, session, since = application.other_session_hold
        who = f"{held_by}/{session}" if session else held_by
        return SubmitDecision(
            "ask", "held_by_other_session",
            f"Another assistant session ({who}) read this application's kit at {since}; "
            "make sure only one session submits.",
        )
    if application.blocked:
        return SubmitDecision(
            "ask", "blocked",
            "Your assistant got stuck on this application. Do not submit; the user resolves it first.",
        )
    screened: Optional[str] = None
    if not email:
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
        current = clean_form_url(site_host)
        stored = clean_form_url(application.form_url)
        if current and stored and not _same_form_page(current, stored):
            return SubmitDecision(
                "ask", "form_page_mismatch",
                "This is not the page where the form was filled (form_filled / form_swept). "
                "Do not submit; go back to that page, or fill and sweep this one, and ask the user.",
            )
    ask = _first_ask(
        cfg, application, screened, counts, override=override, auto=auto,
        outside_grant=outside_grant and cfg.submit_mode != AUTO_SUBMIT_MODE, email=email,
    )
    if ask is None:
        return SubmitDecision("submit", "auto_when_sure", "Auto-submit is on and this site allows it. You may submit.")
    if application.approved:
        return SubmitDecision(
            "submit", "user_approved", "The user said yes to sending this application with this CV. You may submit."
        )
    if application.approval_stale:
        why = APPROVAL_STALE_WORDS.get(application.approval_stale, application.approval_stale).format(
            ttl=settings.SUBMIT_APPROVAL_TTL_MINUTES
        )
        return SubmitDecision(
            "ask", "approval_stale",
            f"The user's yes no longer counts ({application.approval_stale}: {why}). Show what changed and "
            f"ask yes again. {ask.detail}",
        )
    return ask


def _email_stop(application: SubmitFacts, site_host: Optional[str]) -> Optional[SubmitDecision]:
    """Rule 4b-email (gap roadmap slice 31, G078): an email application needs a recorded ``email_drafted``
    whose ``to`` is the ``mailto:`` address (a bare ``mailto:`` is refused). A yes never clears it."""
    if application.email_to is None:
        return SubmitDecision(
            "stop", "email_not_drafted",
            "Record email_drafted with the exact recipient, subject and attachments before sending, "
            "then call check_submit again. Do not send yet.",
        )
    wanted = mailto_address(site_host)
    if not wanted or wanted != application.email_to:
        return SubmitDecision(
            "stop", "email_not_drafted",
            "This mailto address is not the recipient of your newest email_drafted. Record email_drafted "
            "with the exact recipient, subject and attachments before sending, then call check_submit again.",
        )
    return None


def _form_stop(application: SubmitFacts) -> Optional[SubmitDecision]:
    """Rules 4b-4c4: the form must be recorded, swept and complete before a form submit."""
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
    if application.unreadable_fields:
        return SubmitDecision(
            "stop", "unreadable_fields",
            "Some fields could not be read back after typing (form_swept unreadable). Re-read them, "
            "ask the user about any you cannot, record a new form_swept and call check_submit again.",
        )
    if application.form_count_short:
        counted, listed = application.form_count_counted, application.form_count_listed
        said = (
            f"You checked {counted} of {listed} questions; count blank optional ones too "
            "(fields_checked, plus the blanks named in form_filled fields_skipped). "
            if counted is not None and listed is not None
            else "You swept fewer fields than check_form listed questions. "
        )
        return SubmitDecision(
            "stop", "form_count_mismatch",
            said + "Re-read every field, record a new form_swept and call check_submit again.",
        )
    if application.form_check_missing:
        return SubmitDecision(
            "stop", "form_check_missing",
            "check_form was not used for this form. Call check_form with every question on the live form, "
            "tell the user about any knockouts, record_event form_checked, sweep again "
            "and call check_submit again.",
        )
    if application.required_unanswered:
        return SubmitDecision(
            "stop", "required_unanswered",
            "The form has a required question with no saved answer. Never leave a required question blank "
            "and never guess: ask_user for every missing required answer (one message), save the answer, "
            "run check_form again, record a new form_checked (with missing_required), fill, sweep, and "
            "call check_submit again. Optional questions may stay blank.",
        )
    if application.form_pages_incomplete:
        return SubmitDecision(
            "stop", "form_pages_incomplete",
            "A page of this multi-page form has no sweep newer than its check. For every page 1..N: "
            "check_form, form_checked, fill, form_swept (with page and pages_total); then check_submit again.",
        )
    return None


def grant_active(cfg: EffectiveSettings, counts: SubmitCounts, now: datetime) -> bool:
    """Bulk-apply R1: a counted auto grant is in force - both settings set, now before the end, and fewer
    than ``auto_grant_count`` applied events since it began. Pure."""
    if cfg.auto_grant_count is None or not cfg.auto_grant_until:
        return False
    return _grant_not_over(cfg.auto_grant_until, now) and counts.applied_since_grant < cfg.auto_grant_count


def grant_covers(scope: Optional[dict[int, Optional[str]]], application: SubmitFacts) -> bool:
    """Gap roadmap slice 30: the active grant covers this application - it was in the user's queue
    when they confirmed the grant AND its form host now is the host snapshotted then. A snapshot with
    no host never matches (a host that appears later is another site the user never saw). ``scope``
    ``None`` = a grant from before snapshots: covers everything until it ends. Pure."""
    if scope is None:
        return True
    app_id = application.application_id
    if app_id is None or app_id not in scope:
        return False
    host = scope[app_id]
    return host is not None and host == application.form_host


def _grant_scope_ask(scope: Optional[dict[int, Optional[str]]], application: SubmitFacts) -> SubmitDecision:
    """The ``grant_scope`` ask: the user's grant does not cover this application."""
    n = len(scope or {})
    what = (
        "its form is now on another site than when they said yes"
        if application.application_id in (scope or {})
        else "this one was not among them"
    )
    return SubmitDecision(
        "ask", "grant_scope",
        f"Auto was turned on for the {n} application{'' if n == 1 else 's'} in the user's queue when they said "
        f"yes; {what}. Fill the form, stop before submit and ask yes.",
    )


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _in_hours(local: datetime, hours: tuple[str, str]) -> bool:
    minute = local.hour * 60 + local.minute
    start, end = _hhmm_minutes(hours[0]), _hhmm_minutes(hours[1])
    return start <= minute < end if start < end else (minute >= start or minute < end)


def _next_opening(local: datetime, hours: tuple[str, str]) -> datetime:
    """The first moment at or after ``local`` that falls inside the window (``local`` itself when inside)."""
    if _in_hours(local, hours):
        return local
    start = _hhmm_minutes(hours[0])
    opening = local.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
    return opening if opening > local else opening + timedelta(days=1)


def _pace_stop(cfg: EffectiveSettings, counts: SubmitCounts, now: datetime) -> Optional[SubmitDecision]:
    """Rule 4a3 (bulk-apply C1): the user's own fixed pace. ``outside_hours`` first, then ``pace_wait`` with the
    exact next allowed time (in the user's timezone; moved to the next opening when it falls outside the hours).
    Nothing is randomized and a yes clears neither."""
    zone = _zone(counts.user_tz)
    local = now.astimezone(zone)
    hours = cfg.apply_hours
    if hours is not None and not _in_hours(local, hours):
        opening = _next_opening(local, hours)
        return SubmitDecision(
            "stop", "outside_hours",
            f"You set apply hours of {hours[0]} to {hours[1]} (your time). Do not submit now; "
            f"the next allowed time is {opening.isoformat(timespec='minutes')}.",
        )
    if cfg.min_gap_minutes and counts.last_applied_at is not None:
        last = counts.last_applied_at if counts.last_applied_at.tzinfo else counts.last_applied_at.replace(
            tzinfo=timezone.utc
        )
        nxt = (last + timedelta(minutes=cfg.min_gap_minutes)).astimezone(zone)
        if nxt > local:
            if hours is not None:
                nxt = _next_opening(nxt, hours)
            return SubmitDecision(
                "stop", "pace_wait",
                f"You set a gap of {gap_words(cfg.min_gap_minutes)} between applications. Do not submit before "
                f"{nxt.isoformat(timespec='minutes' if float(cfg.min_gap_minutes).is_integer() else 'seconds')} "
                "(your time); work another application or wait.",
            )
    return None


def _first_ask(
    cfg: EffectiveSettings,
    application: SubmitFacts,
    site_host: Optional[str],
    counts: SubmitCounts,
    *,
    override: Optional[str],
    auto: bool,
    outside_grant: bool = False,
    email: bool = False,
) -> Optional[SubmitDecision]:
    """The first reason to ASK, in rule order, or ``None`` when nothing asks. ``outside_grant``: an
    active grant does not cover this application, so the user's own confirm asks as ``grant_scope``. An email
    application (``email``) has no form site: the site rules (6, 7, 10b) are skipped."""
    if application.duplicate_job:
        return SubmitDecision(
            "ask", "duplicate_job",
            "Possible duplicate: this job was already applied to. Fill the form, stop before submit and ask yes.",
        )
    if application.duplicate_role:
        return SubmitDecision(
            "ask", "duplicate_role",
            "Possible repost: an application to the same company with the same title was already sent. "
            "Fill the form, stop before submit and ask yes.",
        )
    if application.cv_unsupported:
        return SubmitDecision(
            "ask", "cv_unsupported_claims",
            "The CV has numbers or terms that are not in the profile (see the CV's checks in the kit). "
            "Show them to the user: fix the CV, or get their yes that each one is true, then ask yes.",
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
    kind = "company" if email else host_kind(parse_site_host(site_host))
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
        if outside_grant:
            return _grant_scope_ask(counts.grant_scope, application)
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
    if application.new_site and not email:
        return SubmitDecision(
            "ask", "new_site_first_time",
            "First application on this site: the user takes one look. "
            "Fill the form, stop before submit and ask yes.",
        )
    return None


# ── THE outreach send gate (feature 10) ──────────────────────────────────────


@dataclass(frozen=True)
class OutreachCounts:
    """What the outreach gate needs counted: recorded ``sent`` marks today (the
    user's own day) and ``sent`` marks to the same company inside the window."""

    sent_today: int
    company_sent_in_window: int = 0


@dataclass(frozen=True)
class OutreachFacts:
    """One contact + one saved message version, as the gate reads them
    (``contacts.outreach_gate_facts`` loads them; every field is a closed fact,
    never text):

    * ``message_found`` - ``message_id`` is a ``message`` row of THIS contact;
    * ``latest_draft`` - no newer ``message`` version exists for the contact;
    * ``channel`` - that version's channel (``email`` | ``linkedin`` | ``other``);
    * ``has_email`` - the contact's CURRENT email (edits applied) is set;
    * ``already_sent`` / ``replied`` - any ``sent`` / ``reply`` mark on the contact;
    * ``linked`` - the contact is tied to an application; ``application_closed`` -
      that application's status is closed (rejected / withdrawn / ghosted);
    * ``found_via`` - where the person was found (``None`` = not said);
    * ``company`` - the linked job's company ("" when unlinked);
    * ``email_source`` - where the CURRENT email came from (``None`` = not said);
    * ``email_bounced`` - a ``bounced`` mark exists for this user on the contact's
      CURRENT address (on any of the user's contacts).

    LinkedIn facts (all closed; defaults keep email fixtures building): ``has_linkedin_url`` - the contact's
    CURRENT profile link is set; ``linkedin_state`` - connected | pending | not_connected | unknown (``None``
    = not said); ``text_chars`` - length of the saved version; ``premium`` - the user said they have LinkedIn
    Premium; ``owner_approved`` - the user pressed Send on this person's card for THIS version
    (``own_browser.derive``); ``claimed_by_other`` - a live claim is held by a session other than the
    caller's; ``invite_limit_until`` - LinkedIn's weekly invitation limit was reported and has not ended;
    ``claimed_by_caller`` - the caller holds a live claim on this person (``own_browser_queue`` take, in
    either browser); ``last_linkedin_sent_at`` / ``linkedin_sent_today`` - the pace ``own_browser.take`` enforces (other
    people's sends and unreleased claims).
    """

    message_found: bool
    latest_draft: bool = False
    channel: str = ""
    has_email: bool = False
    already_sent: bool = False
    replied: bool = False
    linked: bool = False
    application_closed: bool = False
    found_via: Optional[str] = None
    company: str = ""
    email_source: Optional[str] = None
    email_bounced: bool = False
    has_linkedin_url: bool = False
    linkedin_state: Optional[str] = None
    text_chars: int = 0
    premium: bool = False
    owner_approved: bool = False
    claimed_by_other: bool = False
    claimed_by_caller: bool = False
    invite_limit_until: Optional[datetime] = None
    last_linkedin_sent_at: Optional[datetime] = None
    linkedin_sent_today: int = 0


@dataclass(frozen=True)
class OutreachDecision:
    """``send`` - press Send in Gmail; ``ask`` - save a Gmail draft and ask_user;
    ``stop`` - do not send. ``reason`` is a closed code, ``detail`` one sentence."""

    decision: Literal["send", "ask", "stop"]
    reason: str
    detail: str


# LinkedIn's own limits (a connection note: ``settings.LINKEDIN_NOTE_MAX_CHARS``, 300 with Premium). The pace
# constants live in ``services/applications/own_browser`` (``take`` enforces the same ones).
LINKEDIN_MESSAGE_MAX_CHARS = 400

# In the order :func:`may_send_outreach` checks them (first match wins) for an email (or other-channel) version.
OUTREACH_REASONS: tuple[str, ...] = (
    "paused", "message_not_found", "not_latest_draft", "not_email_channel", "no_email", "email_bounced",
    "already_sent", "replied", "application_closed", "daily_outreach_cap_reached", "company_cap_reached",
    "email_guessed", "mode_not_auto", "no_found_via", "unlinked_contact", "auto_mode",
)
# The same for a LinkedIn version (:func:`_may_send_linkedin`). All stop except the last two: ask, then send.
LINKEDIN_OUTREACH_REASONS: tuple[str, ...] = (
    "paused", "message_not_found", "not_latest_draft", "no_linkedin_url", "already_sent", "replied",
    "application_closed", "linkedin_state_unknown", "linkedin_pending", "note_too_long", "message_too_long",
    "invite_limit_reached", "linkedin_daily_cap_reached", "too_soon", "claimed_by_other_session",
    "needs_owner_click", "needs_claim", "owner_clicked",
)
# Said on every LinkedIn answer: its counts are the email caps, not LinkedIn's.
_LINKEDIN_COUNTS_NOTE = " (sent_today and daily_max count email only.)"


def _may_send_linkedin(facts: OutreachFacts, now: datetime) -> OutreachDecision:
    """The LinkedIn half of :func:`may_send_outreach`. ``outreach_send`` never applies here: the only yes is
    the user's click on that person's card for this exact version (``facts.owner_approved``) AND a live claim
    the caller holds on that person (``facts.claimed_by_caller``), in either browser."""
    got = _linkedin_verdict(facts, now)
    return OutreachDecision(got.decision, got.reason, got.detail + _LINKEDIN_COUNTS_NOTE)


def _linkedin_verdict(facts: OutreachFacts, now: datetime) -> OutreachDecision:
    from src.services.applications import own_browser  # noqa: PLC0415 - own_browser imports this package lazily

    def stop(reason: str, detail: str) -> OutreachDecision:
        return OutreachDecision("stop", reason, detail)

    if not facts.has_linkedin_url:
        return stop("no_linkedin_url", "This person has no LinkedIn link saved. Do not send.")
    if facts.already_sent:
        return stop("already_sent", "A message to this person is already recorded as sent.")
    if facts.replied:
        return stop("replied", "This person already replied. Do not send; tell the user instead.")
    if facts.application_closed:
        return stop("application_closed", "This application is closed. Do not send.")
    state = facts.linkedin_state or "unknown"
    if state not in ("connected", "not_connected", "pending"):
        return stop(
            "linkedin_state_unknown",
            "Their LinkedIn state is not saved. Read Connect / Pending / Message on their page and save it with "
            "update_contact(linkedin_state=...); do not send yet.",
        )
    if state == "pending":
        return stop("linkedin_pending", "A connection request to this person is already pending. Do not send.")
    note_max = settings.LINKEDIN_NOTE_MAX_CHARS_PREMIUM if facts.premium else settings.LINKEDIN_NOTE_MAX_CHARS
    if state == "not_connected" and facts.text_chars > note_max:
        return stop(
            "note_too_long",
            f"A connection note is at most {note_max} characters; this draft is {facts.text_chars}. Shorten it "
            "with save_artifact; the user must press Send again for the new text.",
        )
    if state == "connected" and facts.text_chars > LINKEDIN_MESSAGE_MAX_CHARS:
        return stop(
            "message_too_long",
            f"A LinkedIn message is at most {LINKEDIN_MESSAGE_MAX_CHARS} characters; this draft is "
            f"{facts.text_chars}. Shorten it with save_artifact; the user must press Send again for the new text.",
        )
    if state == "not_connected" and facts.invite_limit_until is not None and facts.invite_limit_until > now:
        return stop(
            "invite_limit_reached",
            "LinkedIn's weekly invitation limit was reached. Send no connection note until it resets.",
        )
    if facts.linkedin_sent_today >= own_browser.LINKEDIN_DAILY_MAX:
        return stop(
            "linkedin_daily_cap_reached",
            f"Today's limit of {own_browser.LINKEDIN_DAILY_MAX} LinkedIn sends is reached. Try tomorrow.",
        )
    if facts.last_linkedin_sent_at is not None:
        left = own_browser.LINKEDIN_MIN_GAP_SECONDS - (now - facts.last_linkedin_sent_at).total_seconds()
        if left > 0:
            return stop(
                "too_soon",
                f"The last LinkedIn send was less than {own_browser.LINKEDIN_MIN_GAP_SECONDS} seconds ago. Wait "
                f"{int(left) + 1} seconds, then check again.",
            )
    if facts.claimed_by_other:
        return stop(
            "claimed_by_other_session",
            "Another session holds this person right now. Do not send; leave it to that session.",
        )
    if not facts.owner_approved:
        return OutreachDecision(
            "ask", "needs_owner_click",
            "The user has not pressed Send on this person's card for this draft. Do not send; it waits on Needs you.",
        )
    if not facts.claimed_by_caller:
        return stop(
            "needs_claim",
            "Take this person first: own_browser_queue take, then check_outreach with your session.",
        )
    return OutreachDecision(
        "send", "owner_clicked",
        "The user pressed Send for this draft: paste it on LinkedIn exactly, send, then record outreach_sent.",
    )


def may_send_outreach(
    cfg: EffectiveSettings,
    facts: OutreachFacts,
    counts: OutreachCounts,
    now: datetime,
) -> OutreachDecision:
    """May the assistant press Send on this outreach email? Pure; first match wins.

    STOP (do not send; nothing clears these): paused, message_not_found,
    not_latest_draft, not_email_channel (channel other: the user sends),
    no_email, email_bounced (a bounce is recorded for this address), already_sent, replied, application_closed,
    daily_outreach_cap_reached (``OUTREACH_AUTO_SENDS_MAX_PER_DAY``),
    company_cap_reached (``OUTREACH_COMPANY_MAX`` within
    ``OUTREACH_COMPANY_WINDOW_DAYS``).
    ASK (save a Gmail draft, ask_user): email_guessed (email_source pattern_guess), mode_not_auto
    (``assistant_settings.outreach_send`` is not ``auto``; the Gmail mode never counts), no_found_via, unlinked_contact.
    SEND: auto_mode.
    LinkedIn (:func:`_may_send_linkedin`, after not_latest_draft): stop no_linkedin_url, already_sent, replied,
    application_closed, linkedin_state_unknown, linkedin_pending, note_too_long, message_too_long,
    invite_limit_reached, linkedin_daily_cap_reached, too_soon, claimed_by_other_session, needs_claim (the click
    is there but the caller holds no live claim); ask needs_owner_click; SEND owner_clicked.
    ``outreach_send`` never reaches it.
    """
    if is_paused(cfg.paused_until, now):
        return OutreachDecision("stop", "paused", "Your assistant is paused. Do not send anything.")
    if not facts.message_found:
        return OutreachDecision(
            "stop", "message_not_found",
            "No saved message with that id for this person. Save it with save_artifact first; do not send.",
        )
    if not facts.latest_draft:
        return OutreachDecision(
            "stop", "not_latest_draft", "A newer version of this message exists. Check that one instead; do not send.",
        )
    if facts.channel == "linkedin":
        return _may_send_linkedin(facts, now)
    if facts.channel != "email":
        return OutreachDecision(
            "stop", "not_email_channel",
            "Only email, or LinkedIn after the user's click on that person's card, can be sent by you. "
            "The user sends other-channel messages.",
        )
    if not facts.has_email:
        return OutreachDecision("stop", "no_email", "This person has no email address saved. Do not send.")
    if facts.email_bounced:
        return OutreachDecision(
            "stop", "email_bounced", "An email to this address bounced before. Do not send; tell the user.",
        )
    if facts.already_sent:
        return OutreachDecision("stop", "already_sent", "A message to this person is already recorded as sent.")
    if facts.replied:
        return OutreachDecision(
            "stop", "replied", "This person already replied. Do not send; tell the user instead.",
        )
    if facts.application_closed:
        return OutreachDecision("stop", "application_closed", "This application is closed. Do not send.")
    if counts.sent_today >= settings.OUTREACH_AUTO_SENDS_MAX_PER_DAY:
        return OutreachDecision(
            "stop", "daily_outreach_cap_reached", "Today's limit of outreach emails is reached. Try tomorrow.",
        )
    if facts.company and counts.company_sent_in_window >= settings.OUTREACH_COMPANY_MAX:
        return OutreachDecision(
            "stop", "company_cap_reached",
            f"Enough people at this company were emailed in the last {settings.OUTREACH_COMPANY_WINDOW_DAYS} days. "
            "Do not send.",
        )
    if facts.email_source == "pattern_guess":
        return OutreachDecision(
            "ask", "email_guessed",
            "This address was guessed from a name pattern. Save a Gmail draft and ask_user; the user sends.",
        )
    if cfg.outreach_send != OUTREACH_SEND_AUTO:
        return OutreachDecision(
            "ask", "mode_not_auto",
            "Sending outreach is off (outreach_send is draft_only): save a Gmail draft and ask_user.",
        )
    if not facts.found_via:
        return OutreachDecision(
            "ask", "no_found_via", "Where this person was found is not saved. Save a Gmail draft and ask_user.",
        )
    if not facts.linked:
        return OutreachDecision(
            "ask", "unlinked_contact", "This person is not linked to a job. Save a Gmail draft and ask_user.",
        )
    return OutreachDecision(
        "send", "auto_mode",
        "outreach_send is auto: you may press Send in Gmail, then record outreach_sent "
        "with the Gmail message id as source.",
    )


# ── Loaders (the only I/O here) ──────────────────────────────────────────────


def _applied_after(conn: Any, user_id: str, since: Optional[str]) -> int:
    """This user's real ``applied`` events recorded after ``since`` (``None`` = ever): not a simulation
    marked BEFORE it (a mark recorded after a real submit never gives a grant slot back: review
    2026-10-18) and not a corrected one. The ONE count the practice run and the auto grant share."""
    cnt = conn.execute(
        "SELECT COUNT(*) FROM application_events e "
        "WHERE e.user_id = ? AND e.event_type = 'applied' AND e.recorded_at > ? "
        "AND NOT EXISTS (SELECT 1 FROM application_events s WHERE s.application_id = e.application_id "
        "AND s.user_id = e.user_id AND s.event_type = ? AND s.id < e.id AND s.id NOT IN "
        "(SELECT corrects_event_id FROM application_events WHERE user_id = ? AND corrects_event_id IS NOT NULL))",
        (user_id, since or "", settings.SIMULATION_EVENT_TYPE, user_id),
    ).fetchone()
    return int(cnt[0]) if cnt else 0


def _grant_began(conn: Any, user_id: str) -> tuple[Optional[str], Optional[int]]:
    """``(set_at, row id)`` of the ``auto_grant_until`` row the current counted grant began at, or
    ``(None, None)`` with no history. A new grant begins when the end is set while none was in force
    (cleared, or already over), or when it is moved LATER (see :func:`grant_info`)."""
    rows = conn.execute(
        "SELECT id, value, set_at FROM profile_edits WHERE user_id = ? AND path = ? ORDER BY id",
        (user_id, AUTO_GRANT_UNTIL_PATH),
    ).fetchall()
    began: Optional[str] = None
    began_id: Optional[int] = None
    current = ""
    for row_id, raw, set_at in rows:
        try:
            value = json.loads(raw) if raw is not None else None
        except ValueError:
            value = None
        text = value if isinstance(value, str) else ""
        if not text:
            current = ""
            continue
        try:
            set_time = parse_iso_with_offset(str(set_at))
        except ValueError:
            set_time = datetime.min.replace(tzinfo=timezone.utc)
        over = not _grant_not_over(current, set_time)
        if over or _grant_until_rank(text) > _grant_until_rank(current):
            began, began_id = str(set_at), int(row_id)
        current = text
    return began, began_id


def grant_info(user_id: str) -> tuple[Optional[str], int]:
    """``(began_at, applied_since)`` of the counted auto grant (bulk-apply R1) - DERIVED from the
    ``auto_grant_until`` history, never stored. A new grant begins when the end is set while none was in force
    (cleared, or already over), or when it is moved LATER. A shorter end or any change of the count does not
    restart the count, so a "safer" edit can never free more auto applications. ``(None, n)`` = no history:
    every real applied counts (the safe direction). Read from the overlay rows, oldest first."""
    began, used, _scope = grant_state(user_id)
    return began, used


def grant_state(user_id: str) -> tuple[Optional[str], int, Optional[dict[int, Optional[str]]]]:
    """:func:`grant_info` plus the grant's scope (slice 30, ``grant_scope.scope_for``), on ONE connection."""
    from src.services.profile import grant_scope  # noqa: PLC0415 - grant_scope imports this module

    with pgsync.connect(str(settings.DB_PATH)) as conn:
        began, began_id = _grant_began(conn, user_id)
        # Every real `applied` recorded since the grant began, with NO simulation or correction
        # exclusion (security review 2026-10-18): relabelling a submit must never give a slot back.
        cnt = conn.execute(
            "SELECT COUNT(*) FROM application_events WHERE user_id = ? AND event_type = 'applied' AND recorded_at > ?",
            (user_id, began or ""),
        ).fetchone()
        scope = grant_scope.scope_for(conn, user_id, began, began_id)
        return began, int(cnt[0]) if cnt else 0, scope


def people_hunt_last_at(user_id: str) -> Optional[str]:
    """When the user's scheduled task last marked the people hunt (``reach``) done; ``None`` = never."""
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        row = conn.execute(
            "SELECT done_at FROM assistant_duty_runs WHERE user_id = ? AND duty = 'reach'", (user_id,)
        ).fetchone()
    return str(row[0]) if row and row[0] else None


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
        # Bulk-apply R1: the FIRST counted auto grant also starts the practice run (an account that
        # only ever used grants is turning auto on for the first time then).
        grant = conn.execute(
            "SELECT MIN(set_at) FROM profile_edits WHERE user_id = ? AND path = ? AND value IS NOT NULL "
            "AND value <> 'null'",
            (user_id, AUTO_GRANT_UNTIL_PATH),
        ).fetchone()
        if grant and grant[0] and (since is None or str(grant[0]) < since):
            since = str(grant[0])
        if since is None:
            return None, 0
        return since, _applied_after(conn, user_id, since)


async def count_on_user_today(
    db: JobDatabase, user_id: str, now: datetime, sql: str, params: tuple[Any, ...]
) -> int:
    """How many of the ``recorded_at`` values ``sql`` returns fall on the user's
    OWN today (``spine.user_today``, their stored timezone). ``sql`` must select
    one ``recorded_at`` column and end with ``recorded_at >= ?``: the floor
    (now - 48h; only that window can be "today" whatever the offset) is appended
    to ``params`` here. The ONE day boundary both gates use (submit and outreach)."""
    from src.services.applications import spine  # noqa: PLC0415 — spine imports the profile package

    today = await spine.user_today(db, user_id, now=now)
    cur = await db._db.execute("SELECT timezone FROM users WHERE id = ?", (user_id,))
    row = await cur.fetchone()
    raw_zone = (dict(row).get("timezone") if row else None) or "UTC"
    try:
        zone: ZoneInfo = ZoneInfo(raw_zone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    floor = (now - timedelta(hours=48)).isoformat()
    cur = await db._db.execute(sql, (*params, floor))
    count = 0
    for r in await cur.fetchall():
        try:
            at = datetime.fromisoformat(str(dict(r)["recorded_at"]))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at.astimezone(zone).date() == today:
            count += 1
    return count


async def count_on_user_week(
    db: JobDatabase, user_id: str, now: datetime, sql: str, params: tuple[Any, ...]
) -> int:
    """Like :func:`count_on_user_today` for the user's Monday-Sunday week (bulk-apply C2): ``sql`` selects one
    ``recorded_at`` column and ends with ``recorded_at >= ?``; the floor (now - 9 days) is appended here."""
    from src.services.applications import spine  # noqa: PLC0415 — spine imports the profile package

    today = await spine.user_today(db, user_id, now=now)
    monday = today - timedelta(days=today.weekday())
    zone = _zone(await _user_timezone(db, user_id))
    floor = (now - timedelta(days=9)).isoformat()
    cur = await db._db.execute(sql, (*params, floor))
    count = 0
    for r in await cur.fetchall():
        try:
            at = datetime.fromisoformat(str(dict(r)["recorded_at"]))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if monday <= at.astimezone(zone).date() <= today:
            count += 1
    return count


async def _user_timezone(db: JobDatabase, user_id: str) -> str:
    cur = await db._db.execute("SELECT timezone FROM users WHERE id = ?", (user_id,))
    row = await cur.fetchone()
    return str((dict(row).get("timezone") if row else None) or "UTC")


async def last_applied_at(db: JobDatabase, user_id: str) -> Optional[datetime]:
    """When this user's newest ``applied`` was recorded (bulk-apply C1), with NO exclusions: a pace limit
    counts every submit the server saw, so a later simulation mark or correction can never move the
    anchor back (security review 2026-10-18). ``None`` = none."""
    cur = await db._db.execute(
        "SELECT e.recorded_at FROM application_events e "
        "WHERE e.user_id = ? AND e.event_type = 'applied' ORDER BY e.id DESC LIMIT 20",
        (user_id,),
    )
    newest: Optional[datetime] = None
    for r in await cur.fetchall():
        try:
            at = datetime.fromisoformat(str(dict(r)["recorded_at"]))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if newest is None or at > newest:
            newest = at
    return newest


async def company_applied_count(
    db: JobDatabase, user_id: str, application_id: int, now: datetime, window_days: int
) -> int:
    """DISTINCT OTHER applications of this user with an ``applied`` event inside
    ``window_days`` at the same ``jobs.normalized_company`` as ``application_id``'s job.
    A blank or ``unknown`` company counts 0 (we never guess a match). Applications marked
    by a non-retracted ``simulation`` event recorded BEFORE the ``applied`` are left out
    (same shape as :func:`practice_info`); a mark recorded after the ``applied`` cannot
    re-label it (security review 2026-10-18). An ``applied`` replaced by a correction never
    counts (the ``_has_surviving_applied`` rule). The window is on ``occurred_at``, the date
    the user applied: a stop no yes clears must not fire on history recorded late (review
    2026-10-18 chose this over ``recorded_at``). A correction can move a date INTO the window but
    never out: an application counts while any ``applied`` it replaced was dated inside it
    (CodeRabbit 2026-10-18). Scoped by ``user_id`` (rule #12)."""
    cur = await db._db.execute(
        "SELECT j.normalized_company FROM applications a JOIN jobs j ON j.id = a.job_id "
        "WHERE a.id = ? AND a.user_id = ?",
        (application_id, user_id),
    )
    row = await cur.fetchone()
    company = str(dict(row).get("normalized_company") or "").strip() if row else ""
    if not company or company.lower() == "unknown":
        return 0
    # 0 = all time: every earlier application at this company counts.
    cutoff = "1970-01-01T00:00:00+00:00" if window_days == 0 else (now - timedelta(days=window_days)).isoformat()
    cur = await db._db.execute(
        "SELECT COUNT(DISTINCT e.application_id) FROM application_events e "
        "JOIN applications a ON a.id = e.application_id JOIN jobs j ON j.id = a.job_id "
        "WHERE e.user_id = ? AND a.user_id = ? AND e.event_type = 'applied' "
        "AND (e.occurred_at >= ? OR EXISTS (SELECT 1 FROM application_events o "
        "WHERE o.application_id = e.application_id AND o.user_id = e.user_id AND o.event_type = 'applied' "
        "AND o.id < e.id AND o.occurred_at >= ? "
        "AND EXISTS (SELECT 1 FROM application_events c2 WHERE c2.corrects_event_id = o.id "
        "AND c2.user_id = o.user_id AND c2.event_type = 'applied'))) "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) "
        "AND e.application_id <> ? AND j.normalized_company = ? "
        "AND NOT EXISTS (SELECT 1 FROM application_events s WHERE s.application_id = e.application_id "
        "AND s.user_id = e.user_id AND s.event_type = ? AND s.id < e.id AND s.id NOT IN "
        "(SELECT corrects_event_id FROM application_events WHERE user_id = ? AND corrects_event_id IS NOT NULL))",
        (user_id, user_id, cutoff, cutoff, application_id, company, settings.SIMULATION_EVENT_TYPE, user_id),
    )
    got = await cur.fetchone()
    return int(got[0]) if got else 0


async def load_submit_counts(
    db: JobDatabase, user_id: str, now: datetime, application_id: Optional[int] = None,
    *, window_days: Optional[int] = None,
) -> SubmitCounts:
    """Count what :func:`may_submit` needs for this user: submits today (in the
    user's OWN timezone, ``spine.user_today``), the practice-run counter and, when
    ``application_id`` is given, the applications at that application's company inside
    ``window_days`` (the caller passes ``cfg.company_cap_days``; the operator default
    when omitted). Without an application the company count stays 0 (morning_check)."""
    submitted = await count_on_user_today(
        db, user_id, now,
        "SELECT recorded_at FROM application_events "
        "WHERE user_id = ? AND event_type = 'applied' AND recorded_at >= ?",
        (user_id,),
    )
    _since, applied_since = practice_info(user_id)
    week = await count_on_user_week(
        db, user_id, now,
        "SELECT recorded_at FROM application_events "
        "WHERE user_id = ? AND event_type = 'applied' AND recorded_at >= ?",
        (user_id,),
    )
    last = await last_applied_at(db, user_id)
    tz = await _user_timezone(db, user_id)
    _began, grant_applied, scope = grant_state(user_id)
    company = 0
    if application_id is not None:
        days = window_days if window_days is not None else settings.ASSISTANT_COMPANY_CAP_DEFAULT_DAYS
        company = await company_applied_count(db, user_id, application_id, now, days)
    return SubmitCounts(
        submitted_today=submitted, applied_since_auto_on=applied_since, company_applied_in_window=company,
        submitted_this_week=week, last_applied_at=last, user_tz=tz, applied_since_grant=grant_applied,
        grant_scope=scope,
    )


def daily_left(cfg: EffectiveSettings, counts: SubmitCounts) -> Optional[int]:
    """Bulk-apply Q2: submits left today under ``daily_cap`` (never below 0); ``None`` = no cap."""
    if cfg.daily_cap is None:
        return None
    return max(0, cfg.daily_cap - counts.submitted_today)


def company_left(cfg: EffectiveSettings, company_applied: int) -> Optional[int]:
    """Bulk-apply Q1/Q2: applications left at one company inside ``company_cap_days`` (never below 0);
    ``None`` = no ``company_cap``. ``company_applied`` = :func:`company_applied_count`."""
    if cfg.company_cap is None:
        return None
    return max(0, cfg.company_cap - company_applied)


async def load_outreach_counts(
    db: JobDatabase, user_id: str, now: datetime, company: Optional[str] = None
) -> OutreachCounts:
    """Count what :func:`may_send_outreach` needs: this user's recorded ``sent``
    marks today (the user's own day, same boundary as :func:`load_submit_counts`)
    and, when ``company`` is given, ``sent`` marks to contacts linked to a job at
    that company key (contacts.company_key, rule #1; cold contacts with that company
    count) within ``OUTREACH_COMPANY_WINDOW_DAYS``."""
    sent_today = await count_on_user_today(
        db, user_id, now,
        "SELECT recorded_at FROM contact_outreach WHERE user_id = ? AND entry = 'sent' AND recorded_at >= ?",
        (user_id,),
    )
    company_sent = 0
    key = (company or "").strip()
    if key:
        # Lazy: contacts imports this module.
        from src.services.applications.contacts import count_company_sends  # noqa: PLC0415

        since = (now - timedelta(days=settings.OUTREACH_COMPANY_WINDOW_DAYS)).isoformat()
        company_sent = await count_company_sends(db, user_id, key, since)
    return OutreachCounts(sent_today=sent_today, company_sent_in_window=company_sent)


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
    round_name: str,
    parked: list[str],
    site_base_url: str,
    held_now: list[str] | None = None,
    other: list[str] | None = None,
) -> str:
    """The 422 message for a round marked done while a value it needs is not
    saved (owner decision 2026-10-14).

    ``parked`` — paths with a request ALREADY waiting for the user's Confirm.
    ``held_now`` — paths THIS call would have parked: no request exists yet (the
    whole call is refused), so they must never be described as waiting.
    ``other`` — required paths simply not sent: named too, so the caller can
    resend everything in one call."""
    held = list(held_now or [])
    missing = list(other or [])
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
    if missing:
        parts.append(f"also missing {', '.join(missing)} - ask the user, then save them in the same call")
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
    grant_scope: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """The one read model of the settings, used by ``GET /api/profile`` (top-level
    ``assistant_settings``), ``GET /api/assistant-settings`` and MCP ``get_profile``
    (top-level ``settings``): each field raw + effective, ``paused``, the three
    ALIASED preference values (``inbox_mode``, ``check_every``, ``notes``), the
    practice-run state and the waiting requests. ``waiting`` rows come from
    ``setting_requests.list_waiting`` (already non-expired). ``grant_scope`` is
    ``grant_scope.scope_view`` (slice 30): what a grant confirmed now would cover, and what
    the grant in force covers; ``None`` reads as an empty queue and no grant."""
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
        "company_cap": {"value": raw.company_cap, "effective": eff.company_cap},
        "company_cap_days": {"value": raw.company_cap_days, "effective": eff.company_cap_days},
        "weekly_cap": {"value": raw.weekly_cap, "effective": eff.weekly_cap},
        "min_gap_minutes": {"value": raw.min_gap_minutes, "effective": eff.min_gap_minutes},
        "apply_hours": {
            "value": raw.apply_hours,
            "effective": None if eff.apply_hours is None else {"from": eff.apply_hours[0], "to": eff.apply_hours[1]},
        },
        "auto_grant_count": {"value": raw.auto_grant_count, "effective": eff.auto_grant_count},
        "auto_grant_until": {
            "value": raw.auto_grant_until,
            "effective": effective_path_value(AUTO_GRANT_UNTIL_PATH, raw.auto_grant_until, when),
        },
        "auto_grant_scope": grant_scope or {
            "queue": {"count": 0, "hosts": [], "no_host": 0}, "granted": None, "unscoped": False,
        },
        "paused_until": {
            "value": raw.paused_until,
            "effective": effective_path_value(PAUSED_UNTIL_PATH, raw.paused_until, when),
        },
        "pause_reason": {"value": raw.pause_reason, "effective": eff.pause_reason},
        "auto_employer_emails": {"value": raw.auto_employer_emails, "effective": eff.auto_employer_emails},
        "outreach_send": {"value": raw.outreach_send, "effective": eff.outreach_send},
        "hunt_every_hours": {"value": raw.hunt_every_hours, "effective": eff.hunt_every_hours},
        "people_hunt": {"value": raw.people_hunt, "effective": eff.people_hunt},
        "pc_on_hours": {"value": raw.pc_on_hours, "effective": eff.pc_on_hours},
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

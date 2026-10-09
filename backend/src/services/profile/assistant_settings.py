"""Assistant settings — how much the user's assistant may do on its own.

Owner decision 2026-10-08 (S2). Job360 never applies to anything itself; these
are the user's standing rules that the CONNECTED assistant reads and obeys:

* ``apply_mode`` — ``ask_each`` (default) | ``apply_all`` | ``selective_above_score``
* ``apply_min_score`` — 0-100, default 75, only used by ``selective_above_score``
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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal, Optional
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.core import settings
from src.repositories import pgsync
from src.services.profile import edits as profile_edits
from src.services.profile.models import (
    VALID_APPLY_MODES,
    VALID_DAILY_CHECK_VALUES,
    VALID_SUBMIT_MODES,
    AssistantSettings,
    UserProfile,
)

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

HEAD = "assistant_settings"
APPLY_MODE_PATH = f"{HEAD}.apply_mode"
APPLY_MIN_SCORE_PATH = f"{HEAD}.apply_min_score"
SUBMIT_MODE_PATH = f"{HEAD}.submit_mode"
DAILY_CAP_PATH = f"{HEAD}.daily_cap"
PAUSED_UNTIL_PATH = f"{HEAD}.paused_until"
PAUSE_REASON_PATH = f"{HEAD}.pause_reason"
SETTING_PATHS: tuple[str, ...] = (
    APPLY_MODE_PATH, APPLY_MIN_SCORE_PATH, SUBMIT_MODE_PATH,
    DAILY_CAP_PATH, PAUSED_UNTIL_PATH, PAUSE_REASON_PATH,
)
# The inbox/Gmail mode stays at its old path (an ALIAS, not a move: assistants
# cached instructions that write it). "auto" there is a RISKIER change too
# (owner answer 1, 2026-10-08), so it goes through the same request flow.
INBOX_MODE_PATH = "preferences.daily_check"
# S4 - how far the six-round setup got. NOT in SETTING_PATHS / GATED_PATHS: it is
# progress the assistant reports, never a freedom, so it is never "riskier".
SETUP_PROGRESS_PATH = f"{HEAD}.setup_progress"
SETUP_ROUNDS: tuple[str, ...] = ("you", "visa", "logistics", "equality", "targets", "settings")
SETUP_PROGRESS_FUTURE_SLACK = timedelta(minutes=5)
GATED_PATHS: tuple[str, ...] = SETTING_PATHS + (INBOX_MODE_PATH,)

UNTIL_RESUMED = "until_resumed"
DEFAULT_APPLY_MODE = "ask_each"
DEFAULT_APPLY_MIN_SCORE = 75
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
    return parsed.astimezone(timezone.utc)


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
    if path == APPLY_MODE_PATH:
        return _closed_set(path, value, VALID_APPLY_MODES)
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

    apply_mode: str
    apply_min_score: int
    submit_mode: str
    daily_cap: Optional[int]
    paused_until: str
    pause_reason: str


def effective(s: AssistantSettings) -> EffectiveSettings:
    """Fill the safe defaults: ask each / 75 / confirm / no cap / not paused."""
    return EffectiveSettings(
        apply_mode=s.apply_mode or DEFAULT_APPLY_MODE,
        apply_min_score=DEFAULT_APPLY_MIN_SCORE if s.apply_min_score is None else int(s.apply_min_score),
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
    if path == APPLY_MODE_PATH:
        return raw or DEFAULT_APPLY_MODE
    if path == APPLY_MIN_SCORE_PATH:
        return DEFAULT_APPLY_MIN_SCORE if raw is None else raw
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

_APPLY_RANK = {"ask_each": 0, "selective_above_score": 1, "apply_all": 2}


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


def classify_change(path: str, before: Any, after: Any) -> Risk:
    """``"riskier"`` when the change gives the assistant MORE freedom, else
    ``"safer"``. Pure; ``before`` / ``after`` are EFFECTIVE values (defaults
    filled). An unchanged value is ``"safer"`` (a no-op write).

    ===================  =============================================  =========================
    path                 riskier                                        safer
    ===================  =============================================  =========================
    apply_mode           ask_each -> selective/apply_all; selective ->  anything -> ask_each;
                         apply_all                                      apply_all -> selective
    apply_min_score      a lower number                                 higher or equal
    submit_mode          confirm -> auto_when_sure                      -> confirm
    daily_cap            raised, or a number -> none (cap removed)      none -> a number, lowered
    paused_until         cleared or moved earlier (resume)              set or extended
    pause_reason         never                                          always
    preferences.         anything -> auto (the assistant sends mail     every other value
    daily_check          on its own; "scheduled" counts as auto)
    ===================  =============================================  =========================
    """
    if path == APPLY_MODE_PATH:
        return "riskier" if _APPLY_RANK.get(after, 0) > _APPLY_RANK.get(before, 0) else "safer"
    if path == APPLY_MIN_SCORE_PATH:
        return "riskier" if after < before else "safer"
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
    * ``declined`` - the user's newest word on this application is "don't send".
    """

    status: str
    has_receipt: bool
    submit_override: Optional[str]
    duplicate_job: bool = False
    cv_seen: bool = False
    approved: bool = False
    declined: bool = False


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
    "user_approved", "auto_when_sure",
)


def parse_site_host(raw: Optional[str]) -> Optional[str]:
    """The lowercase host of a URL or bare host (port and trailing dot stripped),
    or ``None`` when there is none or it cannot be read."""
    text = (raw or "").strip()
    if not text or any(ch.isspace() or ord(ch) < 32 for ch in text):
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
    return host or None


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
    5. same job already applied to -> ``duplicate_job``: STOP when the mode is
       auto (nobody is watching), else ask
    6. site unknown -> ask ``unknown_site``
    7. Indeed / LinkedIn style site -> ask ``ask_always_site``
    8. the mode (the job's override, else ``submit_mode``) is ``confirm`` -> ask
    9. auto, but the user has not seen the latest CV -> ask ``cv_not_seen``
    10. first application since auto-submit was turned on -> ask ``practice_run``
    11. otherwise -> submit ``auto_when_sure``

    A stored yes for THIS CV (``approved``) clears every ASK from 5 to 10 (even
    Indeed / LinkedIn and the practice run) and answers ``submit`` /
    ``user_approved``. Rules 1-4 and the auto-mode duplicate stop are NEVER
    cleared by a yes.
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
    override = application.submit_override if application.submit_override in VALID_SUBMIT_MODES else None
    mode = override or cfg.submit_mode
    auto = mode == AUTO_SUBMIT_MODE
    if application.duplicate_job and auto:
        return SubmitDecision(
            "stop", "duplicate_job",
            "Needs you: possible duplicate - this job was already applied to. Do not submit; the user decides.",
        )
    ask = _first_ask(application, site_host, counts, override=override, auto=auto)
    if ask is None:
        return SubmitDecision("submit", "auto_when_sure", "Auto-submit is on and this site allows it. You may submit.")
    if application.approved:
        return SubmitDecision(
            "submit", "user_approved", "The user said yes to sending this application with this CV. You may submit."
        )
    return ask


def _first_ask(
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
        "apply_mode": {"value": raw.apply_mode, "effective": eff.apply_mode},
        "apply_min_score": {"value": raw.apply_min_score, "effective": eff.apply_min_score},
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

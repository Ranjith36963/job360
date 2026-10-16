"""Feedback numbers (feedback loop slice 3) - what the stored events say, as numbers.

Read-only. No migration, no table, no event type, no write path. Every view is
a pure function over ``Facts`` (two queries, both scoped by ``user_id``), and
every number carries its own ``n`` so the assistant can say how thin it is.
Job360 counts; it never ranks, scores or recommends (product rule 4): a view is
a FACT block the assistant may quote, never a reason to skip a job or company.

Reference: career-ops (``/var/tmp/co-src``). Each function cites the file:line
it mirrors, and says ADAPTED where Job360 differs on purpose.

* funnel      - stats.mjs:168-184 (ledger form :202-225); funnel-stages.mjs:3-6, :27-38
* velocity    - funnel-velocity.mjs:90-99 (hops), :186 median, :193-202 p75 (R-7), :216-251
* calibration - calibrate.mjs:63-82 outcomes, :135-217 bands + verdict
* company     - company-history.mjs:78-79 windows, :348-437 facts and label, :87-89 explanation
* lesson_effect - NOT IN REFERENCE (no reference measures it)
* followups   - stats.mjs:455-478 computeFollowupStats, adapted to slice-2 events
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, Optional
from zoneinfo import ZoneInfo

from src.core import settings
from src.services.applications.status import _event_when
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

log = logging.getLogger(__name__)

_APPLIED = settings.STATS_APPLIED_EVENT_TYPE
_REPLIED = settings.STATS_REPLIED_EVENT_TYPE
_REJECTED = settings.STATS_REJECTED_EVENT_TYPE
_RANK = settings.FEEDBACK_FUNNEL_RANK
_REPLY_RANK = _RANK[_REPLIED]
_INTERVIEW_RANK = _RANK["interview_requested"]
_OFFER_RANK = _RANK["offer"]
_HIRED_RANK = _RANK["hired"]
# Outcome status names used by calibration (all are members of the status vocabulary).
_OFFER_TYPES = frozenset({"offer", "hired", "offer_declined"})
_INTERVIEW_TYPES = frozenset(settings.STATS_INTERVIEW_EVENT_TYPES)

_RATE_PLACES = 3


# ── Facts ────────────────────────────────────────────────────────────────────


@dataclass
class Ev:
    id: int
    type: str
    when: datetime
    payload: dict[str, Any]


@dataclass
class Facts:
    apps: list[dict[str, Any]] = field(default_factory=list)
    events: dict[int, list[Ev]] = field(default_factory=dict)  # application_id -> (when, id) order
    truncated: bool = False
    n_events: int = 0


def _scope(user_id: str, since_val: Optional[str]) -> tuple[str, list[Any]]:
    """The WHERE of compute_stats: the caller's own applications, since on
    created_at, simulations left out (stats._SIMULATED_SQL)."""
    from src.services.applications.stats import _SIMULATED_SQL  # noqa: PLC0415

    where = ["a.user_id = ?"]
    params: list[Any] = [user_id]
    if since_val:
        where.append("a.created_at >= ?")
        params.append(since_val)
    where.append("NOT " + _SIMULATED_SQL.format(app="a.id"))
    params.extend([user_id, settings.SIMULATION_EVENT_TYPE])
    return " AND ".join(where), params


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


async def load(db: JobDatabase, user_id: str, since_val: Optional[str]) -> Facts:
    """TWO queries, ``user_id = ?`` on every table (rule 12). Retracted events
    (a ``corrects_event_id`` points at them) are excluded with the exact
    stats.py clause; every event-type name is a bound parameter (S4)."""
    where_sql, where_params = _scope(user_id, since_val)
    limit = settings.STATS_MAX_APPLICATIONS

    cur = await db._db.execute(
        "SELECT a.id, a.job_company, a.fit_score, a.status, a.created_at "  # noqa: S608 - constants only
        f"FROM applications a WHERE {where_sql} ORDER BY a.id DESC LIMIT ?",
        [*where_params, limit + 1],
    )
    rows = [dict(r) for r in await cur.fetchall()]
    facts = Facts(truncated=len(rows) > limit)
    facts.apps = rows[:limit]
    if not facts.apps:
        return facts

    types = sorted(
        {*settings.APPLICATION_STATUS_EVENT_TYPES, *settings.FEEDBACK_FOLLOWUP_EVENT_TYPES,
         settings.FEEDBACK_SKIP_EVENT_TYPE}
    )
    placeholders = ", ".join("?" for _ in types)
    cur = await db._db.execute(
        "SELECT e.id, e.application_id, e.event_type, e.occurred_at, e.recorded_at, e.payload "  # noqa: S608
        "FROM application_events e WHERE e.user_id = ? "
        f"AND e.application_id IN (SELECT a.id FROM applications a WHERE {where_sql} ORDER BY a.id DESC LIMIT ?) "
        f"AND e.event_type IN ({placeholders}) "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id)",
        [user_id, *where_params, limit, *types],
    )
    for r in await cur.fetchall():
        row = dict(r)
        when = _event_when(row)
        if when is None:
            continue
        facts.events.setdefault(int(row["application_id"]), []).append(
            Ev(int(row["id"]), str(row["event_type"]), when, _payload(row.get("payload")))
        )
        facts.n_events += 1
    for evs in facts.events.values():
        evs.sort(key=lambda e: (e.when, e.id))
    return facts


# ── Small helpers ────────────────────────────────────────────────────────────


def _rate(x: int, n: int) -> Optional[float]:
    return round(x / n, _RATE_PLACES) if n else None


def _day(when: datetime, zone: ZoneInfo) -> date:
    return when.astimezone(zone).date()


def _status_events(facts: Facts, app_id: int) -> list[Ev]:
    return [e for e in facts.events.get(app_id, []) if e.type in settings.APPLICATION_STATUS_EVENT_TYPES]


def _rank(events: list[Ev]) -> int:
    """Furthest funnel stage reached (funnel-stages.mjs:27-38): max rank over the log."""
    return max((_RANK.get(e.type, 0) for e in events), default=0)


def _applied_when(events: list[Ev]) -> Optional[datetime]:
    """The FIRST non-retracted ``applied`` event (retracted ones are never loaded)."""
    for e in events:
        if e.type == _APPLIED:
            return e.when
    return None


def _applied_apps(facts: Facts) -> list[tuple[dict[str, Any], list[Ev], datetime]]:
    """(application, status events, first applied time) for every application that HAS an
    applied event. One without it is outside every view but the funnel's ``without_applied``
    (owner decision 2026-10-14, stats.py _gate_on_applied; overrides stats.mjs:171)."""
    out = []
    for app in facts.apps:
        evs = _status_events(facts, int(app["id"]))
        when = _applied_when(evs)
        if when is not None:
            out.append((app, evs, when))
    return out


def _median(nums: list[int]) -> Optional[float]:
    """funnel-velocity.mjs:186."""
    if not nums:
        return None
    s = sorted(nums)
    m = len(s) // 2
    return float(s[m]) if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _p75(nums: list[int]) -> Optional[float]:
    """Linear interpolation between closest ranks (R-7), funnel-velocity.mjs:193-202.
    Pinned by the reference fixture: [3, 6, 20] -> 13."""
    if not nums:
        return None
    s = sorted(nums)
    if len(s) == 1:
        return float(s[0])
    rank = 0.75 * (len(s) - 1)
    lo = int(rank)
    hi = lo if rank == lo else lo + 1
    return s[lo] + (s[hi] - s[lo]) * (rank - lo)


# ── B1 funnel ────────────────────────────────────────────────────────────────


def funnel(facts: Facts) -> dict[str, Any]:
    """stats.mjs:168-184. Furthest stage reached per application; a rejection is a reply."""
    counts = {"applied": 0, "replied": 0, "interview": 0, "offer": 0, "hired": 0}
    without_applied = 0
    applied_ids = {int(a["id"]) for a, _, _ in _applied_apps(facts)}
    for app in facts.apps:
        rank = _rank(_status_events(facts, int(app["id"])))
        if int(app["id"]) not in applied_ids:
            if rank >= _REPLY_RANK:
                without_applied += 1
            continue
        counts["applied"] += 1
        counts["replied"] += rank >= _REPLY_RANK
        counts["interview"] += rank >= _INTERVIEW_RANK
        counts["offer"] += rank >= _OFFER_RANK
        counts["hired"] += rank >= _HIRED_RANK
    n = counts["applied"]
    return {
        "n": n,
        "ever_applied": n,
        "ever_replied": counts["replied"],
        "ever_interview": counts["interview"],
        "ever_offer": counts["offer"],
        "ever_hired": counts["hired"],
        "reply_rate": _rate(counts["replied"], n),
        "interview_rate": _rate(counts["interview"], n),
        "offer_rate": _rate(counts["offer"], n),
        "small_sample": n < settings.STATS_SMALL_SAMPLE_N,
        "without_applied": without_applied,
        "note": "A rejection counts as a reply: it is an answer.",
    }


# ── B2 velocity ──────────────────────────────────────────────────────────────

# funnel-velocity.mjs:90-99 minus the assessment hops (slice 4 adds that event).
_HOPS = (
    ("applied_to_replied", "applied", "replied"),
    ("replied_to_interview", "replied", "interview"),
    ("interview_to_offer", "interview", "offer"),
    ("applied_to_rejected", "applied", "rejected"),
)


def _stage(event_type: str) -> str:
    if event_type in _INTERVIEW_TYPES:
        return "interview"
    return event_type  # applied, replied, offer, rejected and every other status keep their name


def velocity(facts: Facts, zone: ZoneInfo) -> dict[str, Any]:
    """funnel-velocity.mjs:216-251. Days between the FIRST ``from`` observation and the first
    LATER ``to`` one, in the user's zone; same-day hops are counted but left out of the math;
    an application still sitting in ``from`` is censored (still waiting) and left out."""
    # `brought` is the application's birth, stamped when the ad was stored - a backdated
    # `applied` would sort before it and hide a still-waiting application - so it is not an
    # observation. Every other status event is (a ghosted or withdrawn app is not "waiting").
    timelines = [
        [(_day(e.when, zone), _stage(e.type)) for e in evs if e.type != "brought"]
        for _, evs, _ in _applied_apps(facts)
    ]
    hops: list[dict[str, Any]] = []
    for key, src, dst in _HOPS:
        days: list[int] = []
        same_day = waiting = 0
        for tl in timelines:
            from_idx = next((i for i, (_, st) in enumerate(tl) if st == src), None)
            if from_idx is None:
                continue
            nxt = next((o for o in tl[from_idx + 1:] if o[1] == dst), None)
            if nxt is not None:
                d = (nxt[0] - tl[from_idx][0]).days
                if d < 0:
                    continue
                if d == 0:
                    same_day += 1
                    continue
                days.append(d)
            elif from_idx == len(tl) - 1 and dst != "rejected":
                waiting += 1  # censored; counted once, on the forward hop (:234-238)
        enough = len(days) >= settings.VELOCITY_HOP_MIN_N
        med, p75 = (_median(days), _p75(days)) if enough else (None, None)
        hops.append({
            "key": key, "from": src, "to": dst, "n": len(days),
            "median_days": round(med, 1) if med is not None else None,
            "p75_days": round(p75, 1) if p75 is not None else None,
            "insufficient_data": not enough,
            "same_day_excluded": same_day,
            "still_waiting": waiting,
        })
    return {
        "hops": hops,
        "min_n": settings.VELOCITY_HOP_MIN_N,
        "note": "Still-waiting applications are left out of the median - they have not answered yet.",
    }


# ── B3 calibration ───────────────────────────────────────────────────────────


@dataclass
class _Band:
    band: str
    min: Optional[int]
    max: Optional[int]
    n: int = 0
    interviews: int = 0
    offers: int = 0
    interview_rate: Optional[float] = None
    offer_rate: Optional[float] = None


def _band_labels() -> list[tuple[str, Optional[int], Optional[int]]]:
    edges = settings.CALIBRATION_BAND_EDGES
    out: list[tuple[str, Optional[int], Optional[int]]] = [(f"<{edges[0]}", None, edges[0])]
    for lo, hi in zip(edges, edges[1:]):
        out.append((f"{lo}-{hi - 1}", lo, hi))
    out.append((f">={edges[-1]}", edges[-1], None))
    return out


def calibration(facts: Facts) -> dict[str, Any]:
    """calibrate.mjs:135-217. Interview rate per fit-score band, over resolved applications.
    ADAPTED: 0-100 bands (owner #16); score 0 is a real score, only None is unscored."""
    bands = [_Band(label, lo, hi) for label, lo, hi in _band_labels()]
    resolved = in_flight = unscored = ghosted_negative = 0
    for app, evs, _ in _applied_apps(facts):
        types = {e.type for e in evs}
        status = str(app.get("status") or "")
        if types & _OFFER_TYPES:
            reached_interview, reached_offer = True, True
        elif types & _INTERVIEW_TYPES:
            reached_interview, reached_offer = True, False
        elif status in (_REJECTED, "ghosted"):  # ghosted = career-ops no_response (:70)
            reached_interview = reached_offer = False
        elif status in (_APPLIED, _REPLIED):
            in_flight += 1
            continue
        else:
            continue  # withdrawn, considering: outside
        score = app.get("fit_score")
        if score is None:  # :166 order - resolved check first, then unscored
            unscored += 1
            continue
        resolved += 1
        if status == "ghosted" and not reached_interview:
            ghosted_negative += 1
        for b in bands:
            if (b.min is None or score >= b.min) and (b.max is None or score < b.max):
                b.n += 1
                b.interviews += int(reached_interview)
                b.offers += int(reached_offer)
                break
    floor = settings.CALIBRATION_MIN_BAND_N
    for b in bands:  # rates stay null under the floor; the counts are still shown (:176-182)
        ok = b.n >= floor
        b.interview_rate = _rate(b.interviews, b.n) if ok else None
        b.offer_rate = _rate(b.offers, b.n) if ok else None

    rated = [b for b in bands if b.interview_rate is not None]
    if len(rated) < 2:
        verdict = {
            "kind": "insufficient",
            "text": (
                f"Not enough resolved outcomes to compare score bands yet ({resolved} resolved, "
                f"floor {floor} per band). Keep recording outcomes and ask again."
            ),
        }
    else:
        low, high = rated[0], rated[-1]
        lo_rate, hi_rate = low.interview_rate or 0.0, high.interview_rate or 0.0  # both rated: not None
        lo_pct, hi_pct = round(lo_rate * 100), round(hi_rate * 100)
        gap = round((hi_rate - lo_rate) * 100)
        top = settings.CALIBRATION_BAND_EDGES[-1]
        if gap >= settings.CALIBRATION_GAP_POINTS:
            verdict = {"kind": "separating", "text": (
                f"The scoring is separating for you: your {high.band} applications reached interviews at "
                f"{hi_pct}% vs {lo_pct}% for {low.band}. Trusting the score is paying off.")}
        elif gap <= -settings.CALIBRATION_GAP_POINTS:
            verdict = {"kind": "inverted", "text": (
                f"Inverted signal: your {low.band} applications are converting BETTER ({lo_pct}%) than your "
                f"{high.band} ones ({hi_pct}%). Worth reading what the high-scored rejections had in common "
                f"before trusting the next {top}+.")}
        else:
            verdict = {"kind": "flat", "text": (
                f"No meaningful separation yet: {high.band} converts at {hi_pct}% vs {lo_pct}% for "
                f"{low.band}. With more resolved outcomes this usually sharpens one way or the other.")}

    quotes: list[tuple[datetime, int, dict[str, Any]]] = []
    for app in facts.apps:
        for e in _status_events(facts, int(app["id"])):
            text = e.payload.get("verbatim_feedback")
            if isinstance(text, str) and text.strip():
                quotes.append((e.when, e.id, {
                    "application_id": int(app["id"]), "company": app.get("job_company") or "",
                    "fit_score": app.get("fit_score"), "verbatim_feedback": text,
                }))
    quotes.sort(key=lambda q: (q[0], q[1]))  # oldest-first, last N (:215)
    feedback = [q[2] for q in quotes[-settings.CALIBRATION_FEEDBACK_MAX:]]
    return {
        "bands": [asdict(b) for b in bands],
        "min_band_n": floor,
        "resolved": resolved,
        "in_flight": in_flight,
        "unscored": unscored,
        "ghosted_counted_negative": ghosted_negative,
        "verdict": verdict,
        "feedback": feedback,
        "note": (
            "Advisory only: Job360 never changes how you score. Ghosted (45 days silent) counts as a negative "
            "outcome here, as in the reference; the patterns view counts it as no answer."
        ),
    }


# ── B4 company ───────────────────────────────────────────────────────────────

_EXPLANATION = (
    "high-volume inboxes, evergreen requisitions, re-opened searches, and your own unlogged responses "
    "all produce these patterns - facts, not verdicts"
)
_ANSWER_NAME = {"offer_declined": "offer"}


def _answered_fact(app_id: int, evs: list[Ev], zone: ZoneInfo, today: date) -> dict[str, Any]:
    """ADAPTED: furthest stage from the event log (funnel-stages.mjs:27-38), not the current
    status, so an application withdrawn after an interview still counts as answered."""
    best = _rank(evs)
    top = [e for e in evs if _RANK.get(e.type, 0) == best][-1]
    outcome = _ANSWER_NAME.get(top.type) or _stage(top.type)
    on = _day(top.when, zone)
    fact: dict[str, Any] = {
        "application_id": app_id, "kind": "answered", "outcome": outcome, "on": on.isoformat(),
        "stale": (today - on).days > settings.COMPANY_STALE_AFTER_DAYS,
    }
    if outcome == _REJECTED:
        fact["note"] = "a rejection is an answer"
    return fact


def company_cards(
    facts: Facts, now: datetime, zone: ZoneInfo, company: Optional[str] = None, since_given: bool = False
) -> dict[str, Any]:
    """company-history.mjs:348-437. Facts and a label per company, grouped by the rule-1 key."""
    from src.services.applications.kit import normalized_company_key  # noqa: PLC0415

    today = _day(now, zone)
    groups: dict[str, list[tuple[dict[str, Any], list[Ev], datetime]]] = {}
    for item in _applied_apps(facts):
        key = normalized_company_key(str(item[0].get("job_company") or ""))
        if key:
            groups.setdefault(key, []).append(item)
    wanted = normalized_company_key(company) if company else None
    if company:
        groups = {k: v for k, v in groups.items() if k == wanted}

    cards: list[dict[str, Any]] = []
    for key, items in groups.items():
        items.sort(key=lambda it: int(it[0]["id"]))
        card_facts: list[dict[str, Any]] = []
        for app, evs, applied in items:
            app_id = int(app["id"])
            if _rank(evs) >= _REPLY_RANK:
                card_facts.append(_answered_fact(app_id, evs, zone, today))
                continue
            applied_on = _day(applied, zone)
            silent_days = (today - applied_on).days
            if str(app.get("status")) not in (_APPLIED, "ghosted"):
                continue  # ended some other way: no fact
            if silent_days < settings.COMPANY_SILENCE_WINDOW_DAYS:
                continue  # pending (:367-368), or ended some other way: no fact
            checkins = sum(1 for e in facts.events.get(app_id, []) if e.type == "checkin_sent")
            card_facts.append({
                "application_id": app_id, "kind": "silent", "applied_on": applied_on.isoformat(),
                "silent_days": silent_days, "checkins_sent": checkins,
                "confidence": "confirmed-by-followups" if checkins >= 1 else "unconfirmed",
                "stale": silent_days > settings.COMPANY_STALE_AFTER_DAYS,
            })
        live = [f for f in card_facts if not f["stale"]]  # stale facts are listed, not labelled (:420-423)
        silent = any(f["kind"] == "silent" for f in live)
        answered = any(f["kind"] == "answered" for f in live)
        label = "mixed" if silent and answered else "silent-on-you" if silent else \
            "responded-before" if answered else "no-history"
        card: dict[str, Any] = {
            "key": key,
            "company": items[0][0].get("job_company") or "",
            "label": label,
            "applications": len(items),
            "facts": card_facts[: settings.COMPANY_FACTS_MAX],
            "facts_truncated": len(card_facts) > settings.COMPANY_FACTS_MAX,
        }
        if card["facts_truncated"]:
            card["truncated_hint"] = f"only the first {settings.COMPANY_FACTS_MAX} facts are listed"
        cards.append(card)

    cards.sort(key=lambda c: (-c["applications"], c["key"]))
    truncated = len(cards) > settings.STATS_MAX_GROUPS
    out: dict[str, Any] = {
        "cards": cards[: settings.STATS_MAX_GROUPS],
        "silence_window_days": settings.COMPANY_SILENCE_WINDOW_DAYS,
        "stale_after_days": settings.COMPANY_STALE_AFTER_DAYS,
        "truncated": truncated,
        "since_ignored": since_given,
        "explanation": _EXPLANATION,
    }
    if truncated:
        out["truncated_hint"] = "pass company=<name> for one company"
    return out


# ── B5 lesson_effect ─────────────────────────────────────────────────────────


def _side(items: list[tuple[dict[str, Any], list[Ev]]]) -> dict[str, Any]:
    n = len(items)
    replied = sum(1 for _, evs in items if _rank(evs) >= _REPLY_RANK)
    interview = sum(1 for _, evs in items if _rank(evs) >= _INTERVIEW_RANK)
    return {
        "n": n, "replied": replied, "interview": interview,
        "reply_rate": _rate(replied, n), "interview_rate": _rate(interview, n),
        "small_sample": n < settings.STATS_SMALL_SAMPLE_N,
        "still_waiting": sum(1 for app, _ in items if str(app.get("status")) == _APPLIED),
    }


def lesson_effect(facts: Facts, lessons: list[dict[str, Any]], total: int) -> dict[str, Any]:
    """Owner #17 - NOT IN REFERENCE. Reply and interview rates for applications sent before vs
    after each agreed lesson. Counts, not proof of cause."""
    applied = _applied_apps(facts)
    rows: list[dict[str, Any]] = []
    for lesson in lessons:
        when = _event_when({"occurred_at": lesson.get("occurred_at"), "recorded_at": None})
        before: list[tuple[dict[str, Any], list[Ev]]] = []
        after: list[tuple[dict[str, Any], list[Ev]]] = []
        for app, evs, applied_at in applied:
            (before if when is not None and applied_at < when else after).append((app, evs))
        b, a = _side(before), _side(after)
        rows.append({
            "event_id": lesson["event_id"], "application_id": lesson["application_id"],
            "lesson": str(lesson.get("detail") or "")[:200], "agreed_on": lesson.get("occurred_at"),
            "before": b, "after": a,
            "comparable": b["n"] >= settings.STATS_CLAIM_MIN_N and a["n"] >= settings.STATS_CLAIM_MIN_N,
        })
    out: dict[str, Any] = {
        "lessons": rows,
        "note": (
            "Before/after counts, not proof of cause: other things changed too, and newer applications "
            "have had less time to get an answer."
        ),
    }
    if total > len(lessons):
        out["truncated"] = True
        out["truncated_hint"] = f"only the newest {len(lessons)} of {total} lessons are compared"
    return out


# ── B6 followups ─────────────────────────────────────────────────────────────


def _followups_available() -> bool:
    return all(t in settings.APPLICATION_NOTE_EVENT_TYPES for t in settings.FEEDBACK_FOLLOWUP_EVENT_TYPES)


def followups(facts: Facts, now: datetime, zone: ZoneInfo) -> dict[str, Any]:
    """stats.mjs:455-478 computeFollowupStats, adapted to slice-2 events. Counts only: never
    "due" (that is slice 2's doctor ``auto_emails.due``)."""
    keys = ("checkins_sent", "feedback_asked", "feedback_followups_sent", "applications_with_checkin",
            "applied_without_checkin", "rejections_without_ask", "skipped", "checkin_days")
    if not _followups_available():
        return {"available": False, **{k: None for k in keys}}
    checkin_t, asked_t, followup_t = settings.FEEDBACK_FOLLOWUP_EVENT_TYPES
    checkin_days = int(getattr(settings, "AUTO_EMAIL_CHECKIN_DAYS", 7))
    today = _day(now, zone)
    skipped: dict[str, int] = {}
    counts = {checkin_t: 0, asked_t: 0, followup_t: 0}
    with_checkin: set[int] = set()
    skip_kinds: dict[int, set[str]] = {}
    for app_id, evs in facts.events.items():
        for e in evs:
            if e.type in counts:
                counts[e.type] += 1
                if e.type == checkin_t:
                    with_checkin.add(app_id)
            elif e.type == settings.FEEDBACK_SKIP_EVENT_TYPE:
                kind = str(e.payload.get("kind") or "unknown")
                skipped[kind] = skipped.get(kind, 0) + 1
                skip_kinds.setdefault(app_id, set()).add(kind)
    applied_without = rejections_without = 0
    applied_ids = {int(a["id"]): w for a, _, w in _applied_apps(facts)}
    for app in facts.apps:
        app_id = int(app["id"])
        status = str(app.get("status"))
        types = {e.type for e in facts.events.get(app_id, [])}
        kinds = skip_kinds.get(app_id, set())
        if status == _APPLIED and app_id in applied_ids:
            if (today - _day(applied_ids[app_id], zone)).days >= checkin_days \
                    and checkin_t not in types and "checkin" not in kinds:
                applied_without += 1
        elif status == _REJECTED and asked_t not in types and "feedback_request" not in kinds:
            rejections_without += 1
    return {
        "available": True,
        "checkins_sent": counts[checkin_t], "feedback_asked": counts[asked_t],
        "feedback_followups_sent": counts[followup_t],
        "applications_with_checkin": len(with_checkin),
        "applied_without_checkin": applied_without,
        "rejections_without_ask": rejections_without,
        "skipped": dict(sorted(skipped.items())),
        "checkin_days": checkin_days,
    }


# ── Orchestration (called by stats.compute_stats) ────────────────────────────


async def compute_views(
    db: JobDatabase,
    user_id: str,
    since_val: Optional[str],
    selected: list[str],
    *,
    company: Optional[str],
    view: str,
) -> dict[str, Any]:
    """One loader, then each selected view over the same facts. A wrong number must not be
    served silently: an unexpected error is logged and RE-RAISED."""
    from src.services.applications import lessons as lessons_service  # noqa: PLC0415
    from src.services.applications.spine import user_zone  # noqa: PLC0415

    started = time.monotonic()
    try:
        zone = await user_zone(db, user_id)
        now = datetime.now(timezone.utc)
        facts = await load(db, user_id, since_val)
        out: dict[str, Any] = {}
        for name in selected:
            if name == "funnel":
                out[name] = funnel(facts)
            elif name == "velocity":
                out[name] = velocity(facts, zone)
            elif name == "calibration":
                out[name] = calibration(facts)
            elif name == "company":
                # History is the point: `since` is ignored (and says so).
                company_facts = facts if not since_val else await load(db, user_id, None)
                out[name] = company_cards(company_facts, now, zone, company, since_given=bool(since_val))
            elif name == "lesson_effect":
                rows, total = lessons_service.list_lessons(user_id, limit=settings.PROFILE_LESSONS_MAX)
                out[name] = lesson_effect(facts, rows, total)
            elif name == "followups":
                out[name] = followups(facts, now, zone)
        if facts.truncated:
            for block in out.values():
                if isinstance(block, dict) and "truncated" not in block:
                    block["truncated"] = True
                    block["truncated_hint"] = (
                        f"only the newest {settings.STATS_MAX_APPLICATIONS} applications were read; "
                        "pass since=YYYY-MM-DD to narrow"
                    )
    except Exception:
        log.exception("stats_view_failed", extra={"user_id": safe_log_value(user_id),
                                                  "view": safe_log_value(view, max_len=40)})
        raise
    get_audit_logger().info(
        "stats_view",
        extra={
            "event": "stats_view", "user_id": safe_log_value(user_id), "view": safe_log_value(view, max_len=40),
            "applications": len(facts.apps), "events": facts.n_events, "truncated": facts.truncated,
            "ms": round((time.monotonic() - started) * 1000),
        },
    )
    return out


__all__ = ["Facts", "calibration", "company_cards", "compute_views", "followups", "funnel", "lesson_effect",
           "load", "velocity"]


"""Autonomy agreement report: the watch-only ``autonomy_shadow`` verdicts against the OWNER's own decisions.

Read-only, one user. ``evidence.py`` records what autonomy WOULD clear (verdict codes, never text); this joins
the newest shadow of each application to the owner's WEB events on that application and says, per kind
(``essay``, ``fixed`` right-to-work, ``cv``), how often they agree. It mirrors the offline
``grade_agreement`` and refines it:

* only ``recorded_by = 'web'`` events are the owner's; an assistant's, a token's or a scheduled run's never count;
* one case per application and question (the NEWEST shadow), not one per check_submit;
* a kind is judged by its own decisions: ``cv_seen`` says nothing about an essay, and the NEWEST send
  decision (``submit_approved`` / ``submit_declined``) wins, not "any declined";
* a ``fixed`` case exists only when the fill really had a right-to-work question (the event keeps only
  ``rtw_ok``, and ``ok`` is also True for "no right-to-work question");
* an essay is edited only by an edit of THAT question (``answer_verified`` with ``edited`` true, or a web
  ``answer_corrected``); a fixed case by an edit of a right-to-work question.

``wrong_facts``: a shadow that would have cleared something the owner then edited or corrected (a web
``answer_corrected`` counts whether it came before or after the receipt: the shadow cleared it either way).
Examples carry ids and codes, never text.
Test applications (``simulation``) are left out. Every query is scoped by ``user_id``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications import evidence
from src.services.applications.kit import _json
from src.services.applications.test_marker import TEST_ID_SQL
from src.services.profile.answer_match import normalize_question

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

KINDS = ("essay", "fixed", "cv")
MAX_EXAMPLES = 5
_DECISIONS = ("submit_approved", "submit_declined", "cv_seen", "answer_verified", "answer_corrected")
_KEY_MAX = 80


def _qkey(payload: dict[str, Any]) -> tuple[str, str]:
    """(saved key, normalised question cut like the shadow's own key) of an answer event's payload."""
    return str(payload.get("key") or ""), normalize_question(payload.get("question"))[:_KEY_MAX]


def _hits(ev: dict[str, Any], question_key: str) -> bool:
    key, qnorm = ev["key"], ev["qnorm"]
    return bool(question_key) and question_key in (key, qnorm)


def _newest(decisions: list[dict[str, Any]], *types: str) -> Optional[str]:
    for ev in reversed(decisions):  # ascending ids
        if ev["type"] in types:
            return str(ev["type"])
    return None


def _rtw_edit(decisions: list[dict[str, Any]]) -> bool:
    return any(ev["edit"] and evidence._is_rtw(ev["question"], ev["key"]) for ev in decisions)


def _empty() -> dict[str, Any]:
    return {k: {"cases": 0, "agree": 0, "wrong_facts": 0} for k in KINDS}


async def report(db: JobDatabase, user_id: str) -> dict[str, Any]:
    from src.services.applications import kit  # noqa: PLC0415

    test_sql, test_params = TEST_ID_SQL("e.application_id", "e.user_id")
    cur = await db._db.execute(
        "SELECT e.id, e.application_id, e.payload FROM application_events e "  # noqa: S608
        f"WHERE e.user_id = ? AND e.event_type = ? AND NOT {test_sql} ORDER BY e.id",
        (user_id, evidence.EVENT_TYPE, *test_params),
    )
    shadows: dict[int, dict[str, Any]] = {}
    for r in await cur.fetchall():
        d = dict(r)
        shadows[int(d["application_id"])] = _json(d["payload"])  # ascending: the newest wins
    decisions: dict[int, list[dict[str, Any]]] = {}
    if shadows:
        marks = ",".join("?" for _ in shadows)
        cur = await db._db.execute(
            "SELECT id, application_id, event_type, payload FROM application_events "  # noqa: S608
            f"WHERE user_id = ? AND recorded_by = 'web' AND application_id IN ({marks}) "
            f"AND event_type IN ({','.join('?' for _ in _DECISIONS)}) ORDER BY id",
            (user_id, *shadows, *_DECISIONS),
        )
        for r in await cur.fetchall():
            d = dict(r)
            p = _json(d["payload"])
            key, qnorm = _qkey(p)
            kind = d["event_type"]
            decisions.setdefault(int(d["application_id"]), []).append({
                "type": kind, "key": key, "qnorm": qnorm, "question": str(p.get("question") or ""),
                "edit": kind == "answer_corrected" or (kind == "answer_verified" and p.get("edited") is True),
                    })
    per_kind = _empty()
    pending = 0
    examples: list[dict[str, Any]] = []

    def case(app_id: int, kind: str, qkey: str, would: bool, owner: Optional[bool], wrong: bool) -> None:
        nonlocal pending
        if owner is None:
            pending += 1
            return
        row = per_kind[kind]
        row["cases"] += 1
        row["agree"] += int(would == owner)
        row["wrong_facts"] += int(wrong)
        if would != owner or wrong:
            examples.append({"application_id": app_id, "kind": kind, "question_key": qkey,
                             "would_clear": would, "owner": "approved" if owner else "disagreed"})

    for app_id, sh in sorted(shadows.items()):
        evs = decisions.get(app_id, [])
        send = _newest(evs, "submit_approved", "submit_declined")
        essays = [e for e in sh.get("essays") or [] if isinstance(e, dict)]
        for es in essays:
            qkey = str(es.get("question_key") or "")
            mine = [e for e in evs if _hits(e, qkey)]
            edited = any(e["edit"] for e in mine)
            verified = any(e["type"] == "answer_verified" and not e["edit"] for e in mine)
            would = es.get("would_clear") is True
            owner: Optional[bool] = None
            if edited or verified or send is not None:
                owner = (send == "submit_approved" or verified) and send != "submit_declined" and not edited
            case(app_id, "essay", qkey, would, owner, would and edited)
        if "rtw_ok" in sh and any(
            evidence._is_rtw(a["question"], str(a.get("key") or "")) for a in await kit.last_fill(db, user_id, app_id)
        ):
            would = sh.get("rtw_ok") is True
            edited = _rtw_edit(evs)
            owner = None if not (edited or send) else (send == "submit_approved" and not edited)
            case(app_id, "fixed", "right_to_work", would, owner, would and edited)
        if isinstance(sh.get("cv"), dict):
            seen = _newest(evs, "cv_seen", "submit_approved", "submit_declined")
            owner = None if seen is None else seen != "submit_declined"
            case(app_id, "cv", "cv", sh["cv"].get("would_clear") is True, owner, False)

    kinds: dict[str, Any] = {}
    for k, v in per_kind.items():
        pct = round(100.0 * v["agree"] / v["cases"], 1) if v["cases"] else None
        kinds[k] = {**v, "agreement_pct": pct, "bar_met": bool(
            v["cases"] >= settings.AUTONOMY_BAR_MIN_CASES and pct is not None
            and pct >= settings.AUTONOMY_BAR_MIN_AGREE_PCT and v["wrong_facts"] == 0)}
    examples.sort(key=lambda e: e["application_id"], reverse=True)
    return {
        "kinds": kinds, "pending": pending, "examples": examples[:MAX_EXAMPLES],
        "bar": {"min_cases": settings.AUTONOMY_BAR_MIN_CASES, "min_agree_pct": settings.AUTONOMY_BAR_MIN_AGREE_PCT},
    }

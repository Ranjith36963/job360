"""Autonomy agreement report: the watch-only ``autonomy_shadow`` verdicts against the OWNER's own decisions.

Read-only, one user. ``evidence.py`` records what autonomy WOULD clear (verdict codes, never text) and which
fill (``form_filled_event_id``) and CV (``cv_artifact_id``) it judged; this pairs each such shadow with the
owner's WEB events for that same fill and says, per kind (``essay``, ``fixed`` right-to-work, ``cv``), how
often they agree. It mirrors the offline ``grade_agreement`` and tightens it:

* only ``recorded_by = 'web'`` events are the owner's; an assistant's, a token's or an agent's never count;
* a decision pairs with a shadow only when it is bound to that fill: its own ``form_filled_event_id`` (the
  Send fingerprint, a Verify/Edit) equals the shadow's, or it has none and its id lies after that fill and
  before the next one. A yes recorded before the fill never pairs. An unpaired case is ``pending``;
* an AGREE needs a positive owner decision (Send, or a Verify without edit; ``cv_seen`` / Send for the CV).
  A decline is a disagreement when the shadow would have cleared, and is left out when it would not;
* one essay case per application and fill (all its essays together), so one Send is never 20 cases;
* a ``fixed`` case opens only from the shadow's own ``rtw_present``;
* ``wrong_facts``: a shadow that would have cleared something the owner edited or corrected. A right-to-work
  edit counts whichever fill it was made on.

``bar_met`` compares integers (``100 * agree >= MIN_PCT * cases``). Examples carry ids and codes, never text.
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
_NO_NEXT = 1 << 62

APPROVED, EDITED, DECLINED = "approved", "edited", "declined"


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _paired(ev: dict[str, Any], fill: Optional[int], next_fill: int) -> bool:
    """The decision belongs to this fill: bound to it by its own id, else recorded after it and before the next."""
    if fill is None:
        return False
    if ev["fill"] is not None:
        return bool(ev["fill"] == fill)
    return bool(fill < ev["id"] < next_fill)


def _matches(ev: dict[str, Any], aliases: set[str]) -> bool:
    return bool(any(a and a in aliases for a in (ev["key"], ev["qfull"], ev["qfull"][:_KEY_MAX])))


def _newest(events: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    return max(events, key=lambda e: e["id"]) if events else None


def _verdict(pos: list[dict[str, Any]], neg: list[dict[str, Any]], edited: bool) -> Optional[str]:
    """What the owner did on one case: an edit, else the newer of a positive and a decline, else nothing."""
    if edited:
        return EDITED
    last_pos, last_neg = _newest(pos), _newest(neg)
    if last_neg is not None and (last_pos is None or last_neg["id"] > last_pos["id"]):
        return DECLINED
    return APPROVED if last_pos is not None else None


def _empty() -> dict[str, Any]:
    return {k: {"cases": 0, "agree": 0, "wrong_facts": 0} for k in KINDS}


async def report(db: JobDatabase, user_id: str) -> dict[str, Any]:
    test_sql, test_params = TEST_ID_SQL("e.application_id", "e.user_id")
    cur = await db._db.execute(
        "SELECT e.id, e.application_id, e.payload FROM application_events e "  # noqa: S608
        f"WHERE e.user_id = ? AND e.event_type = ? AND NOT {test_sql} ORDER BY e.id",
        (user_id, evidence.EVENT_TYPE, *test_params),
    )
    # The newest shadow of each (application, fill): a second check_submit on the same fill is not a second case.
    shadows: dict[tuple[int, Optional[int]], dict[str, Any]] = {}
    for r in await cur.fetchall():
        d = dict(r)
        payload = _json(d["payload"])
        shadows[(int(d["application_id"]), _int(payload.get("form_filled_event_id")))] = payload
    apps = sorted({a for a, _ in shadows})
    decisions: dict[int, list[dict[str, Any]]] = {a: [] for a in apps}
    fills: dict[int, dict[int, dict[str, Any]]] = {a: {} for a in apps}
    if apps:
        marks = ",".join("?" for _ in apps)
        cur = await db._db.execute(
            "SELECT id, application_id, event_type, payload FROM application_events "  # noqa: S608
            f"WHERE user_id = ? AND recorded_by = 'web' AND application_id IN ({marks}) "
            f"AND event_type IN ({','.join('?' for _ in _DECISIONS)}) ORDER BY id",
            (user_id, *apps, *_DECISIONS),
        )
        for r in await cur.fetchall():
            d = dict(r)
            p = _json(d["payload"])
            raw_stamp = p.get("fingerprint")
            stamp: dict[str, Any] = raw_stamp if isinstance(raw_stamp, dict) else {}
            kind = d["event_type"]
            decisions[int(d["application_id"])].append({
                "id": int(d["id"]), "type": kind, "key": str(p.get("key") or ""),
                "qfull": normalize_question(p.get("question")), "question": str(p.get("question") or ""),
                "edit": kind == "answer_corrected" or (kind == "answer_verified" and p.get("edited") is True),
                "fill": _int(p.get("form_filled_event_id")) or _int(stamp.get("form_filled_event_id")),
                "artifact": _int(p.get("artifact_id")),
            })
        cur = await db._db.execute(
            "SELECT id, application_id, payload FROM application_events "  # noqa: S608
            f"WHERE user_id = ? AND event_type = 'form_filled' AND application_id IN ({marks})",
            (user_id, *apps),
        )
        for r in await cur.fetchall():
            d = dict(r)
            fills[int(d["application_id"])][int(d["id"])] = _json(d["payload"])

    per_kind = _empty()
    pending = 0
    examples: list[dict[str, Any]] = []

    def case(app_id: int, kind: str, qkey: str, would: bool, state: Optional[str], wrong: bool) -> None:
        nonlocal pending
        if state is None:
            pending += 1
            return
        if state == DECLINED and not would:
            return  # a decline of something autonomy would not have cleared: not a case
        agree = (would and state == APPROVED) or (not would and state == EDITED)
        row = per_kind[kind]
        row["cases"] += 1
        row["agree"] += int(agree)
        row["wrong_facts"] += int(wrong)
        if not agree or wrong:
            examples.append({"application_id": app_id, "kind": kind, "question_key": qkey,
                             "would_clear": would, "owner": "approved" if state == APPROVED else "disagreed"})

    for (app_id, fill), sh in sorted(shadows.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        evs = decisions[app_id]
        later = [f for f in fills[app_id] if fill is not None and f > fill]
        nxt = min(later) if later else _NO_NEXT
        # a refill replaced this fill before the owner decided: nothing is waiting on it any more
        superseded = bool(later) and not any(_paired(e, fill, nxt) for e in evs)
        mine = [e for e in evs if _paired(e, fill, nxt)]
        sends = [e for e in mine if e["type"] in ("submit_approved", "submit_declined")]
        verified = [e for e in mine if e["type"] == "answer_verified" and not e["edit"]]
        neg = [e for e in sends if e["type"] == "submit_declined"]
        send_yes = [e for e in sends if e["type"] == "submit_approved"]

        essays = [e for e in sh.get("essays") or [] if isinstance(e, dict) and e.get("question_key")]
        if essays and not superseded:
            aliases: set[str] = set()
            keys = {str(e["question_key"]) for e in essays}
            aliases |= keys
            for a in (fills[app_id].get(fill or -1) or {}).get("answers") or []:
                if isinstance(a, dict) and (str(a.get("key") or "") in keys
                                            or normalize_question(a.get("question"))[:_KEY_MAX] in keys):
                    aliases |= {str(a.get("key") or ""), normalize_question(a.get("question")),
                                normalize_question(a.get("question"))[:_KEY_MAX]}
            aliases.discard("")
            hits = [e for e in mine if e["type"] in ("answer_verified", "answer_corrected") and _matches(e, aliases)]
            edited = any(e["edit"] for e in hits)
            yes = send_yes + [e for e in hits if not e["edit"]]
            would = all(e.get("would_clear") is True for e in essays)
            first = next((str(e["question_key"]) for e in essays if e.get("would_clear") is not True),
                         str(essays[0]["question_key"]))
            case(app_id, "essay", first, would, _verdict(yes, neg, edited), would and edited)

        if sh.get("rtw_present") is True and "rtw_ok" in sh:
            would = sh.get("rtw_ok") is True
            # a right-to-work edit counts whichever fill it was made on: that answer was wrong
            edited = any(e["edit"] and evidence._is_rtw(e["question"], e["key"]) for e in evs)
            yes = send_yes + [e for e in verified if evidence._is_rtw(e["question"], e["key"])]
            if not superseded or edited:
                case(app_id, "fixed", "right_to_work", would, _verdict(yes, neg, edited), would and edited)

        if isinstance(sh.get("cv"), dict) and not superseded:
            cv_id = _int(sh.get("cv_artifact_id"))
            cv_yes = [e for e in evs if e["type"] in ("cv_seen", "submit_approved")
                      and cv_id is not None and e["artifact"] == cv_id]
            case(app_id, "cv", "cv", sh["cv"].get("would_clear") is True, _verdict(cv_yes, neg, False), False)

    kinds: dict[str, Any] = {}
    for k, v in per_kind.items():
        pct = round(100.0 * v["agree"] / v["cases"], 1) if v["cases"] else None
        kinds[k] = {**v, "agreement_pct": pct, "bar_met": bool(
            v["cases"] >= settings.AUTONOMY_BAR_MIN_CASES
            and 100 * v["agree"] >= settings.AUTONOMY_BAR_MIN_AGREE_PCT * v["cases"]
            and v["wrong_facts"] == 0)}
    examples.sort(key=lambda e: e["application_id"], reverse=True)
    return {
        "kinds": kinds, "pending": pending, "examples": examples[:MAX_EXAMPLES],
        "bar": {"min_cases": settings.AUTONOMY_BAR_MIN_CASES, "min_agree_pct": settings.AUTONOMY_BAR_MIN_AGREE_PCT},
    }

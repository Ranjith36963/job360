"""Autonomy agreement report: the watch-only ``autonomy_shadow`` verdicts against the OWNER's own decisions.

Read-only, one user. ``evidence.py`` records what autonomy WOULD clear (verdict codes, never text) and which
fill (``form_filled_event_id``) and CV (``cv_artifact_id``) it judged; this pairs each such shadow with the
owner's WEB events for that same fill and says, per kind (``essay``, ``fixed`` right-to-work, ``cv``), how
often they agree. It mirrors the offline ``grade_agreement`` and tightens it:

* only ``recorded_by = 'web'`` events are the owner's; an assistant's, a token's or an agent's never count;
* a fill is a run of consecutive ``form_filled`` events with the same answers hash (a retype of the same
  answers is the same fill, as ``kit.approval_stale`` reads it). A decision pairs with a shadow only when it
  is bound to that fill: its own ``form_filled_event_id`` (Send fingerprint, Verify/Edit) is one of the fill's,
  or it has none and lies after the fill's first event and before the next fill. A yes before the fill never
  pairs. An unpaired case is ``pending``; a fill replaced before the owner decided is not waiting any more;
* an AGREE needs a positive owner decision (Send, or a Verify without edit of EVERY essay; ``cv_seen`` / Send
  for the CV). A decline is a disagreement when the shadow would have cleared, and is left out when it would not;
* one essay case per application and fill (all its essays together), so one Send is never 20 cases;
* a ``fixed`` case opens only from the shadow's own ``rtw_present``, and is judged only by the decisions on
  its own fill: a right-to-work edit is a wrong fact once, for the shadow of the fill it was made on;
* one CV case per application and CV: any web decline of a fill using that CV is a disagreement when the
  shadow cleared it, else an approval of that CV agrees.

``wrong_facts``: a shadow that would have cleared something the owner edited or corrected on that fill.
``bar_met`` compares integers (``100 * agree >= MIN_PCT * cases``). Examples carry ids and codes, never text.
Test applications (``simulation``) are left out. Every query is scoped by ``user_id``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications import evidence
from src.services.applications.kit import _json, answers_sha256
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


def _groups(fills: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Fill id -> its fill group ``{start, members, next}``: consecutive fills with the same answers hash."""
    out: dict[int, dict[str, Any]] = {}
    runs: list[dict[str, Any]] = []
    prev: Optional[str] = None
    for fid in sorted(fills):
        sha = answers_sha256(fills[fid])
        if runs and sha is not None and sha == prev:
            runs[-1]["members"].add(fid)
        else:
            runs.append({"start": fid, "members": {fid}, "next": _NO_NEXT})
        prev = sha
        out[fid] = runs[-1]
    for before, after in zip(runs, runs[1:]):
        before["next"] = after["start"]
    return out


def _paired(ev: dict[str, Any], grp: Optional[dict[str, Any]]) -> bool:
    """The decision belongs to this fill: bound to it by its own id, else recorded after it and before the next."""
    if grp is None:
        return False
    if ev["fill"] is not None:
        return ev["fill"] in grp["members"]
    return bool(grp["start"] < ev["id"] < grp["next"])


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


def _aliases(essay_key: str, answers: list[Any]) -> set[str]:
    """The ways an owner's event can name this essay: the shadow's key, the fill row's key and its question text."""
    out = {essay_key}
    for a in answers:
        if isinstance(a, dict):
            full = normalize_question(a.get("question"))
            if essay_key in (str(a.get("key") or ""), full[:_KEY_MAX]):
                out |= {str(a.get("key") or ""), full, full[:_KEY_MAX]}
    out.discard("")
    return out


def _rtw_sig(payload: dict[str, Any]) -> Optional[tuple[str, ...]]:
    """The normalised answers of a fill's right-to-work rows; None when it has none."""
    rows = [
        normalize_question(a.get("answer")) for a in payload.get("answers") or []
        if isinstance(a, dict) and a.get("answer")
        and evidence._is_rtw(str(a.get("question") or ""), str(a.get("key") or ""))
    ]
    return tuple(sorted(rows)) or None


def _rtw_edits(
    newest: dict[Any, Any], decisions: dict[int, list[dict[str, Any]]], fills: dict[int, dict[int, dict[str, Any]]],
    group_of: dict[int, dict[int, dict[str, Any]]],
) -> tuple[dict[tuple[int, int], list[dict[str, Any]]], dict[tuple[int, int], int]]:
    """A right-to-work edit made on fill F counts against every shadow on the application whose fill is F or
    older and whose right-to-work answer is the one F carries (F with no such row: only the newest older
    shadow that had one); never
    against a newer fill. Returns (edits per shadow, wrong facts per shadow): each edit is ONE wrong fact,
    charged to the newest such shadow that would have cleared."""
    shadows = {k: v[2] for k, v in newest.items() if k[1] is not None and v[1].get("rtw_present") is True
               and "rtw_ok" in v[1] and v[2] is not None}
    clears = {k: v[1].get("rtw_ok") is True for k, v in newest.items() if k in shadows}
    quals: dict[tuple[int, int], list[dict[str, Any]]] = {}
    wrong: dict[tuple[int, int], int] = {}
    for app_id, evs in decisions.items():
        starts = sorted({g["start"]: g for g in group_of[app_id].values()}.items())
        for e in evs:
            if not (e["edit"] and evidence._is_rtw(e["question"], e["key"])):
                continue
            if e["fill"] is not None:
                at = group_of[app_id].get(e["fill"])
            else:  # an answer_corrected sits after the newest fill before it
                before = [g for st, g in starts if st < e["id"]]
                at = before[-1] if before else None
            if at is None:
                continue
            sig = _rtw_sig(fills[app_id][at["start"]])
            older = [k for k, g in shadows.items() if k[0] == app_id and g["start"] <= at["start"]]
            if sig is None:  # no right-to-work row to compare: only the newest older shadow that had one
                hit = [max(older, key=lambda k: k[1] or 0)] if older else []
            else:
                hit = [k for k in older if _rtw_sig(fills[app_id][shadows[k]["start"]]) == sig]
            for k in hit:
                quals.setdefault((k[0], k[1]), []).append(e)
            cleared = [k for k in hit if clears[k]]
            if cleared:
                top = max(cleared, key=lambda k: k[1] or 0)
                wrong[(top[0], top[1])] = wrong.get((top[0], top[1]), 0) + 1
    return quals, wrong


async def report(db: JobDatabase, user_id: str) -> dict[str, Any]:
    test_sql, test_params = TEST_ID_SQL("e.application_id", "e.user_id")
    cur = await db._db.execute(
        "SELECT e.id, e.application_id, e.payload FROM application_events e "  # noqa: S608
        f"WHERE e.user_id = ? AND e.event_type = ? AND NOT {test_sql} ORDER BY e.id",
        (user_id, evidence.EVENT_TYPE, *test_params),
    )
    shadows: list[tuple[int, int, dict[str, Any]]] = []  # (event id, application id, payload), ascending
    for r in await cur.fetchall():
        d = dict(r)
        shadows.append((int(d["id"]), int(d["application_id"]), _json(d["payload"])))
    apps = sorted({a for _, a, _ in shadows})
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
    group_of = {a: _groups(f) for a, f in fills.items()}

    # The newest shadow of each (application, fill group): a second check_submit on the same fill is not a second case.
    newest: dict[tuple[int, Optional[int]], tuple[int, dict[str, Any], Optional[dict[str, Any]]]] = {}
    for seq, app_id, payload in shadows:
        fill = _int(payload.get("form_filled_event_id"))
        grp = group_of[app_id].get(fill) if fill is not None else None
        newest[(app_id, grp["start"] if grp else None)] = (seq, payload, grp)

    per_kind = _empty()
    pending = 0
    examples: list[dict[str, Any]] = []

    def case(app_id: int, kind: str, qkey: str, would: bool, state: Optional[str], wrong: int) -> None:
        nonlocal pending
        if state is None:
            pending += 1
            return
        if state == DECLINED and not would:
            return  # a decline of something autonomy would not have cleared: not a case
        if state == EDITED and not would:
            return  # an edit never makes an agreement; of something autonomy would not have cleared it is no case
        agree = would and state == APPROVED
        row = per_kind[kind]
        row["cases"] += 1
        row["agree"] += int(agree)
        row["wrong_facts"] += wrong
        if not agree or wrong:
            examples.append({"application_id": app_id, "kind": kind, "question_key": qkey,
                             "would_clear": would, "owner": "approved" if state == APPROVED else "disagreed"})

    rtw_quals, rtw_wrong = _rtw_edits(newest, decisions, fills, group_of)
    cvs: dict[tuple[int, Optional[int]], dict[str, Any]] = {}
    for (app_id, _start), (seq, sh, grp) in sorted(newest.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        evs = decisions[app_id]
        mine = [e for e in evs if _paired(e, grp)]
        # a refill replaced this fill before the owner decided: nothing is waiting on it any more
        superseded = grp is not None and grp["next"] != _NO_NEXT and not mine
        rtw_edits = rtw_quals.get((app_id, grp["start"]), []) if grp else []
        sends = [e for e in mine if e["type"] in ("submit_approved", "submit_declined")]
        verified = [e for e in mine if e["type"] == "answer_verified" and not e["edit"]]
        neg = [e for e in sends if e["type"] == "submit_declined"]
        send_yes = [e for e in sends if e["type"] == "submit_approved"]

        if isinstance(sh.get("cv"), dict):
            cv_id = _int(sh.get("cv_artifact_id"))
            slot = cvs.setdefault((app_id, cv_id), {"seq": -1, "sh": sh, "declined": False})
            slot["declined"] = slot["declined"] or bool(neg)
            if seq > slot["seq"]:
                slot["seq"], slot["sh"] = seq, sh
        essays = [e for e in sh.get("essays") or [] if isinstance(e, dict) and e.get("question_key")]
        if essays and not superseded:
            answers = (fills[app_id].get(grp["start"]) or {}).get("answers") if grp else None
            per_essay = {str(e["question_key"]): _aliases(str(e["question_key"]), answers or []) for e in essays}
            allnames = set().union(*per_essay.values())
            hits = [e for e in mine if e["type"] in ("answer_verified", "answer_corrected") and _matches(e, allnames)]
            edited = any(e["edit"] for e in hits)
            every_verified = all(
                any(not e["edit"] and e["type"] == "answer_verified" and _matches(e, names) for e in hits)
                for names in per_essay.values()
            )
            # without a Send, a Verify approves the case only when it covers every essay in it
            yes = send_yes + ([e for e in hits if not e["edit"]] if every_verified else [])
            cleared = set().union(*(per_essay[str(e["question_key"])] for e in essays if e.get("would_clear") is True))
            bad = [e for e in hits if e["edit"] and _matches(e, cleared)]  # an edit of an essay it would have cleared
            would = all(e.get("would_clear") is True for e in essays) or bool(bad)
            first = next((str(e["question_key"]) for e in essays if e.get("would_clear") is not True),
                         str(essays[0]["question_key"]))
            case(app_id, "essay", first, would, _verdict(yes, neg, edited), len(bad))

        if sh.get("rtw_present") is True and "rtw_ok" in sh and (not superseded or rtw_edits):
            would = sh.get("rtw_ok") is True
            yes = send_yes + [e for e in verified if evidence._is_rtw(e["question"], e["key"])]
            case(app_id, "fixed", "right_to_work", would, _verdict(yes, neg, bool(rtw_edits)),
                 rtw_wrong.get((app_id, grp["start"]), 0) if grp else 0)

    for (app_id, cv_id), slot in sorted(cvs.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        approved = cv_id is not None and any(
            e["type"] in ("cv_seen", "submit_approved") and e["artifact"] == cv_id for e in decisions[app_id])
        state = DECLINED if slot["declined"] else (APPROVED if approved else None)
        case(app_id, "cv", "cv", slot["sh"]["cv"].get("would_clear") is True, state, 0)

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

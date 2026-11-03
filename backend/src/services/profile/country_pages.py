"""Country pages (owner 2026-11-02): "For every country there should be instructions."

One place PER COUNTRY (or region / ``ANY``) built from what memory already holds - the right-to-work row
(with the user's own ``instructions``), ``preferences.salary_by_country``, ``user_info.logistics.countries``
and the ``user_info.answers`` bound to that country. A read model only: nothing new is stored but the row's
``instructions`` and the draft mark below. Each effective value is the country's own row, else its
region's, else ``ANY`` (``regions.record_code_for``), labelled with where it came from. Nothing set =
nothing shown (rule #29).

DRAFTS. An assistant may CREATE a country row (UAE, Switzerland...) from the nearest region's values.
``profile_edits`` records who wrote a path (``set_by``), but one path holds every country, so the row an
assistant created or changed carries ``set_by: "assistant"`` itself. :func:`stamp` sets it from the
writer on every ``user_info.right_to_work`` write, never from the caller's body; the user's web edit of
that row (or a web save that drops the mark) confirms it. A drafted row is a guess: the kit marks its
facts unconfirmed, check_submit never reads them as the user's word, and its facts are asked once on the
questionnaire (an answer the user remembers for that country then wins over the guess).
"""
from __future__ import annotations

from typing import Any, Optional

from src.services.profile.countries import to_iso2
from src.services.profile.regions import is_region, region_code, region_of

RTW_PATH = "user_info.right_to_work"
DRAFT_MARK = "assistant"
ROW_META = ("country", "set_by", "instructions")
# The per-country facts the common questions ask (common_form_questions.json kit_key right_to_work.{cc}.*).
ASKED_FACTS = ("work_authorization", "needs_sponsorship", "onsite")


def _rows(records: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in records if isinstance(records, list) else []:
        if isinstance(rec, dict) and rec.get("country"):
            out.setdefault(str(rec["country"]).upper(), rec)
    return out


def rtw_rows(rtw: Any) -> dict[str, dict[str, Any]]:
    return _rows(rtw.get("countries") if isinstance(rtw, dict) else None)


def is_drafted(rec: Any) -> bool:
    return isinstance(rec, dict) and rec.get("set_by") == DRAFT_MARK


def row_mode(rtw: Any) -> bool:
    """True when the stored value carries draft marks: then an unmarked row is the user's own (``stamp``
    marks every row an assistant created or changed). Without marks the path's own writer decides."""
    return any(is_drafted(r) for r in rtw_rows(rtw).values())


def _facts(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if k != "set_by"}


def stamp(value: Any, stored: Any, *, by_web: bool, stored_users: bool) -> Any:
    """``value`` (a validated right_to_work) with the draft marks the WRITER earns. Web: a row stays
    drafted only when sent back unchanged with its mark (an edit, or no mark, confirms it). Assistant: a
    new row, a changed row, a row still drafted, or every row when the stored value was not the user's
    (``stored_users`` false) is marked; an unchanged row of the user's stays unmarked."""
    if not isinstance(value, dict) or not isinstance(value.get("countries"), list):
        return value
    old = rtw_rows(stored)
    out = []
    for rec in value["countries"]:
        prev = old.get(str(rec.get("country") or "").upper())
        if by_web:
            mark = is_drafted(rec) and is_drafted(prev) and prev is not None and _facts(rec) == _facts(prev)
        else:
            mark = not stored_users or prev is None or is_drafted(prev) or _facts(rec) != _facts(prev)
        out.append({**_facts(rec), **({"set_by": DRAFT_MARK} if mark else {})})
    return {**value, "countries": out}


_PRIVATE = ("work_authorization", "needs_sponsorship")


def drafts_only(value: Any, stored: Any) -> bool:
    """An assistant write that remembers nothing private the user has not already saved: the user's rows
    and the top-level facts are as stored, and each drafted row's visa facts are those of the row it
    starts from (its own stored row, else the region's or ANY's). A new private fact still needs the
    user's remember (weak spot #6)."""
    if not isinstance(value, dict) or not row_mode(value):
        return False
    top = {k: v for k, v in value.items() if k != "countries"}
    old = stored if isinstance(stored, dict) else {}
    if top != {k: v for k, v in old.items() if k != "countries"}:
        return False
    from src.services.profile.regions import record_code_for  # noqa: PLC0415

    rows = rtw_rows(old)
    for code, rec in rtw_rows(value).items():
        if not is_drafted(rec):
            continue
        own = rows.get(code)
        via = None if own is not None or code == "ANY" or is_region(code) else record_code_for(frozenset(rows), code)
        base = own if own is not None else rows.get(via) if via else None
        if any(rec.get(k) != (base or {}).get(k) for k in _PRIVATE):
            return False
    return True


def confirm_row(rtw: Any, key: str) -> Any:
    """``rtw`` with the draft mark off the row a kit key ``right_to_work.<CC>.<field>`` names: the user
    verified that fact on the web (the Ready card's Verify), so the row is theirs."""
    parts = key.split(".")
    if len(parts) != 3 or not isinstance(rtw, dict) or not isinstance(rtw.get("countries"), list):
        return rtw
    cc = parts[1].upper()
    rows = [_facts(r) if isinstance(r, dict) and str(r.get("country") or "").upper() == cc else r
            for r in rtw["countries"]]
    return {**rtw, "countries": rows}


def place_code(text: str) -> Optional[str]:
    """``"DE"`` / ``"Germany"`` / ``"EUROPE"`` / ``"ANY"`` -> its code; ``None`` when unreadable."""
    t = str(text or "").strip()
    if t.upper() == "ANY":
        return "ANY"
    return region_code(t) or to_iso2(t, strict=True)


def _chain(code: str) -> list[str]:
    region = None if is_region(code) or code == "ANY" else region_of(code)
    return list(dict.fromkeys([code, *([region] if region else []), "ANY"]))


def _pick(rows: dict[str, dict[str, Any]], chain: list[str]) -> Optional[dict[str, Any]]:
    for code in chain:
        if code in rows:
            return {"from": code, "value": {k: v for k, v in rows[code].items() if k not in ROW_META}}
    return None


def instructions_for(rtw: Any, code: Optional[str]) -> Optional[dict[str, Any]]:
    """The user's instructions for a place: its own row's, else its region's, else ANY's. A drafted row's
    lines are not the user's word and are skipped. ``None`` when none apply (rule #29)."""
    if not code:
        return None
    rows = rtw_rows(rtw)
    for via in _chain(code.upper()):
        rec = rows.get(via)
        if rec and rec.get("instructions") and not is_drafted(rec):
            return {"from": via, "lines": list(rec["instructions"])}
    return None


def key_is_users(rtw: Any, key: str) -> Optional[bool]:
    """For a kit key ``right_to_work.<CC>.<field>``: False when the row answering it is drafted, True when
    the value carries draft marks and that row is unmarked (the user's), ``None`` = the path's writer
    decides (no marks, or not a country key)."""
    parts = key.split(".")
    if len(parts) != 3 or parts[0] != "right_to_work":
        return None
    rows = rtw_rows(rtw)
    from src.services.profile.regions import record_code_for  # noqa: PLC0415

    via = record_code_for(frozenset(rows), parts[1])
    rec = rows.get(via) if via else None
    if rec is None:
        return None
    if is_drafted(rec):
        return False
    return True if row_mode(rtw) else None


def remembered_answers(answers: Any, code: str) -> dict[str, dict[str, Any]]:
    """``{topic: record}`` the user approved for this one country (the questionnaire's remember)."""
    return {
        str(a["topic"]): a for a in answers or []
        if isinstance(a, dict) and a.get("approved") is True and a.get("topic") and a.get("answer")
        and str(a.get("country") or "").upper() == code
    }


def country_page(
    profile: Any, place: str, path_confirmed: bool = True, answers_by_user: bool = True
) -> Optional[dict[str, Any]]:
    """The page for one ISO country or region; ``None`` when ``place`` is unreadable. Only what is set
    is shown (rule #29). ``path_confirmed`` = the right_to_work path's newest write is the user's;
    ``answers_by_user`` = so is the answers list's (an assistant's approved answer confirms no fact)."""
    code = place_code(place)
    if code is None:
        return None
    info, prefs = profile.user_info, profile.preferences
    rtw = info.right_to_work or {}
    chain = _chain(code)
    page: dict[str, Any] = {"country": code}
    found = {
        "instructions": instructions_for(rtw, code),
        "right_to_work": _pick(rtw_rows(rtw), chain),
        "salary": _pick(_rows(prefs.salary_by_country), chain),
        "logistics": _pick(_rows((info.logistics or {}).get("countries")), chain),
    }
    page.update({k: v for k, v in found.items() if v})
    if len(chain) > 2 and any(v and v["from"] == chain[1] for v in found.values()):
        page["region_fallback"] = chain[1]  # only when the region really answers something here
    answers = [a for a in info.answers or [] if isinstance(a, dict) and str(a.get("country") or "").upper() == code]
    if answers:
        page["answers"] = answers
    own = rtw_rows(rtw).get(code)
    if own is not None:
        drafted = is_drafted(own)
        page["assistant_drafted"] = drafted
        page["confirmed"] = not drafted and (path_confirmed or row_mode(rtw))
        if drafted:
            done = remembered_answers(info.answers, code) if answers_by_user else {}
            page["to_confirm"] = [
                f"right_to_work.{code}.{k}" for k in ASKED_FACTS
                if k in own and f"right_to_work.{code}.{k}" not in done
            ]
    return page


def page_codes(profile: Any) -> list[str]:
    """Every place the user has a right-to-work, salary or logistics row for, in saved order."""
    info, prefs = profile.user_info, profile.preferences
    codes = [*rtw_rows(info.right_to_work), *_rows(prefs.salary_by_country),
             *_rows((info.logistics or {}).get("countries"))]
    return list(dict.fromkeys(codes))


def answers_by_user(overlay: list[dict[str, Any]]) -> bool:
    """No live assistant write of the answers list: its approved answers are the user's own (#1261)."""
    from src.services.profile.edits import is_assistant_actor  # noqa: PLC0415

    return not any(
        r.get("path") == "user_info.answers" and is_assistant_actor(str(r.get("set_by") or "")) for r in overlay
    )


def path_confirmed(overlay: list[dict[str, Any]]) -> bool:
    """The right_to_work path's newest write is the user's (web, or their word) or there is none."""
    from src.services.profile.edits import is_confirmed  # noqa: PLC0415

    row = next((r for r in overlay if r.get("path") == RTW_PATH), None)
    return row is None or is_confirmed(str(row.get("set_by") or ""), str(row.get("source") or ""))

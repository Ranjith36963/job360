"""Watch-only autonomy evidence (autonomy slice 3, "shadow"; DECISION_AUTONOMY 2026-11-02).

For one application: would autonomy clear its written answers, its CV and its right-to-work answers?
Deterministic, no model and no network. NOTHING here changes a verdict: ``kit.gate_facts(shadow=True)``
attaches the result to ``SubmitFacts.autonomy_shadow`` (``may_submit`` never reads it), check_submit shows
it and records one server-only ``autonomy_shadow`` event of verdict codes (no answer text): the labels a
later slice is measured on.

* Essay (a ``written`` answer of the newest ``form_filled``): every claim token (a number, a year, a
  capitalised or symbol-bearing word such as an employer, school, title or tool) is in the user's fact bank
  (profile source text, approved answers, CVs the user approved on the web, this job's company and title);
  not a near-copy (3-word-shingle Jaccard >= :data:`SAME_PLAYBOOK_JACCARD`) of an essay already sent to
  another employer; never a sensitive or legal question (``kit._question_is_sensitive``).
* CV (the one the receipt would freeze): ``cv_checks.check_text("cv")`` finds nothing, and every bullet
  overlaps a profile sentence (word Jaccard >= :data:`BULLET_JACCARD`).
* Right to work / sponsorship answers: equal to the user's saved answer for THIS job's country (the
  step-2 binding, ``ready._approved_record`` + ``answer_match.fits_country``); a mismatch is loud.
Words are scanned one by one (no regex on caller text).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional

from src.services.applications import cv_checks
from src.services.profile.answer_match import normalize_question

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

EVENT_TYPE = "autonomy_shadow"
SAME_PLAYBOOK_JACCARD = 0.8
BULLET_JACCARD = 0.5
_SHINGLE = 3
_OUTER = ".,;:!?()[]{}\"'‘’“”"
_SENTENCE_ENDS = (".", "!", "?", ":")
_RTW_WORDS = frozenset({
    "sponsor", "sponsorship", "visa", "authorization", "authorisation", "authorized", "authorised", "permit",
})
_RTW_KEYS = ("right_to_work.", "sponsorship.")
_MAX_PRIOR = 500


def _words(text: str) -> list[str]:
    return [w for w in (t.strip(_OUTER).casefold() for t in str(text or "").split()) if w]


def _shingles(text: str) -> set[tuple[str, ...]]:
    w = _words(text)
    return {tuple(w[i:i + _SHINGLE]) for i in range(max(1, len(w) - _SHINGLE + 1))} if w else set()


def _jaccard(a: set[Any], b: set[Any]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def claim_tokens(text: str) -> list[str]:
    """Numbers, years, all-caps words, words with ``+``/``#`` and capitalised words that do not open a
    sentence (an employer, a school, a title, a tool), in order. "I" and a sentence's first word are not."""
    out: list[str] = []
    start = True
    for raw in str(text or "").split():
        tok = raw.strip(_OUTER)
        if tok.endswith(("'s", "’s")):
            tok = tok[:-2]
        if tok and (
            any(c.isdigit() for c in tok) or "+" in tok or "#" in tok or (len(tok) > 1 and tok.isupper())
            or (not start and tok[0].isupper() and tok != "I" and not tok.startswith(("I'", "I’")))
        ):
            out.append(tok)
        start = raw.rstrip("\"')’”").endswith(_SENTENCE_ENDS)
    return out


def _digit_runs(tok: str) -> list[str]:
    runs, cur = [], ""
    for c in tok + " ":
        if c.isdigit() or (c in ".," and cur):
            cur += c
        elif cur:
            runs.append(cur.rstrip(".,"))
            cur = ""
    return [r for r in runs if r]


def unsupported_tokens(text: str, bank: str) -> list[str]:
    """Claim tokens of ``text`` the fact bank never says (numbers by value, words as whole words)."""
    cores = {cv_checks._core(m) for m in _digit_runs(bank)}
    words = set(_words(bank))
    low = " ".join(bank.split()).casefold()
    bad: list[str] = []
    for tok in claim_tokens(text):
        runs = _digit_runs(tok)
        if runs:
            ok = all(cv_checks._core(r) in cores for r in runs)
        else:
            t = tok.casefold()
            ok = t in words or (("+" in t or "#" in t) and t in low)
        if not ok and tok not in bad:
            bad.append(tok)
    return bad


def essay_verdict(
    question: str, key: str, answer: str, bank: str, prior_essays: list[str], *, sensitive: bool,
) -> dict[str, Any]:
    reasons: list[str] = []
    if sensitive:
        reasons.append("sensitive")
    reasons.extend(f"unsupported:{t}" for t in unsupported_tokens(answer, bank)[: cv_checks.MAX_LISTED])
    mine = _shingles(answer)
    if any(_jaccard(mine, _shingles(p)) >= SAME_PLAYBOOK_JACCARD for p in prior_essays):
        reasons.append("same_playbook")
    return {"question_key": key or normalize_question(question)[:80], "would_clear": not reasons,
            "reasons": reasons}


def _sentences(text: str) -> list[set[str]]:
    """The profile's lines, each cut into sentences (a word ending ``.``/``!``/``?``), as word sets."""
    out: list[set[str]] = []
    for line in text.splitlines():
        cur: list[str] = []
        for raw in line.split():
            cur.append(raw)
            if raw.endswith((".", "!", "?")):
                out.append(set(_words(" ".join(cur))))
                cur = []
        if cur:
            out.append(set(_words(" ".join(cur))))
    return out


def cv_verdict(cv_text: Optional[str], profile: Any) -> dict[str, Any]:
    if not cv_text:
        return {"would_clear": False, "reasons": ["no_cv"]}
    reasons = [i["code"] for i in cv_checks.check_text("cv", cv_text, profile)]
    lines = [ln for ln in _sentences(cv_checks.profile_source_text(profile)) if len(ln) >= 3]
    for line in cv_checks._body_lines(cv_text):
        s = line.strip()
        if s.startswith(cv_checks._BULLET_STARTS):
            mine = set(_words(s.lstrip("".join(cv_checks._BULLET_STARTS))))
            if mine and not any(_jaccard(mine, ln) >= BULLET_JACCARD for ln in lines):
                reasons.append("bullet_unmatched")
                break
    return {"would_clear": not reasons, "reasons": reasons}


def _is_rtw(question: str, key: str) -> bool:
    words = set(_words(normalize_question(question)))
    return key.casefold().startswith(_RTW_KEYS) or bool(words & _RTW_WORDS) or {"right", "work"} <= words


def rtw_verdict(
    answers: list[dict[str, Any]], profile: Any, company_key: str, country: Optional[str],
) -> dict[str, Any]:
    """The fill's right-to-work answers against the user's saved answer for THIS job's country."""
    from src.services.applications import ready  # noqa: PLC0415 - ready imports kit, kit imports this lazily

    rows = [a for a in answers if a.get("answer") and _is_rtw(str(a["question"]), str(a.get("key") or ""))]
    if not rows:
        return {"ok": True, "detail": "no right-to-work question"}
    if not country:
        return {"ok": False, "detail": "the job's country is not set"}
    for a in rows:
        rec = ready._approved_record(profile, str(a["question"]), company_key, str(a.get("key") or "") or None, country)
        if rec is None:
            return {"ok": False, "detail": f"no saved answer for {country}: \"{str(a['question'])[:80]}\""}
        if normalize_question(rec["answer"]) != normalize_question(a["answer"]):
            return {"ok": False, "detail": f"differs from the saved {country} answer: \"{str(a['question'])[:80]}\""}
    return {"ok": True, "detail": f"matches the saved {country} answers"}


async def _bank(db: JobDatabase, user_id: str, profile: Any, app_row: dict[str, Any]) -> str:
    """Profile source text + approved answers + CVs the user approved on the web + this job's company and title."""
    parts = [cv_checks.profile_source_text(profile, include_targets=False)]
    parts += [str(r.get("answer") or "") for r in (profile.user_info.answers or [])
              if isinstance(r, dict) and r.get("approved") is True]
    cur = await db._db.execute(
        "SELECT payload FROM application_events WHERE user_id = ? AND recorded_by = 'web' "
        "AND event_type IN ('cv_seen', 'submit_approved') ORDER BY id DESC LIMIT 200",
        (user_id,),
    )
    ids = {int(p["artifact_id"]) for p in (_load(dict(r)["payload"]) for r in await cur.fetchall())
           if isinstance(p.get("artifact_id"), int)}
    for art_id in sorted(ids)[-50:]:
        cur = await db._db.execute(
            "SELECT text FROM application_artifacts WHERE id = ? AND user_id = ? AND kind = 'cv'", (art_id, user_id)
        )
        got = await cur.fetchone()
        if got:
            parts.append(str(dict(got)["text"] or ""))
    parts += [str(app_row.get("job_company") or ""), str(app_row.get("job_title") or "")]
    return "\n".join(parts)


def _load(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        got = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


async def _prior_essays(db: JobDatabase, user_id: str, application_id: int) -> list[str]:
    """``written`` answers of fills on the user's OTHER applications that have a receipt (sent essays)."""
    cur = await db._db.execute(
        "SELECT f.payload FROM application_events f WHERE f.user_id = ? AND f.event_type = 'form_filled' "
        "AND f.application_id <> ? AND EXISTS (SELECT 1 FROM application_receipts r "
        "WHERE r.application_id = f.application_id AND r.user_id = f.user_id) ORDER BY f.id DESC LIMIT ?",
        (user_id, application_id, _MAX_PRIOR),
    )
    out: list[str] = []
    for r in await cur.fetchall():
        for a in _load(dict(r)["payload"]).get("answers") or []:
            if isinstance(a, dict) and a.get("source") == "written" and a.get("answer") and not a.get("redacted"):
                out.append(str(a["answer"]))
    return out


async def for_application(db: JobDatabase, user_id: str, app_row: dict[str, Any]) -> dict[str, Any]:
    """``{watch_only, essays: [{question_key, would_clear, reasons}], cv: {would_clear, reasons}, cv_review,
    rtw: {ok, detail}}`` for the newest fill and the CV the receipt would freeze."""
    from src.services.applications import cv_review, kit, spine  # noqa: PLC0415 - kit imports this module lazily
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    application_id = int(app_row["id"])
    profile = load_profile(user_id) or UserProfile()
    answers = await kit.last_fill(db, user_id, application_id)
    essays = [a for a in answers if a["source"] == "written" and a["answer"]]
    bank = await _bank(db, user_id, profile, app_row) if essays else ""
    prior = await _prior_essays(db, user_id, application_id) if essays else []
    cv_id = await spine.frozen_cv_id(db, user_id, application_id)
    fills = await kit._events(db, user_id, application_id, ("form_filled",))
    art = await spine.get_artifact(db, user_id, application_id, cv_id) if cv_id is not None else None
    country = (str(app_row.get("job_country") or "").strip().upper()) or None
    return {
        "watch_only": True,
        # What the verdicts were about, so the agreement report pairs them with the owner's yes for THIS fill and CV.
        "form_filled_event_id": int(fills[0]["id"]) if fills else None,
        "cv_artifact_id": cv_id,
        "rtw_present": any(
            a.get("answer") and _is_rtw(str(a["question"]), str(a.get("key") or "")) for a in answers
        ),
        "essays": [
            essay_verdict(
                a["question"], str(a.get("key") or ""), a["answer"], bank, prior,
                sensitive=kit._question_is_sensitive(a["question"], a.get("key")),
            )
            for a in essays
        ],
        "cv": cv_verdict(str(art["text"]) if art and art["kind"] == "cv" else None, profile),
        # Owner 2026-11-05: the separate reviewer's verified verdict on that same CV (strong | weak | none).
        "cv_review": await cv_review.review_state(
            db, user_id, application_id, cv_id, kit.sha256_text(str(art["text"]))
        ) if art and art["kind"] == "cv" else cv_review.NONE,
        "rtw": rtw_verdict(answers, profile, kit.normalized_company_key(str(app_row.get("job_company") or "")),
                           country),
    }


def event_payload(shadow: dict[str, Any]) -> dict[str, Any]:
    """The stored event: verdict codes only. A reason's token (``unsupported:<word>``) and the rtw detail
    are dropped, so no answer text is ever kept."""
    return {
        "essays": [{"question_key": e["question_key"], "would_clear": e["would_clear"],
                    "reasons": sorted({r.split(":", 1)[0] for r in e["reasons"]})} for e in shadow["essays"]],
        "cv": {"would_clear": shadow["cv"]["would_clear"], "reasons": shadow["cv"]["reasons"],
               **({"review": shadow["cv_review"]} if "cv_review" in shadow else {})},
        "rtw_ok": shadow["rtw"]["ok"],
        **{k: shadow[k] for k in ("form_filled_event_id", "cv_artifact_id", "rtw_present") if k in shadow},
    }

"""Ask once (owner answers 2026-11-02, questions 2 and 3).

Remember ONLY on the owner's press: an answer to a standard (not per-job) form question is saved as an
approved ``user_info.answers`` record bound to its key AND the job's country, only when the owner presses
"remember" on Needs you ("OK all and remember", or a row's own Remember). Nothing else saves for reuse.
Only a country-specific question (:func:`is_country_specific`: right to work, sponsorship, salary,
relocation, on-site) is bound to the job's country; every other answer (phone, notice period) is global.
A saved record is what ``answer_match`` / ``ready._approved_record`` already read as the user's word, so
the next job in that country asking it needs no owner; another country still asks (``fits_country``).

The questionnaire: up to :data:`PER_COUNTRY` common questions per country the user works in, picked
from ``common_questions`` rows that are guessed (to verify) or not known, most-guessed first.
"""
from __future__ import annotations

from typing import Any, Optional

from src.services.profile import common_questions

PER_COUNTRY = 10
# Rows asked again on every job (how you heard, a referral, consent, the CV check, an essay) or
# written for one job (``said_*`` sentences, doctor lessons): never remembered for reuse.
PER_JOB_KEYS = frozenset({
    "how_heard", "referred", "worked_before", "consent", "demographic_consent", "cv_ok",
    "cv_unsupported_terms", "essays", "job_country",
})
_PER_JOB_PREFIXES = ("said_", "earlier_", "doctor_repeat_", "consent.", "certify.", "form.consent")
# THE one list of country-specific words (lead 2026-11-02). They name the kit's per-country key families
# (``right_to_work.{cc}.*``, ``sponsorship.{cc}.*``, ``salary.{cc}``, ``logistics.{cc}.willing_to_relocate``,
# ``right_to_work.{cc}.onsite``) and the same questions in a form's own words. Matched word by word on the
# key and the question (no regex on caller text); "right to work" is the pair right + work.
COUNTRY_WORDS = frozenset({
    "sponsor", "sponsorship", "visa", "visas", "authorization", "authorisation", "authorized", "authorised",
    "permit", "citizen", "citizenship", "salary", "compensation", "pay", "relocate", "relocation", "relocating",
    "onsite", "office", "hybrid",
})
_COUNTRY_KEY_FAMILIES = ("right_to_work.", "sponsorship.", "salary.")


def _per_job_wordings() -> frozenset[str]:
    from src.services.profile.answer_match import normalize_question  # noqa: PLC0415

    return frozenset(normalize_question(q["question"]) for q in common_questions.catalog() if q.get("per_job"))


def reusable(key: str, question: str) -> bool:
    """False for a per-job row: its key, or a catalog per-job question's wording."""
    from src.services.profile.answer_match import normalize_question  # noqa: PLC0415

    k = key.strip().casefold()
    if k in PER_JOB_KEYS or k.startswith(_PER_JOB_PREFIXES):
        return False
    return normalize_question(question) not in _per_job_wordings()


def is_country_specific(key: str, question: str) -> bool:
    """True when the answer holds for one country only: a per-country kit key family, a key segment that is
    a country code (``logistics.GB.willing_to_relocate``), or a :data:`COUNTRY_WORDS` word in the key or
    question (right to work, sponsorship, salary, relocation, on-site)."""
    from src.services.profile.answer_match import normalize_question  # noqa: PLC0415

    if key.casefold().startswith(_COUNTRY_KEY_FAMILIES):
        return True
    if any(len(p) == 2 and p.isalpha() and p.isupper() for p in key.split(".")):
        return True
    words = set(normalize_question(key).split()) | set(normalize_question(question).split())
    return bool(words & COUNTRY_WORDS) or {"right", "work"} <= words or {"on", "site"} <= words


def remember(user_id: str, rows: list[dict[str, str]], country: Optional[str]) -> list[str]:
    """Save each ``{topic, question, answer}`` as the user's approved answer (the user's own web press: the
    caller is a session-only route): bound to ``country`` when :func:`is_country_specific`, else global.
    Upserts on topic + country. Returns the topics saved; a per-job row is skipped, and so is a
    country-specific one when the job's country is unknown (it must never become global). All-or-nothing:
    a bad value writes nothing (422)."""
    from src.services.profile import edits as profile_edits  # noqa: PLC0415
    from src.services.profile import user_info  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile, load_profile_with_overlay, save_profile  # noqa: PLC0415

    keep = [
        r for r in rows if str(r.get("answer") or "").strip() and reusable(r["topic"], r["question"])
        and (country or not is_country_specific(r["topic"], r["question"]))
    ]
    if not keep:
        return []
    loaded, _ = load_profile_with_overlay(user_id)
    records = [dict(r) for r in (loaded or UserProfile()).user_info.answers or [] if isinstance(r, dict)]
    for r in keep:
        new = {"question": r["question"], "answer": r["answer"].strip(), "approved": True, "topic": r["topic"],
               **({"country": country.upper()}
                  if country and is_country_specific(r["topic"], r["question"]) else {})}
        at = user_info.answer_upsert_index(records, new)
        if at is None:
            records.append(new)
        else:
            records[at] = new
    value = profile_edits.validate_edit(user_info.USER_INFO_ANSWERS_PATH, records)
    if load_profile(user_id, with_overlay=False) is None:
        save_profile(UserProfile(), user_id, source_action="agent_edit")
    profile_edits.record_edits(
        user_id, profile_edits.WEB_ACTOR, [(user_info.USER_INFO_ANSWERS_PATH, value)],
        enforce_rate_limit=False, source=profile_edits.SOURCE_USER_SAID,
    )
    return [r["topic"] for r in keep]


def _key(row: dict[str, Any]) -> str:
    """The topic a questionnaire row is remembered under: its catalog topic, else its kit key."""
    return str(row.get("topic") or row.get("kit_key") or row["id"])


def questionnaire(profile: Any, overlay: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``[{country, questions}]``: per saved country, up to :data:`PER_COUNTRY` rows still guessed or not
    known (``summary()``'s to_verify + missing), guessed first, then by how many forms ask it. A row the
    user already remembered is done: a global one for every country, a country-specific one
    (:func:`is_country_specific`) for its country only (``fits_country``). Read-only."""
    from src.services.profile.answer_match import find_answer  # noqa: PLC0415

    rows = [r for r in common_questions.common_questions(profile, overlay) if not r["per_job"]]
    saved = getattr(getattr(profile, "user_info", None), "answers", None) or []
    out: list[dict[str, Any]] = []
    for cc in common_questions._countries(profile):
        todo = [
            {**r, "topic": _key(r), "question": r["question"].replace("{country}", cc)}
            for r in rows
            if r["country"] in (cc, None) and not r["verified"]
            and find_answer(saved, topic=_key(r), country=cc) is None
        ]
        todo.sort(key=lambda r: (r["label"] != "guessed", -int(r["seen"])))
        out.append({"country": cc, "questions": todo[:PER_COUNTRY]})
    return out


def open_questions(user_id: str) -> int:
    """How many questionnaire rows wait on the user, every country together (the Needs-you count's
    ``answer_once``). Read-only; 0 when the profile cannot be read, so a bad profile never breaks the badge."""
    import logging  # noqa: PLC0415

    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile_with_overlay  # noqa: PLC0415

    try:
        loaded, overlay = load_profile_with_overlay(user_id)
        return sum(len(c["questions"]) for c in questionnaire(loaded or UserProfile(), overlay))
    except Exception:  # noqa: BLE001 - the count must answer; the card's own read shows the error
        logging.getLogger(__name__).exception("ask_once.open_questions failed")
        return 0

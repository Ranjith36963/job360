"""Ask once (owner answers 2026-11-02, questions 2 and 3).

Remember ONLY on the owner's press: an answer to a standard (not per-job) form question is saved as an
approved ``user_info.answers`` record bound to its key AND the job's country, only when the owner presses
"remember" on Needs you ("OK all and remember", or a row's own Remember). Nothing else saves for reuse.
Memory first (owner 2026-11-05, plan S2): a question in Memory's families (:func:`memory_question`: right
to work, sponsorship, salary, notice, relocation, on-site, location, equality, contact) is NEVER saved: the
user sets it once on their country page (or Memory) in Job360, and nothing here writes Memory. Every other
answer (an essay, how you heard) is saved globally. A saved record is what ``answer_match`` /
``ready._approved_record`` already read as the user's word, so the next job asking it needs no owner.

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


# Memory first (owner 2026-11-05, plan S1): the kit blocks Memory holds. A form question in one of them is
# answered from Memory (the country page, its region's, ANY), never from a saved answer.
MEMORY_BLOCKS = ("contact", "right_to_work", "logistics", "salary", "languages", "equality")


# Pay words a form uses for the salary it expects ("Desired Pay", "Desired compensation"; live run 2026-11-06):
# read as "salary", so they name the salary rows (the job country's range) and nothing else.
_PAY_WORDS = frozenset({"pay", "compensation"})
# Contact links (live run 2026-11-06): a label asking for the USER'S OWN link on ONE site ("Link to your
# LinkedIn profile"). ALLOW-list: every word is the site, a link word or one of the user's own phrasing words;
# anything else (how you heard, two sites, a referrer's link) is not the user's link.
_LINK_SITES = {"linkedin": "contact.linkedin_url", "github": "contact.github_url"}
_LINK_WORDS = frozenset({"url", "link", "profile", "page", "address"})
_OWN_LINK_WORDS = frozenset({"your", "my", "to", "a", "the", "please", "share", "provide", "enter", "add", "paste",
                             "what", "is", "public", "full", "optional"})
# Someone else's link, or a handle where a full URL is not wanted: never the user's saved URL (a gap).
NOT_OWN_LINK_WORDS = frozenset({"referrer", "referred", "reference", "manager", "supervisor", "company", "employer",
                                "recruiter", "colleague", "friend", "team", "username", "handle"})


def _contact_link(words: set[str]) -> Optional[str]:
    """The contact URL key a label asking for the user's own link names (:data:`_LINK_SITES`), else ``None``."""
    sites = {_LINK_SITES[w] for w in words if w in _LINK_SITES}
    if len(sites) != 1 or words - set(_LINK_SITES) - _LINK_WORDS - _OWN_LINK_WORDS:
        return None
    return next(iter(sites)) if len(words) == 1 or words & _LINK_WORDS else None


def not_own_link(question: str) -> bool:
    """True when a question about a LinkedIn / GitHub link asks for someone else's, or for a handle."""
    from src.services.profile.answer_match import normalize_question  # noqa: PLC0415

    words = set(normalize_question(question).split())
    return bool(words & set(_LINK_SITES)) and bool(words & NOT_OWN_LINK_WORDS)


def _signals(words: set[str]) -> set[str]:
    """The :data:`COUNTRY_WORDS` in ``words`` by their first four letters (sponsor / sponsorship are one);
    "right to work" counts as "authorization", "on site" as "onsite" (:func:`is_country_specific`) and a
    :data:`_PAY_WORDS` word as "salary"."""
    out = {"sala" if w in _PAY_WORDS else w[:4] for w in words if w in COUNTRY_WORDS}
    if {"right", "work"} <= words:
        out.add("authorization"[:4])
    if {"on", "site"} <= words:
        out.add("onsite"[:4])
    return out


def _field_words(kit_key: str) -> set[str]:
    """``right_to_work.{cc}.needs_sponsorship`` -> {needs, sponsorship}; ``salary.{cc}`` -> {salary}."""
    from src.services.profile.answer_match import normalize_question  # noqa: PLC0415

    parts = [p for p in kit_key.split(".") if p != "{cc}"]
    return set(normalize_question(parts[-1]).split())


def _is_kit_key(template: str, key: str) -> bool:
    """``key`` is the catalog kit key ``template`` for some place (``right_to_work.GB.onsite``)."""
    want, got = template.split("."), key.split(".")
    return len(want) == len(got) and all(w == g or w == "{cc}" and g.isupper() for w, g in zip(want, got))


def memory_question(key: Optional[str], question: str) -> tuple[bool, Optional[str]]:
    """``(in_memory_family, kit_key)`` for a form question. In a family when ``key`` is a catalog Memory key,
    when :func:`is_country_specific`, or when the wording is a catalog Memory row's (or names its field). The
    kit key (``right_to_work.{cc}.needs_sponsorship``) is the ONE catalog row whose own field word the
    question names and whose words cover the question's; none or several = ``None`` (never a guess)."""
    from src.services.profile.answer_match import normalize_question  # noqa: PLC0415

    k = (key or "").strip()
    words = set(normalize_question(question).split())
    if not k and not_own_link(question):
        return False, None  # a referrer's LinkedIn, a GitHub username: never the user's saved URL
    if words & _PAY_WORDS:
        words.add("salary")  # "Desired pay" names the salary field (:data:`_PAY_WORDS`)
    rows = [q for q in common_questions.catalog() if q.get("kit_key")]
    # A catalog row's own wording, or (for a field named in two words or more: notice_period) all its words.
    exact = [q["kit_key"] for q in rows if "{cc}" not in q["kit_key"] and (
        normalize_question(q["question"]) == normalize_question(question)
        or len(_field_words(q["kit_key"])) > 1 and _field_words(q["kit_key"]) <= words)]
    named = [q["kit_key"] for q in rows if _is_kit_key(q["kit_key"], k)]
    if named:
        return True, named[0]
    link = None if k else _contact_link(words)
    if link:
        return True, link
    family = is_country_specific(k, question) or bool(exact)
    if not family:
        return False, None
    if exact:
        return True, (exact[0] if len(set(exact)) == 1 else None)
    asked = _signals(words | set(normalize_question(k).split()))
    hits = []
    for q in rows:
        own = _signals(_field_words(q["kit_key"]))
        if own & asked and asked <= own | _signals(set(normalize_question(q["question"]).split())):
            hits.append(q["kit_key"])
    full = [h for h in hits if _field_words(h) <= words]
    most = max((len(_field_words(h)) for h in full), default=0)
    picked = [h for h in full if len(_field_words(h)) == most] if full else hits
    return True, (picked[0] if len(picked) == 1 else None)


def memory_note(topic: str, question: str, country: Optional[str]) -> Optional[str]:
    """Why a row in Memory's families (:func:`memory_question`) is never kept as a saved answer (owner
    2026-11-05, plan S2): Memory and the country pages are set by the user in Job360 itself, so the reason
    names the page to set it on. ``None`` for a row outside Memory's families."""
    family, template = memory_question(topic, question)
    if not family:
        return None
    where = common_questions.save_path({"kit_key": template}) if template else "Memory"
    per_country = template is None or "{cc}" in template
    page = f"your {country} page" if per_country and country else "your Memory page"
    return (f"a Memory fact ({where}), never kept as a saved answer: the user sets it on {page} "
            "in Job360 (Memory)")


def remember(user_id: str, rows: list[dict[str, str]], country: Optional[str]) -> dict[str, Any]:
    """Save each ``{topic, question, answer}`` as the user's approved answer (the user's own web press: the
    caller is a session-only route); ``country`` is the job's, named in a Memory row's reason.
    Upserts on topic. A row in Memory's families is never saved (:func:`memory_note`, plan S2): it is in
    ``not_saved`` with the page to set it on; Memory itself is not written. Every country-specific row
    (:func:`is_country_specific`) is in Memory's families, so what is saved is global. A per-job row is
    skipped. Returns ``{saved: [topic], not_saved: {topic: why}}``. All-or-nothing: a bad value writes
    nothing (422)."""
    from src.services.profile import edits as profile_edits  # noqa: PLC0415
    from src.services.profile import user_info  # noqa: PLC0415
    from src.services.profile.models import UserProfile  # noqa: PLC0415
    from src.services.profile.storage import load_profile, load_profile_with_overlay, save_profile  # noqa: PLC0415

    not_saved: dict[str, str] = {}
    keep = []
    for r in rows:
        if not str(r.get("answer") or "").strip() or not reusable(r["topic"], r["question"]):
            continue
        note = memory_note(r["topic"], r["question"], country)
        if note:
            not_saved[r["topic"]] = note
        else:
            keep.append(r)
    if not keep:
        return {"saved": [], "not_saved": not_saved}
    loaded, _ = load_profile_with_overlay(user_id)
    records = [dict(r) for r in (loaded or UserProfile()).user_info.answers or [] if isinstance(r, dict)]
    for r in keep:
        new = {"question": r["question"], "answer": r["answer"].strip(), "approved": True, "topic": r["topic"]}
        records = user_info.upsert_answer(records, new)
    value = profile_edits.validate_edit(user_info.USER_INFO_ANSWERS_PATH, records)
    if load_profile(user_id, with_overlay=False) is None:
        save_profile(UserProfile(), user_id, source_action="agent_edit")
    profile_edits.record_edits(
        user_id, profile_edits.WEB_ACTOR, [(user_info.USER_INFO_ANSWERS_PATH, value)],
        enforce_rate_limit=False, source=profile_edits.SOURCE_USER_SAID,
    )
    return {"saved": [r["topic"] for r in keep], "not_saved": not_saved}


def _key(row: dict[str, Any]) -> str:
    """The topic a questionnaire row is remembered under: its catalog topic, else its kit key."""
    return str(row.get("topic") or row.get("kit_key") or row["id"])


def _conflict_row(c: dict[str, Any], cc: str) -> dict[str, Any]:
    """A questionnaire row for a saved topic whose answers disagree (the same shape as a catalog row)."""
    return {
        "id": f"conflict.{c['topic']}", "group": "work", "question": c["question"], "country": cc,
        "kit_key": None, "topic": c["topic"], "answer": None, "source": None, "state": "missing",
        "verified": False, "label": "guessed", "seen": 0, "per_job": False, "save_to": "user_info.answers",
        "set_by": None, "set_at": None, "guess_source": None, "sensitive": False,
    }


def questionnaire(profile: Any, overlay: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``[{country, questions}]``: per saved country, up to :data:`PER_COUNTRY` rows still guessed or not
    known (``summary()``'s to_verify + missing), guessed first, then by how many forms ask it. A row the
    user already remembered is done: a global one for every country, a country-specific one
    (:func:`is_country_specific`) for its country only (``fits_country``). Read-only."""
    from src.services.profile.answer_match import conflicts, find_answer, topic_key  # noqa: PLC0415

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
        # Two saved answers to one topic that disagree (case-insensitive): asked, never picked. Shown first.
        for c in reversed(conflicts(saved, country=cc)):
            row = next((r for r in todo if topic_key(r["topic"]) == topic_key(c["topic"])), None)
            if row is None:
                row = _conflict_row(c, cc)
                todo.insert(0, row)
            row["conflict"] = c["values"]
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

"""The Needs you badge's answer flags must not redo the profile's work per open fill.

2026-11-12, production (Server-Timing ``b_flags``): on the owner's large account
``ready.answer_flags_for`` took ~1.5 s of the ~2 s badge on every signed-in page. Per fill it rebuilt
the kit (``kit.compute_answers``) and, per typed answer that matched a saved answer, rescanned every
saved answer for conflicts (``answer_match.conflicting_topics``, quadratic in the saved answers). Both
read only the one profile and the job's country (+ a company a saved answer is scoped to). Timing is
not asserted (it flakes): the CALL COUNT is, and the flags must equal the old per-fill reading.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.services.applications import kit, ready
from src.services.profile import answer_match
from src.services.profile.models import UserInfo, UserProfile

_SAVED = [
    {"question": "What is your notice period?", "answer": "One month", "approved": True, "topic": "notice_period"},
    {"question": "Notice period?", "answer": "Four weeks", "approved": True, "topic": "notice_period",
     "company": "Contoso"},
    {"question": "Do you need sponsorship?", "answer": "No", "approved": True, "topic": "sponsorship.GB.yes_no"},
    {"question": "Do you need sponsorship?", "answer": "Yes", "approved": True, "topic": "sponsorship.US.yes_no"},
    {"question": "Expected salary?", "answer": "50000", "approved": True, "topic": "salary_expectation"},
    {"question": "Expected salary?", "answer": "60000", "approved": True, "topic": "salary_expectation"},
    {"question": "Willing to travel?", "answer": "Yes", "approved": True, "topic": "travel"},
]


def _profile() -> tuple[UserProfile, list[dict[str, Any]]]:
    info = UserInfo(contact={"email": "a@example.com"}, equality={"gender": "prefer not to say"}, answers=_SAVED)
    overlay = [{"path": "user_info.answers", "set_by": "web", "set_at": "2026-11-01T10:00:00+00:00", "source": ""},
               {"path": "user_info.contact", "set_by": "agent:x", "set_at": "2026-11-01T10:00:00+00:00",
                "source": "user_said"}]
    return UserProfile(user_info=info), overlay


def _fills() -> dict[int, dict[str, Any]]:
    typed = [
        {"question": "What is your notice period?", "answer": "Two weeks", "source": "approved", "key": "notice_period"},
        {"question": "Do you need sponsorship?", "answer": "No", "source": "approved", "key": "sponsorship.GB.yes_no"},
        {"question": "Expected salary?", "answer": "50000", "source": "guessed", "key": "salary_expectation"},
        {"question": "Willing to travel?", "answer": "Yes", "source": "memory", "key": "travel"},
        {"question": "Email", "answer": "a@example.com", "source": "memory", "key": "contact.email"},
        {"question": "Do you have a criminal record?", "answer": "", "source": "guessed", "key": "criminal"},
    ]
    companies = ["Contoso", "Fabrikam", "Initech", "Globex", "Umbrella", "Hooli"]
    return {
        app_id: {"job_country": "GB" if app_id % 2 else "us", "job_company": companies[app_id - 1],
                 "filled_at": "2026-11-05T10:00:00+00:00", "answers": [dict(a) for a in typed]}
        for app_id in range(1, 7)
    }


_APPROVALS = {2: [{"question": "Willing to travel?", "answer": "No", "approved": True, "job_only": True,
                   "recorded_at": "2026-11-06T10:00:00+00:00", "topic": "travel"}]}


def _per_fill(profile: UserProfile, overlay: list[dict[str, Any]], fills: dict[int, dict[str, Any]]) -> dict:
    """The old reading: everything rebuilt for each fill and each answer (no shared memo)."""
    saved = {str(r["path"]): (r.get("set_by"), r.get("set_at")) for r in overlay}
    out = {}
    for app_id, f in fills.items():
        country = (str(f.get("job_country") or "").strip().upper()) or None
        blocks, _missing = kit.compute_answers(profile, overlay, country)
        have = {i["key"]: i for block in blocks.values() for i in block}
        mine = ready.with_job_approvals(profile, _APPROVALS.get(app_id, []))
        company = ready._company_key(str(f.get("job_company") or ""))  # noqa: SLF001
        flags = []
        for a in f["answers"]:  # one answer per call: nothing can be shared between answers
            flags += ready._answer_rows([a], have, saved, mine, company, f["filled_at"], country)[1]  # noqa: SLF001
        out[app_id] = flags
    return out


@pytest.fixture
def _no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    async def approvals(_db: Any, _user_id: str, app_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        return {i: _APPROVALS[i] for i in app_ids if i in _APPROVALS}

    async def uploads(_db: Any, _user_id: str, _app_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        return {}

    monkeypatch.setattr(ready, "job_approvals_many", approvals)
    monkeypatch.setattr(ready.uploads_service, "uploads_since_last_receipt_many", uploads)


def _counted(monkeypatch: pytest.MonkeyPatch, module: Any, name: str) -> list[int]:
    calls: list[int] = []
    real = getattr(module, name)

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, counting)
    return calls


@pytest.mark.usefixtures("_no_db")
async def test_six_fills_in_two_countries_read_the_kit_and_conflicts_once_per_country(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, overlay = _profile()
    kits = _counted(monkeypatch, kit, "compute_answers")
    scans = _counted(monkeypatch, answer_match, "conflicting_topics")
    flags = await ready.answer_flags_for(None, "u1", _fills(), loaded=(profile, overlay))  # type: ignore[arg-type]
    assert len(flags) == 6
    assert len(kits) <= 2, f"compute_answers ran {len(kits)} times for 6 fills in 2 countries"
    # GB and US, plus Contoso (the one employer a saved answer is scoped to): never once per answer or fill.
    assert len(scans) <= 3, f"conflicting_topics ran {len(scans)} times for 6 fills"


@pytest.mark.usefixtures("_no_db")
async def test_flags_equal_the_per_fill_reading() -> None:
    profile, overlay = _profile()
    fills = _fills()
    flags = await ready.answer_flags_for(None, "u1", fills, loaded=(profile, overlay))  # type: ignore[arg-type]
    expected = _per_fill(profile, overlay, fills)
    assert flags == expected
    codes = {f["code"] for v in flags.values() for f in v}
    assert {"changed", "guessed", "sensitive", "blank"} <= codes  # value-presence: real flags, not empty lists

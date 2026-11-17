"""The saved-answer lookups read the records ONCE, never once per topic / per record / per country.

2026-11-17, production (``Server-Timing`` on ``GET /api/needs-you-count``, owner account: 72 saved answers, 35
topics, 29 countries): ``b_derive`` 188-243 ms, ``answer_once`` 186-211 ms, ``b_flags`` ~100 ms. ~95% of it
was three scans: ``answer_match.conflicts`` (``_candidates`` once per topic, each rescanning every record),
``superseded`` / ``find_answer(topic=)`` (a full scan per call) and ``ask_once.memory_question`` (the whole
question catalog normalised on every call, ~1,560 calls a request). The fix is a topic index the caller
builds once (``answer_match.build_index`` -> ``index=``) and a cached ``memory_question``.

The CORE proof is equivalence: every lookup with the index equals the same lookup WITHOUT it (the old code
path, still there, is the oracle), whole objects compared, for every country and company. The work-count
tests then show the records are scanned a fixed number of times however many topics there are. Timing is not
asserted (it flakes); the call count is. No value here depends on today's date.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable

import pytest

from src.services.applications import derive_memo, kit, ready
from src.services.profile import answer_match, ask_once, common_questions
from src.services.profile.models import UserInfo, UserProfile
from tests.test_needs_you_derive_memo import _body, _seed, _store

COUNTRIES = [None, "GB", "gb", "US", "DE", "NL", "SE", "AE", "IN", "CA", "FR"]
COMPANY_NAMES = ["Contoso", "Fabrikam", "Initech", "Globex", "Umbrella"]


def _rec(question: str, answer: str, topic: str | None, **extra: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {"question": question, "answer": answer, "approved": True,
                           "saved_at": "2026-11-01T10:00:00+00:00", **extra}
    if topic is not None:
        rec["topic"] = topic
    return rec


def owner_answers() -> list[dict[str, Any]]:
    """72 saved answers shaped like the owner's (fake values): 35 topics, company-scoped (5 companies), country
    bound (by topic and by ``country``), aliased, topic-less, duplicates with different ``saved_at``, a conflict,
    a job_only, consent, unapproved, case / space variants of one topic, and records that are not dicts."""
    out: list[dict[str, Any]] = []
    for i in range(20):  # plain topics, one record each
        out.append(_rec(f"Plain question {i}?", f"plain {i}", f"plain.t{i:02d}"))
    for i in range(6):  # aliased: matched by an alias wording too
        out.append(_rec(f"Aliased question {i}?", f"alias {i}", f"aliased.t{i}",
                        aliases=[f"Other wording {i}", f"  Third WORDING {i}!! "]))
    out += [  # country-bound topics
        _rec("Do you need sponsorship?", "No", "sponsorship.GB.yes_no"),
        _rec("Do you need sponsorship?", "Yes", "sponsorship.US.yes_no"),
        _rec("Right to work?", "Yes", "right_to_work.DE.status"),
        _rec("Sponsorship anywhere?", "No", "sponsorship.remote.any"),
        _rec("Sponsorship, no country?", "No", "sponsorship.yes_no"),
        _rec("Sponsorship, no country?", "Yes", "sponsorship.yes_no", company="Contoso"),  # that employer's own
    ]
    out += [  # a record bound to a country by the user's "remember" press, and the same topic unbound
        _rec("Notice period?", "1 month", "notice_period"),
        _rec("Notice period?", "2 weeks", "notice_period", country="GB"),
        _rec("Notice period?", "6 weeks", "notice_period", country="US"),
        _rec("Visa status?", "Citizen", "visa_status", country="gb"),
    ]
    for i, name in enumerate(COMPANY_NAMES):  # company-scoped, over the same topics as unscoped ones
        out.append(_rec(f"Plain question {i}?", f"{name} wording {i}", f"plain.t{i:02d}", company=name))
        out.append(_rec(f"Scoped only {i}?", f"scoped {i}", f"scoped.t{i}", company=name))
    out.append(_rec("Plain question 7?", "Contoso second", "plain.t07", company="Contoso"))
    out.append(_rec("Plain question 7?", "Contoso third", "plain.t07", company="Contoso"))  # scoped twins: a conflict
    out += [  # duplicates of one topic, different saved_at: same answer (no conflict) and different (conflict)
        _rec("Willing to travel?", "Yes", "travel", saved_at="2026-10-01T10:00:00+00:00"),
        _rec("Willing to travel?", "Yes", "travel", saved_at="2026-11-02T10:00:00+00:00"),
        _rec("Expected salary?", "50000", "salary_expectation", saved_at="2026-10-01T10:00:00+00:00"),
        _rec("What salary do you expect?", "60000", "salary_expectation", saved_at="2026-11-02T10:00:00+00:00"),
        _rec("Favourite colour?", "Green", "COLOUR"),
        _rec("Favourite colour?", "Blue", "colour"),  # case twin: one topic, two answers
        _rec("Favourite fruit?", "Apple", "  Fruit "),  # space padded
    ]
    out += [  # topic-less (question wording only)
        _rec("How did you hear about us?", "A friend", None),
        _rec("Anything else?", "No", None),
        _rec("Anything else?", "Nothing", None),
        _rec("What is your pronoun?", "they", ""),
    ]
    out += [  # never matched / never usable
        _rec("Consent to data processing?", "Yes", "consent.processing"),
        _rec("I certify this is true", "Yes", "certify.true"),
        _rec("Unapproved question?", "x", "unapproved.t", approved=False),
        _rec("Unapproved question 2?", "y", "unapproved.t2", approved="yes"),
        _rec("Job only question?", "Only here", "job.only", job_only=True),
        _rec("Notice period?", "job only notice", "notice_period", job_only=True),
        _rec("Empty answer?", "", "empty.t"),
    ]
    out += [None, "junk", 3]  # type: ignore[list-item]  # the list is user data: any entry can be anything
    return out


def profile_and_overlay() -> tuple[UserProfile, list[dict[str, Any]]]:
    info = UserInfo(contact={"email": "a@example.com"}, answers=owner_answers())
    overlay = [{"path": "user_info.answers", "set_by": "web", "set_at": "2026-11-01T10:00:00+00:00", "source": ""}]
    return UserProfile(user_info=info), overlay


def company_keys() -> list[str | None]:
    return [None, *(kit.normalized_company_key(n) for n in COMPANY_NAMES[:2]), kit.normalized_company_key("Nobody Ltd")]


def _all_topics(answers: list[Any]) -> list[str]:
    topics = {str(r.get("topic")) for r in answers if isinstance(r, dict) and r.get("topic") is not None}
    return sorted(topics | {"missing.topic", "  TRAVEL ", "Notice_Period", " "})


def _all_questions(answers: list[Any]) -> list[str]:
    qs: set[str] = {"???", "", "Never asked this", "Other wording 2", "third wording 3"}
    for r in answers:
        if isinstance(r, dict):
            qs.add(str(r.get("question") or ""))
            qs.update(str(a) for a in (r.get("aliases") or []))
    return sorted(qs)


class _Oracle:
    """Every new path off: the readings the code had before the index (``index=None``, uncached memory_question)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.mp = monkeypatch

    def __enter__(self) -> _Oracle:
        def none(*_a: Any, **_k: Any) -> None:
            return None

        raw = ask_once._memory_question  # noqa: SLF001 - the function without its cache
        self.mp.setattr(answer_match, "build_index", none)
        self.mp.setattr(kit, "build_index", none)
        self.mp.setattr(ask_once, "memory_question", raw)
        self.mp.setattr(kit, "memory_question", raw)
        self.mp.setattr(derive_memo, "approved_memo", lambda _profile: None)
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.mp.undo()


def test_every_lookup_with_the_index_equals_the_lookup_without_it() -> None:
    answers = owner_answers()
    topics, questions = _all_topics(answers), _all_questions(answers)
    checked = {"topic": 0, "question": 0, "both": 0, "conflicts": 0, "superseded": 0}
    for ck in company_keys():
        for cc in COUNTRIES:
            idx = answer_match.build_index(answers, company_key=ck, country=cc)
            assert answer_match.conflicts(answers, country=cc, company_key=ck, index=idx) == \
                answer_match.conflicts(answers, country=cc, company_key=ck)
            assert answer_match.conflicting_topics(answers, country=cc, company_key=ck, index=idx) == \
                answer_match.conflicting_topics(answers, country=cc, company_key=ck)
            checked["conflicts"] += len(answer_match.conflicts(answers, country=cc, company_key=ck))
            for rec in answers:
                if isinstance(rec, dict):
                    assert answer_match.superseded(rec, answers, country=cc, company_key=ck, index=idx) == \
                        answer_match.superseded(rec, answers, country=cc, company_key=ck)
                    checked["superseded"] += int(answer_match.superseded(rec, answers, country=cc, company_key=ck))
            kw: dict[str, Any] = {"company_key": ck, "country": cc}
            for t in topics:  # topic only
                assert answer_match.resolve_answer(answers, topic=t, index=idx, **kw) == \
                    answer_match.resolve_answer(answers, topic=t, **kw)
                assert answer_match.find_answer(answers, topic=t, index=idx, **kw) == \
                    answer_match.find_answer(answers, topic=t, **kw)
                checked["topic"] += int(answer_match.find_answer(answers, topic=t, **kw) is not None)
            for q in questions:  # question only, and topic then question
                assert answer_match.resolve_answer(answers, question=q, index=idx, **kw) == \
                    answer_match.resolve_answer(answers, question=q, **kw)
                checked["question"] += int(answer_match.find_answer(answers, question=q, **kw) is not None)
                for t in topics[::7]:
                    assert answer_match.resolve_answer(answers, topic=t, question=q, index=idx, **kw) == \
                        answer_match.resolve_answer(answers, topic=t, question=q, **kw)
                    checked["both"] += int(answer_match.find_answer(answers, topic=t, question=q, **kw) is not None)
            assert answer_match.resolve_answer(answers, index=idx, **kw) == answer_match.resolve_answer(answers, **kw)
    # value-presence (rule #21): the shape really exercised every branch, not a list of Nones
    assert all(v > 0 for v in checked.values()), checked


def test_an_index_for_another_list_country_or_company_is_ignored_not_trusted() -> None:
    answers = owner_answers()
    ck = company_keys()[1]
    gb = answer_match.build_index(answers, company_key=ck, country="GB")
    for cc, key in (("US", ck), ("GB", None), (None, ck)):
        assert answer_match.conflicts(answers, country=cc, company_key=key, index=gb) == \
            answer_match.conflicts(answers, country=cc, company_key=key)
        assert answer_match.find_answer(answers, topic="notice_period", country=cc, company_key=key, index=gb) == \
            answer_match.find_answer(answers, topic="notice_period", country=cc, company_key=key)
    other = list(answers)  # an equal list that is not the one the index read
    assert answer_match.find_answer(other, topic="travel", company_key=ck, country="GB", index=gb) == \
        answer_match.find_answer(other, topic="travel", company_key=ck, country="GB")


def test_the_candidates_keep_the_old_order_and_the_old_level_rules() -> None:
    answers = owner_answers()
    idx = answer_match.build_index(answers, company_key=None, country="GB")
    got = idx.for_topic("Notice_Period")
    assert got == answer_match._candidates(  # noqa: SLF001
        answers, topic="notice_period", question=None, company_key=None, country="GB")
    assert [r["answer"] for r in got] == ["2 weeks"]  # the GB-bound one outranks the unbound and the US-bound
    assert idx.for_topic("no.such.topic") == []
    got[:] = []  # a caller changing the list it got never changes the index
    assert idx.for_topic("notice_period") != []


def test_compute_answers_equals_the_old_reading_for_every_country_and_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, overlay = profile_and_overlay()
    places = [(cc, ck) for ck in company_keys() for cc in COUNTRIES]
    new = [kit._compute_answers(profile, overlay, cc, ck) for cc, ck in places]  # noqa: SLF001
    with _Oracle(monkeypatch):
        old = [kit._compute_answers(profile, overlay, cc, ck) for cc, ck in places]  # noqa: SLF001
    assert new == old
    texts = [i for (blocks, _m), in ((n,) for n in new) for i in blocks["approved_text"]]
    assert texts and any(m for _b, m in new), "the shape produced no approved_text / missing: nothing was compared"
    assert any("ambiguous" in json.dumps(m) for _b, m in new)  # a conflict reached `missing`


def test_the_questionnaire_equals_the_old_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    profile, overlay = profile_and_overlay()
    profile.user_info.right_to_work = {"countries": [{"country": "GB", "work_authorization": "citizen"}]}
    extra = ["us", "DE", "NL", "SE", "AE", "IN", "CA", "FR"]
    new = ask_once.questionnaire(profile, overlay, extra)
    with _Oracle(monkeypatch):
        old = ask_once.questionnaire(profile, overlay, extra)
    assert new == old
    assert len(new) == 9 and sum(len(c["questions"]) for c in new) > 10  # real rows in every country
    assert any("conflict" in q for c in new for q in c["questions"])


def test_approved_record_with_a_memo_equals_the_old_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    profile, _overlay = profile_and_overlay()
    questions = _all_questions(owner_answers())
    topics: list[str | None] = [None, *_all_topics(owner_answers())[::3]]
    memo: dict[Any, Any] = {}
    got = hits = 0
    for ck in company_keys():
        for cc in COUNTRIES[:6]:
            for q in questions[::5]:
                for t in topics[::2]:
                    new = ready._approved_record(profile, q, ck, t, cc, memo=memo)  # noqa: SLF001
                    old = ready._approved_record(profile, q, ck, t, cc)  # noqa: SLF001
                    assert new == old, (q, ck, t, cc)
                    got += 1
                    hits += int(old is not None)
    assert hits > 100, f"only {hits} of {got} lookups found a record: the comparison proved little"


@pytest.mark.asyncio
async def test_the_whole_badge_body_equals_the_old_reading(
    authenticated_async_context: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = authenticated_async_context.fixture_user_id
    _store(uid)
    async with authenticated_async_context() as client:
        await _seed(client, 0, 6)
        new = await _body(client)
        with _Oracle(monkeypatch):
            old = await _body(client)
    assert json.dumps(new, sort_keys=True) == json.dumps(old, sort_keys=True)
    assert new["answer_once"] >= 1 and new["by_kind"], new  # a real reading, not an empty one


def _spy(monkeypatch: pytest.MonkeyPatch, module: Any, name: str) -> list[int]:
    calls: list[int] = []
    real = getattr(module, name)

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, counting)
    return calls


def _with_topics(n_topics: int, n_records: int = 60) -> list[dict[str, Any]]:
    return [_rec(f"Question {i}?", f"answer {i}", f"topic.t{i % n_topics}", saved_at=f"2026-11-{i % 28 + 1:02d}")
            for i in range(n_records)]


@pytest.mark.parametrize("n_topics", [4, 60])
def test_a_compute_answers_scans_the_records_a_fixed_number_of_times(
    monkeypatch: pytest.MonkeyPatch, n_topics: int,
) -> None:
    """The scan's per-record country filter (``answer_match.fits_country``, run on every record a scan reads)
    ran ~topics x records times; it now runs at most once per record, whatever the number of topics. (kit's own
    per-record check imports the name directly and is not counted here.)"""
    answers = _with_topics(n_topics)
    info = UserInfo(contact={"email": "a@example.com"}, answers=answers)
    scans = _spy(monkeypatch, answer_match, "fits_country")
    kit._compute_answers(UserProfile(user_info=info), [], "GB", None)  # noqa: SLF001
    assert len(scans) <= len(answers), f"{len(scans)} record reads for {len(answers)} records, {n_topics} topics"


def test_the_questionnaire_builds_one_index_per_country(monkeypatch: pytest.MonkeyPatch) -> None:
    profile, overlay = profile_and_overlay()
    builds = _spy(monkeypatch, answer_match, "build_index")
    scans = _spy(monkeypatch, answer_match, "_is_usable")  # every record a scan reads goes through it
    countries = ["US", "DE", "NL"]
    groups = ask_once.questionnaire(profile, overlay, countries)
    assert len(builds) == len(groups) == 3
    # One read of every record per index: the questionnaire's one per country, plus the kit runs
    # common_questions makes (one per country and one with none); never one scan per question or topic.
    assert len(scans) <= (2 * len(groups) + 1) * len(profile.user_info.answers)


def test_memory_question_normalises_the_catalog_once_per_distinct_question(monkeypatch: pytest.MonkeyPatch) -> None:
    """``memory_question(key, question)`` takes no user data (its signature is the proof) and reads only module
    constants and the lru-cached catalog, so its answer is the same for every user and every call."""
    assert list(inspect.signature(ask_once.memory_question).parameters) == ["key", "question"]
    ask_once._memory_question_cached.cache_clear()  # noqa: SLF001
    reads = _spy(monkeypatch, common_questions, "catalog")
    pairs = [(None, "What is your notice period?"), ("notice_period", "Notice period"),
             (None, "Do you need visa sponsorship?")]
    first = [ask_once.memory_question(k, q) for k, q in pairs]
    for _ in range(30):
        assert [ask_once.memory_question(k, q) for k, q in pairs] == first
    assert len(reads) <= len(pairs) * 2, f"the catalog was read {len(reads)} times for {len(pairs)} questions"
    after_first = len(reads)
    ask_once.memory_question(None, "What is your notice period?")
    assert len(reads) == after_first


def test_a_needs_you_scope_starts_with_an_empty_memory_question_cache() -> None:
    """The cache serves one request (``derive_memo.scope``), like the memo: a request never reads what an earlier
    one cached, so its cost does not depend on which requests ran before it."""
    ask_once.memory_question(None, "What is your notice period?")
    assert ask_once._memory_question_cached.cache_info().currsize >= 1  # noqa: SLF001
    with derive_memo.scope():
        assert ask_once._memory_question_cached.cache_info().currsize == 0  # noqa: SLF001
        ask_once.memory_question(None, "What is your notice period?")
        assert ask_once._memory_question_cached.cache_info().currsize == 1  # noqa: SLF001


def test_memory_question_is_never_mixed_between_users(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two profiles with different saved answers get the same, user-free answer; neither profile reaches it."""
    ask_once._memory_question_cached.cache_clear()  # noqa: SLF001
    raw = ask_once._memory_question  # noqa: SLF001
    a, b = profile_and_overlay()[0], UserProfile(user_info=UserInfo(answers=[_rec("Q?", "A", "t")]))
    seen = []
    for profile in (a, b, a):
        got = [ask_once.memory_question(str(r.get("topic") or ""), str(r["question"]))
               for r in profile.user_info.answers if isinstance(r, dict)]
        assert got == [raw(str(r.get("topic") or ""), str(r["question"]))
                       for r in profile.user_info.answers if isinstance(r, dict)]
        seen.append(got)
    assert seen[0] == seen[2]
    # kit reaches it through the same function: two users' kits differ only by their own answers
    cc_a = kit._compute_answers(a, [], "GB", None)  # noqa: SLF001
    cc_b = kit._compute_answers(b, [], "GB", None)  # noqa: SLF001
    assert cc_a != cc_b and [i["key"] for i in cc_b[0]["approved_text"]] == ["Q?"]


def test_approved_record_builds_its_lists_once_per_memo(monkeypatch: pytest.MonkeyPatch) -> None:
    profile, _overlay = profile_and_overlay()
    builds = _spy(monkeypatch, kit, "_records")
    memo: dict[Any, Any] = {}
    questions = [f"Plain question {i}?" for i in range(20)]
    for q in questions:
        ready._approved_record(profile, q, None, None, "GB", memo=memo)  # noqa: SLF001
    assert len(builds) == 1, f"the record lists were built {len(builds)} times for {len(questions)} calls, one memo"
    other = UserProfile(user_info=UserInfo(answers=[_rec("Q?", "A", "t")]))  # another profile in the same memo
    assert ready._approved_record(other, "Q?", None, None, "GB", memo=memo) is not None  # noqa: SLF001
    builds.clear()
    for q in questions[:3]:
        ready._approved_record(profile, q, None, None, "GB")  # noqa: SLF001
    assert len(builds) == 3  # no memo: exactly as before, a reading per call


def test_the_index_is_built_once_per_memo_country_and_company(monkeypatch: pytest.MonkeyPatch) -> None:
    profile, _overlay = profile_and_overlay()
    builds = _spy(monkeypatch, answer_match, "build_index")
    memo: dict[Any, Any] = {}
    for i in range(30):
        ready._approved_record(profile, f"Plain question {i % 20}?", None, None, "GB", memo=memo)  # noqa: SLF001
        ready._approved_record(profile, f"Plain question {i % 20}?", None, None, "US", memo=memo)  # noqa: SLF001
    assert len(builds) == 2, f"{len(builds)} indexes for two countries in one memo"


_FUNCS: list[Callable[..., Any]] = [answer_match.resolve_answer, answer_match.find_answer, answer_match.conflicts,
                                    answer_match.conflicting_topics, answer_match.superseded]


@pytest.mark.parametrize("fn", _FUNCS, ids=lambda f: f.__name__)
def test_index_is_optional_and_keyword_only(fn: Callable[..., Any]) -> None:
    p = inspect.signature(fn).parameters["index"]
    assert p.default is None and p.kind is inspect.Parameter.KEYWORD_ONLY

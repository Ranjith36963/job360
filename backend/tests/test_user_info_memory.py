"""S1 — user info MEMORY (owner decision 2026-10-08, three stores).

Six memory paths under the ``user_info.*`` head (their own
``user_profiles.user_info`` column, one writer ``storage.save_user_info``,
overlaid by ``profile_edits`` rows) plus one normal preference,
``preferences.salary_by_country``. Pure-validator tests need no database; the
API tests go through the real ``PATCH /api/profile`` door. Helpers are copied,
never imported from another test module (a cross-module fixture import breaks
schema isolation).
"""
from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile import edits
from src.services.profile.edits import ProfileEditError, validate_edit
from src.services.profile.models import (
    OVERLAY_ONLY_PREFERENCE_FIELDS,
    UserInfo,
    UserPreferences,
)

CONTACT = "user_info.contact"
RTW = "user_info.right_to_work"
LOGI = "user_info.logistics"
LANGS = "user_info.languages"
EQ = "user_info.equality"
ANSWERS = "user_info.answers"
SALARY = "preferences.salary_by_country"
SIX = (CONTACT, RTW, LOGI, LANGS, EQ, ANSWERS)
SEVEN = (*SIX, SALARY)

DOB = "1990-04-17"
PHONE = "+44 7700 900123"


def _reject(path: str, value: Any) -> str:
    """The 422 detail for a value the validator must refuse."""
    with pytest.raises(ProfileEditError) as info:
        validate_edit(path, value)
    assert info.value.status_code == 422
    return info.value.detail


def _sal(**kw: Any) -> dict[str, Any]:
    return {"country": "ae", "amount": 25000, "currency": "aed", "period": "month", **kw}


# ═══════════════════════════════════════════════════════════════════════════
# Paths, declared fields, guards
# ═══════════════════════════════════════════════════════════════════════════


def test_the_seven_paths_are_editable_and_the_memory_head_matches_the_dataclass():
    assert set(SEVEN) <= set(edits.editable_paths())
    assert set(SIX) == {f"user_info.{f.name}" for f in dataclasses.fields(UserInfo)}
    assert not [p for p in edits.editable_paths() if p.startswith("preferences.user_info")]
    assert "salary_by_country" in UserPreferences.__dataclass_fields__
    assert "salary_by_country" not in OVERLAY_ONLY_PREFERENCE_FIELDS, "salary is a normal web-owned preference"


def test_guard_every_overlay_only_field_is_a_declared_editable_field():
    declared = set(UserPreferences.__dataclass_fields__)
    paths = set(edits.editable_paths())
    assert OVERLAY_ONLY_PREFERENCE_FIELDS == {"daily_check", "check_every"}
    for name in OVERLAY_ONLY_PREFERENCE_FIELDS:
        assert name in declared, f"{name} is overlay-only but not a UserPreferences field"
        assert f"preferences.{name}" in paths, f"{name} is overlay-only but not an editable path"


# ═══════════════════════════════════════════════════════════════════════════
# Validator: shapes, normalisation, dropping empties
# ═══════════════════════════════════════════════════════════════════════════


def test_round_trip_all_six_normalises():
    contact = validate_edit(CONTACT, {
        "email": "  ada@example.com ", "phone": PHONE, "address_lines": ["1 High St", "", "Flat\x07 2"],
        "address_city": "London", "address_postcode": "N1 9GU", "address_country": "gb",
        "date_of_birth": DOB, "residence_city": "Berlin", "residence_country": "de",
        "legal_first_name": "Ada", "legal_last_name": "Lovelace", "preferred_name": "Ada\x00",
    })
    assert contact == {
        "email": "ada@example.com", "phone": PHONE, "address_lines": ["1 High St", "Flat 2"],
        "address_city": "London", "address_postcode": "N1 9GU", "address_country": "GB",
        "date_of_birth": DOB, "residence_city": "Berlin", "residence_country": "DE",
        "legal_first_name": "Ada", "legal_last_name": "Lovelace", "preferred_name": "Ada",
    }
    rtw = validate_edit(RTW, {
        "countries": [{
            "country": "de", "work_authorization": "Visa", "needs_sponsorship": False,
            "visa_type": "Blue Card", "visa_expires": "2027-03",
        }],
        "citizenship": ["in", "IN", "gb"], "sanctions_country_citizen": False,
    })
    assert rtw == {
        "countries": [{
            "country": "DE", "work_authorization": "visa", "needs_sponsorship": False,
            "visa_type": "Blue Card", "visa_expires": "2027-03",
        }],
        "citizenship": ["IN", "GB"], "sanctions_country_citizen": False,
    }, "false is a real answer and must be kept"
    logi = validate_edit(LOGI, {
        "notice_period": "1 month", "earliest_start": "2026-12-01",
        "countries": [{
            "country": "de", "willing_to_relocate": False, "relocate_where": "Berlin\x00 only",
            "travel_ok_pct": 0, "driving_licence": True, "driving_licence_country": "in",
        }],
    })
    assert logi == {
        "notice_period": "1 month", "earliest_start": "2026-12-01",
        "countries": [{
            "country": "DE", "willing_to_relocate": False, "relocate_where": "Berlin only",
            "travel_ok_pct": 0, "driving_licence": True, "driving_licence_country": "IN",
        }],
    }, "false and 0 are real answers and must be kept"
    assert validate_edit(LANGS, [{"language": " French ", "level": "FLUENT"}]) == [
        {"language": "French", "level": "fluent"}
    ]
    assert validate_edit(EQ, {"gender": "Prefer not to say", "veteran": "No"}) == {
        "gender": "Prefer not to say", "veteran": "No",
    }
    answers = validate_edit(ANSWERS, [{"question": "Why us?\t", "answer": "Line one\r\nLine\x07 two"}])
    assert answers[0]["question"] == "Why us?"
    assert answers[0]["answer"] == "Line one\nLine two", "an answer keeps its newlines, loses control chars"
    assert answers[0]["approved"] is False
    assert set(answers[0]) == {"question", "answer", "approved", "recorded_at"}
    assert answers[0]["recorded_at"], "stamped now when absent"


def test_empty_answers_are_dropped_and_an_empty_whole_value_is_nothing():
    assert validate_edit(CONTACT, {"email": "", "phone": None, "address_lines": [], "address_city": "  "}) == {}
    assert validate_edit(CONTACT, {}) == {}
    assert validate_edit(RTW, {"countries": [], "citizenship": []}) == {}
    assert validate_edit(LOGI, {"countries": [], "notice_period": ""}) == {}
    assert validate_edit(LANGS, []) == []
    assert validate_edit(EQ, {"gender": ""}) == {}
    assert validate_edit(ANSWERS, []) == []
    only_country = validate_edit(
        LOGI, {"countries": [{"country": "de", "relocate_where": "", "driving_licence": None}]}
    )
    assert only_country == {"countries": [{"country": "DE"}]}, "a record with only a country is allowed"
    assert validate_edit(CONTACT, None) is None, "null clears the path"
    assert validate_edit(SALARY, []) == []


def test_name_keys_round_trip_and_are_lines():
    out = validate_edit(CONTACT, {"legal_first_name": "Ada", "legal_last_name": "L", "preferred_name": "A"})
    assert out == {"legal_first_name": "Ada", "legal_last_name": "L", "preferred_name": "A"}
    assert "PROFILE_EDIT_MAX_ITEM_CHARS" in _reject(
        CONTACT, {"legal_last_name": "x" * (settings.PROFILE_EDIT_MAX_ITEM_CHARS + 1)}
    )


def test_recorded_at_and_approved_are_kept_as_sent():
    out = validate_edit(ANSWERS, [{
        "question": "q", "answer": "a", "approved": True, "recorded_at": "2026-10-01T09:00:00+00:00",
    }])
    assert out[0]["approved"] is True and out[0]["recorded_at"] == "2026-10-01T09:00:00+00:00"
    assert "future" in _reject(ANSWERS, [{"question": "q", "answer": "a", "recorded_at": "2999-01-01"}])
    assert "recorded_at" in _reject(ANSWERS, [{"question": "q", "answer": "a", "recorded_at": "not a date"}])


# ═══════════════════════════════════════════════════════════════════════════
# Validator: refusals
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("path", "value", "allowed_word"),
    [
        (CONTACT, {"mobile": "1"}, "email"),
        (RTW, {"salary": 1}, "citizenship"),
        (LOGI, {"salary": 1}, "notice_period"),
        (LANGS, [{"language": "French", "level": "native", "x": 1}], "language"),
        (EQ, {"religion": "x"}, "gender"),
        (ANSWERS, [{"question": "q", "answer": "a", "extra": 1}], "approved"),
        (SALARY, [_sal(extra=1)], "period"),
    ],
)
def test_unknown_key_is_a_422_naming_the_allowed_keys(path, value, allowed_word):
    detail = _reject(path, value)
    assert "unknown key" in detail and "allowed keys: " in detail and allowed_word in detail


def test_nested_unknown_key_names_the_nested_location():
    detail = _reject(RTW, {"countries": [{"country": "de", "foo": 1}]})
    assert detail.startswith("user_info.right_to_work.countries[0]: unknown key 'foo' — allowed keys: country,")
    detail = _reject(LOGI, {"countries": [{"country": "de", "foo": 1}]})
    assert "user_info.logistics.countries[0]: unknown key 'foo'" in detail


def test_unknown_key_name_is_truncated_and_control_chars_stripped():
    detail = _reject(CONTACT, {"x" * 100 + "\n\r\x07evil": "1"})
    assert "x" * 60 in detail and "x" * 61 not in detail
    assert "\n" not in detail and "\r" not in detail and "\x07" not in detail and "evil" not in detail


def test_salary_target_and_minimums_are_refused_in_the_memory():
    assert "unknown key 'salary_target'" in _reject(
        RTW, {"countries": [{"country": "de", "salary_target": {"amount": 1, "currency": "EUR"}}]}
    )
    assert "unknown key 'salary_minimum'" in _reject(LOGI, {"countries": [{"country": "de", "salary_minimum": 1}]})
    assert "unknown key 'salary_minimum'" in _reject(SALARY, [_sal(salary_minimum=50000)])
    assert "unknown key 'minimum'" in _reject(SALARY, [_sal(minimum=50000)])


def test_whole_value_of_the_wrong_type_is_refused():
    assert "must be an object with keys" in _reject(CONTACT, [])
    assert "must be an object with keys" in _reject(RTW, ["DE"])
    assert "must be a list of objects with keys" in _reject(RTW, {"countries": {}})
    assert "must be a list of objects with keys" in _reject(LANGS, {})
    assert "must be an object with keys" in _reject(LANGS, ["x"])
    assert "must be a list of objects with keys" in _reject(ANSWERS, "text")
    assert "must be a list of objects with keys" in _reject(SALARY, {"country": "DE"})


def test_type_errors():
    assert "must be a string, got int" in _reject(CONTACT, {"address_city": 5})
    assert "must be a boolean, got str" in _reject(RTW, {"countries": [{"country": "de", "needs_sponsorship": "no"}]})
    for bad in (101, -1, 50.5, True, "50"):
        assert "travel_ok_pct must be a whole number 0-100" in _reject(
            LOGI, {"countries": [{"country": "de", "travel_ok_pct": bad}]}
        )
    assert "must be a string, got list" in _reject(EQ, {"gender": ["x"]})
    assert "must be a boolean" in _reject(RTW, {"sanctions_country_citizen": "no"})
    assert "must be a boolean" in _reject(LOGI, {"countries": [{"country": "de", "driving_licence": 1}]})


def test_line_too_long_names_the_setting():
    detail = _reject(CONTACT, {"address_city": "x" * (settings.PROFILE_EDIT_MAX_ITEM_CHARS + 1)})
    assert "user_info.contact.address_city exceeds the 200-character limit (PROFILE_EDIT_MAX_ITEM_CHARS)" in detail


@pytest.mark.parametrize("email", ["nope", "a@b", "a b@c.com", "@x.com", "a@@b.com"])
def test_email_must_look_like_one(email):
    assert "is not an email address (expected name@domain)" in _reject(CONTACT, {"email": email})


@pytest.mark.parametrize("phone", ["12345", "+1 (555) 123-4567 ext 9", "abcdefgh", "1" * 16, "07700 900123 #"])
def test_phone_rules(phone):
    assert "phone must be 6-15 digits, with only + ( ) - . and spaces" in _reject(CONTACT, {"phone": phone})


@pytest.mark.parametrize("dob", ["1990-13-01", "1990-02-30", "17/04/1990", "2999-01-01", "1990-4-17"])
def test_date_of_birth_must_be_a_real_past_date(dob):
    assert "date_of_birth must be a real date as YYYY-MM-DD in the past" in _reject(CONTACT, {"date_of_birth": dob})


@pytest.mark.parametrize("value", ["2027", "2027-13", "2027-02-30", "March 2027"])
def test_visa_expires_shapes(value):
    assert "user_info.right_to_work.countries[0].visa_expires must be YYYY-MM or YYYY-MM-DD" in _reject(
        RTW, {"countries": [{"country": "de", "visa_expires": value}]}
    )


def test_a_past_visa_expiry_is_allowed():
    out = validate_edit(RTW, {"countries": [{"country": "de", "visa_expires": "2019-05-01"}]})
    assert out["countries"][0]["visa_expires"] == "2019-05-01"


def test_earliest_start_that_looks_like_a_date_must_be_real():
    assert "earliest_start" in _reject(LOGI, {"earliest_start": "2026-02-31"})
    assert validate_edit(LOGI, {"earliest_start": "as soon as possible"}) == {
        "earliest_start": "as soon as possible"
    }


def test_remote_is_not_a_country_record():
    for path, value in (
        (RTW, {"countries": [{"country": "remote"}]}),
        (LOGI, {"countries": [{"country": "remote"}]}),
        (SALARY, [_sal(country="remote")]),
    ):
        detail = _reject(path, value)
        assert "ISO alpha-2" in detail and "no 'remote' record" in detail, path
    assert "ISO alpha-2" in _reject(CONTACT, {"residence_country": "Germany"})
    assert "ISO alpha-2" in _reject(RTW, {"citizenship": ["GBR"]})


@pytest.mark.parametrize("path", [RTW, LOGI])
def test_country_is_required_and_unique_after_normalising(path):
    assert f"{path}.countries[0] needs a country (ISO alpha-2)" in _reject(
        path, {"countries": [{"visa_type": "x"} if path == RTW else {"relocate_where": "x"}]}
    )
    assert f"{path}.countries: country 'DE' appears twice — one record per country" in _reject(
        path, {"countries": [{"country": "de"}, {"country": "DE"}]}
    )


def test_contradictions_are_refused_in_right_to_work():
    def rec(**kw: Any) -> dict[str, Any]:
        return {"countries": [{"country": "de", **kw}]}

    assert (
        "user_info.right_to_work.countries[0]: work_authorization 'citizen' contradicts needs_sponsorship true"
        in _reject(RTW, rec(work_authorization="citizen", needs_sponsorship=True))
    )
    assert "work_authorization 'permanent_resident' contradicts needs_sponsorship true" in _reject(
        RTW, rec(work_authorization="permanent_resident", needs_sponsorship=True)
    )
    assert "work_authorization 'needs_sponsorship' contradicts needs_sponsorship false" in _reject(
        RTW, rec(work_authorization="needs_sponsorship", needs_sponsorship=False)
    )
    # consistent combinations pass
    validate_edit(RTW, rec(work_authorization="citizen", needs_sponsorship=False))
    validate_edit(RTW, rec(work_authorization="needs_sponsorship", needs_sponsorship=True))


def test_closed_sets_name_their_values():
    assert "work_authorization must be one of: citizen, needs_sponsorship, permanent_resident, visa" in _reject(
        RTW, {"countries": [{"country": "de", "work_authorization": "tourist"}]}
    )
    assert "level must be one of: basic, fluent, native, professional" in _reject(
        LANGS, [{"language": "French", "level": "beginner"}]
    )
    assert "needs a language and a level" in _reject(LANGS, [{"language": "French"}])


def test_duplicate_language_is_refused_case_insensitively():
    assert "language 'french' appears twice" in _reject(
        LANGS, [{"language": "French", "level": "native"}, {"language": "FRENCH", "level": "basic"}]
    )


def test_address_lines_cap_at_three():
    assert "address_lines exceeds the 3-line limit" in _reject(CONTACT, {"address_lines": ["a", "b", "c", "d"]})


def test_answer_caps_and_shape():
    q_max, a_max = settings.USER_INFO_QUESTION_MAX_CHARS, settings.USER_INFO_ANSWER_MAX_CHARS
    assert (q_max, a_max) == (300, 2000)
    validate_edit(ANSWERS, [{"question": "q" * q_max, "answer": "a" * a_max}])
    assert "USER_INFO_QUESTION_MAX_CHARS" in _reject(ANSWERS, [{"question": "q" * (q_max + 1), "answer": "a"}])
    assert "USER_INFO_ANSWER_MAX_CHARS" in _reject(ANSWERS, [{"question": "q", "answer": "a" * (a_max + 1)}])
    assert "needs a non-empty question and answer" in _reject(ANSWERS, [{"question": "q", "answer": "  "}])
    assert "needs a non-empty question and answer" in _reject(ANSWERS, [{"answer": "a"}])
    assert "must be a boolean" in _reject(ANSWERS, [{"question": "q", "answer": "a", "approved": "yes"}])


def test_duplicate_question_is_refused_ignoring_case_and_space():
    assert "the same question appears twice" in _reject(
        ANSWERS, [{"question": "Why us?", "answer": "a"}, {"question": "  why   US? ", "answer": "b"}]
    )


def test_record_cap_and_encoded_ceiling():
    cap = settings.PROFILE_EDIT_MAX_RECORDS
    assert cap == 50
    assert "50-record limit (PROFILE_EDIT_MAX_RECORDS)" in _reject(
        ANSWERS, [{"question": f"q{i}", "answer": "a"} for i in range(cap + 1)]
    )
    assert "50-record limit (PROFILE_EDIT_MAX_RECORDS)" in _reject(
        LANGS, [{"language": f"l{i}", "level": "basic"} for i in range(cap + 1)]
    )
    assert "50-record limit (PROFILE_EDIT_MAX_RECORDS)" in _reject(
        RTW, {"countries": [{"country": f"{chr(65 + i // 26)}{chr(65 + i % 26)}"} for i in range(cap + 1)]}
    )
    assert "50-record limit (PROFILE_EDIT_MAX_RECORDS)" in _reject(
        SALARY, [_sal(country=f"{chr(65 + i // 26)}{chr(65 + i % 26)}") for i in range(cap + 1)]
    )
    # 50 answers of 2000 chars each = ~100k encoded: under the 120000 ceiling.
    big = [{"question": f"q{i}", "answer": "a" * 2000, "recorded_at": "2026-10-01"} for i in range(50)]
    assert len(validate_edit(ANSWERS, big)) == 50
    assert settings.USER_INFO_ANSWERS_MAX_CHARS == 120000
    # Non-ASCII escapes (json.dumps) push the same list over the ceiling.
    heavy = [{"question": f"q{i}", "answer": "é" * 2000, "recorded_at": "2026-10-01"} for i in range(50)]
    assert "USER_INFO_ANSWERS_MAX_CHARS" in _reject(ANSWERS, heavy)


def test_422_messages_never_echo_the_submitted_value():
    secret_phone, secret_dob, secret_gender = "SECRETPHONE9", "9999-99-99", "SECRETGENDER"
    details = [
        _reject(CONTACT, {"phone": secret_phone}),
        _reject(CONTACT, {"date_of_birth": secret_dob}),
        _reject(CONTACT, {"email": "SECRETMAIL"}),
        _reject(EQ, {"gender": ["SECRETGENDER"]}),
        _reject(RTW, {"citizenship": ["SECRETGENDER"]}),
        _reject(RTW, {"countries": [{"country": "SECRETGENDER"}]}),
        _reject(RTW, {"countries": [{"country": "de", "work_authorization": "SECRETGENDER"}]}),
        _reject(RTW, {"countries": [{"country": "de", "visa_expires": "SECRETGENDER"}]}),
        _reject(LOGI, {"earliest_start": "9999-99-99"}),
        _reject(LANGS, [{"language": "x", "level": "SECRETGENDER"}]),
        _reject(ANSWERS, [{"question": "q", "answer": "a", "recorded_at": "SECRETGENDER"}]),
        _reject(SALARY, [_sal(currency="SECRETGENDER")]),
        _reject(SALARY, [_sal(period="SECRETGENDER")]),
        _reject(SALARY, [_sal(country="SECRETGENDER")]),
    ]
    for detail in details:
        for secret in (secret_phone, secret_dob, secret_gender, "SECRETMAIL", "SECRETGENDER", "9999-99-99"):
            assert secret not in detail, detail


# ═══════════════════════════════════════════════════════════════════════════
# preferences.salary_by_country
# ═══════════════════════════════════════════════════════════════════════════


def test_salary_by_country_round_trip_keeps_order_and_never_converts():
    out = validate_edit(SALARY, [
        {"country": "ae", "amount": 25000, "currency": "aed", "period": "month"},
        {"country": "de", "amount": 85000.5, "currency": "eur", "period": "Year"},
    ])
    assert out == [
        {"country": "AE", "amount": 25000, "currency": "AED", "period": "month"},
        {"country": "DE", "amount": 85000.5, "currency": "EUR", "period": "year"},
    ]


@pytest.mark.parametrize(
    "record",
    [
        {"country": "de", "currency": "EUR", "period": "year"},
        {"country": "de", "amount": 1, "period": "year"},
        {"country": "de", "amount": 1, "currency": "EUR"},
        {"amount": 1, "currency": "EUR", "period": "year"},
        _sal(period="week"),
        _sal(period=1),
        _sal(amount=True),
        _sal(amount="85000"),
        _sal(amount=float("inf")),
        _sal(amount=float("nan")),
        _sal(amount=0),
        _sal(amount=-5),
        _sal(amount=1e12 + 1e6),
        _sal(amount=10**400),  # too big for a float: a 422, never an OverflowError 500
        _sal(currency="EURO"),
        _sal(currency="E1R"),
        _sal(currency=5),
    ],
)
def test_salary_by_country_bad_records_are_refused(record):
    assert (
        "needs country, amount (a number > 0), currency (ISO 4217, 3 letters like 'EUR') "
        "and period (year or month)"
    ) in _reject(SALARY, [record])


def test_salary_by_country_duplicate_country_is_refused():
    assert "preferences.salary_by_country: country 'DE' appears twice — one record per country" in _reject(
        SALARY, [_sal(country="de"), _sal(country="DE")]
    )


# ═══════════════════════════════════════════════════════════════════════════
# API: the real PATCH door
# ═══════════════════════════════════════════════════════════════════════════


def _seed_profile(user_id: str, *, source_action: str = "cv_upload") -> None:
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"], preferred_locations=["London"]),
        ),
        user_id,
        source_action=source_action,
    )


async def _patch(client: AsyncClient, *edits_: dict[str, Any]):
    return await client.patch("/api/profile", json={"edits": list(edits_)})


async def _get(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/profile")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _mint_token(client: AsyncClient, name: str = "claude-code") -> str:
    resp = await client.post("/api/tokens", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def _versions(client: AsyncClient) -> int:
    return len((await client.get("/api/profile/versions")).json()["versions"])


def _base_info(user_id: str) -> UserInfo:
    from src.services.profile.storage import load_profile

    profile = load_profile(user_id, with_overlay=False)
    assert profile is not None
    return profile.user_info


async def _raw_user_info(user_id: str) -> str | None:
    from src.repositories import pg

    async with pg.connect(str(settings.DB_PATH)) as db:
        cur = await db.execute("SELECT user_info FROM user_profiles WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
    return None if row is None else row[0]


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))


@pytest.fixture
def audit_capture():
    logger = logging.getLogger("job360.audit")
    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)


ALL_EDITS = [
    {"path": CONTACT, "value": {"email": "ada@example.com", "phone": PHONE, "date_of_birth": DOB,
                                 "residence_country": "gb", "preferred_name": "Ada"}},
    {"path": RTW, "value": {"countries": [{"country": "de", "work_authorization": "visa"}],
                             "citizenship": ["gb"]}},
    {"path": LOGI, "value": {"notice_period": "1 month",
                              "countries": [{"country": "de", "travel_ok_pct": 20}]}},
    {"path": LANGS, "value": [{"language": "English", "level": "native"}]},
    {"path": EQ, "value": {"gender": "Prefer not to say"}},
    {"path": ANSWERS, "value": [{"question": "Why us?", "answer": "Because.", "approved": True}]},
    {"path": SALARY, "value": [{"country": "ae", "amount": 25000, "currency": "aed", "period": "month"}]},
]


@pytest.mark.asyncio
async def test_patch_writes_all_seven_in_one_call_and_get_profile_shows_real_values(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await _patch(client, *ALL_EDITS)
        assert resp.status_code == 200, resp.text
        body = await _get(client)
    info = body["user_info"]
    assert info["contact"]["email"] == "ada@example.com"
    assert info["contact"]["residence_country"] == "GB"
    assert info["contact"]["preferred_name"] == "Ada"
    assert info["right_to_work"] == {
        "countries": [{"country": "DE", "work_authorization": "visa"}], "citizenship": ["GB"],
    }
    assert info["logistics"] == {"notice_period": "1 month", "countries": [{"country": "DE", "travel_ok_pct": 20}]}
    assert info["languages"] == [{"language": "English", "level": "native"}]
    assert info["equality"] == {"gender": "Prefer not to say"}
    assert info["answers"][0]["answer"] == "Because."
    assert info["answers"][0]["approved"] is True
    assert info["answers"][0]["recorded_at"]
    assert body["preferences"]["salary_by_country"] == [
        {"country": "AE", "amount": 25000, "currency": "AED", "period": "month"}
    ]
    assert not [k for k in body["preferences"] if k.startswith("user_info")]
    # the overlay rows are provenance, the BASE column is untouched (three stores)
    assert await _raw_user_info(fixture_user_id) == "{}"


@pytest.mark.asyncio
async def test_one_bad_edit_writes_nothing_and_creates_no_profile_row(
    authenticated_async_context, fixture_user_id
):
    from src.services.profile.storage import load_profile

    async with authenticated_async_context() as client:
        assert load_profile(fixture_user_id, with_overlay=False) is None
        resp = await _patch(
            client,
            {"path": LOGI, "value": {"notice_period": "1 month"}},
            {"path": CONTACT, "value": {"phone": "SECRETPHONE9"}},
        )
        assert resp.status_code == 422
        assert "SECRETPHONE9" not in resp.text
        assert load_profile(fixture_user_id, with_overlay=False) is None, "a 422 must not create a profile row"
        _seed_profile(fixture_user_id)
        resp = await _patch(
            client,
            {"path": LOGI, "value": {"notice_period": "1 month"}},
            {"path": SALARY, "value": [{"country": "ae", "amount": 1, "currency": "AED"}]},
        )
        assert resp.status_code == 422
        body = await _get(client)
        assert body["user_info"]["logistics"] == {}
        assert body["preferences"]["salary_by_country"] == []


@pytest.mark.asyncio
async def test_mcp_get_profile_lists_the_seven_paths_and_values(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from src.api import mcp_server

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _patch(client, *ALL_EDITS)).status_code == 200
        tool = mcp_server.build_server()._tool_manager.get_tool("get_profile")
        assert tool is not None
        mcp_server._current_user.set(mcp_server.CurrentUser(id=fixture_user_id, email="e2e@example.com"))
        try:
            result = await tool.fn()
        finally:
            mcp_server._current_user.set(None)
    for path in SEVEN:
        assert path in result["editable_paths"]
        assert path in result["fields"]
    assert result["fields"][LOGI]["notice_period"] == "1 month"
    assert result["fields"][CONTACT]["phone"] == PHONE
    assert result["fields"][SALARY][0]["currency"] == "AED"


@pytest.mark.asyncio
async def test_two_actors_both_show_in_the_path_history(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client, name="claude-code")
        assert (await _patch(client, {"path": CONTACT, "value": {"preferred_name": "web Ada"}})).status_code == 200
    async with _bearer_client(token) as agent:
        assert (await _patch(agent, {"path": CONTACT, "value": {"preferred_name": "agent Ada"}})).status_code == 200
    async with authenticated_async_context() as client:
        rows = (await client.get("/api/profile/edits/history", params={"path": CONTACT})).json()["rows"]
    assert [r["set_by"] for r in rows] == ["token:claude-code", "web"]
    assert rows[0]["value"] == {"preferred_name": "agent Ada"}
    assert rows[1]["value"] == {"preferred_name": "web Ada"}
    assert all(r["set_at"] for r in rows)


# ═══════════════════════════════════════════════════════════════════════════
# Three stores: web save, clear scopes, restore, Keep, Take back
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_web_preferences_save_leaves_the_memory_alone_and_writes_no_memory_history(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        assert (await _patch(agent, *ALL_EDITS[:6])).status_code == 200
    async with authenticated_async_context() as client:
        before = (await _get(client))["user_info"]
        resp = await client.post(
            "/api/profile/preferences",
            data={"preferences": json.dumps({
                "preferred_locations": ["Leeds"],
                "user_info": {"contact": {"email": "posted@example.com"}},
                "user_info_contact": {"email": "posted@example.com"},
            })},
        )
        assert resp.status_code == 200, resp.text
        after = await _get(client)
        assert after["preferences"]["preferred_locations"] == ["Leeds"]
        assert after["user_info"] == before, "a posted user_info key is ignored"
        for path in SIX:
            rows = (await client.get("/api/profile/edits/history", params={"path": path})).json()["rows"]
            assert len(rows) == 1 and rows[0]["set_by"].startswith("token:"), f"a web row appeared for {path}"
        assert _base_info(fixture_user_id) == UserInfo()


@pytest.mark.asyncio
async def test_web_preferences_save_stores_salary_as_a_normal_preference(
    authenticated_async_context, fixture_user_id
):
    record = {"country": "de", "amount": 85000, "currency": "eur", "period": "year"}
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.post(
            "/api/profile/preferences", data={"preferences": json.dumps({"salary_by_country": [record]})}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["preferences"]["salary_by_country"] == [{**record, "country": "DE", "currency": "EUR"}]
        # omitted keeps it
        resp = await client.post(
            "/api/profile/preferences", data={"preferences": json.dumps({"preferred_locations": ["Leeds"]})}
        )
        assert resp.json()["preferences"]["salary_by_country"][0]["amount"] == 85000
        bad = await client.post(
            "/api/profile/preferences",
            data={"preferences": json.dumps({"salary_by_country": [{"country": "de", "amount": 1}]})},
        )
        assert bad.status_code == 422
        rows = (await client.get("/api/profile/edits/history", params={"path": SALARY})).json()["rows"]
        assert rows and rows[-1]["set_by"] == "web"


@pytest.mark.asyncio
async def test_clear_memory_clears_only_the_memory_and_takes_no_version(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        assert (await _patch(agent, *ALL_EDITS, {"path": "preferences.daily_check", "value": "auto"})).status_code == 200
    async with authenticated_async_context() as client:
        assert (await client.post("/api/profile/edits/keep", json={"path": CONTACT})).status_code == 200
        assert _base_info(fixture_user_id).contact["email"] == "ada@example.com"
        versions = await _versions(client)
        resp = await client.post("/api/profile/clear", data={"section": "memory"})
        assert resp.status_code == 200, resp.text
        body = await _get(client)
        assert body["user_info"] == {
            "contact": {}, "right_to_work": {}, "logistics": {}, "languages": [], "equality": {}, "answers": [],
        }
        assert _base_info(fixture_user_id) == UserInfo(), "the base column is emptied too"
        assert body["preferences"]["target_job_titles"] == ["Data Engineer"], "the CV/preferences are untouched"
        assert body["preferences"]["daily_check"] == "auto"
        assert body["preferences"]["salary_by_country"][0]["currency"] == "AED", "salary is a preference, not memory"
        assert await _versions(client) == versions, "memory is not in a version snapshot"
        rows = (await client.get("/api/profile/edits/history", params={"path": CONTACT})).json()["rows"]
        assert rows[0]["value"] is None, "the clear is a row, not a delete"


@pytest.mark.asyncio
async def test_clear_preferences_keeps_the_memory_and_clears_salary(authenticated_async_context, fixture_user_id):
    record = {"country": "de", "amount": 85000, "currency": "eur", "period": "year"}
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
        assert (await client.post(
            "/api/profile/preferences", data={"preferences": json.dumps({"salary_by_country": [record]})}
        )).status_code == 200
    async with _bearer_client(token) as agent:
        assert (await _patch(agent, *ALL_EDITS, {"path": "preferences.daily_check", "value": "auto"})).status_code == 200
    async with authenticated_async_context() as client:
        assert (await client.post("/api/profile/edits/keep", json={"path": EQ})).status_code == 200
        resp = await client.post("/api/profile/clear", data={"section": "preferences"})
        assert resp.status_code == 200, resp.text
        body = await _get(client)
        assert body["preferences"]["target_job_titles"] == [], "the clear really ran"
        assert body["preferences"]["salary_by_country"] == [], "salary is cleared, base and overlay"
        assert body["preferences"]["daily_check"] == "auto"
        assert body["user_info"]["contact"]["email"] == "ada@example.com"
        assert body["user_info"]["equality"] == {"gender": "Prefer not to say"}
        assert len(body["user_info"]["answers"]) == 1
        assert _base_info(fixture_user_id).equality == {"gender": "Prefer not to say"}
        from src.services.profile.storage import load_profile

        assert load_profile(fixture_user_id, with_overlay=False).preferences.salary_by_country == []


@pytest.mark.asyncio
async def test_clear_cv_leaves_the_memory_and_clear_all_resets_it(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _patch(
            client, *ALL_EDITS, {"path": "preferences.daily_check", "value": "auto"}
        )).status_code == 200
        from src.services.profile.storage import save_user_info

        save_user_info(fixture_user_id, UserInfo(contact={"preferred_name": "Base Ada"}), "test")
        assert (await client.post("/api/profile/clear", data={"section": "cv"})).status_code == 200
        body = await _get(client)
        assert body["user_info"]["contact"]["email"] == "ada@example.com"
        assert _base_info(fixture_user_id).contact == {"preferred_name": "Base Ada"}
        resp = await client.post("/api/profile/clear", data={"section": "all"})
        assert resp.status_code == 200, resp.text
        body = await _get(client)
        assert body["preferences"]["daily_check"] == ""
        assert body["preferences"]["salary_by_country"] == []
        assert all(not v for v in body["user_info"].values())
        assert _base_info(fixture_user_id) == UserInfo()
        for path in (*SIX, SALARY):
            rows = (await client.get("/api/profile/edits/history", params={"path": path})).json()["rows"]
            assert rows[0]["value"] is None, path
            assert len(rows) == 2, f"{path}: one write + one clearing row, history kept"


@pytest.mark.asyncio
async def test_clear_rejects_an_unknown_scope_and_names_memory(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.post("/api/profile/clear", data={"section": "everything"})
        assert resp.status_code == 400 and "memory" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_version_restore_keeps_the_memory_and_a_web_daily_check_and_restores_salary(
    authenticated_async_context, fixture_user_id
):
    """REGRESSION — `_resync_web_rows_to_base` rewrote every web-authored
    overlay row to the base's value on a restore; overlay-only fields are not
    in the base, so a restore wiped a web-set daily_check (and would wipe the
    memory the same way). Memory is not in a snapshot; salary_by_country IS
    (it lives in preferences) and restores like salary_min."""
    record = {"country": "de", "amount": 85000, "currency": "eur", "period": "year"}
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        _seed_profile(fixture_user_id, source_action="cv_reupload")
        assert (await _patch(
            client,
            {"path": "preferences.daily_check", "value": "auto"},
            {"path": LOGI, "value": {"notice_period": "1 month"}},
            {"path": LANGS, "value": [{"language": "English", "level": "native"}]},
        )).status_code == 200
        from src.services.profile.storage import save_user_info

        save_user_info(fixture_user_id, UserInfo(equality={"gender": "Base"}), "test")
        assert (await client.post(
            "/api/profile/preferences", data={"preferences": json.dumps({"salary_by_country": [record]})}
        )).status_code == 200
        assert (await _get(client))["preferences"]["salary_by_country"][0]["amount"] == 85000
        versions = (await client.get("/api/profile/versions")).json()["versions"]
        assert len(versions) >= 3
        resp = await client.post(f"/api/profile/versions/{versions[-1]['id']}/restore")
        assert resp.status_code == 200, resp.text
        body = await _get(client)
    assert body["preferences"]["daily_check"] == "auto"
    assert body["user_info"]["logistics"] == {"notice_period": "1 month"}
    assert body["user_info"]["languages"] == [{"language": "English", "level": "native"}]
    assert body["user_info"]["equality"] == {"gender": "Base"}, "the base memory column survives a restore"
    assert body["preferences"]["salary_by_country"] == [], "salary rides the snapshot, so it restores"


@pytest.mark.asyncio
async def test_keep_copies_into_the_base_column_without_a_version_and_take_back_then_404s(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        assert (await _patch(agent, {"path": LOGI, "value": {"notice_period": "1 month"}})).status_code == 200
    async with authenticated_async_context() as client:
        assert _base_info(fixture_user_id).logistics == {}
        versions = await _versions(client)
        kept = await client.post("/api/profile/edits/keep", json={"path": LOGI})
        assert kept.status_code == 200, kept.text
        assert _base_info(fixture_user_id).logistics == {"notice_period": "1 month"}, "Keep wrote the base column"
        assert await _versions(client) == versions, "Keep on memory takes no snapshot"
        assert json.loads(await _raw_user_info(fixture_user_id) or "{}")["logistics"] == {
            "notice_period": "1 month"
        }
        again = await client.post("/api/profile/edits/take-back", json={"path": LOGI})
        assert again.status_code == 404, "the newest row is the human's now"
        resp = await client.post(
            "/api/profile/preferences", data={"preferences": json.dumps({"preferred_locations": ["Leeds"]})}
        )
        assert resp.status_code == 200, resp.text
        assert (await _get(client))["user_info"]["logistics"] == {"notice_period": "1 month"}


@pytest.mark.asyncio
async def test_take_back_of_an_assistant_memory_edit_falls_back_to_the_base(
    authenticated_async_context, fixture_user_id
):
    from src.services.profile.storage import save_user_info

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
        save_user_info(fixture_user_id, UserInfo(contact={"preferred_name": "Base Ada"}), "test")
    async with _bearer_client(token) as agent:
        assert (await _patch(agent, {"path": CONTACT, "value": {"preferred_name": "Agent Ada"}})).status_code == 200
    async with authenticated_async_context() as client:
        body = await _get(client)
        assert body["user_info"]["contact"] == {"preferred_name": "Agent Ada"}
        edit = next(e for e in body["agent_edits"] if e["path"] == CONTACT)
        assert edit["previous_value"] == {"preferred_name": "Base Ada"}
        resp = await client.post("/api/profile/edits/take-back", json={"path": CONTACT})
        assert resp.status_code == 200, resp.text
        assert resp.json()["user_info"]["contact"] == {"preferred_name": "Base Ada"}


# ═══════════════════════════════════════════════════════════════════════════
# Storage: ONE writer for the user_info column
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_fresh_save_profile_row_has_an_empty_user_info(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context():
        _seed_profile(fixture_user_id)
        assert await _raw_user_info(fixture_user_id) == "{}"


@pytest.mark.asyncio
async def test_save_profile_with_a_fresh_object_never_wipes_stored_memory(
    authenticated_async_context, fixture_user_id
):
    """The wipe class: uploads, re-extraction, the CLI and a restore all build a
    fresh UserProfile and call save_profile — that must leave the memory alone."""
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import load_profile, save_profile, save_user_info

    async with authenticated_async_context():
        _seed_profile(fixture_user_id)
        assert save_user_info(fixture_user_id, UserInfo(contact={"preferred_name": "Ada"}), "test") is True
        stored = await _raw_user_info(fixture_user_id)
        save_profile(UserProfile(cv_data=CVData(raw_text="A different CV")), fixture_user_id, "cv_upload")
        assert await _raw_user_info(fixture_user_id) == stored
        assert load_profile(fixture_user_id, with_overlay=False).user_info.contact == {"preferred_name": "Ada"}


@pytest.mark.asyncio
async def test_gdpr_export_includes_the_memory_column(authenticated_async_context, fixture_user_id):
    """Article 20 — the user's own memory (base column + overlay rows) is in
    their export, with real values (rule #21), not just the key."""
    from src.services.profile.storage import save_user_info

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert save_user_info(fixture_user_id, UserInfo(contact={"preferred_name": "Base Ada"}), "test")
        assert (await _patch(client, {"path": EQ, "value": {"gender": "Prefer not to say"}})).status_code == 200
        export = await client.get("/api/auth/users/me/export")
        assert export.status_code == 200, export.text
        body = export.json()
    (row,) = body["user_profiles"]
    assert json.loads(row["user_info"])["contact"] == {"preferred_name": "Base Ada"}
    assert any(r["path"] == EQ and "Prefer not to say" in str(r["value"]) for r in body["profile_edits"])


@pytest.mark.asyncio
async def test_save_user_info_without_a_profile_row_creates_nothing(authenticated_async_context, fixture_user_id):
    from src.services.profile.storage import load_profile, save_user_info

    async with authenticated_async_context():
        assert save_user_info(fixture_user_id, UserInfo(contact={"preferred_name": "Ada"}), "test") is False
        assert load_profile(fixture_user_id, with_overlay=False) is None


# ═══════════════════════════════════════════════════════════════════════════
# Audit log: ids, counts, never values
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_audit_lines_carry_user_actor_path_and_no_submitted_value(
    authenticated_async_context, fixture_user_id, audit_capture
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        ok = await _patch(client, *ALL_EDITS)
        assert ok.status_code == 200, ok.text
        bad = await _patch(
            client,
            {"path": LOGI, "value": {"notice_period": "SECRETNOTICE"}},
            {"path": CONTACT, "value": {"phone": "SECRETPHONE9"}},
        )
        assert bad.status_code == 422
    events: dict[str, list[dict[str, Any]]] = {}
    for rec in audit_capture.records:
        events.setdefault(rec.get("event"), []).append(rec)
    saved = events["memory_saved"]
    assert {r["path"] for r in saved} == set(SIX), "one line per memory path written, none for salary"
    for rec in saved:
        assert rec["user_id"] == fixture_user_id and rec["actor"] == "web"
        assert rec["result"] == "ok" and rec["cleared"] is False and rec["answered"] >= 1
    counts = {r["path"]: r["answered"] for r in saved}
    assert counts[RTW] == 3, "2 top-level keys + 1 country record"
    assert counts[LANGS] == 1
    edit = events["profile_edit"][0]
    assert edit["user_id"] == fixture_user_id and edit["result"] == "ok" and edit["actor"] == "web"
    assert SALARY in edit["paths"], "salary is audited through profile_edit paths"
    rejected = events["profile_edit_rejected"]
    assert len(rejected) == 1
    rej = rejected[0]
    assert rej["user_id"] == fixture_user_id and rej["actor"] == "web"
    assert rej["failed_path"] == CONTACT and rej["status"] == 422 and rej["result"] == "rejected"
    assert rej["paths"] == [LOGI, CONTACT] and rej["levelname"] == "WARNING"
    assert "detail" not in rej
    everything = json.dumps(audit_capture.records, default=str)
    for secret in (
        "SECRETNOTICE", "SECRETPHONE9", PHONE, DOB, "ada@example.com", "Prefer not to say", "Because.",
        "AED", "1 month",
    ):
        assert secret not in everything, secret
    assert "notice_period" not in everything and "preferred_name" not in everything, (
        "key names stay out of the audit lines"
    )


@pytest.mark.asyncio
async def test_keep_and_clear_log_the_base_write_without_values(
    authenticated_async_context, fixture_user_id, audit_capture
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        assert (await _patch(agent, {"path": EQ, "value": {"gender": "SECRETGENDER"}})).status_code == 200
    async with authenticated_async_context() as client:
        assert (await client.post("/api/profile/edits/keep", json={"path": EQ})).status_code == 200
        assert (await client.post("/api/profile/clear", data={"section": "memory"})).status_code == 200
    base = [r for r in audit_capture.records if r.get("event") == "memory_base_saved"]
    # (storage logs through the same audit logger the routes use)
    assert [r["source_action"] for r in base] == ["keep_edit", "clear_memory"]
    for rec in base:
        assert rec["user_id"] == fixture_user_id and rec["result"] == "ok"
    kept = [r for r in audit_capture.records if r.get("event") == "profile_edit_kept"][0]
    assert kept["user_id"] == fixture_user_id
    assert "SECRETGENDER" not in json.dumps(audit_capture.records, default=str)


@pytest.mark.asyncio
async def test_profile_cleared_log_carries_the_user(authenticated_async_context, fixture_user_id, caplog):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        with caplog.at_level(logging.INFO, logger="job360.api.profile"):
            assert (await client.post("/api/profile/clear", data={"section": "memory"})).status_code == 200
    cleared = [r for r in caplog.records if getattr(r, "event", "") == "profile_cleared"]
    assert len(cleared) == 1
    assert cleared[0].user_id == fixture_user_id and cleared[0].section == "memory"

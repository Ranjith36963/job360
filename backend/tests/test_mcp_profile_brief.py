"""S4 - get_profile(view="brief") is the small default; view="full" serves fields/shapes/editable_paths.

Measured live (2026-10-31): every get_profile call in every scheduled duty returned 55-61k chars
(83k with include_raw) and Claude Code saved each one to a file. The bulk was `fields`, `shapes`,
`editable_paths` and `agent_edits`, plus `assistant_notes` sent twice. The brief keeps what a run
reads under 8,000 chars on a realistic profile; full keeps everything else, once.

The profile is seeded the way a real one is filled: a CV upload, then the assistant's own
update_profile calls through the real MCP tool (so agent_edits, memory and answers are real rows).
Helpers are copied, never imported from another test module (schema isolation).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

BRIEF_LIMIT = 8_000

SKILLS = [
    "Python", "SQL", "FastAPI", "PostgreSQL", "Docker", "Kubernetes", "AWS", "Terraform", "Airflow", "dbt",
    "Spark", "Kafka", "Pandas", "NumPy", "scikit-learn", "PyTorch", "LangChain", "RAG", "Redis", "GraphQL",
    "TypeScript", "React", "Next.js", "Git", "CI/CD", "GitHub Actions", "Linux", "Bash", "Snowflake", "BigQuery",
    "Looker", "Tableau", "Data Modelling", "ETL", "REST APIs", "Microservices", "Celery", "pytest", "MLflow",
    "Prometheus", "Grafana", "Elasticsearch", "MongoDB", "Go", "Rust", "Azure", "GCP", "Databricks",
]
POSITIONS = [
    {"title": f"Data Engineer {i}", "company": f"Company {i} Ltd", "dates": f"Jan 20{10 + i} - Dec 20{11 + i}",
     "location": "London", "bullets": ["Built batch and streaming pipelines on AWS; owned the warehouse."] * 3}
    for i in range(6)
]
NOTES = [
    "Never apply to recruitment agencies.",
    "Keep cover letters under 250 words.",
    "Prefer companies with a four-day week.",
    "Do not apply to crypto or gambling companies.",
    "Always mention my open-source work on dbt packages.",
    "Ask me before applying to anything below senior level.",
]
ANSWERS = [
    {"question": f"Common question number {i}: why this kind of role?", "answer": "Because I enjoy it. " * 12,
     "approved": i % 2 == 0, "topic": f"common_q_{i}"}
    for i in range(20)
] + [
    {"question": "Can you work from India?", "answer": "Yes", "approved": True, "topic": "work_from.IN"},
    {"question": "Notice period in days?", "answer": "30", "approved": True, "topic": "notice_period_days"},
]
MEMORY_EDITS = [
    {"path": "user_info.contact", "value": {
        "email": "ada@example.com", "phone": "+44 7700 900123", "address_country": "GB",
        "residence_city": "London", "residence_country": "GB", "legal_first_name": "Ada",
        "legal_last_name": "Lovelace", "linkedin_url": "https://www.linkedin.com/in/ada-example"}},
    {"path": "user_info.right_to_work", "value": {
        "countries": [
            {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False},
            {"country": "IE", "work_authorization": "citizen", "needs_sponsorship": False},
            {"country": "ANY", "remote_ok": True, "onsite": "only_if_sponsored", "note": "remote anywhere"},
        ],
        "citizenship": ["GB", "IE"], "sanctions_country_citizen": False}},
    {"path": "user_info.logistics", "value": {
        "notice_period": "1 month",
        "countries": [{"country": "GB", "willing_to_relocate": True, "driving_licence": True,
                       "driving_licence_type": "full", "driving_licence_country": "GB"}]}},
    {"path": "user_info.languages", "value": [
        {"language": "English", "level": "native"}, {"language": "French", "level": "professional"}]},
    {"path": "user_info.answers", "value": ANSWERS},
    {"path": "preferences.salary_by_country", "value": [
        {"country": "GB", "min": 70000, "max": 95000, "currency": "GBP", "period": "year"},
        {"country": "IE", "min": 80000, "max": 105000, "currency": "EUR", "period": "year"}]},
    {"path": "preferences.preferred_locations", "value": ["London", "Dublin", "Remote"]},
    {"path": "preferences.work_arrangement", "value": "hybrid"},
    {"path": "preferences.dealbreakers", "value": ["No on-call rota every week", "No relocation outside Europe"]},
    {"path": "preferences.avoid_companies", "value": ["Initech", "Globex"]},
    {"path": "preferences.daily_check", "value": "ask"},
    {"path": "cv_data.cv_positions", "value": POSITIONS},
]


def _seed_cv(user_id: str) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(
                raw_text="Senior data engineer. " * 400, name="Ada Lovelace", headline="Senior Data Engineer",
                location="London", skills=SKILLS, job_titles=["Senior Data Engineer", "Data Engineer"],
                companies=[p["company"] for p in POSITIONS], education=["BSc Mathematics, UCL"],
                summary="Data engineer with ten years building pipelines. " * 4,
                linkedin_raw_text="LinkedIn export text. " * 300,
            ),
            preferences=UserPreferences(
                target_job_titles=["Senior Data Engineer", "Analytics Engineer", "ML Engineer"],
                experience_level="senior", assistant_notes=list(NOTES),
            ),
        ),
        user_id, source_action="cv_upload",
    )


@pytest.fixture
async def tools(authenticated_async_context, fixture_user_id):
    """``(get_profile, update_profile)`` bound to the fixture user, on a realistic seeded profile."""
    from src.api import mcp_server

    _seed_cv(fixture_user_id)
    async with authenticated_async_context():
        manager = mcp_server.build_server()._tool_manager
        mcp_server._current_user.set(mcp_server.CurrentUser(
            id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
        ))
        try:
            get_profile = manager.get_tool("get_profile").fn
            update_profile = manager.get_tool("update_profile").fn
            edits = [{**e, "source": "user_said", "remember": True} for e in MEMORY_EDITS]
            await update_profile(edits)
            yield get_profile, update_profile
        finally:
            mcp_server._current_user.set(None)


def _size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, default=str))


async def _refused(update_profile, *edits: dict[str, Any]) -> str:
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as info:
        await update_profile(list(edits))
    return str(info.value)


@pytest.mark.asyncio
async def test_brief_is_the_default_and_holds_what_a_run_reads(tools):
    get_profile, _ = tools
    brief = await get_profile()
    assert brief == await get_profile(view="brief")
    for key in ("instructions", "memory_notes", "memory_check", "settings", "job_titles", "experience_level",
                "top_skills", "search_brief", "is_complete", "basics", "answers", "profile_edit_id",
                "raw_available", "full_view"):
        assert key in brief, key
    for key in ("fields", "shapes", "editable_paths", "agent_edits", "assistant_notes", "common_questions_open"):
        assert key not in brief, key
    assert "notes" not in brief["settings"]
    # Value presence, not key presence: the run reads the real facts.
    assert brief["instructions"] == NOTES
    assert brief["basics"]["user_info.contact"]["email"] == "ada@example.com"
    assert [c["country"] for c in brief["basics"]["user_info.right_to_work"]["countries"]] == ["GB", "IE", "ANY"]
    assert brief["basics"]["preferences.salary_by_country"][0]["min"] == 70000
    assert brief["basics"]["preferences.daily_check"] == "ask"
    assert brief["basics"]["preferences.target_job_titles"][0] == "Senior Data Engineer"
    assert "preferences.salary_min" not in brief["basics"]  # unset = absent (rule #29)
    assert brief["answers"]["saved"] == len(ANSWERS)
    assert brief["answers"]["approved"] == sum(1 for a in ANSWERS if a["approved"])
    assert "work_from.IN" in brief["answers"]["topics"]
    assert brief["answers"]["open_common_questions"] > 0  # the list rides the full view
    assert 'view="full"' in brief["full_view"]
    assert _size(brief) <= BRIEF_LIMIT, _size(brief)


@pytest.mark.asyncio
async def test_full_has_fields_and_shapes_and_notes_once(tools):
    get_profile, _ = tools
    full = await get_profile(view="full")
    brief = await get_profile()
    for key in ("fields", "shapes", "editable_paths", "agent_edits", "skills", "instructions",
                "common_questions_open"):
        assert key in full, key
    assert "assistant_notes" not in full and "notes" not in full["settings"]
    assert full["fields"]["preferences.assistant_notes"] == NOTES  # the one copy of the notes' values
    assert full["fields"]["user_info.answers"][0]["topic"] == "common_q_0"
    assert "user_info.right_to_work" in full["shapes"]
    assert "user_info.contact" in full["editable_paths"]
    assert {r["path"] for r in full["agent_edits"]} >= {"user_info.contact", "user_info.answers"}
    print(f"\nget_profile sizes on the seeded profile: brief={_size(brief)} full={_size(full)}")
    assert _size(full) > _size(brief)


@pytest.mark.asyncio
async def test_include_raw_works_with_either_view(tools):
    get_profile, _ = tools
    brief = await get_profile(include_raw=True)
    full = await get_profile(view="full", include_raw=True)
    assert brief["raw"]["cv"].startswith("Senior data engineer.") and "fields" not in brief
    assert brief["raw"] == full["raw"]
    page = await get_profile(include_raw=True, raw_section="linkedin")
    assert set(page["raw"]) >= {"linkedin", "truncated"}


def test_view_is_a_closed_described_argument():
    from pydantic import ValidationError

    from src.api import mcp_server

    tool = mcp_server.build_server()._tool_manager.get_tool("get_profile")
    prop = tool.parameters["properties"]["view"]
    assert prop["enum"] == ["brief", "full"] and prop["default"] == "brief"
    assert 'view="full"' not in prop["description"] and "fields, shapes" in prop["description"]
    with pytest.raises(ValidationError):
        tool.fn_metadata.arg_model.model_validate({"view": "everything"})


@pytest.mark.asyncio
async def test_bad_shape_refusal_carries_the_shape_for_that_path(tools):
    _, update_profile = tools
    text = await _refused(
        update_profile,
        {"path": "preferences.work_arrangement", "value": "remote"},
        {"path": "user_info.logistics", "value": {"current_salary": {"amount": 1, "foo": 2}}},
    )
    assert "Nothing was saved." in text
    assert "resend ALL 2 paths in one call" in text
    shape_line = next(line for line in text.splitlines() if line.startswith("Shape for user_info.logistics: "))
    shape = json.loads(shape_line.split(": ", 1)[1])
    assert "notice_period" in shape["allowed_keys"] and "example" in shape
    assert "Shape for preferences.work_arrangement" not in text  # only the refused path


@pytest.mark.asyncio
async def test_malformed_edit_refusal_carries_the_shape(tools):
    _, update_profile = tools
    text = await _refused(update_profile, {"path": "user_info.languages", "valu": [{"language": "English"}]})
    assert "Nothing was saved." in text
    assert "Shape for user_info.languages: " in text


@pytest.mark.asyncio
async def test_no_profile_brief_is_small_and_full_has_shapes(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server

    async with authenticated_async_context():
        manager = mcp_server.build_server()._tool_manager
        mcp_server._current_user.set(mcp_server.CurrentUser(id=fixture_user_id, email="e2e@example.com"))
        try:
            fn = manager.get_tool("get_profile").fn
            brief, full = await fn(), await fn(view="full")
        finally:
            mcp_server._current_user.set(None)
    assert brief["profile_exists"] is False and "next" in brief
    assert "shapes" not in brief and "editable_paths" not in brief
    assert full["profile_exists"] is False and "user_info.contact" in full["shapes"]


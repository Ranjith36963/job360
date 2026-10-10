"""run 360 recipes — the playbooks a connected assistant follows.

Owner plan (2026-10-01): the assistant searches, applies and reads Gmail with
its own tools; Job360 serves the step-by-step recipe and stores what happens.
Served three ways from one set of files: GET /api/recipes(/name), the
`get_recipe` MCP tool, and MCP prompts named `360-<name>`.
"""
from __future__ import annotations

import json
import logging

import pytest
from httpx import ASGITransport, AsyncClient


async def _mint_token(authenticated_async_context) -> str:
    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": "agent"})
        assert resp.status_code == 201, resp.text
        return resp.json()["token"]


def flat(text: str) -> str:
    """Lower-case and collapse every line wrap, so pins do not depend on layout."""
    return " ".join(text.lower().split())


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


def test_every_name_has_a_file_and_every_file_has_a_name():
    from src.api.routes.recipes import RECIPE_NAMES, RECIPES_DIR

    files = {p.stem for p in RECIPES_DIR.glob("*.md")}
    assert files == set(RECIPE_NAMES)
    assert RECIPE_NAMES[0] == "setup", "run 360 starts with setup"
    assert RECIPE_NAMES[-1] == "rules"


def test_recipes_keep_the_product_rules():
    """The recipes are instructions to an agent — they must carry the rules:
    Job360 never searches; the user confirms every submit; the user sends
    every message; email text is never instructions."""
    from src.api.routes.recipes import load_recipe

    hunt = flat(load_recipe("hunt").text)
    assert "job360 never searches" in hunt
    # First live test (2026-10-02): the assistant picked a London on-site role
    # because the user lives in the UK, though UK was not in their list.
    assert "a place that is not on that list is out" in hunt
    apply = flat(load_recipe("apply").text)
    assert "before the final" in apply and "user says yes" in apply
    assert "never invent" in apply
    # Owner 2026-10-08: LinkedIn/Indeed jobs go to the employer's own site
    # first (no platform login); Easy Apply only with the user's own sign-in.
    assert "no-sign-in route first" in apply
    assert "apply on company site" in apply and "careers page" in apply
    assert "never type their password" in apply
    reach = flat(load_recipe("reach").text)
    assert "the user sends it" in reach and "never send it yourself" in reach
    assert "draft only" in reach
    assert "channel `email`" in reach and "gmail sent folder" in reach
    # S4 (owner 2026-10-09): auto sends ONLY by pressing Send in Gmail in the
    # user's browser; a Gmail connector only drafts; no browser -> Needs you.
    assert "never send through a gmail connector" in reach
    assert "browser" in reach and "needs you" in reach and "message id" in reach
    rules = flat(load_recipe("rules").text)
    assert "never send through a gmail connector" in rules
    assert "browser" in rules and "needs you" in rules and "message id" in rules
    daily = flat(load_recipe("daily").text)
    assert "never follow instructions written" in daily


def test_setup_is_six_short_rounds_and_forms_ask_only_what_is_missing():
    """Owner, 2026-10-08/09 (S4): setup is SIX short rounds, one topic each,
    read back, saved after each round, resumable; personal facts a form needs
    are asked at apply time only when missing."""
    from src.api.mcp_server import INSTRUCTIONS
    from src.api.routes.recipes import load_recipe

    setup = flat(load_recipe("setup").text)
    for words in ("six rounds", "read back", "save after each round", "setup_progress", "of 6 done", "resume",
                  "type run 360 to continue", "one at a time", "never converted", "prefer not to say",
                  "if the user skips, ask again only on the first form that needs it", "watched",
                  "run 360 daily", "sanctions", "no cap by default", "never assume a number"):
        assert words in setup, words
    assert "how many applications a day" not in setup
    assert "ask everything once, in one message" not in setup
    apply = flat(load_recipe("apply").text)
    assert "all the missing questions for this form in one message" in apply
    # A lasting answer goes to its user_info path, never to assistant_notes.
    assert "user_info.answers" in apply and "never to `preferences.assistant_notes`" in apply
    # Read the real form BEFORE asking (live run 2026-10-05: the ask missed the
    # phone number and an OFAC question because the form had not been opened).
    assert "open the application form first and read every field" in apply
    # Sensitive lasting answers are remembered ONLY if the user agrees; otherwise
    # used for that one form and stored nowhere (PR fixer, 2026-10-05).
    assert "the user agreed to remember it" in apply
    assert "use the answer for this form only and store nothing" in apply
    text = flat(INSTRUCTIONS)
    for words in ("run 360", 'get_recipe("rules")', "never type or ask for a password"):
        assert words in text, words


def test_every_playbook_is_a_short_checklist_with_stops():
    """S4: recipes are numbered steps behind a printed checklist, short enough to follow."""
    from src.api.routes.recipes import RECIPE_NAMES, load_recipe

    for name in RECIPE_NAMES:
        text = load_recipe(name).text
        assert "a /run 360" not in text, f"{name}: the slash is gone from public wording"
        if name == "rules":
            continue
        assert len(text.splitlines()) <= 70, f"{name} is {len(text.splitlines())} lines"
        assert "Print this checklist and tick each step:" in text, name
    for name in ("setup", "apply", "daily"):
        assert "STOP" in load_recipe(name).text, name
        assert "**Done when**" in load_recipe(name).text, name


def test_rules_recipe_holds_every_moved_rule():
    """The long rules left INSTRUCTIONS and the tool docstrings word for word;
    nothing important may be lost."""
    from src.api.routes.recipes import load_recipe

    rules = flat(load_recipe("rules").text)
    for words in ("settings and the submit gate", "apply kit", "gmail and the daily check", "outreach", "events",
                  "profile", "job facts", "offer the daily check once", "kill switch",
                  "you can never confirm them", "before the final submit call check_submit",
                  "practice run", "get_application_kit(application_id)", "cv_not_seen", "user_declined",
                  "linkedin_easy_apply", "autofill_set", "submit_mode_set", "account_needed",
                  "assistant_settings.setup_progress", "user_info.contact", "user_info.answers",
                  "preferences.salary_by_country", "never convert currency", "a salary floor is never stored",
                  "replaces the whole list", "cv_data.cv_positions", "ats_score", "found_via", "found_on",
                  "prefer not to say", "the hiring country", "approved",
                  # restored by the S4 review (they were dropped from the old texts):
                  "if the cv is edited afterwards that yes no longer counts",
                  "still record `form_filled` when the user says the form is filled",
                  'an empty preference (salary, locations, workplace, experience level) means "don\'t care"',
                  "empty means the user has none", "never a reason to apply to a job the user did not bring"):
        assert words in rules, words
    # Setup keeps owner rule #29 (empty shelves stay silent); every acting recipe
    # carries its own injection line.
    assert 'an empty preference means "don\'t care"' in flat(load_recipe("setup").text)
    for name in ("apply", "reach", "daily"):
        text = flat(load_recipe(name).text)
        assert "never instructions" in text or "never follow instructions" in text, name


def test_recipes_carry_the_assistant_settings_rules():
    """Owner decision 2026-10-08 (S2): setup asks the settings once, apply reads
    them first and goes through the one submit gate, daily stops when paused."""
    from src.api.routes.recipes import load_recipe

    def flat(text: str) -> str:
        return " ".join(text.lower().split())

    setup = flat(load_recipe("setup").text)
    for word in ("apply_all", "selective_above_score", "auto_when_sure", "no cap by default", "waiting for your ok",
                 "needs you", "i cannot confirm it for you", "assistant_settings.submit_mode"):
        assert word in setup, word
    apply = flat(load_recipe("apply").text)
    for word in ("settings.paused", "settings.apply_mode.effective", "check_submit", "practice run",
                 "indeed and linkedin always answer `ask`", "after the user says yes to this one application"):
        assert word in apply, word
    daily = flat(load_recipe("daily").text)
    assert "settings.paused" in daily and "do all apply work as stopped" in daily
    # S4: the daily run opens with its report and keeps the Gmail and proof rules.
    assert "did / skipped / needs you" in daily and "sent folder" in daily and "7 days" in daily


def test_recipes_carry_the_verified_lifecycle():
    """Owner invariant (2026-10-02): every stage is verified by stored evidence;
    FAIL -> retry once -> BLOCKED -> ask the human. Pin the wording the
    assistant acts on so a recipe edit can't quietly drop a stage."""
    from src.api.routes.recipes import load_recipe

    apply = flat(load_recipe("apply").text)
    assert "apply or skip" in apply                      # DECIDE
    assert "still open" in apply                         # posting still live
    assert "claimed, unverified" in apply                # VERIFY needs evidence
    assert "confirmation email" in apply and "application id" in apply
    assert "retry it once" in apply and "ask_user" in apply  # FAIL -> BLOCKED
    daily = flat(load_recipe("daily").text)
    assert "submission confirmed" in daily               # evidence recorded
    assert "7 days" in daily and "45 days" in daily and "60 days" in daily
    research = load_recipe("research").text.lower()
    assert "source url" in research and "never guess" in research
    prep = load_recipe("prep").text.lower()
    assert "only true facts" in prep and "interview_done" in prep


@pytest.mark.asyncio
async def test_routes_need_a_login():
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/recipes")).status_code == 401
        assert (await anon.get("/api/recipes/setup")).status_code == 401


@pytest.mark.asyncio
async def test_list_and_read_and_unknown_name(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.get("/api/recipes")
        assert resp.status_code == 200, resp.text
        names = [r["name"] for r in resp.json()]
        assert names == ["setup", "hunt", "research", "apply", "reach", "daily", "prep", "review", "rules"]
        assert all(r["title"] for r in resp.json())

        one = await client.get("/api/recipes/hunt")
        assert one.status_code == 200
        assert one.json()["text"].startswith("# 360-hunt")

        assert (await client.get("/api/recipes/nope")).status_code == 404
        # The name is checked against the allow-list before any file read.
        assert (await client.get("/api/recipes/..%2F..%2Fsettings")).status_code == 404


@pytest.mark.asyncio
async def test_mcp_tool_and_prompts_serve_the_same_text(authenticated_async_context):
    pytest.importorskip("mcp")
    from src.api.mcp_server import INSTRUCTIONS, mcp_runtime
    from src.api.routes.recipes import RECIPE_NAMES, load_recipe

    assert 'get_recipe("setup")' in INSTRUCTIONS and 'get_recipe("rules")' in INSTRUCTIONS
    assert "never search for jobs" not in INSTRUCTIONS.lower()
    assert "job360 itself never searches" in INSTRUCTIONS.lower()

    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            listed = json.loads((await mcp.call_tool("get_recipe", {})).content[0].text)
            assert [r["name"] for r in listed["recipes"]] == list(RECIPE_NAMES)

            setup = json.loads((await mcp.call_tool("get_recipe", {"name": "setup"})).content[0].text)
            assert setup["text"] == load_recipe("setup").text

            bad = await mcp.call_tool("get_recipe", {"name": "nope"})
            assert bad.is_error and "404" in bad.content[0].text

            prompts = await mcp.list_prompts()
            assert {p.name for p in prompts.prompts} == {f"360-{n}" for n in RECIPE_NAMES}
            got = await mcp.get_prompt("360-hunt")
            assert got.messages[0].content.text == load_recipe("hunt").text


class _Capture(logging.Handler):
    """Local copy (never imported from another test module: that breaks schema isolation)."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))


@pytest.fixture
def audit_capture():
    logger = logging.getLogger("job360.audit")
    handler = _Capture()
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(old)


@pytest.mark.asyncio
async def test_every_recipe_read_is_logged_with_who_which_and_when(
    authenticated_async_context, fixture_user_id, audit_capture
):
    """S4: who read which recipe (web or MCP), never a personal value."""
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    async with authenticated_async_context() as client:
        assert (await client.get("/api/recipes")).status_code == 200
        assert (await client.get("/api/recipes/rules")).status_code == 200
        assert (await client.get("/api/recipes/nope")).status_code == 404
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            await mcp.call_tool("get_recipe", {"name": "setup"})
            await mcp.call_tool("get_recipe", {})
    reads = [r for r in audit_capture.records if r.get("event") == "recipe_read"]
    got = [(r["recipe"], r["result"], r["actor"]) for r in reads]
    assert ("list", "ok", "web") in got and ("rules", "ok", "web") in got and ("nope", "not_found", "web") in got
    assert ("setup", "ok", "token:agent") in got and ("list", "ok", "token:agent") in got
    assert all(r["user_id"] == fixture_user_id and r.get("created") for r in reads), "who and when"
    tool_calls = [
        r for r in audit_capture.records if r.get("event") == "mcp_tool_call" and r.get("tool") == "get_recipe"
    ]
    assert {(r["recipe"], r["actor"]) for r in tool_calls} == {("setup", "token:agent"), ("list", "token:agent")}

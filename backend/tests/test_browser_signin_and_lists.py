"""Two pages seen live (2026-10-20): Workday's first apply step is a sign-in with no password box yet,
and an Ashby job LIST (filter dropdowns and links) read as an application form."""
from __future__ import annotations

import pytest

from tests.test_browser_agent import _ats_pool, _resolver, _serve, needs_chromium


def _field(name, type_="text", tag="input"):
    return {"tag": tag, "type": type_, "name": name}


def test_only_a_workday_create_account_or_sign_in_step_is_an_account_step():
    from src.services.browser_agent.sites import account_step, site_for

    wd = site_for("https://acme.wd3.myworkdayjobs.com/en-US/External/job/1/apply/applyManually")
    step = "current step 1 of 6\nCreate Account/Sign In\nstep 2 of 6\nMy Information"
    assert account_step(wd, "Sign In", "")
    assert account_step(wd, "Acme Careers", step)
    # Later steps, the job search page with a header "Sign In" button, and other sites are not.
    assert not account_step(wd, "My Information", "step 1 of 6\nCreate Account/Sign In\ncurrent step 2 of 6")
    assert not account_step(wd, "Search for Jobs", "Sign In\nSearch for Jobs\nSenior Engineer")
    assert not account_step(site_for("https://jobs.lever.co/acme/1"), "Sign In", step)
    assert not account_step(None, "Sign In", step)


def test_an_account_step_needs_the_user_but_a_captcha_still_wins():
    from src.services.browser_agent.safety import blocker_for

    b = blocker_for(["https://acme.wd3.myworkdayjobs.com/x"], "", False, account_step=True)
    assert b is not None and b.reason == "login_required"
    assert "never types a password" in b.detail and "never makes an account" in b.detail
    assert "Job360 website" in b.detail
    b = blocker_for(["https://acme.wd3.myworkdayjobs.com/x"], "Checking your browser", False, account_step=True)
    assert b is not None and b.reason == "captcha"


def test_filter_dropdowns_and_links_are_a_job_list_not_a_form():
    from src.services.browser_agent.sites import looks_like_application

    filters = [_field(n, "select-one", "select") for n in ("Department", "Employment Type", "Location")]
    links = [{"tag": "a", "type": "", "name": f"Engineer {i}"} for i in range(800)]
    assert not looks_like_application(filters + links)
    ashby_form = [
        _field("Legal Name"), _field("Email", "email"), _field("Resume", "file"), _field("Phone", "tel"),
        {"tag": "button", "type": "button", "name": "Yes"}, {"tag": "button", "type": "button", "name": "No"},
        {"tag": "button", "type": "submit", "name": "Submit Application"},
    ]
    assert looks_like_application(ashby_form)
    # Without the file box the typed fields still make it the form, dropdowns included.
    assert looks_like_application([_field("Legal Name"), _field("Email", "email"), *filters[:1]])


WORKDAY_SIGN_IN = """<!doctype html><html><head><title>Sign In</title></head><body>
<div data-automation-id="progressBar"><ol>
  <li><span>current step 1 of 6</span> <span>Create Account/Sign In</span></li>
  <li><span>step 2 of 6</span> <span>My Information</span></li></ol></div>
<h2>Sign In</h2>
<button type="button">Sign in with Google</button>
<button type="button">Sign in with LinkedIn</button>
<button type="button">Sign in with email</button>
</body></html>"""
WORKDAY_SEARCH = """<!doctype html><html><head><title>Search for Jobs</title></head><body>
<header><button type="button">Sign In</button></header>
<label for="q">Search for jobs or keywords</label><input id="q">
<a href="/en-US/External/job/1">Senior Engineer</a><a href="/en-US/External/job/2">Data Analyst</a>
</body></html>"""
ASHBY_LIST = """<!doctype html><html><head><title>Jobs at Acme</title></head><body>
<label for="d">Department</label><select id="d"><option>All</option><option>Research</option></select>
<label for="e">Employment Type</label><select id="e"><option>All</option><option>Full time</option></select>
<label for="l">Location</label><select id="l"><option>All</option><option>London</option></select>
""" + "".join(f'<p><a href="/acme/{i}">Engineer {i}</a></p>' for i in range(60)) + "</body></html>"
ASHBY_FORM = """<!doctype html><html><head><title>Engineer - Acme</title></head><body>
<label for="n">Legal Name</label><input id="n">
<label for="m">Email</label><input id="m" type="email">
<label for="r">Resume</label><input id="r" type="file">
<label for="p">Phone</label><input id="p" type="tel">
<p>Are you authorised to work in the UK?</p><button type="button">Yes</button><button type="button">No</button>
<button type="submit">Submit Application</button>
</body></html>"""


@pytest.fixture
async def pages_site():
    from aiohttp import web

    def page(html):
        async def handler(_):
            return web.Response(text=html, content_type="text/html")

        return handler

    app = web.Application()
    app.router.add_get("/en-US/External/job/1/apply/applyManually", page(WORKDAY_SIGN_IN))
    app.router.add_get("/en-US/External", page(WORKDAY_SEARCH))
    app.router.add_get("/acme", page(ASHBY_LIST))
    app.router.add_get("/acme/1/application", page(ASHBY_FORM))
    runner, port, tls_port = await _serve(app)
    yield port, tls_port
    await runner.cleanup()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_workday_sign_in_step_needs_you_and_an_ashby_list_is_not_the_form(pages_site):
    from src.services.browser_agent.agent import run

    public = "93.184.216.34"
    resolve, _ = _resolver({"acme.wd3.myworkdayjobs.com": public, "jobs.ashbyhq.com": public})
    pool = _ats_pool(resolve, pages_site)
    try:
        wd = "https://acme.wd3.myworkdayjobs.com/en-US/External"
        page = await run(pool, "u1", "open", url=wd + "/job/1/apply/applyManually")
        assert page["status"] == "needs_you" and page["reason"] == "login_required"
        assert "never makes an account" in page["detail"]
        page = await run(pool, "u1", "open", url=wd)
        assert page["status"] == "ok" and page["site"]["name"] == "workday"

        page = await run(pool, "u1", "open", url="https://jobs.ashbyhq.com/acme")
        assert page["status"] == "ok" and page["site"]["name"] == "ashby"
        assert page["application_form"] is False
        page = await run(pool, "u1", "open", url="https://jobs.ashbyhq.com/acme/1/application")
        assert page["application_form"] is True
    finally:
        await pool.shutdown()

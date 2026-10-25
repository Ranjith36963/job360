"""Offline tests for hunt_kit/kit_co8.py (public Telegram channels).

Fixtures are shaped like the sample data in career-ops' tests/providers/telegram-channel.test.mjs (MIT, 8f48928) and written
fresh. Nothing touches the network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
sys.path.insert(0, str(KIT_DIR))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


co = _load("kit_co8_under_test", "kit_co8.py")
hk = _load("hunt_kit_co8_under_test", "hunt_kit.py")
F = co.ATS_FETCHERS
NAMES = ("telegramchannel",)
NOW = datetime.now(timezone.utc)


def _stub(routes: dict):
    """http stub: first route whose key is in the URL wins; a callable value gets (url, headers, data)."""
    calls: list = []

    def http(url, *, method="GET", data=None, headers=None, as_json=True, once=False):
        calls.append((url, method, headers or {}, data, once))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, headers or {}, data) if callable(resp) else resp
        return 404, None

    http.calls = calls
    return http


def _run(name: str, row: dict, http):
    stats: dict = {}
    return F[name](row, http, stats), stats


def _row(ats: str, token: str, **kw) -> dict:
    return {"name": ats.title() + " Co", "ats": ats, "token": token, **kw}


def _hosts(http) -> set:
    return {urlsplit(c[0]).hostname for c in http.calls}


def test_the_reader_is_registered_and_wired():
    assert set(F) == set(NAMES)
    assert "kit_co8" in hk.ATS_MODULES
    assert set(F) <= set(hk.BOARD_SOURCES)
    assert set(F) <= set(hk._merged("ATS_FETCHERS", hk.ATS_MODULES))
    src = (KIT_DIR / "kit_co8.py").read_text(encoding="utf-8")
    assert src.count("Borrowed from career-ops") >= len(F)
    served = (KIT_DIR.parents[1] / "src" / "api" / "routes" / "hunt_kit.py").read_text(encoding="utf-8")
    assert '"kit_co8.py"' in served
    rows = json.loads((KIT_DIR / "companies.json").read_text(encoding="utf-8"))
    assert not [r for r in rows if r["ats"] in NAMES]  # no channel is shipped: the reference's example is Russian-language


@pytest.mark.parametrize("url,ats,token", [
    ("https://t.me/devjobs/12509", "telegramchannel", "devjobs"),
    ("https://t.me/s/devjobs", "telegramchannel", "devjobs"),
    ("https://t.me/joinchat", "telegramchannel", ""),
])
def test_job_links_are_read_as_this_board(url, ats, token):
    r = hk.board_from_job_url(url)
    assert (r["kind"], r["ats"], r["token"]) == ("ats", ats, token)


def test_parse_extra_reads_the_new_token():
    rows = hk.parse_extra("telegramchannel:devjobs")
    assert [(r["ats"], r["token"]) for r in rows] == [("telegramchannel", "devjobs")]


# ----------------------------------------------------------------------------- Telegram
def _iso(delta_days: float, base=NOW) -> str:
    return (base - timedelta(days=delta_days)).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _post(pid, when, text_html, cls="", channel="devjobs"):
    body = ('<a class="tgme_widget_message_photo_wrap" href="https://t.me/devjobs/1"></a>' if text_html is None
            else f'<div class="tgme_widget_message_text js-message_text" dir="auto">{text_html}</div>')
    stamp = f'<time datetime="{when}" class="time">16:01</time>' if when else ""
    return (f'<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message {cls} js-widget_message" '
            f'data-post="{channel}/{pid}"><div class="tgme_widget_message_author"><a href="https://t.me/devjobs">Jobs</a></div>{body}'
            f'<span class="tgme_widget_message_meta"><a class="tgme_widget_message_date" href="https://t.me/devjobs/{pid}">{stamp}</a></span></div></div>')


def _tg_page(*posts):
    return f'<html><head><meta property="og:title" content="Dev Jobs"></head><body>{"".join(posts)}</body></html>'


def _a(href, text=None):
    return f'<a href="{href}" target="_blank">{text or href}</a>'


P_LABEL = _post(120, _iso(1), f"Senior Python Engineer<br/>#remote<br/>Company: Contoso<br/>Stack: Python &amp; Helm<br/>Apply: "
                f"{_a('https://contoso.example/careers/vacancy/12345?utm_source=tg&amp;amp;utm_medium=post')}"
                f' · {_a("https://vk.com/contoso", "VK")} · {_a("https://t.me/s/devjobs?q=%23senior", "#senior")}')
P_AT = _post(119, _iso(2), f"Data Engineer [Remote] @ Fabrikam<br/>{_a('https://boards.greenhouse.io/fabrikam/jobs/4411')}")
P_PIPE = _post(118, _iso(3), f"Game Designer | Nexters<br/>{_a('https://www.nexters.example/careers/game-designer-playable-ads')}")
P_PREFER = _post(117, _iso(4), f"Backend Developer @ Ozon<br/>{_a('https://djinni.co/jobs/35075-backend')}<br/>"
                 f"Direct: {_a('https://ozon.example/vacancies/backend-7788')}")
P_DIGEST = _post(116, _iso(5), f"Weekly digest @ Acme<br/>{_a('https://acme.example/jobs/1001')}<br/>{_a('https://acme.example/jobs/1002')}")
P_SHORT = _post(115, _iso(6), f"Tester @ Initech<br/>{_a('https://bit.ly/abc123')}<br/>{_a('http://initech.example/jobs/9999')}")
P_NONAME = _post(114, _iso(7), f"We are hiring a dev<br/>{_a('https://x.example/jobs/12345')}")
P_ANON = _post(113, _iso(8), f"Developer @ Our Client<br/>{_a('https://x.example/jobs/12345')}")
P_HASHTAG = _post(112, _iso(9), f"#middle #python<br/>Globex<br/>Middle Python Developer<br/>{_a('https://globex.example/vacancy/5550')}")
SERVICE = _post(111, _iso(10), "Channel created", cls="service_message")
MEDIA = _post(110, _iso(10), None)
REPLY = _post(109, _iso(10), '<div class="tgme_widget_message_text js-message_reply_text">Quoted</div>')
NO_PREVIEW = '<html><head><meta property="og:title" content="Telegram: Contact @gophersjob"></head><body></body></html>'


def _tg_jobs(*posts, **kw):
    http = _stub({"t.me/s/devjobs": (200, _tg_page(*posts))})
    jobs, st = _run("telegramchannel", _row("telegramchannel", "devjobs", **kw), http)
    return jobs, st, http


def test_telegram_posts_become_jobs_only_with_an_employer_and_one_vacancy_link():
    jobs, st, http = _tg_jobs(P_LABEL, P_AT, P_PIPE, P_PREFER, P_DIGEST, P_SHORT, P_NONAME, P_ANON, P_HASHTAG, SERVICE, MEDIA, REPLY)
    got = {j["extra"]["post_id"]: j for j in jobs}
    assert sorted(got) == [112, 117, 118, 119, 120]
    assert (got[120]["title"], got[120]["company"]) == ("Senior Python Engineer", "Contoso")
    assert got[120]["extra"]["apply_url"] == "https://contoso.example/careers/vacancy/12345?utm_source=tg&utm_medium=post"
    assert (got[119]["company"], got[119]["extra"]["apply_kind"]) == ("Fabrikam", "employer")  # an ATS page is the employer's own
    assert got[118]["company"] == "Nexters" and got[118]["title"] == "Game Designer | Nexters"  # the headline is the first line, as in the reference
    assert (got[117]["extra"]["apply_url"], got[117]["extra"]["apply_kind"]) == ("https://ozon.example/vacancies/backend-7788", "employer")  # beats the board mirror
    assert (got[112]["company"], got[112]["title"]) == ("Globex", "Middle Python Developer")
    for j in jobs:
        assert j["url"] == f"https://t.me/devjobs/{j['extra']['post_id']}" and j["posted"] and j["location"] == ""
        assert j["description"].endswith(f"Source: {j['url']}\nApply: {j['extra']['apply_url']}")
    assert {urlsplit(j["url"]).hostname for j in jobs} == {"t.me"}
    assert _hosts(http) == {"t.me"} and http.calls[0][0] == "https://t.me/s/devjobs"
    assert len(http.calls) == 2 and http.calls[1][0].startswith("https://t.me/s/devjobs?before=")  # re-served page: the second read ends it
    assert st["jobs"] == 5


def test_telegram_vacancy_links_are_https_vacancy_pages_never_social_short_or_form_links():
    ok = {"https://contoso.example/jobs/12345": "employer", "https://djinni.co/jobs/9-x": "board",
          "https://www.linkedin.com/jobs/view/4123456789/": "board", "https://x.example/page?gh_jid=77": "employer"}
    for url, kind in ok.items():
        assert co._classify(url) == kind
    for url in ("http://contoso.example/jobs/12345", "https://t.me/devjobs/5", "https://bit.ly/abc", "https://forms.gle/xyz",
                "https://vk.com/jobs/12345", "https://contoso.example/", "https://contoso.example/en/", "https://contoso.example/jobs/",
                "https://contoso.example/about", "https://www.linkedin.com/company/x/jobs", "https://u:p@contoso.example/jobs/12345",
                "https://contoso.example:8443/jobs/12345", "https://localhost/jobs/12345", "https://[::1/jobs/1", "javascript:alert(1)", ""):
        assert co._classify(url) == "", url
    assert co._apply_link(["https://a.example/jobs/1?utm_source=x", "https://a.example/jobs/1?utm_source=y"]) == ("https://a.example/jobs/1?utm_source=x", "employer")
    assert co._apply_link(["https://a.example/jobs/1", "https://b.example/jobs/2"]) is None  # a digest


def test_telegram_employer_shapes_and_what_is_refused():
    e = co._employer
    assert e(["Title", "Company: Contoso | Berlin"]) == "Contoso"
    assert e(["Title @ Fabrikam"]) == "Fabrikam" and e(["Title | Nexters"]) == "Nexters"
    assert e(["Title | 5 years | Senior | IC"]) == "" and e(["Title | Senior"]) == ""
    assert e(["Junior Android Developer", "at Kaspi — big fintech"]) == "Kaspi"
    assert e(["#a #b", "Globex"]) == "Globex" and e(["#a #b", "Senior Engineer"]) == ""
    assert e(["Title @ Remote"]) == "" and e(["Title @ September 02, 2026"]) == "" and e(["Title @ lowercase"]) == ""
    assert e(["Company: undisclosed"]) == "" and e(["Just a title"]) == ""


def test_telegram_pages_back_with_before_until_the_window_is_reached():
    p1 = _tg_page(P_LABEL, P_AT)
    old = _post(100, _iso(45), f"Old Role @ Oldco<br/>{_a('https://old.example/jobs/12345')}")
    p2 = _tg_page(_post(118, _iso(3), f"Mid Role @ Midco<br/>{_a('https://mid.example/jobs/12345')}"), old)
    http = _stub({"before=119": (200, p2), "t.me/s/devjobs": (200, p1)})
    jobs, _ = _run("telegramchannel", _row("telegramchannel", "devjobs"), http)
    assert [j["company"] for j in jobs] == ["Contoso", "Fabrikam", "Midco"]  # the 45-day-old post is outside the window
    assert [c[0] for c in http.calls] == ["https://t.me/s/devjobs", "https://t.me/s/devjobs?before=119"]
    assert _hosts(http) == {"t.me"}


def test_telegram_a_repeated_page_ends_the_channel_and_pages_are_capped():
    same = _stub({"t.me/s/devjobs": (200, _tg_page(P_LABEL, P_AT))})
    jobs, _ = _run("telegramchannel", _row("telegramchannel", "devjobs"), same)
    assert len(jobs) == 2 and len(same.calls) == 2
    def page(u, h, d):
        before = int(u.split("before=")[1]) if "before=" in u else 10_000
        name = "Co" + "".join(chr(97 + int(c)) for c in str(before))
        return 200, _tg_page(_post(before - 1, _iso(1), f"Role @ {name}<br/>{_a(f'https://{name}.example/jobs/12345')}"))
    endless = _stub({"t.me/s/devjobs": page})
    assert len(_run("telegramchannel", _row("telegramchannel", "devjobs"), endless)[0]) == 10 and len(endless.calls) == 10


def test_telegram_dead_channels_are_failed_boards_and_quiet_ones_are_empty():
    for page in (NO_PREVIEW, _tg_page(P_LABEL, P_AT).replace("tgme_widget_message_wrap", "tgme_post_wrap_v2")):
        jobs, st = _run("telegramchannel", _row("telegramchannel", "devjobs"), _stub({"t.me/s/devjobs": (200, page)}))
        assert jobs == [] and "ok" not in st
    for http in (_stub({}), _stub({"t.me/s/devjobs": (302, None)})):
        assert "ok" not in _run("telegramchannel", _row("telegramchannel", "devjobs"), http)[1]
    for posts in ((SERVICE,), (MEDIA, MEDIA.replace("/110", "/109")), (P_NONAME, P_DIGEST)):
        jobs, st, _http = _tg_jobs(*posts)
        assert jobs == [] and st["ok"] == 1
    # a refusal on a later page keeps what the first page gave
    http = _stub({"before=119": (429, None), "t.me/s/devjobs": (200, _tg_page(P_LABEL, P_AT))})
    assert len(_run("telegramchannel", _row("telegramchannel", "devjobs"), http)[0]) == 2
    for bad in ("", "abcd", "a b c d e", "x/../y123", "@" + "a" * 33, "devjobs?x=1"):
        http = _stub({})
        assert _run("telegramchannel", _row("telegramchannel", bad), http)[0] == [] and http.calls == []
    at = _stub({"t.me/s/devjobs": (200, _tg_page(P_LABEL))})
    assert len(_run("telegramchannel", _row("telegramchannel", "@devjobs"), at)[0]) == 1


def test_telegram_one_bad_post_and_foreign_channels_cost_only_themselves():
    other = _post(130, _iso(1), f"Role @ Zed<br/>{_a('https://zed.example/jobs/12345')}", channel="otherchannel")
    weird = _post(131, "not-a-date", f"Role @ Yarn<br/>{_a('https://yarn.example/jobs/12345')}")
    nobreak = _post(132, _iso(1), "x" * 5000 + f"<br/>Company: Wolf<br/>{_a('https://wolf.example/jobs/12345', '&#xZZ; <b')}")
    jobs, st, _http = _tg_jobs(other, weird, nobreak, P_LABEL)
    assert sorted(j["extra"]["post_id"] for j in jobs) == [120, 131, 132]
    assert next(j for j in jobs if j["extra"]["post_id"] == 131)["posted"] == ""
    assert len(next(j for j in jobs if j["extra"]["post_id"] == 132)["title"]) <= 121


def test_telegram_hostile_markup_is_linear_and_loses_nothing():
    def run(n, closed):
        end = "</div></div></div>" if closed else ""
        posts = "".join(f'<div class="tgme_widget_message_wrap"><div data-post="devjobs/{i}"><div class="tgme_widget_message_text">'
                        f'Role @ Co<br>{_a(f"https://c{i}.example/jobs/12345")}{end}' for i in range(1, n + 1))
        t = time.perf_counter()
        jobs, _ = _run("telegramchannel", _row("telegramchannel", "devjobs"), _stub({"t.me/s/devjobs": (200, "<html>" + posts)}))
        return len(jobs), time.perf_counter() - t
    assert [run(n, True)[0] for n in (100, 400)] == [100, 400]  # well-formed: every post kept
    # Best of three for each size: one busy moment on a shared CI runner must not read as a slow parser.
    t1 = min(run(100, False)[1] for _ in range(3))
    t4 = min(run(400, False)[1] for _ in range(3))  # unclosed wraps nest into one post (a digest, so no job)
    assert t4 < 2.0 and t4 < max(t1, 0.02) * 12
    deep = "<span>" * 5000 + "Role @ Deep" + "<br>" * 3000
    jobs, _ = _run("telegramchannel", _row("telegramchannel", "devjobs"), _stub({"t.me/s/devjobs": (200, _tg_page(_post(5, _iso(1), deep)))}))
    assert jobs == []

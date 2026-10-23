"""Offline tests for the visa-sponsor register builder (build_companies.py) and the kit's
"visa_maybe_register" row. Fixtures are trimmed copies shaped like the real IND page, the Home
Office CSV and the Companies House bulk CSV. HTTP is a stub; nothing touches the network."""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"
FIX = Path(__file__).resolve().parent / "fixtures" / "hunt_kit" / "registers"


def _load(name: str, file: str) -> ModuleType:
    if str(KIT_DIR) not in sys.path:
        sys.path.insert(0, str(KIT_DIR))
    spec = importlib.util.spec_from_file_location(name, KIT_DIR / file)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bc = _load("build_companies_under_test", "build_companies.py")
hk = _load("hunt_kit_registers_under_test", "hunt_kit.py")


def _stub(routes: dict[str, tuple[int, Any]]):
    """http stub: first route whose key is in the URL wins; unknown URLs answer 404."""
    calls: list[str] = []

    def http(url: str, *, as_json: bool = True, headers: dict[str, str] | None = None, max_bytes: int | None = None) -> tuple[int, Any]:
        calls.append(url)
        for frag, resp in routes.items():
            if frag in url:
                return resp
        return 404, None

    http.calls = calls  # type: ignore[attr-defined]
    return http


# ---------------------------------------------------------------- names
@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("Mollie B.V.", "mollie"),
        ("Adyen N.V.", "adyen"),
        ("ACME LIMITED", "acme"),
        ("Acme Ltd", "acme"),
        ("Acme (UK) Limited", "acme"),
        ("Uber Netherlands B.V.", "uber"),
        ("Société Générale S.A.", "societe generale"),
        ("Booking.com B.V.", "booking com"),
        ("Acme Holding B.V.", "acme"),
        ("The Hut Group Ltd", "hut group"),  # "group" stays: it can be part of the brand
        ("B.V.", "b v"),  # never strips a name to nothing
        ("Smith & Sons GmbH & Co. KG", "smith sons"),
    ],
)
def test_norm_name_folds_accents_and_drops_legal_and_country_suffixes(raw: str, key: str) -> None:
    assert bc.norm_name(raw) == key


def test_slug_variants_are_only_joined_and_hyphenated() -> None:
    assert bc.slug_variants("booking com") == ["bookingcom", "booking-com"]
    assert bc.slug_variants("mollie") == ["mollie"]
    assert bc.slug_variants("ab") == []  # too short to probe
    # never the first word or hq/inc/ai variants (the kit's --discover makes those; too loose here)
    assert "booking" not in bc.slug_variants("booking com")


def test_trading_names_split_t_a() -> None:
    assert bc.trading_names("Deliveroo Ltd T/A Roofoods") == ["Deliveroo Ltd", "Roofoods"]
    assert bc.trading_names("Plain Ltd") == ["Plain Ltd"]


# ---------------------------------------------------------------- registers
def test_parse_ind_reads_the_table_rows() -> None:
    rows = bc.parse_ind((FIX / "ind_register.html").read_text(encoding="utf-8"))
    names = [r["name"] for r in rows]
    assert names == [
        "Aa-Dee Machinefabriek en Staalbouw Nederland B.V.",  # the page's doubled quotes are dropped
        "5CA B.V.",
        "Adyen N.V.",
        "Café Mollie B.V.",  # entity decoded
    ]  # header row, the menu table, the duplicate and the row without a KVK number are skipped
    assert rows[2]["kvk"] == "34259528"


def test_govuk_csv_link_and_ch_zip_link() -> None:
    page = ('<a href="/x.pdf">x</a><a class="govuk-link" href="https://assets.publishing.service.gov.uk/media/ab/'
            'SP_-_Worker_and_Temporary_Worker_Web_Register_-_2026-10-16.csv">CSV</a>')
    assert bc.govuk_csv_link(page).endswith("Web_Register_-_2026-10-16.csv")
    assert bc.govuk_csv_link("<p>nothing</p>") == ""
    ch = '<li><a href="BasicCompanyDataAsOneFile-2026-10-01.zip">one file</a></li>'
    assert bc.ch_zip_link(ch) == "https://download.companieshouse.gov.uk/BasicCompanyDataAsOneFile-2026-10-01.zip"


def test_parse_uk_csv_keeps_skilled_worker_rows_once() -> None:
    rows = bc.parse_uk_csv((FIX / "uk_register.csv").read_text(encoding="utf-8"))
    assert [r["name"] for r in rows] == [
        "AaruvikA Limited", "Monzo Bank Limited", "Deliveroo Ltd T/A Roofoods", "Smith & Sons (UK) Limited",
    ]


def test_companies_house_sic_filter(tmp_path: Path) -> None:
    z = tmp_path / "BasicCompanyDataAsOneFile-2026-10-01.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.write(FIX / "ch_basic.csv", "BasicCompanyDataAsOneFile-2026-10-01.csv")
    keep = bc.ch_sic_names(bc.read_ch_zip(z))
    # 62xxx software and 72xxx R&D kept; a bank (64) dropped; any of the 4 SIC columns counts (63120);
    # a dissolved company dropped even with SIC 62
    assert keep == {"aaruvika", "deliveroo", "lab seven"}
    uk = bc.parse_uk_csv((FIX / "uk_register.csv").read_text(encoding="utf-8"))
    kept = [r["name"] for r in uk if bc.norm_name(bc.trading_names(r["name"])[0]) in keep]
    assert kept == ["AaruvikA Limited", "Deliveroo Ltd T/A Roofoods"]


def test_companies_house_unreachable_stops_with_a_clear_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def down(*a: object, **k: object) -> bytes:
        raise OSError("HTTP 503")

    monkeypatch.setattr(bc, "_fetch", down)
    with pytest.raises(SystemExit) as e:
        bc.ch_zip_path(tmp_path, None)
    assert "--ch PATH" in str(e.value) and "120,000" in str(e.value)


# ---------------------------------------------------------------- precision rule
def test_name_match_accepted_and_other_company_rejected() -> None:
    gh = {"n": 3, "name": "Adyen", "t": ["Engineer"]}
    assert bc.accept("adyen", "adyen", gh) == "name"
    assert bc.accept("bol com", "bolcom", {"n": 1, "name": "Bol.com"}) == "name"  # space-insensitive
    # the ATS names a different company: never accepted, however good the slug looks
    assert bc.accept("adyen", "adyen", {"n": 3, "name": "Adyen Payments Brazil"}) is None


def test_slug_match_needs_full_name_and_six_chars_and_jobs() -> None:
    assert bc.accept("active theory", "activetheory", {"n": 4, "name": None}) == "slug"
    assert bc.accept("active theory", "active-theory", {"n": 4, "name": None}) == "slug"
    assert bc.accept("mollie", "mollie", {"n": 2, "name": None}) == "slug"
    assert bc.accept("abt", "abt", {"n": 3, "name": None}) is None  # short slug: too many companies share it
    assert bc.accept("active theory", "active", {"n": 4, "name": None}) is None  # first word only
    assert bc.accept("active theory", "activetheory", {"n": 0, "name": None}) is None  # no jobs
    assert bc.accept("active theory", "activetheory", {"n": 0}) is None


def test_probes_read_the_company_name_each_ats_gives() -> None:
    http = _stub({
        "boards-api.greenhouse.io/v1/boards/adyen/jobs": (200, {"jobs": [{"title": "SRE", "location": {"name": "Amsterdam"}}]}),
        "boards-api.greenhouse.io/v1/boards/adyen": (200, {"name": "Adyen"}),
        "widget/accounts/acme": (200, {"name": "Acme", "jobs": [{"title": "Dev", "city": "Leeds", "country": "UK"}]}),
        "acme.recruitee.com": (200, {"offers": [{"title": "Dev", "company_name": "Acme BV", "location": "Utrecht"}]}),
        "companies/acme/postings": (200, {"totalFound": 7, "content": [{"name": "Dev", "company": {"name": "Acme"}}]}),
        "api.lever.co/v0/postings/acme": (200, [{"text": "Dev", "categories": {"location": "London"}}]),
    })
    gh = bc.probe_greenhouse(http, "adyen")
    assert gh["name"] == "Adyen" and gh["n"] == 1 and gh["loc"] == ["Amsterdam"]
    assert bc.probe_workable(http, "acme")["name"] == "Acme"
    assert bc.probe_recruitee(http, "acme")["name"] == "Acme BV"
    assert bc.probe_smartrecruiters(http, "acme")["n"] == 7
    lever = bc.probe_lever(http, "acme")
    assert lever["name"] is None and lever["n"] == 1
    assert bc.probe_greenhouse(http, "nobody") == {"n": 0}  # a clear 404 is a cached miss
    assert bc.probe_ashby(_stub({"ashbyhq": (429, None)}), "acme") == {"err": 429}  # throttled: retried, not cached


def test_lever_tries_the_eu_host() -> None:
    http = _stub({"api.eu.lever.co/v0/postings/mollie": (200, [{"text": "Dev"}])})
    assert bc.probe_lever(http, "mollie")["n"] == 1
    assert [u.split("/v0")[0] for u in http.calls] == ["https://api.lever.co", "https://api.eu.lever.co"]


def test_teamtailor_and_join_names() -> None:
    rss = ('<?xml version="1.0"?><rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel>'
           "<title>Acme</title><item><title>Dev</title><tt:locations><tt:location><tt:city>Leeds</tt:city>"
           "<tt:country>United Kingdom</tt:country></tt:location></tt:locations></item></channel></rss>")
    tt = bc.probe_teamtailor(_stub({"acme.teamtailor.com": (200, rss)}), "acme")
    assert tt["name"] == "Acme" and tt["n"] == 1 and tt["loc"] == ["Leeds, United Kingdom"]
    state = {"props": {"pageProps": {"initialState": {
        "company": {"name": "JOIN"}, "jobs": {"items": [{"title": "Dev", "idParam": "1", "city": {"cityName": "Berlin"}}]}}}}}
    page = '<script id="__NEXT_DATA__" type="application/json">%s</script>' % json.dumps(state)
    jn = bc.probe_join(_stub({"join.com/companies/join": (200, page)}), "join")
    assert jn["name"] == "JOIN" and jn["n"] == 1


def test_collect_hits_and_merge_enrich_existing_rows_without_duplicating() -> None:
    targets = bc.build_targets([("NL-IND", "Adyen N.V."), ("GB-HO", "Adyen Ltd"), ("NL-IND", "Active Theory B.V."),
                                ("NL-IND", "ABT B.V.")])
    assert set(targets) == {"adyen", "active theory", "abt"}
    assert bc.build_targets([("NL-IND", "LinkedIn Netherlands B.V.")]) == {}  # the kit never reads big job sites
    assert targets["adyen"]["registers"] == {"NL-IND", "GB-HO"}
    results = {
        ("greenhouse", "adyen"): {"n": 5, "name": "Adyen", "t": ["SRE"]},
        ("lever", "activetheory"): {"n": 4, "name": None, "t": ["Designer"]},
        ("lever", "abt"): {"n": 3, "name": None},  # short slug: rejected
        ("ashby", "adyen"): {"n": 2, "name": None},  # slug "adyen" is 5 chars: rejected
    }
    hits = bc.collect_hits(targets, results)
    assert set(hits) == {("greenhouse", "adyen"), ("lever", "activetheory")}
    existing = [
        {"name": "Adyen", "ats": "greenhouse", "board": "adyen", "source": "career-ops"},
        {"name": "Other", "ats": "ashby", "board": "other", "source": "jobsync"},
    ]
    rows, added, enriched = bc.merge(existing, hits)
    assert (added, enriched) == (1, 1)
    assert rows[0] == {"name": "Adyen", "ats": "greenhouse", "board": "adyen", "source": "career-ops",
                       "country": "GB,NL", "sponsor_register": "GB-HO,NL-IND", "match": "name"}
    assert rows[1] == existing[1]  # untouched
    assert rows[2] == {"name": "Active Theory B.V.", "ats": "lever", "board": "activetheory", "source": "nl-ind",
                       "country": "NL", "sponsor_register": "NL-IND", "match": "slug"}
    assert len([r for r in rows if r["board"] == "adyen"]) == 1


def test_slug_hit_never_enriches_a_row_that_names_another_company() -> None:
    hits = {("ashby", "access"): {"name": "Access LTD", "registers": {"GB-HO"}, "match": "slug"}}
    base = [{"name": "Access Destination Services", "ats": "ashby", "board": "access", "source": "jobsync"}]
    rows, added, enriched = bc.merge(base, hits)
    assert rows == base and (added, enriched) == (0, 0)


def test_demo_boards_with_identical_jobs_are_rejected() -> None:
    demo = {"n": 3, "name": None, "t": ["General Application", "SEO Marketing Manager", "Social Media"]}
    targets = bc.build_targets([("NL-IND", "Travix B.V."), ("NL-IND", "Highberg B.V."), ("GB-HO", "Gospooky Ltd")])
    results = {("personio", "travix"): dict(demo), ("personio", "highberg"): dict(demo),
               ("personio", "gospooky"): dict(demo)}
    assert bc.collect_hits(targets, results) == {}  # three boards, same sample jobs: an ATS demo board
    del results[("personio", "gospooky")]
    assert set(bc.collect_hits(targets, results)) == {("personio", "travix"), ("personio", "highberg")}


def _countries(loc: str) -> frozenset[str]:
    return hk.parse_loc(loc).countries


def test_verify_keeps_a_slug_hit_with_a_job_in_the_register_country() -> None:
    hits = {("lever", "freetrade"): {"name": "Freetrade Limited", "registers": {"GB-HO", "NL-IND"}, "match": "slug",
                                     "t": ["Backend Engineer"], "n": 2}}
    jobs = {("lever", "freetrade"): [["Backend Engineer", "London, UK"], ["iOS Engineer", "Remote"]]}
    out, st = bc.verify_hits(hits, lambda a, s: jobs[(a, s)], _countries)
    assert set(out) == {("lever", "freetrade")}
    assert out[("lever", "freetrade")]["registers"] == {"GB-HO"}  # only the register whose country has a job
    assert out[("lever", "freetrade")]["where"] == "London, UK"
    assert st["slug_kept"] == 1


def test_verify_drops_a_slug_hit_with_only_us_jobs_and_keeps_name_hits() -> None:
    hits = {
        ("ashby", "august"): {"name": "August", "registers": {"GB-HO"}, "match": "slug", "t": ["AE"], "n": 2},
        ("greenhouse", "adyen"): {"name": "Adyen N.V.", "registers": {"NL-IND"}, "match": "name", "t": ["SRE"], "n": 1},
    }
    calls: list[tuple[str, str]] = []

    def fetch(a: str, s: str) -> list[list[str]]:
        calls.append((a, s))
        return [["Account Executive", "New York, NY"], ["Engineer", "San Francisco, CA"]]

    out, st = bc.verify_hits(hits, fetch, _countries)
    assert set(out) == {("greenhouse", "adyen")} and st["slug_no_country_job"] == 1
    assert calls == [("ashby", "august")]  # a name hit is not refetched
    # a slug board that did not answer is not kept this run
    out, st = bc.verify_hits({("ashby", "august"): hits[("ashby", "august")]}, lambda a, s: None, _countries)
    assert out == {} and st["unverified"] == 1


def test_verify_drops_sample_only_boards_even_on_a_name_match() -> None:
    hits = {("recruitee", "accenture"): {"name": "Accenture B.V.", "registers": {"NL-IND"}, "match": "name",
                                         "t": ["sales executive", "Senior Marketer (Sample)"], "n": 2}}
    only_sample = [["Senior Marketer (Sample)", "Amsterdam"], ["Sample job", "Amsterdam"]]
    assert bc.verify_hits(hits, lambda a, s: only_sample, _countries)[0] == {}
    real = only_sample + [["Sales Executive", "Amsterdam"]]
    assert set(bc.verify_hits(hits, lambda a, s: real, _countries)[0]) == {("recruitee", "accenture")}


def test_board_jobs_uses_the_kit_readers() -> None:
    http = _stub({"boards-api.greenhouse.io/v1/boards/acme/jobs": (
        200, {"jobs": [{"title": "Dev", "location": {"name": "Leeds"}, "absolute_url": "https://x/1"}]})})
    assert bc.board_jobs(hk, http, "greenhouse", "acme") == [["Dev", "Leeds"]]
    assert bc.board_jobs(hk, http, "greenhouse", "gone") is None


def test_rebuild_is_idempotent_and_keeps_the_other_register() -> None:
    hits = {("lever", "activetheory"): {"name": "Active Theory B.V.", "registers": {"NL-IND"}, "match": "slug"},
            ("greenhouse", "monzo"): {"name": "Monzo Ltd", "registers": {"GB-HO"}, "match": "name"}}
    base = [{"name": "Adyen", "ats": "greenhouse", "board": "adyen", "source": "career-ops"}]
    rows, _, _ = bc.merge(base, hits)
    again, _, _ = bc.merge(bc.strip_registers(rows, ["NL-IND", "GB-HO"]), hits)
    assert bc.dump_rows(again) == bc.dump_rows(rows)
    only_nl = bc.strip_registers(rows, ["NL-IND"])  # --only nl rebuild: the GB rows stay
    assert [r["board"] for r in only_nl] == ["adyen", "monzo"]
    assert bc.strip_registers(rows, ["NL-IND", "GB-HO"]) == base


def test_dump_rows_matches_the_committed_layout() -> None:
    raw = (KIT_DIR / "companies.json").read_text(encoding="utf-8")
    assert bc.dump_rows(json.loads(raw)) == raw


def test_run_probes_uses_and_fills_the_cache(tmp_path: Path) -> None:
    path = tmp_path / "probes.jsonl"
    cache = bc.ProbeCache(path)
    cache.put("lever:cached", {"n": 2, "name": None})
    cache.close()
    cache = bc.ProbeCache(path)
    http = _stub({"api.lever.co/v0/postings/fresh": (200, [{"text": "Dev"}]), "postings/flaky": (503, None)})
    res, left = bc.run_probes([("lever", "cached"), ("lever", "fresh"), ("lever", "flaky")], http, cache, jitter=0)
    cache.close()
    assert left == 0 and res[("lever", "cached")]["n"] == 2 and res[("lever", "fresh")]["n"] == 1
    assert not any("cached" in u for u in http.calls)  # answered from disk
    lines = [json.loads(x)["k"] for x in path.read_text(encoding="utf-8").splitlines()]
    assert lines == ["lever:cached", "lever:fresh"]  # the 503 is not kept: the next run tries again


# ---------------------------------------------------------------- the kit's maybe-visa row
def prefs(work: str | None = "IN", **kw: Any) -> Any:
    return hk.make_prefs(work, **kw)


GB = frozenset({"GB"})


def test_maybe_register_row_for_onsite_jobs_in_the_register_country() -> None:
    desc = "Build data pipelines in our London office."
    assert hk.classify_location("London, UK", desc, prefs(), register=GB) == "visa_maybe_register"
    assert hk.classify_location("Hybrid - Manchester", desc, prefs(), register=GB) == "visa_maybe_register"
    assert hk.classify_location("", desc, prefs(), register=GB) == "visa_maybe_register"  # no parsed country
    # no register, or a job in another country than the register's: unchanged (dropped)
    assert hk.classify_location("London, UK", desc, prefs()) is None
    assert hk.classify_location("Berlin, Germany", desc, prefs(), register=GB) is None


def test_maybe_register_never_for_remote_refusals_locks_or_when_off() -> None:
    desc = "Build data pipelines."
    assert hk.classify_location("Remote - UK", desc, prefs(), register=GB) is None
    assert hk.classify_location("London, UK", "We do not offer visa sponsorship.", prefs(), register=GB) is None
    assert hk.classify_location("London, UK", "Candidates must have the right to work in the UK only.",
                                prefs(), register=GB) is None
    assert hk.classify_location("London, UK", desc, prefs(visa_onsite=False), register=GB) is None
    # the ad itself sponsors: that stays the stronger visa_onsite row
    assert hk.classify_location("London, UK", "We sponsor visas for this role.", prefs(), register=GB) == "visa_onsite"
    # a country you can already work in stays work_country
    assert hk.classify_location("London, UK", desc, prefs("GB"), register=GB) == "work_country"


def test_board_register_reads_the_row_fields() -> None:
    assert hk.board_register({"sponsor_register": "GB-HO,NL-IND", "country": "GB,NL"}) == (
        "GB-HO,NL-IND", frozenset({"GB", "NL"}))
    assert hk.board_register({"name": "x"}) == ("", frozenset())


def _args(**kw: object) -> Any:
    a = hk.build_parser().parse_args([])
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_run_tags_register_jobs_and_leaves_other_boards_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"name": "Monzo", "ats": "greenhouse", "token": "monzo", "source": "gb-ho", "country": "GB",
         "sponsor_register": "GB-HO", "match": "name"},
        {"name": "Plain", "ats": "greenhouse", "token": "plain", "source": "jobsync"},
    ]
    monkeypatch.setattr(hk, "load_companies", lambda p: [dict(r) for r in rows])

    def fake_board(col: Any, b: dict[str, Any]) -> None:
        hk._offer_all(col, "greenhouse", [dict(
            title="Machine Learning Engineer", url="https://x.example/%s/1" % b["token"], loc="London, UK",
            posted="", desc="Build models in London.", company=b["name"], board=b["token"])])

    monkeypatch.setitem(hk._BOARD_FN, "greenhouse", fake_board)
    col = hk.run(_args(sources="greenhouse", universe=False, work_countries="IN", history="none",
                       titles="machine learning engineer", days=3650))
    assert [(r["company"], r["row"], r.get("sponsor_register")) for r in col.results] == [
        ("Monzo", "visa_maybe_register", "GB-HO")]
    assert getattr(col.tl, "register", None) is None


def test_load_companies_keeps_register_fields(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text(json.dumps([{"name": "Monzo", "ats": "greenhouse", "board": "monzo", "source": "gb-ho",
                              "country": "GB", "sponsor_register": "GB-HO", "match": "name"}]), encoding="utf-8")
    (row,) = hk.load_companies(p)
    assert row["token"] == "monzo" and row["sponsor_register"] == "GB-HO"


def test_slug_matched_board_never_carries_the_register_into_the_hunt():
    """Review 2026-10-19: "Academia Limited" (UK sponsor) slug-matched Academia.edu's board, so its
    London jobs would have cited another company's licence. Only a NAME match is tagged."""
    assert hk.board_register({"sponsor_register": "GB-HO", "country": "GB", "match": "slug"}) == ("", frozenset())
    assert hk.board_register({"sponsor_register": "GB-HO", "country": "GB", "match": "name"}) == (
        "GB-HO", frozenset({"GB"}))


def test_a_right_to_work_lock_rules_out_a_maybe_even_with_no_work_countries():
    """Review 2026-10-19: with no --work-countries the lock check was skipped."""
    p = hk.make_prefs("", True, True, True, "", "", False)
    reg = frozenset({"GB"})
    locked = "You must have the right to work in the UK."
    assert hk.classify_location("London", locked, p, False, reg) is None
    assert hk.classify_location("London", "Build agents with us.", p, False, reg) == "visa_maybe_register"


def test_host_label_boards_refuse_a_token_that_is_not_one_dns_label():
    """A board token goes into the host name ({token}.recruitee.com): a dot, slash or '@' would
    point the request at another host, so such a row is skipped (security review 2026-10-19)."""
    from src.hunt_kit.kit_ats import _host_label

    assert _host_label({"token": "acme-labs"}) == "acme-labs"
    for bad in ("evil.com/x?", "a@b", "a.b", "-acme", "acme-", "", "x" * 64):
        assert _host_label({"token": bad}) == "", bad

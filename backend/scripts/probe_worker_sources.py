"""One-off live probe for the pillar3-fixes worker's 11 sources.
Fetches raw payloads directly (bypassing the pipeline) to inspect what
fields are actually available. Read-only, no DB writes. DELETE when done.
"""
import asyncio
import json
import re
import sys

import aiohttp

UA = "Job360/1.0 (UK Job Search Aggregator)"
HEADERS = {"User-Agent": UA}


async def get_text(session, url, params=None, headers=None):
    h = dict(HEADERS)
    if headers:
        h.update(headers)
    try:
        async with session.get(url, params=params, headers=h, timeout=aiohttp.ClientTimeout(total=20)) as r:
            print(f"  status={r.status} url={r.url}")
            return await r.text()
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


async def probe_realworkfromanywhere(session):
    print("\n=== realworkfromanywhere: RSS + detail ld+json ===")
    xml = await get_text(session, "https://www.realworkfromanywhere.com/rss.xml")
    if not xml:
        return
    m = re.search(r"<link>(https://[^<]+)</link>", xml)
    links = re.findall(r"<link>(https://[^<]+)</link>", xml)
    # first real job link (skip the channel link, usually first)
    job_link = links[1] if len(links) > 1 else (links[0] if links else None)
    print("sample job link:", job_link)
    if not job_link:
        return
    page = await get_text(session, job_link)
    if not page:
        return
    m2 = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.DOTALL)
    if not m2:
        print("no ld+json found")
        return
    try:
        data = json.loads(m2.group(1))
        print("ld+json keys:", sorted(data.keys()) if isinstance(data, dict) else type(data))
        for k in ("employmentType", "jobLocationType", "applicantLocationRequirements", "validThrough", "baseSalary", "datePosted"):
            print(f"  {k} = {data.get(k) if isinstance(data, dict) else None}")
    except Exception as e:
        print("json parse error", e)


async def probe_uni_jobs(session):
    print("\n=== uni_jobs: Cambridge RSS + detail HTML ===")
    xml = await get_text(session, "http://www.jobs.cam.ac.uk/job/?format=rss")
    if not xml:
        return
    links = re.findall(r"<link>(https?://[^<]+)</link>", xml)
    job_link = links[1] if len(links) > 1 else (links[0] if links else None)
    print("sample job link:", job_link)
    if not job_link:
        return
    page = await get_text(session, job_link)
    if not page:
        return
    print("page length:", len(page))
    for label in ("Salary", "Closing date", "Reference", "Category", "Department", "Employment type", "Hours", "Duration", "Fixed-term", "Contract"):
        m = re.search(rf"<h6>\s*{re.escape(label)}\s*</h6>\s*<p>\s*([^<]+?)\s*</p>", page, re.IGNORECASE)
        print(f"  {label}: {m.group(1) if m else None}")
    # Dump all h6 sidebar labels actually present
    h6s = re.findall(r"<h6>\s*([^<]+?)\s*</h6>", page)
    print("  ALL h6 labels found:", h6s)


async def probe_linkedin(session):
    print("\n=== linkedin: guest search page ===")
    params = {"keywords": "software engineer", "location": "United Kingdom", "f_TPR": "r604800", "start": "0"}
    html = await get_text(session, "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search", params=params)
    if not html:
        return
    print("html length:", len(html))
    print("has base-search-card__title:", "base-search-card__title" in html)
    print("has base-search-card__subtitle:", "base-search-card__subtitle" in html)
    titles = re.findall(r'<h3[^>]*class="[^"]*base-search-card__title[^"]*"[^>]*>\s*([^<]+)', html, re.IGNORECASE)
    companies = re.findall(r'<h4[^>]*class="[^"]*base-search-card__subtitle[^"]*"[^>]*>\s*([^<]+)', html, re.IGNORECASE)
    print("titles found:", len(titles), titles[:3])
    print("companies found:", len(companies), companies[:3])
    if not companies:
        # look at what h4 tags actually look like
        h4s = re.findall(r'<h4[^>]{0,200}', html, re.IGNORECASE)
        print("sample h4 tags:", h4s[:5])
    links = re.findall(r'href="(https://[^"]*linkedin\.com/jobs/view/[^"]*)"', html, re.IGNORECASE)
    print("links found:", len(links))
    if links:
        job_url = links[0].split("?")[0]
        print("fetching detail:", job_url)
        page = await get_text(session, job_url)
        if page:
            print("detail page length:", len(page))
            m = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.DOTALL)
            print("has ld+json:", bool(m))
            if m:
                try:
                    data = json.loads(m.group(1))
                    print("ld+json keys:", sorted(data.keys()) if isinstance(data, dict) else type(data))
                except Exception as e:
                    print("parse err", e, "len:", len(m.group(1)))


async def probe_climatebase(session):
    print("\n=== climatebase: __NEXT_DATA__ ===")
    html = await get_text(session, "https://climatebase.org/jobs", params={"l": "United Kingdom", "q": "data"})
    if not html:
        return
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        print("no NEXT_DATA")
        return
    data = json.loads(m.group(1))
    page_props = data.get("props", {}).get("pageProps", {})
    job_list = page_props.get("jobs", [])
    print("job count:", len(job_list))
    if job_list:
        item = job_list[0]
        print("job item keys:", sorted(item.keys()))
        for k in ("description", "description_short", "responsibilities", "id", "slug", "url"):
            v = item.get(k)
            print(f"  {k}: {str(v)[:200] if v else v}")


async def probe_eightykhours(session):
    print("\n=== eightykhours: Algolia hit fields ===")
    app_id = "W6KM1UDIB3"
    api_key = "d1d7f2c8696e7b36837d5ed337c4a319"
    url = f"https://{app_id}-dsn.algolia.net/1/indexes/jobs_prod/query"
    headers = {"X-Algolia-Application-Id": app_id, "X-Algolia-API-Key": api_key, "Content-Type": "application/json"}
    body = {"query": "software engineer", "hitsPerPage": 5}
    async with session.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
        print("status", r.status)
        data = await r.json()
    hits = data.get("hits", [])
    print("hits:", len(hits))
    if hits:
        h = hits[0]
        print("hit keys:", sorted(h.keys()))
        for k in ("salary", "salary_min", "salary_max", "compensation", "remote", "workplace", "is_remote", "location_type", "job_types", "tags", "employment_type"):
            print(f"  {k}: {h.get(k)}")


async def probe_bcs_jobs(session):
    print("\n=== bcs_jobs: page structure ===")
    for url in ("https://www.bcs.org/jobs-board", "https://www.bcs.org/jobs"):
        html = await get_text(session, url)
        if html:
            print(f"{url} -> len={len(html)}")
            print(html[:500])
            break


async def probe_aijobs_ai(session):
    print("\n=== aijobs_ai: detail ld+json extra fields ===")
    html = await get_text(session, "https://aijobs.ai/")
    if not html:
        return
    m = re.search(r'<a\s[^>]*href="((?:https?://(?:www\.)?aijobs\.ai)?/jobs?/[^"#?]+)"', html, re.IGNORECASE)
    if not m:
        print("no job link found")
        return
    path = m.group(1)
    url = path if path.startswith("http") else f"https://aijobs.ai{path}"
    print("detail url:", url)
    page = await get_text(session, url)
    if not page:
        return
    m2 = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.DOTALL)
    if not m2:
        print("no ld+json")
        return
    raw = m2.group(1)
    try:
        data = json.loads(raw)
        print("ld+json keys:", sorted(data.keys()) if isinstance(data, dict) else type(data))
    except Exception as e:
        print("strict parse failed:", e)
        for k in ("employmentType", "jobLocationType", "workplaceType", "hiringOrganization", "jobLocation", "baseSalary"):
            mm = re.search(rf'"{k}"\s*:\s*("[^"]*"|\{{[^}}]*\}}|\[[^\]]*\])', raw)
            print(f"  {k} (regex): {mm.group(1)[:150] if mm else None}")


async def probe_nhs_jobs(session):
    print("\n=== nhs_jobs: XML detail + detail page ===")
    xml = await get_text(session, "https://www.jobs.nhs.uk/api/v1/search_xml", params={"keywords": "nurse", "page": "1"})
    if not xml:
        return
    print("xml length:", len(xml))
    m = re.search(r"<url>([^<]+)</url>", xml)
    print("sample url:", m.group(1) if m else None)
    m2 = re.search(r"<description>([^<]*)</description>", xml)
    desc = m2.group(1) if m2 else ""
    print("sample description len:", len(desc), repr(desc[:250]))
    if m:
        page = await get_text(session, m.group(1))
        if page:
            print("detail page length:", len(page))
            ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.DOTALL)
            print("has ld+json on detail page:", bool(ld))


async def probe_weworkremotely(session):
    print("\n=== weworkremotely: RSS fields ===")
    xml = await get_text(session, "https://weworkremotely.com/remote-jobs.rss")
    if not xml:
        return
    # dump all distinct child tag names inside first <item>
    m = re.search(r"<item>(.*?)</item>", xml, re.DOTALL)
    if m:
        tags = re.findall(r"<(\w+)[ >]", m.group(1))
        print("item child tags:", sorted(set(tags)))
        for t in ("salary", "category", "compensation"):
            mm = re.search(rf"<{t}[^>]*>([^<]*)</{t}>", m.group(1))
            print(f"  {t}: {mm.group(1) if mm else 'NOT PRESENT'}")


async def probe_teaching_vacancies(session):
    print("\n=== teaching_vacancies: API item fields ===")
    data_text = await get_text(session, "https://teaching-vacancies.service.gov.uk/api/v1/jobs.json")
    if not data_text:
        return
    data = json.loads(data_text)
    jobs = data.get("jobs") or data.get("data") or []
    print("jobs:", len(jobs))
    if jobs:
        item = jobs[0]
        print("item keys:", sorted(item.keys()))
        for k in ("baseSalary", "workingPattern", "keyStage", "subject", "occupationalCategory", "jobLocationType", "phases", "subjects"):
            print(f"  {k}: {str(item.get(k))[:200]}")


async def main():
    targets = sys.argv[1:] or ["all"]
    async with aiohttp.ClientSession() as session:
        fns = {
            "realworkfromanywhere": probe_realworkfromanywhere,
            "uni_jobs": probe_uni_jobs,
            "linkedin": probe_linkedin,
            "climatebase": probe_climatebase,
            "eightykhours": probe_eightykhours,
            "bcs_jobs": probe_bcs_jobs,
            "aijobs_ai": probe_aijobs_ai,
            "nhs_jobs": probe_nhs_jobs,
            "weworkremotely": probe_weworkremotely,
            "teaching_vacancies": probe_teaching_vacancies,
        }
        run = fns.keys() if "all" in targets else targets
        for name in run:
            try:
                await fns[name](session)
            except Exception as e:
                print(f"\n=== {name}: TOP-LEVEL ERROR: {e} ===")


if __name__ == "__main__":
    asyncio.run(main())

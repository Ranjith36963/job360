import asyncio
import json
import re

import aiohttp


async def get_json(session, url, **kw):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20), **kw) as r:
            if r.status != 200:
                print(f"  [{r.status}] {url}")
                return None
            return await r.json()
    except Exception as e:
        print(f"  [ERR] {url}: {e}")
        return None


async def post_json(session, url, body, headers):
    try:
        async with session.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                print(f"  [{r.status}] {url}")
                return None
            return await r.json()
    except Exception as e:
        print(f"  [ERR] {url}: {e}")
        return None


async def get_text(session, url):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                print(f"  [{r.status}] {url}")
                return None
            return await r.text()
    except Exception as e:
        print(f"  [ERR] {url}: {e}")
        return None


async def main():
    async with aiohttp.ClientSession() as session:
        print("=== SMARTRECRUITERS wise detail: compensation field ===")
        data = await get_json(session, "https://api.smartrecruiters.com/v1/companies/wise/postings", params={"limit": "10"})
        if data and data.get("content"):
            for item in data["content"][:5]:
                detail = await get_json(session, f"https://api.smartrecruiters.com/v1/companies/wise/postings/{item.get('id')}")
                if detail:
                    print(item.get("name"), "-> compensation:", detail.get("compensation"), " experienceLevel:", detail.get("experienceLevel"))

        print("\n=== ASHBY workplaceType raw values (cohere) ===")
        data = await get_json(session, "https://api.ashbyhq.com/posting-api/job-board/cohere", params={"includeCompensation": "true"})
        if data and data.get("jobs"):
            jobs = data["jobs"]
            vals = {}
            for j in jobs:
                v = j.get("workplaceType")
                vals[v] = vals.get(v, 0) + 1
            print("workplaceType distribution:", vals)
            et_vals = {}
            for j in jobs:
                v = j.get("employmentType")
                et_vals[v] = et_vals.get(v, 0) + 1
            print("employmentType distribution:", et_vals)

        print("\n=== WORKDAY bulletFields salary check + timeType across companies ===")
        for tenant, wd, site in [("astrazeneca", "wd3", "Careers"), ("nvidia", "wd5", "NVIDIAExternalCareerSite")]:
            body = {"appliedFacets": {}, "searchText": "engineer", "limit": 20, "offset": 0}
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            data = await post_json(session, f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs", body, headers)
            if data and data.get("jobPostings"):
                jobs = data["jobPostings"]
                tt_vals = set(j.get("timeType") for j in jobs if j.get("timeType"))
                money_bullets = [b for j in jobs for b in (j.get("bulletFields") or []) if re.search(r"[$£€]", b)]
                print(f"{tenant}: n={len(jobs)} timeType_vals={tt_vals} money_bullets_sample={money_bullets[:3]}")

        print("\n=== SUCCESSFACTORS: real job detail page JSON-LD ===")
        text = await get_text(session, "https://jobs.baesystems.com/sitemap.xml")
        if text:
            m = re.search(r"<loc>([^<]*sitemap[^<]*)</loc>", text)
            print("first child sitemap:", m.group(1) if m else None)
            if m:
                child = await get_text(session, m.group(1))
                if child:
                    joburl_m = re.search(r"<loc>([^<]*/job/[^<]*)</loc>", child)
                    print("sample job url:", joburl_m.group(1) if joburl_m else None)
                    if joburl_m:
                        page = await get_text(session, joburl_m.group(1))
                        if page:
                            ld = re.search(r'<script type=.application/ld\+json.>(.*?)</script>', page, re.DOTALL)
                            if ld:
                                try:
                                    d = json.loads(ld.group(1))
                                    print("JSON-LD keys:", sorted(d.keys()))
                                    print("datePosted:", d.get("datePosted"))
                                    print("employmentType:", d.get("employmentType"))
                                    print("validThrough:", d.get("validThrough"))
                                    # SHAPE only — this probe asks whether the
                                    # JSON-LD carries a pay block, never what it
                                    # says. Printing the figure adds nothing to
                                    # the answer and puts pay data in logs.
                                    _pay = d.get("baseSalary")
                                    print(
                                        "baseSalary present:",
                                        "absent" if not _pay
                                        else f"dict(keys={sorted(_pay)})"
                                        if isinstance(_pay, dict)
                                        else type(_pay).__name__,
                                    )
                                except Exception as e:
                                    print("json err", e, ld.group(1)[:500])
                            else:
                                print("no JSON-LD found; page len", len(page))

if __name__ == "__main__":
    asyncio.run(main())

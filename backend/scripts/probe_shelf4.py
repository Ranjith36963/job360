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
        print("=== SUCCESSFACTORS occupationalCategory + validThrough across several BAE jobs ===")
        # GUARD BOTH DEREFERENCES. `get_text` returns None on any non-200 or
        # exception, and `re.search` returns None when the sitemap's shape
        # changes — so an upstream outage or a redesign crashed this probe with
        # an AttributeError instead of saying which of the two had happened. A
        # probe that cannot report its own failure is not a probe.
        # (CodeRabbit, PR #388.)
        text = await get_text(session, "https://jobs.baesystems.com/sitemap.xml")
        if not text:
            print("-> sitemap.xml did not answer (non-200 or network error)")
            return
        m = re.search(r"<loc>([^<]*sitemap1[^<]*)</loc>", text)
        if not m:
            print("-> sitemap.xml no longer contains a `sitemap1` <loc> — its shape changed")
            return
        child = await get_text(session, m.group(1))
        if not child:
            print("-> the child sitemap did not answer")
            return
        job_urls = re.findall(r"<loc>([^<]*/job/[^<]*)</loc>", child)[:8]
        for u in job_urls:
            page = await get_text(session, u)
            if not page:
                continue
            ld = re.search(r'<script type=.application/ld\+json.>(.*?)</script>', page, re.DOTALL)
            if not ld:
                print(u, "-> no JSON-LD")
                continue
            try:
                d = json.loads(ld.group(1))
            except Exception as e:
                print(u, "json err", e)
                continue
            print(u.split("/")[-1][:40], "| occCat:", d.get("occupationalCategory"), "| validThrough:", d.get("validThrough"), "| datePosted:", d.get("datePosted"))

        print("\n=== QinetiQ microdata page: check for itemprop datePosted/employmentType ===")
        text2 = await get_text(session, "https://careers.qinetiq.com/sitemap.xml")
        if text2:
            job_urls2 = re.findall(r"<loc>([^<]*/job/[^<]*)</loc>", text2)[:3]
            if not job_urls2:
                # maybe sitemap index
                idx = re.findall(r"<loc>([^<]+)</loc>", text2)[:3]
                print("qinetiq sitemap entries sample:", idx)
                for su in idx:
                    child2 = await get_text(session, su)
                    if child2:
                        j2 = re.findall(r"<loc>([^<]*/job/[^<]*)</loc>", child2)[:3]
                        if j2:
                            job_urls2 = j2
                            break
            for u in job_urls2:
                page = await get_text(session, u)
                if not page:
                    continue
                print(u)
                for prop in ["datePosted", "employmentType", "validThrough", "baseSalary", "occupationalCategory"]:
                    hits = re.findall(rf'itemprop=.{prop}.[^>]*>([^<]*)', page)
                    print(f"  itemprop {prop}: {hits[:2]}")
                ld2 = re.search(r'<script type=.application/ld\+json.>(.*?)</script>', page, re.DOTALL)
                print("  has JSON-LD:", bool(ld2))

        print("\n=== SMARTRECRUITERS experienceLevel distribution across companies ===")
        for slug in ["wise", "revolut", "checkout", "astrazeneca", "samsung-r-and-d-institute-uk"]:
            data = await get_json(session, f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": "100"})
            if not data or not data.get("content"):
                continue
            vals = {}
            for item in data["content"]:
                el = item.get("experienceLevel")
                v = el.get("id") if isinstance(el, dict) else None
                vals[v] = vals.get(v, 0) + 1
            print(f"{slug}: n={len(data['content'])} experienceLevel dist={vals}")

if __name__ == "__main__":
    asyncio.run(main())

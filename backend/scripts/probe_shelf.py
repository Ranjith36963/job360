"""One-off probe: fetch real ATS payloads and inspect raw field availability
for shelves my sources currently leave 0%. DELETE after use."""
import asyncio
import json

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
        print("=== GREENHOUSE deepmind ===")
        data = await get_json(session, "https://boards-api.greenhouse.io/v1/boards/deepmind/jobs?content=true")
        if data and data.get("jobs"):
            job = data["jobs"][0]
            print("keys:", sorted(job.keys()))
            print(json.dumps({k: v for k, v in job.items() if k not in ("content",)}, indent=2)[:3000])
            # look for pay ranges / metadata
            for j in data["jobs"][:20]:
                md = j.get("metadata")
                if md:
                    print("METADATA sample:", json.dumps(md, indent=2)[:1500])
                    break
            pay_count = sum(1 for j in data["jobs"] if j.get("pay_input_ranges"))
            print("pay_input_ranges present on", pay_count, "/", len(data["jobs"]))

        print("\n=== LEVER spotify ===")
        data = await get_json(session, "https://api.lever.co/v0/postings/spotify", params={"mode": "json"})
        if data:
            job = data[0]
            print("keys:", sorted(job.keys()))
            print("categories:", job.get("categories"))
            print("salaryRange:", job.get("salaryRange"))
            print("workplaceType:", job.get("workplaceType"))
            # check fill rates
            n = len(data)
            commitment_n = sum(1 for j in data if (j.get("categories") or {}).get("commitment"))
            salary_n = sum(1 for j in data if j.get("salaryRange"))
            print(f"n={n} commitment={commitment_n} salaryRange={salary_n}")

        print("\n=== WORKABLE huggingface ===")
        data = await get_json(session, "https://apply.workable.com/api/v1/widget/accounts/huggingface", params={"details": "true"})
        if data and data.get("jobs"):
            job = data["jobs"][0]
            print("keys:", sorted(job.keys()))
            print(json.dumps({k: v for k, v in job.items() if k != "description"}, indent=2)[:3000])
            n = len(data["jobs"])
            exp_n = sum(1 for j in data["jobs"] if j.get("experience"))
            remote_n = sum(1 for j in data["jobs"] if "remote" in j or "telecommuting" in j)
            print(f"n={n} experience={exp_n}")
            print("remote-ish keys present:", [k for k in job.keys() if "remote" in k.lower() or "location" in k.lower() or "workplace" in k.lower()])

        print("\n=== ASHBY anthropic ===")
        data = await get_json(session, "https://api.ashbyhq.com/posting-api/job-board/anthropic", params={"includeCompensation": "true"})
        if data and data.get("jobs"):
            job = data["jobs"][0]
            print("keys:", sorted(job.keys()))
            print("employmentType:", job.get("employmentType"))
            print("workplaceType/isRemote:", job.get("isRemote"), job.get("workplaceType"))
            print(json.dumps({k: v for k, v in job.items() if k not in ("descriptionPlain", "descriptionHtml", "compensation")}, indent=2)[:2500])

        print("\n=== SMARTRECRUITERS wise ===")
        data = await get_json(session, "https://api.smartrecruiters.com/v1/companies/wise/postings", params={"limit": "10"})
        if data and data.get("content"):
            job = data["content"][0]
            print("keys:", sorted(job.keys()))
            print(json.dumps(job, indent=2)[:2500])
            detail = await get_json(session, f"https://api.smartrecruiters.com/v1/companies/wise/postings/{job.get('id')}")
            if detail:
                print("DETAIL keys:", sorted(detail.keys()))
                print(json.dumps({k: v for k, v in detail.items() if k != 'jobAd'}, indent=2)[:2500])

        print("\n=== PINPOINT arm ===")
        data = await get_json(session, "https://arm.pinpointhq.com/postings.json")
        postings = data.get("data", data) if isinstance(data, dict) else data
        if postings:
            job = postings[0]
            print("keys:", sorted(job.keys()))
            print(json.dumps({k: v for k, v in job.items() if k not in ("description", "key_responsibilities", "skills_knowledge_expertise", "benefits")}, indent=2)[:2500])
            n = len(postings)
            for f in ("worktype", "work_type", "remote", "job_type", "category", "department", "seniority", "level"):
                cnt = sum(1 for j in postings if j.get(f))
                if cnt:
                    print(f"  field {f}: {cnt}/{n}")

        print("\n=== RECRUITEE wonderkind ===")
        data = await get_json(session, "https://wonderkind.recruitee.com/api/offers/")
        if data and data.get("offers"):
            job = data["offers"][0]
            print("keys:", sorted(job.keys()))
            print(json.dumps({k: v for k, v in job.items() if k not in ("description", "requirements")}, indent=2)[:3000])

        print("\n=== WORKDAY astrazeneca ===")
        body = {"appliedFacets": {}, "searchText": "engineer", "limit": 5, "offset": 0}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        data = await post_json(session, "https://astrazeneca.wd3.myworkdayjobs.com/wday/cxs/astrazeneca/Careers/jobs", body, headers)
        if data and data.get("jobPostings"):
            job = data["jobPostings"][0]
            print("keys:", sorted(job.keys()))
            print(json.dumps(job, indent=2)[:2000])
            ext_path = job.get("externalPath")
            detail = await get_json(session, f"https://astrazeneca.wd3.myworkdayjobs.com/wday/cxs/astrazeneca/Careers{ext_path}")
            if detail:
                info = detail.get("jobPostingInfo", {})
                print("jobPostingInfo keys:", sorted(info.keys()))
                print(json.dumps({k: v for k, v in info.items() if k != "jobDescription"}, indent=2)[:2500])

        print("\n=== PERSONIO flatpay ===")
        text = await get_text(session, "https://flatpay.jobs.personio.de/xml?language=en")
        if text:
            print(text[:3000])

if __name__ == "__main__":
    asyncio.run(main())

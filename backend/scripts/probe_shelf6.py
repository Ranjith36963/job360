import asyncio

import aiohttp


async def get_json(session, url, **kw):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20), **kw) as r:
            if r.status != 200:
                return None
            return await r.json()
    except Exception:
        return None


async def get_text(session, url):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                return None
            return await r.text()
    except Exception:
        return None


async def main():
    async with aiohttp.ClientSession() as session:
        print("=== RECRUITEE employment_type_code + tags across companies ===")
        for slug in ["wonderkind", "dckgroup", "transperfect", "itxuklimited", "theentouragegroup"]:
            data = await get_json(session, f"https://{slug}.recruitee.com/api/offers/")
            if not data or "offers" not in data:
                continue
            offers = data["offers"]
            n = len(offers)
            et_vals = {}
            for o in offers:
                v = o.get("employment_type_code")
                et_vals[v] = et_vals.get(v, 0) + 1
            tags_n = sum(1 for o in offers if o.get("tags"))
            sample_tags = next((o.get("tags") for o in offers if o.get("tags")), None)
            print(f"{slug}: n={n} employment_type_code={et_vals} tags_filled={tags_n} sample_tags={sample_tags}")

        print("\n=== PINPOINT employment_type_text fill across boards ===")
        for slug in ["arm", "priorygroup", "davies", "networkplus", "natcen"]:
            data = await get_json(session, f"https://{slug}.pinpointhq.com/postings.json")
            postings = data.get("data", data) if isinstance(data, dict) else data
            if not postings:
                continue
            n = len(postings)
            et = sum(1 for p in postings if p.get("employment_type_text") or p.get("employment_type"))
            wt = sum(1 for p in postings if p.get("workplace_type_text") or p.get("workplace_type"))
            print(f"{slug}: n={n} employment_type_filled={et} workplace_type_filled={wt}")

        print("\n=== PERSONIO full XML schema (raw tags) ===")
        text = await get_text(session, "https://flatpay.jobs.personio.de/xml?language=en")
        if text:
            import re
            first_pos = re.search(r"<position>.*?</position>", text, re.DOTALL)
            if first_pos:
                print(first_pos.group(0)[:2500])

if __name__ == "__main__":
    asyncio.run(main())

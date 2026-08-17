import asyncio
import re

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
        print("=== PINPOINT employment_type_text DISTINCT VALUES across boards ===")
        for slug in ["arm", "priorygroup", "davies", "networkplus", "natcen"]:
            data = await get_json(session, f"https://{slug}.pinpointhq.com/postings.json")
            postings = data.get("data", data) if isinstance(data, dict) else data
            if not postings:
                continue
            vals = {}
            for p in postings:
                v = p.get("employment_type_text") or p.get("employment_type")
                vals[v] = vals.get(v, 0) + 1
            print(f"{slug}: {vals}")

        print("\n=== PERSONIO occupationCategory + keywords fill across companies ===")
        for slug in ["personio", "flatpay", "stark", "merantix", "intigriti", "olio", "gridx", "maltego"]:
            text = await get_text(session, f"https://{slug}.jobs.personio.de/xml?language=en")
            if not text:
                continue
            positions = re.findall(r"<position>.*?</position>", text, re.DOTALL)
            occ_vals = set()
            kw_n = 0
            for p in positions:
                m = re.search(r"<occupationCategory>([^<]*)</occupationCategory>", p)
                if m and m.group(1).strip():
                    occ_vals.add(m.group(1).strip())
                m2 = re.search(r"<keywords>([^<]*)</keywords>", p)
                if m2 and m2.group(1).strip():
                    kw_n += 1
            print(f"{slug}: n={len(positions)} occupationCategory_vals={occ_vals} keywords_filled={kw_n}")

if __name__ == "__main__":
    asyncio.run(main())

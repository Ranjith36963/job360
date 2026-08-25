import asyncio

import aiohttp


async def get_json(session, url, **kw):
    """Fetch JSON, and SAY when it could not be fetched.

    This returned a bare None for a 404, a 500, a timeout and a parse error
    alike, and every caller then did `if not data: continue`. So "the provider
    is down" and "the provider has no such field" printed as the same silence —
    a probe reporting an outage as absent data is worse than no probe, because
    it produces a confident conclusion about the wrong thing.
    (CodeRabbit, PR #388.)
    """
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20), **kw) as r:
            if r.status != 200:
                print(f"   !! {url} -> HTTP {r.status} (provider failure, NOT missing data)")
                return None
            return await r.json()
    except Exception as exc:  # noqa: BLE001 — a probe reports every failure
        # Type only: these URLs can carry credentials in a query string.
        print(f"   !! {url.split('?')[0]} -> {type(exc).__name__} (provider failure, NOT missing data)")
        return None


async def main():
    async with aiohttp.ClientSession() as session:
        print("=== WORKABLE telecommuting key presence + function values ===")
        for slug in ["huggingface", "appvia", "suade", "yapily"]:
            data = await get_json(session, f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"})
            if not data or "jobs" not in data:
                continue
            jobs = data["jobs"]
            has_key = sum(1 for j in jobs if "telecommuting" in j)
            true_n = sum(1 for j in jobs if j.get("telecommuting") is True)
            false_n = sum(1 for j in jobs if j.get("telecommuting") is False)
            funcs = {}
            for j in jobs:
                f = j.get("function")
                funcs[f] = funcs.get(f, 0) + 1
            print(f"{slug}: n={len(jobs)} has_key={has_key} true={true_n} false={false_n} functions={funcs}")

        print("\n=== LEVER commitment vocab across more companies ===")
        for slug in ["mistral", "healx", "palantir", "spotify", "joinzoe"]:
            data = await get_json(session, f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
            if not data or not isinstance(data, list):
                continue
            vals = {}
            for j in data:
                c = (j.get("categories") or {}).get("commitment")
                vals[c] = vals.get(c, 0) + 1
            print(f"{slug}: n={len(data)} commitment={vals}")

if __name__ == "__main__":
    asyncio.run(main())

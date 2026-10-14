"""Opt-in big job boards through the python-jobspy library (MIT, speedyapply/JobSpy).

Importing this module does no network and no file IO. `jobspy` is imported lazily
inside run_boards. We only call scrape_jobs; we never pass proxies, cookies,
credentials or a login. No ranking or scoring here.
"""
from __future__ import annotations

import sys
from typing import Any

WARNING = (
    "These sites' terms forbid automated access. The kit reads only their public "
    "no-login pages - never your account. Your internet address may be blocked "
    "for a while. You chose to turn this on."
)

KNOWN_SITES = ("linkedin", "indeed", "glassdoor", "google", "naukri", "bayt", "zip_recruiter")
_SITE_ALIASES = {"ziprecruiter": "zip_recruiter", "zip-recruiter": "zip_recruiter",
                 "google_jobs": "google", "googlejobs": "google"}
MAX_RESULTS = 200

# ISO2 -> JobSpy country name (names JobSpy's Country enum accepts).
ISO2_TO_JOBSPY = {
    "AR": "argentina", "AU": "australia", "AT": "austria", "BH": "bahrain", "BE": "belgium",
    "BR": "brazil", "CA": "canada", "CL": "chile", "CN": "china", "CO": "colombia",
    "CR": "costa rica", "CZ": "czech republic", "DK": "denmark", "EC": "ecuador",
    "EG": "egypt", "FI": "finland", "FR": "france", "DE": "germany", "GR": "greece",
    "HK": "hong kong", "HU": "hungary", "IN": "india", "ID": "indonesia", "IE": "ireland",
    "IL": "israel", "IT": "italy", "JP": "japan", "KW": "kuwait", "LU": "luxembourg",
    "MY": "malaysia", "MX": "mexico", "MA": "morocco", "NL": "netherlands",
    "NZ": "new zealand", "NG": "nigeria", "NO": "norway", "OM": "oman", "PK": "pakistan",
    "PA": "panama", "PE": "peru", "PH": "philippines", "PL": "poland", "PT": "portugal",
    "QA": "qatar", "RO": "romania", "SA": "saudi arabia", "SG": "singapore",
    "ZA": "south africa", "KR": "south korea", "ES": "spain", "SE": "sweden",
    "CH": "switzerland", "TW": "taiwan", "TH": "thailand", "TR": "turkey", "UA": "ukraine",
    "AE": "united arab emirates", "GB": "uk", "UK": "uk", "US": "usa", "UY": "uruguay",
    "VE": "venezuela", "VN": "vietnam",
}


def _norm_site(site: str) -> str:
    s = str(site).strip().lower()
    return _SITE_ALIASES.get(s, s)


def _s(value: Any) -> str:
    """Safe string: None / NaN -> ''."""
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    text = str(value)
    return "" if text.lower() in ("nan", "nat", "none") else text.strip()


def _records(result: Any) -> list[dict[str, Any]]:
    """DataFrame (or list of dicts) -> list of plain dicts."""
    if result is None:
        return []
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        out = to_dict("records")
        return [r for r in out if isinstance(r, dict)]
    return []


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = _s(value).lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return None


def _to_job(row: dict[str, Any], site: str) -> dict[str, Any] | None:
    """Map one JobSpy row to a kit Job dict. Prefers the direct employer URL."""
    url = _s(row.get("job_url_direct")) or _s(row.get("job_url"))
    title = _s(row.get("title"))
    if not url or not title:
        return None
    posted = _s(row.get("date_posted"))[:10]
    extra = {k: _s(row.get(k)) for k in ("job_url", "min_amount", "max_amount", "currency",
                                        "interval", "job_type", "id")
             if _s(row.get(k))}
    return {
        "title": title,
        "company": _s(row.get("company")),
        "url": url,
        "location": _s(row.get("location")),
        "posted": posted,
        "description": _s(row.get("description")),
        "source": f"jobspy:{_s(row.get('site')) or site}",
        "board": _s(row.get("site")) or site,
        "remote": _bool_or_none(row.get("is_remote")),
        "visa_flag": None,
        "extra": extra,
    }


def run_boards(sites: list[str], queries: list[str], *, days: int, remote: bool,
               countries: list[str], results_wanted: int, full_text: bool,
               stats: dict[str, Any]) -> list[dict[str, Any]]:
    """Search the chosen big boards via python-jobspy. Never raises.

    stats gets "tried", "ok", "jobs" plus per-site keys "<site>_tried/_ok/_jobs"
    and "notes" (list of strings) when a site returns nothing or fails.
    """
    try:
        import jobspy  # type: ignore  # lazy: heavy, optional
    except Exception:  # noqa: BLE001 - any import failure means "not available"
        print("pip install python-jobspy", file=sys.stderr)
        return []
    print(WARNING, file=sys.stderr)
    for key in ("tried", "ok", "jobs"):
        stats.setdefault(key, 0)
    notes: list[str] = stats.setdefault("notes", [])

    chosen: list[str] = []
    for s in sites or []:
        n = _norm_site(s)
        if n in KNOWN_SITES and n not in chosen:
            chosen.append(n)
        elif n not in KNOWN_SITES:
            notes.append(f"{s}: unknown site, skipped")
    iso = [c.strip().upper() for c in (countries or []) if c and c.strip()]
    names = [ISO2_TO_JOBSPY[c] for c in iso if c in ISO2_TO_JOBSPY]
    # Plain searches per work country (is_remote left unset: JobSpy treats True as remote-ONLY),
    # plus one explicit remote search.
    plans: list[tuple[str, bool]] = [(n, False) for n in names] or [("", False)]
    if remote:
        plans.append((names[0] if names else "", True))
    wanted = max(1, min(int(results_wanted or 15), MAX_RESULTS))
    hours = max(1, int(days) * 24) if days else None

    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for site in chosen:
        st = {"tried": 0, "ok": 0, "jobs": 0}
        for query in queries or []:
            for cname, remote_only in plans:
                kwargs: dict[str, Any] = {
                    "site_name": [site],
                    "search_term": query,
                    "results_wanted": wanted,
                    "linkedin_fetch_description": bool(full_text),
                    "description_format": "markdown",
                }
                if remote_only:
                    kwargs["is_remote"] = True
                if hours:
                    kwargs["hours_old"] = hours
                if cname:
                    kwargs["country_indeed"] = cname
                    kwargs["location"] = cname
                st["tried"] += 1
                try:
                    rows = _records(jobspy.scrape_jobs(**kwargs))
                except Exception as exc:  # noqa: BLE001 - a blocked site must not stop others
                    notes.append(f"{site}: failed ({type(exc).__name__})")
                    continue
                st["ok"] += 1
                for row in rows:
                    job = _to_job(row, site)
                    if job and job["url"] not in seen:
                        seen.add(job["url"])
                        jobs.append(job)
                        st["jobs"] += 1
        if st["jobs"] == 0:
            extra = " (known broken upstream)" if site == "google" else ""
            notes.append(f"{site}: returned 0 jobs{extra}")
        for k, v in st.items():
            stats[k] += v
            stats[f"{site}_{k}"] = v
    return jobs

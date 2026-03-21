# SOURCES.md — Source Implementation Reference

Adding a new job source touches 5-7 files. Follow this checklist and use the matching template.

---

## Checklist (9 Steps)

1. **Create source file** in `src/sources/<name>.py` — use the matching template below
2. **Import** the class in `src/main.py`
3. **Add to `SOURCE_REGISTRY`** dict in `src/main.py`
4. **Add to `_build_sources()`** list in `src/main.py` (passing `search_config=sc`)
5. **Add `RATE_LIMITS`** entry in `src/config/settings.py`
6. **Add test** in `tests/test_sources.py` with `_TEST_CONFIG` and mocked HTTP
7. **Add mock URL** to `_mock_free_sources()` in `tests/test_main.py`
8. **Update count assertion** in `tests/test_cli.py` (currently 48)
9. **(If keyed)** Add env var to `src/config/settings.py` and `.env.example`
9. **(If ATS)** Add company slugs to `src/config/companies.py`

---

## Template 1: Free JSON API

Based on `src/sources/arbeitnow.py` — simplest source (~36 lines).

```python
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource

logger = logging.getLogger("job360.sources.<name>")


class <Name>Source(BaseJobSource):
    name = "<name>"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        data = await self._get_json("https://api.example.com/jobs")
        if not data:
            return []
        for item in data:
            text = f"{item.get('title', '')} {item.get('description', '')}".lower()
            if not any(kw in text for kw in self.relevance_keywords):
                continue
            date_found = item.get("created_at") or datetime.now(timezone.utc).isoformat()
            jobs.append(Job(
                title=item.get("title", ""),
                company=item.get("company", ""),
                location=item.get("location", ""),
                description=item.get("description", ""),
                apply_url=item.get("url", ""),
                source=self.name,
                date_found=date_found,
            ))
        logger.info(f"<Name>: found {len(jobs)} relevant jobs")
        return jobs
```

**Key points:**
- No `__init__` needed — `BaseJobSource.__init__` handles `session` and `search_config`
- Filter with `self.relevance_keywords` on title + description
- Return `[]` if API returns nothing

---

## Template 2: Keyed API

Based on `src/sources/reed.py` — shows API key pattern with early return.

```python
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource

logger = logging.getLogger("job360.sources.<name>")


class <Name>Source(BaseJobSource):
    name = "<name>"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = "", search_config=None):
        super().__init__(session, search_config=search_config)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.info("<Name>: no API key, skipping")
            return []
        jobs = []
        headers = {"Authorization": f"Bearer {self._api_key}"}
        for query in self.job_titles[:12]:
            data = await self._get_json(
                "https://api.example.com/search",
                params={"q": query, "location": "UK"},
                headers=headers,
            )
            if not data:
                continue
            for item in data.get("results", []):
                jobs.append(Job(
                    title=item.get("title", ""),
                    company=item.get("company", ""),
                    location=item.get("location", ""),
                    description=item.get("description", ""),
                    apply_url=item.get("url", ""),
                    source=self.name,
                    date_found=item.get("date") or datetime.now(timezone.utc).isoformat(),
                ))
        logger.info(f"<Name>: found {len(jobs)} jobs")
        return jobs
```

**Key points:**
- Custom `__init__` accepts `api_key` and `search_config=None`, passes `search_config` to super
- `is_configured` property for clean skip logic
- Return `[]` early with info log if no key
- Use `self.job_titles` for search queries

**Registration in `_build_sources()`:**
```python
<Name>Source(session, api_key=<NAME>_API_KEY, search_config=sc),
```

---

## Template 3: ATS Board

Based on `src/sources/greenhouse.py` — iterates over company slugs.

```python
import re
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.config.companies import <NAME>_COMPANIES, COMPANY_NAME_OVERRIDES

logger = logging.getLogger("job360.sources.<name>")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


class <Name>Source(BaseJobSource):
    name = "<name>"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config=None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else <NAME>_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://boards-api.example.com/v1/boards/{slug}/jobs"
            data = await self._get_json(url)
            if not data or "jobs" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in data["jobs"]:
                title = item.get("title", "")
                location = item.get("location", "")
                if not _is_uk_or_remote(location):
                    continue
                text = f"{title} {item.get('description', '')}".lower()
                if not any(kw in text for kw in self.relevance_keywords):
                    continue
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=item.get("description", "")[:5000],
                    apply_url=item.get("url", ""),
                    source=self.name,
                    date_found=item.get("updated_at") or datetime.now(timezone.utc).isoformat(),
                ))
        logger.info(f"<Name>: found {len(jobs)} relevant jobs across {len(self._companies)} companies")
        return jobs
```

**Key points:**
- Custom `__init__` accepts `companies` list with default from `companies.py`
- Uses `_is_uk_or_remote()` for location filtering
- Iterates over company slugs, fetches each board API
- Strip HTML tags from descriptions with regex

---

## Template 4: RSS/XML Feed

Based on `src/sources/nhs_jobs.py` — uses `_get_text()` + `ET.fromstring()`.

```python
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource

logger = logging.getLogger("job360.sources.<name>")


class <Name>Source(BaseJobSource):
    name = "<name>"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        xml_text = await self._get_text("https://example.com/feed.xml")
        if not xml_text:
            return []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"<Name>: XML parse error: {e}")
            return []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            text = f"{title} {desc}".lower()
            if not any(kw in text for kw in self.relevance_keywords):
                continue
            jobs.append(Job(
                title=title,
                company="<Source Name>",
                location="UK",
                description=desc,
                apply_url=link,
                source=self.name,
                date_found=item.findtext("pubDate") or datetime.now(timezone.utc).isoformat(),
            ))
        logger.info(f"<Name>: found {len(jobs)} relevant jobs")
        return jobs
```

**Key points:**
- Use `_get_text()` not `_get_json()` for XML content
- Parse with `xml.etree.ElementTree` (stdlib, no extra dependency)
- Extract `<item>` elements from RSS `<channel>`
- Handle `ET.ParseError` gracefully

---

## Template 5: HTML Scraper

Based on `src/sources/jobtensor.py` — uses regex parsing.

```python
import re
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource

logger = logging.getLogger("job360.sources.<name>")


class <Name>Source(BaseJobSource):
    name = "<name>"

    async def fetch_jobs(self) -> list[Job]:
        if not self.relevance_keywords:
            logger.info("<Name>: no keywords configured, skipping")
            return []
        query = self.search_queries[0] if self.search_queries else self.relevance_keywords[0]
        html = await self._get_text(
            "https://example.com/search",
            params={"q": query},
        )
        if not html:
            return []
        return self._parse_html(html)

    def _parse_html(self, html: str) -> list[Job]:
        jobs = []
        now = datetime.now(timezone.utc).isoformat()
        # Extract job links via regex
        link_pattern = re.compile(
            r'<a[^>]+href="(/jobs/[^"]*?)"[^>]*>([^<]+)</a>',
            re.IGNORECASE,
        )
        for match in link_pattern.finditer(html):
            path, title = match.group(1), match.group(2).strip()
            text = title.lower()
            if not any(kw in text for kw in self.relevance_keywords):
                continue
            jobs.append(Job(
                title=title,
                company="Unknown",
                location="UK",
                description=title,
                apply_url=f"https://example.com{path}",
                source=self.name,
                date_found=now,
            ))
        logger.info(f"<Name>: found {len(jobs)} relevant jobs")
        return jobs
```

**Key points:**
- Use `_get_text()` to fetch raw HTML
- Parse with regex — no BeautifulSoup dependency in this project
- Filter with `self.relevance_keywords`
- Return early if no keywords configured

---

## Helper: `_is_uk_or_remote()`

Located in `src/sources/base.py`. Returns `True` if location is UK, remote, or unknown:

```python
from src.sources.base import _is_uk_or_remote

_is_uk_or_remote("London, UK")      # True
_is_uk_or_remote("Remote")          # True
_is_uk_or_remote("")                # True (unknown = don't filter)
_is_uk_or_remote("New York, US")    # False (foreign indicator)
```

Uses `UK_TERMS`, `REMOTE_TERMS`, `FOREIGN_INDICATORS` from `src/filters/skill_matcher.py`.

---

## Test Template

Add to `tests/test_sources.py`:

```python
def test_<name>_source():
    async def _test():
        with aioresponses() as m:
            m.get(re.compile(r"https://api\.example\.com/.*"),
                  payload=[{"title": "AI Engineer", "company": "TestCo",
                            "location": "London", "url": "https://example.com/1"}],
                  repeat=True)
            async with aiohttp.ClientSession() as session:
                source = <Name>Source(session, search_config=_TEST_CONFIG)
                jobs = await source.fetch_jobs()
                assert isinstance(jobs, list)
    _run(_test())
```

Add mock URL to `_mock_free_sources()` in `tests/test_main.py`:
```python
m.get(re.compile(r"https://api\.example\.com/.*"), payload=[], repeat=True)
```

---

## Rate Limits

Add to `RATE_LIMITS` in `src/config/settings.py`:
```python
"<name>": {"concurrent": 2, "delay": 1.0},  # Free API
"<name>": {"concurrent": 1, "delay": 2.0},  # Keyed API
"<name>": {"concurrent": 1, "delay": 3.0},  # Scraper (be polite)
```

"""The connector check (owner, 2026-10-25): which helper tools the user's assistant has.

Job360 cannot see an assistant's other connectors; the assistant can (its own tool list). It reports
them with MCP ``report_capabilities`` and Job360 tells the user which useful ones to connect, from the
catalog file ``src/data/connector_catalog.json`` (DATA, not code: it never ranks, never calls a vendor
and holds no key). Unset = unknown, never guessed (rule #29).

Cards, in this order: a connector the user HAS that needs a sign-in or a paid plan gets a "fix it" card
first; then a missing essential (Gmail, and the user's own Job360 calendar link) stays until connected
or dismissed; then a missing recommended one (a job search, a people lookup) - at setup, or later only
when a run needed it, never more than one new suggestion a week. Optional ones are listed in Settings,
never pushed. A dismissed card never comes back. Scoped by ``user_id`` (rule #12).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlsplit

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

ASSISTANTS: tuple[str, ...] = ("claude", "chatgpt", "gemini", "other")
CATEGORIES: tuple[str, ...] = (
    "gmail", "calendar", "job_search", "people_lookup", "company_lookup", "browser", "web_search",
)
STATUSES: tuple[str, ...] = ("ok", "needs_sign_in", "not_on_plan", "failing")
TIERS: tuple[str, ...] = ("essential", "recommended", "optional")
VIA_MAX_CHARS = 40
CALENDAR_FEED = "calendar_feed"  # the user's own Job360 calendar link: not a connector, essential
NEW_CARD_EVERY = timedelta(days=7)
STALE_AFTER = timedelta(days=60)
CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "connector_catalog.json"

CATEGORY_WORDS: dict[str, str] = {
    "gmail": "Gmail", "calendar": "a calendar", "job_search": "a job search", "people_lookup": "a people lookup",
    "company_lookup": "a company lookup", "browser": "a browser", "web_search": "a web search",
    CALENDAR_FEED: "your Job360 calendar link",
}
STATUS_WORDS: dict[str, str] = {
    "needs_sign_in": "needs you to sign in again", "not_on_plan": "is not on your plan",
    "failing": "stopped working",
}
CALENDAR_FEED_CARD = {
    "unlocks": "Your interviews and follow-up reminders show up in your own calendar app (any app works).",
    "link": "/settings/account",
}


class CatalogError(ValueError):
    pass


def _https_on(url: Any, domains: list[str]) -> bool:
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == d or host.endswith("." + d) for d in domains)


def check_catalog(raw: dict[str, Any]) -> dict[str, Any]:
    """The catalog, checked: closed assistants, categories and tiers; every link https on a listed vendor
    domain; short names. Raises ``CatalogError`` naming the first problem."""
    domains = [str(d).lower() for d in raw.get("vendor_domains") or []]
    tiers = raw.get("tiers") or {}
    if set(tiers) != set(CATEGORIES) or any(t not in TIERS for t in tiers.values()):
        raise CatalogError("tiers must give every category one of essential | recommended | optional")
    for key, a in (raw.get("assistants") or {}).items():
        if key not in ASSISTANTS:
            raise CatalogError(f"unknown assistant {key!r}")
        if a.get("connect_url") is not None and not _https_on(a["connect_url"], domains):
            raise CatalogError(f"{key}: connect_url must be https on a vendor domain")
        for e in a.get("entries") or []:
            where = f"{key}/{e.get('name')!r}"
            if e.get("category") not in CATEGORIES:
                raise CatalogError(f"{where}: unknown category {e.get('category')!r}")
            if e.get("tier") not in TIERS:
                raise CatalogError(f"{where}: tier must be one of {', '.join(TIERS)}")
            if not isinstance(e.get("name"), str) or not 0 < len(e["name"]) <= VIA_MAX_CHARS:
                raise CatalogError(f"{where}: name must be 1-{VIA_MAX_CHARS} characters")
            for field in ("connect_url", "source_url"):
                if e.get(field) is not None and not _https_on(e[field], domains):
                    raise CatalogError(f"{where}: {field} must be https on a vendor domain")
            if e.get("free") not in (True, False, None):
                raise CatalogError(f"{where}: free must be true, false or null")
    return raw


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    return check_catalog(json.loads(CATALOG_PATH.read_text(encoding="utf-8")))


# ── The report (what the assistant has) ─────────────────────────────────────


def clean_report(categories: list[dict[str, Any]], needed: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Closed values only: ``[{category, via, status}]`` (one per category, last wins) and ``needed``
    (categories a run needed but the assistant lacks). Raises ``ValueError`` naming the rule."""
    out: dict[str, dict[str, Any]] = {}
    for item in categories:
        cat = item.get("category")
        if cat not in CATEGORIES:
            raise ValueError(f"category must be one of {', '.join(CATEGORIES)}")
        via = " ".join(str(item.get("via") or "").split())
        if len(via) > VIA_MAX_CHARS or "://" in via:
            raise ValueError(f"via is the connector's name: at most {VIA_MAX_CHARS} characters, no link")
        status = item.get("status") or "ok"
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        out[cat] = {"category": cat, "via": via, "status": status}
    bad = [n for n in needed if n not in CATEGORIES]
    if bad:
        raise ValueError(f"needed must hold categories only: {', '.join(CATEGORIES)}")
    return [out[c] for c in CATEGORIES if c in out], sorted(set(needed) - set(out))


async def latest_reports(db: JobDatabase, user_id: str) -> dict[str, dict[str, Any]]:
    """``{assistant: {categories, needed, reported_at, reported_by}}``, the newest report per assistant."""
    cur = await db._db.execute(
        "SELECT r.assistant, r.categories, r.needed, r.reported_at, r.reported_by FROM assistant_capability_reports r "
        "WHERE r.user_id = ? AND r.id = (SELECT MAX(x.id) FROM assistant_capability_reports x "
        "WHERE x.user_id = r.user_id AND x.assistant = r.assistant)",
        (user_id,),
    )
    out: dict[str, dict[str, Any]] = {}
    for row in await cur.fetchall():
        d = dict(row)
        out[str(d["assistant"])] = {
            "categories": json.loads(d["categories"] or "[]"), "needed": json.loads(d["needed"] or "[]"),
            "reported_at": d["reported_at"], "reported_by": d["reported_by"],
        }
    return out


async def save_report(
    db: JobDatabase, user_id: str, assistant: str, categories: list[dict[str, Any]], needed: list[str],
    actor: str, now: datetime,
) -> tuple[str, bool]:
    """Append the report unless it repeats the newest one (idempotent). ``(reported_at, saved)``."""
    last = (await latest_reports(db, user_id)).get(assistant)
    if last and last["categories"] == categories and last["needed"] == needed:
        return str(last["reported_at"]), False
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    await db._db.execute(
        "INSERT INTO assistant_capability_reports (user_id, assistant, categories, needed, reported_by, reported_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, assistant, json.dumps(categories), json.dumps(needed), actor, stamp),
    )
    await db._db.commit()
    return stamp, True


async def has_calendar_feed(db: JobDatabase, user_id: str) -> bool:
    cur = await db._db.execute("SELECT 1 FROM calendar_feeds WHERE user_id = ?", (user_id,))
    return await cur.fetchone() is not None


async def card_state(db: JobDatabase, user_id: str) -> dict[str, dict[str, Optional[str]]]:
    cur = await db._db.execute(
        "SELECT card_key, first_shown_at, dismissed_at FROM connector_cards WHERE user_id = ?", (user_id,)
    )
    return {str(dict(r)["card_key"]): dict(r) for r in await cur.fetchall()}


async def mark_shown(db: JobDatabase, user_id: str, keys: list[str], now: datetime) -> None:
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    for key in keys:
        await db._db.execute(
            "INSERT INTO connector_cards (user_id, card_key, first_shown_at) VALUES (?, ?, ?) "
            "ON CONFLICT (user_id, card_key) DO NOTHING",
            (user_id, key, stamp),
        )
    await db._db.commit()


async def dismiss(db: JobDatabase, user_id: str, key: str, now: datetime) -> None:
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    await db._db.execute(
        "INSERT INTO connector_cards (user_id, card_key, first_shown_at, dismissed_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (user_id, card_key) DO UPDATE SET dismissed_at = EXCLUDED.dismissed_at",
        (user_id, key, stamp, stamp),
    )
    await db._db.commit()


# ── What to show (pure) ──────────────────────────────────────────────────────


def suggestions(assistant: str, category: str) -> list[dict[str, Any]]:
    """The catalog entries for one category, in catalog order with the free ones first (never by payment)."""
    listed = (catalog()["assistants"].get(assistant) or {}).get("entries", [])
    entries = [e for e in listed if e["category"] == category]
    return sorted(entries, key=lambda e: 0 if e.get("free") is True else 1)


def _offer(assistant: str, category: str) -> dict[str, Any]:
    entries = suggestions(assistant, category)
    main = next((e for e in entries if e["tier"] != "optional"), entries[0] if entries else None)
    return {"suggested": main, "alternatives": [e for e in entries if e is not main]}


def cards(
    reports: dict[str, dict[str, Any]], *, has_feed: bool, state: dict[str, dict[str, Any]], setup_done: bool,
    now: datetime,
) -> list[dict[str, Any]]:
    """The cards to show now, in order: fix, then essential, then recommended (see the module docstring).
    Each: {key, kind (fix | add), tier, assistant, category, title, unlocks, link, suggested, alternatives}."""
    tiers = catalog()["tiers"]
    out: list[dict[str, Any]] = []

    def visible(key: str) -> bool:
        return not (state.get(key) or {}).get("dismissed_at")

    for assistant, rep in sorted(reports.items()):
        label = (catalog()["assistants"].get(assistant) or {}).get("label") or "Your assistant"
        link = (catalog()["assistants"].get(assistant) or {}).get("connect_url")
        for item in rep["categories"]:
            key = f"fix:{assistant}:{item['category']}"
            if item["status"] != "ok" and visible(key):
                name = item["via"] or CATEGORY_WORDS[item["category"]]
                out.append({"key": key, "kind": "fix", "tier": tiers[item["category"]], "assistant": assistant,
                            "category": item["category"], "title": f"{name} in {label} {STATUS_WORDS[item['status']]}.",
                            "unlocks": "Reconnect it in your assistant's settings, then run 360 again.", "link": link,
                            "suggested": None, "alternatives": []})
    if not has_feed and visible(f"add:{CALENDAR_FEED}"):
        out.append({"key": f"add:{CALENDAR_FEED}", "kind": "add", "tier": "essential", "assistant": None,
                    "category": CALENDAR_FEED, "title": "Add your Job360 calendar link.",
                    "unlocks": CALENDAR_FEED_CARD["unlocks"], "link": CALENDAR_FEED_CARD["link"], "suggested": None,
                    "alternatives": []})
    recent_new = any(
        (s.get("first_shown_at") or "") >= (now - NEW_CARD_EVERY).isoformat(timespec="seconds")
        for k, s in state.items() if k.startswith("add:") and k.split(":")[-1] in ("job_search", "people_lookup")
    )
    for assistant, rep in sorted(reports.items()):
        has = {i["category"] for i in rep["categories"]}
        label = (catalog()["assistants"].get(assistant) or {}).get("label") or "Your assistant"
        for category in CATEGORIES:
            tier = tiers[category]
            key = f"add:{assistant}:{category}"
            if category in has or tier == "optional" or not visible(key):
                continue
            offer = _offer(assistant, category)
            if tier == "recommended" and key not in state and setup_done:
                # After setup: only when a run needed it, and at most one NEW suggestion a week.
                if category not in rep["needed"] or recent_new:
                    continue
                recent_new = True
            main = offer["suggested"]
            out.append({"key": key, "kind": "add", "tier": tier, "assistant": assistant, "category": category,
                        "title": f"Connect {main['name'] if main else CATEGORY_WORDS[category]} to {label}.",
                        "unlocks": main["unlocks"] if main else "", "link": (main or {}).get("connect_url"),
                        "suggested": main, "alternatives": offer["alternatives"]})
    order = {("fix", t): 0 for t in TIERS} | {("add", "essential"): 1, ("add", "recommended"): 2}
    return sorted(out, key=lambda c: order[(c["kind"], c["tier"])])


def summary(reports: dict[str, dict[str, Any]], *, has_feed: bool) -> dict[str, Any]:
    """For doctor: the newest report per assistant and what is missing or failing (closed values)."""
    tiers = catalog()["tiers"]
    out: dict[str, Any] = {"reported": [], "missing_essential": [] if has_feed else [CALENDAR_FEED],
                           "missing_recommended": [], "failing": []}
    for assistant, rep in sorted(reports.items()):
        has = {i["category"] for i in rep["categories"]}
        out["reported"].append({"assistant": assistant, "reported_at": rep["reported_at"],
                                "categories": rep["categories"]})
        for category in CATEGORIES:
            if category not in has and tiers[category] == "essential":
                out["missing_essential"].append(f"{assistant}:{category}")
            elif category not in has and tiers[category] == "recommended":
                out["missing_recommended"].append(f"{assistant}:{category}")
        out["failing"] += [
            f"{assistant}:{i['category']}:{i['status']}" for i in rep["categories"] if i["status"] != "ok"
        ]
    return out


def stale_entries(now: datetime) -> list[str]:
    """Catalog entries verified more than 60 days ago (re-check them: vendors rename and re-gate often)."""
    out = []
    for key, a in catalog()["assistants"].items():
        for e in a.get("entries", []):
            when = e.get("verified_on")
            if when and datetime.fromisoformat(when).replace(tzinfo=timezone.utc) < now - STALE_AFTER:
                out.append(f"{key}/{e['name']}")
    return out

"""Slice 4 — measure which career sites Job360's cloud browser can work on.

READ-ONLY by design: it opens each page and looks. It never types, clicks,
uploads or submits, and it uses its own throwaway browser (no user's
sign-ins). Run it from production once ``BROWSER_AGENT_ENABLED=1`` (it needs
the open internet, which a dev container may not have)::

    python -m src.cli browser-probe https://boards.greenhouse.io/acme/jobs/123 ...

For each page it reports the outcome the assistant would meet:

* ``ok``          — loaded, form fields visible (``fields``, ``file_inputs``, ``submit_buttons``);
* ``no_form``     — loaded, but no form on this page (an ad page: the apply form is a click away);
* ``captcha`` / ``login_required`` — the browser would stop and hand the job to Needs you;
* ``blocked``     — the address was refused (not the public internet);
* ``error``       — did not load (``detail`` says why: a 403 from a bot wall shows here).
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlsplit

from src.services.browser_agent.agent import BrowserError, BrowserPool, run
from src.services.browser_agent.safety import control_kind
from src.services.fetch.guard import ResolveFn

PROBE_USER = "__probe__"
_FIELD_TYPES = ("", "text", "email", "tel", "url", "number", "date", "search")


def summarise(page: dict[str, Any]) -> dict[str, Any]:
    """One row of the report from one ``look`` result (pure: tested without a browser)."""
    elements = page.get("elements") or []
    fields = sum(
        1 for e in elements
        if e.get("tag") in ("input", "textarea", "select") and (e.get("type") or "") in (*_FIELD_TYPES, "select-one")
        or e.get("tag") == "textarea"
    )
    files = sum(1 for e in elements if e.get("tag") == "input" and e.get("type") == "file")
    submits = sum(
        1 for e in elements
        if control_kind(e.get("tag", ""), e.get("type", ""), e.get("name", ""), e.get("autocomplete", ""),
                        bool(e.get("in_form")), e.get("role", "")) == "submit"
    )
    if page.get("status") == "needs_you":
        outcome = str(page.get("reason") or "needs_you")
    elif fields or files:
        outcome = "ok"
    else:
        outcome = "no_form"
    return {
        "outcome": outcome, "title": (page.get("title") or "")[:80], "fields": fields,
        "file_inputs": files, "submit_buttons": submits,
    }


async def probe(urls: list[str], *, resolve: ResolveFn, pool: Optional[BrowserPool] = None) -> list[dict[str, Any]]:
    """Open each URL in a fresh throwaway browser and report what the assistant would meet."""
    own = pool is None
    pool = pool or BrowserPool(max_sessions=1, idle_s=600, resolve=resolve)
    rows: list[dict[str, Any]] = []
    try:
        for url in urls:
            host = urlsplit(url).hostname or url
            try:
                page = await run(pool, PROBE_USER, "open", url=url)
                row = {"host": host, **summarise(page)}
            except BrowserError as exc:
                outcome = "blocked" if exc.reason == "blocked_address" else "error"
                row = {"host": host, "outcome": outcome, "detail": exc.detail[:160]}
            finally:
                await pool.close(PROBE_USER)  # a fresh browser per site: no cookie carries over
            rows.append(row)
    finally:
        if own:
            await pool.shutdown()
    return rows


def table(rows: list[dict[str, Any]]) -> str:
    """The report as plain text, one line per site."""
    lines = [f"{'site':40} {'outcome':15} fields files submit"]
    for r in rows:
        lines.append(
            f"{r['host'][:40]:40} {r['outcome']:15} {r.get('fields', '-')!s:>6} {r.get('file_inputs', '-')!s:>5} "
            f"{r.get('submit_buttons', '-')!s:>6}" + (f"  {r['detail']}" if r.get("detail") else "")
        )
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    lines.append("total: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return "\n".join(lines)

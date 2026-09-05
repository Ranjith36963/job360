"""URL fetch on the web (docs/plans/2026-09-04-url-fetch/spec.md).

The web fallback for a human at `/bring`: the user pastes a link, we fetch it
under a hard SSRF guard and hand back extracted fields for the form to
pre-fill. No MCP tool exposes this — an agent already has its own fetch
(VISION rule 5); this package exists only for the browser path.

Submodules:
    outcomes.py — the closed 8-value outcome enum + one message per value.
    guard.py    — the SSRF decision core (screen_ip/screen_url/screen_host,
                  GuardedResolver, verify_peername). Pure — no I/O, no aiohttp
                  client, injectable resolver/clock.
    fetcher.py  — the manual redirect loop, budgets, size caps, content-type
                  check, and the extraction call.
    extract.py  — the three-rung extraction ladder (JSON-LD, meta, heuristic).
"""
from __future__ import annotations

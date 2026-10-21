"""``stats(view="sites")``: how each form host treated the user (bulk-apply P1).

Counts only, over facts the user's assistant already recorded: the host on the
newest ``form_filled``, the ``applied`` event, and each ``blocked`` event's
reason (its ``page_host``, else the application's form host). Nothing is
inferred, scored or ranked; ``n`` is the number of applications the row counts.
A ``blocked`` that was later resolved still counts: it happened on that site.
Logs the user and the number of hosts, never a host name or free text.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from src.core import settings
from src.services.applications.blocked import BLOCKED_REASONS

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

NOTE = (
    "Facts only: counts per form host from what was recorded. A blocked that was later resolved still counts. "
    "Never rank or skip a job or company on these numbers; small n says little."
)


def _payload(raw: Any) -> dict[str, Any]:
    try:
        p = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return p if isinstance(p, dict) else {}


def _host(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


async def build(db: JobDatabase, user_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """``rows`` are the scoped application rows of ``compute_stats`` (``application_id``, ``applied``)."""
    from src.utils.logger import get_audit_logger, safe_log_value  # noqa: PLC0415

    applied_by_id = {int(r["application_id"]): bool(r.get("applied")) for r in rows}
    events: list[dict[str, Any]] = []
    if applied_by_id:
        marks = ",".join("?" for _ in applied_by_id)
        cur = await db._db.execute(
            "SELECT e.application_id, e.event_type, e.payload FROM application_events e "  # noqa: S608
            f"WHERE e.user_id = ? AND e.event_type IN ('form_filled', 'blocked') AND e.application_id IN ({marks}) "
            "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) ORDER BY e.id",
            (user_id, *applied_by_id),
        )
        events = [dict(r) for r in await cur.fetchall()]

    form_host: dict[int, str] = {}  # newest form_filled host per application (events are oldest first)
    for ev in events:
        if ev["event_type"] == "form_filled":
            host = _host(_payload(ev["payload"]).get("host"))
            if host:
                form_host[int(ev["application_id"])] = host
    blocks: dict[tuple[str, int], set[str]] = {}  # (host, application) -> reasons
    for ev in events:
        if ev["event_type"] != "blocked":
            continue
        p = _payload(ev["payload"])
        app_id = int(ev["application_id"])
        host = _host(p.get("page_host")) or form_host.get(app_id, "")
        if host:
            reason = p.get("reason") if p.get("reason") in BLOCKED_REASONS else "other"
            blocks.setdefault((host, app_id), set()).add(str(reason))

    sites: dict[str, dict[str, Any]] = {}

    def site(host: str) -> dict[str, Any]:
        return sites.setdefault(host, {"host": host, "apps": set(), "forms_filled": 0, "applied": 0, "blocked": {}})

    for app_id, host in form_host.items():
        s = site(host)
        s["apps"].add(app_id)
        s["forms_filled"] += 1
        s["applied"] += 1 if applied_by_id.get(app_id) else 0
    for (host, app_id), reasons in blocks.items():
        s = site(host)
        s["apps"].add(app_id)
        for reason in reasons:
            s["blocked"][reason] = s["blocked"].get(reason, 0) + 1

    out = []
    for s in sites.values():
        blocked = dict(sorted(s["blocked"].items()))
        out.append({
            "host": s["host"], "n": len(s["apps"]), "forms_filled": s["forms_filled"], "applied": s["applied"],
            "blocked": blocked, "blocked_total": sum(blocked.values()),
        })
    out.sort(key=lambda x: (-x["n"], x["host"]))
    truncated = len(out) > settings.STATS_MAX_GROUPS
    get_audit_logger().info(
        "stats_view",
        extra={"event": "stats_view", "user_id": safe_log_value(user_id), "view": "sites", "hosts": len(out)},
    )
    return {
        "hosts": len(out), "sites": out[: settings.STATS_MAX_GROUPS], "truncated": truncated,
        "applications_without_host": len(set(applied_by_id) - set(form_host) - {a for _, a in blocks}),
        "note": NOTE,
    }

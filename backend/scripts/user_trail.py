"""One user's timeline: who did what, when, and why it was refused.

    python scripts/user_trail.py someone@example.com --days 7

Against production (DATABASE_PUBLIC_URL is injected by Railway):

    railway run -s Postgres python backend/scripts/user_trail.py someone@example.com

Merges four tables into one time-ordered list:
  * audit_log                  tool/route calls, status, path, closed reason code
  * profile_edits              who set which path, when (the VALUE is never read)
  * application_events         event types (detail / payload never read)
  * assistant_setting_requests asks and their decision (value never read)

SAFETY, built in (this output gets pasted into chats and issues):
  * READ-ONLY. Only SELECTs; the session is set read-only.
  * The email is MASKED in the output.
  * NO values, notes, CV text, tokens or free text. Audit `detail` is passed
    through an allow-list of keys, and every string must look like a path or a
    code, else it prints as "(redacted)".
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

MAX_ROWS_PER_TABLE = 2000

# Audit `detail` keys that are safe by construction (paths, closed codes).
_DETAIL_KEYS = ("tool", "route", "path", "paths", "failed_path", "reason", "http_status")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:/\-\[\]]{1,100}$")


def mask_email(email: Optional[str]) -> str:
    """Never print a full address. Enough to identify, not enough to harvest."""
    if not email or "@" not in email:
        return "(none)"
    user, domain = email.split("@", 1)
    shown = user[:2] if len(user) > 2 else ""
    return f"{shown}***@{domain}"


def _safe(value: Any) -> str:
    """A path or code prints as-is; anything else (free text, values) is hidden."""
    if value is None:
        return "-"
    text = str(value)
    return text if _SAFE_TOKEN.match(text) else "(redacted)"


def _parse_ts(raw: Any) -> Optional[datetime]:
    """Best-effort UTC parse of the TEXT timestamps the tables carry."""
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not raw:
        return None
    text = str(raw).strip().replace("T", " ").replace("Z", "+00:00")
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _audit_fields(detail_raw: Any) -> dict[str, str]:
    """Allow-listed, sanitised fields out of an audit_log.detail JSON blob."""
    out: dict[str, str] = {}
    try:
        detail = json.loads(detail_raw) if isinstance(detail_raw, str) else (detail_raw or {})
    except ValueError:
        return out
    if not isinstance(detail, dict):
        return out
    for key in _DETAIL_KEYS:
        if key not in detail:
            continue
        val = detail[key]
        if val is None or val == "" or val == []:
            continue  # an empty value must not shadow the next field (failed_path "" -> paths)
        if isinstance(val, list):
            out[key] = ",".join(_safe(v) for v in val[:12]) + ("..." if len(val) > 12 else "")
        else:
            out[key] = _safe(val)
    return out


def _table_exists(cur: Any, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name = %s",
        (table,),
    )
    return cur.fetchone() is not None


def collect(cur: Any, user_id: str) -> list[dict[str, Any]]:
    """Every timeline entry for one user_id, unsorted. Values are never selected."""
    rows: list[dict[str, Any]] = []

    if _table_exists(cur, "audit_log"):
        cur.execute(
            "SELECT occurred_at, event, status, detail FROM audit_log "
            "WHERE user_id = %s ORDER BY id DESC LIMIT %s",
            (user_id, MAX_ROWS_PER_TABLE),
        )
        for at, event, status, detail in cur.fetchall():
            f = _audit_fields(detail)
            rows.append({
                "at": at,
                "actor": "-",
                "what": f.get("tool") or f.get("route") or _safe(event),
                "status": _safe(status) if status else "-",
                "path": f.get("failed_path") or f.get("path") or f.get("paths") or "-",
                "reason": f.get("reason")
                or (f"http_{f['http_status']}" if "http_status" in f else "-"),
                "src": "audit",
            })

    if _table_exists(cur, "profile_edits"):
        cur.execute(
            "SELECT set_at, set_by, path FROM profile_edits "
            "WHERE user_id = %s ORDER BY id DESC LIMIT %s",
            (user_id, MAX_ROWS_PER_TABLE),
        )
        for at, by, path in cur.fetchall():
            rows.append({"at": at, "actor": _safe(by), "what": "profile_edit", "status": "ok",
                         "path": _safe(path), "reason": "-", "src": "edit"})

    if _table_exists(cur, "application_events"):
        cur.execute(
            "SELECT recorded_at, recorded_by, event_type, application_id FROM application_events "
            "WHERE user_id = %s ORDER BY id DESC LIMIT %s",
            (user_id, MAX_ROWS_PER_TABLE),
        )
        for at, by, etype, app_id in cur.fetchall():
            rows.append({"at": at, "actor": _safe(by), "what": f"event:{_safe(etype)}",
                         "status": "ok", "path": f"application/{int(app_id)}",
                         "reason": "-", "src": "app_event"})

    if _table_exists(cur, "assistant_setting_requests"):
        cur.execute(
            "SELECT requested_at, requested_by, path, decision, decided_at, decided_by "
            "FROM assistant_setting_requests WHERE user_id = %s ORDER BY id DESC LIMIT %s",
            (user_id, MAX_ROWS_PER_TABLE),
        )
        for req_at, req_by, path, decision, dec_at, dec_by in cur.fetchall():
            rows.append({"at": req_at, "actor": _safe(req_by), "what": "setting_request",
                         "status": "waiting" if decision is None else "asked",
                         "path": _safe(path), "reason": "-", "src": "setting"})
            if decision is not None:
                rows.append({"at": dec_at, "actor": _safe(dec_by), "what": "setting_decision",
                             "status": _safe(decision), "path": _safe(path), "reason": "-",
                             "src": "setting"})
    return rows


def build_trail(
    cur: Any, email: str, days: int = 7, now: Optional[datetime] = None
) -> Optional[list[dict[str, Any]]]:
    """Sorted (oldest first) entries inside the window, or None if no such user."""
    cur.execute("SELECT id FROM users WHERE lower(email) = lower(%s)", (email,))
    row = cur.fetchone()
    if row is None:
        return None
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    kept = []
    for entry in collect(cur, row[0]):
        ts = _parse_ts(entry["at"])
        if ts is None or ts < cutoff:
            continue
        entry["ts"] = ts
        kept.append(entry)
    kept.sort(key=lambda e: e["ts"])
    return kept


def render(email: str, days: int, entries: list[dict[str, Any]]) -> str:
    """The printable table. Pure function of already-sanitised entries."""
    lines = [f"TRAIL  {mask_email(email)}  last {days} day(s)  {len(entries)} entries", ""]
    lines.append(
        f"{'time (UTC)':<20}{'actor':<14}{'tool / route':<26}{'status':<10}{'path':<34}reason"
    )
    for e in entries:
        lines.append(
            f"{e['ts'].strftime('%Y-%m-%d %H:%M:%S'):<20}{e['actor'][:13]:<14}"
            f"{e['what'][:25]:<26}{e['status'][:9]:<10}{e['path'][:33]:<34}{e['reason']}"
        )
    if not entries:
        lines.append("(nothing in this window)")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("email", help="the user's email (matched case-insensitively, printed masked)")
    ap.add_argument("--days", type=int, default=7, help="look-back window (default 7)")
    args = ap.parse_args(argv)

    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("no DATABASE_PUBLIC_URL / DATABASE_URL in the environment", file=sys.stderr)
        return 1

    import psycopg  # noqa: PLC0415 — keeps `--help` usable without the driver

    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            entries = build_trail(cur, args.email, args.days)
    if entries is None:
        print(f"no user found for {mask_email(args.email)}")
        return 1
    print(render(args.email, args.days, entries))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

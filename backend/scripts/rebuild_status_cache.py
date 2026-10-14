"""Rebuild the ``applications.status`` / ``stage`` cache from the event log.

``applications.status`` is a CACHE (hard rule M3/S7): the event log is the
truth and ``status.replay_status`` derives the status from it. When the
derivation rule changes (2026-10-14: "furthest stage wins" replaced "the last
status event wins"), rows written under the old rule keep their old value
until their next event. This script replays every application's events and
writes the cache slot ONLY where it differs.

* Dry run by default: prints counts (and each change) and writes nothing.
  ``--apply`` writes.
* Idempotent: a second ``--apply`` finds nothing to change.
* Touches ONLY ``applications.status`` and ``applications.stage`` (``stage``
  only when the new status has a legacy stage — ``considering`` has none, the
  same rule ``spine.append_event`` follows). Never reads-for-write, updates
  or deletes ``application_events`` (append-only, M3).

Usage (from backend/):
    python scripts/rebuild_status_cache.py            # dry run (same as --dry-run)
    python scripts/rebuild_status_cache.py --apply    # write the differences
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Ensure backend/ is on sys.path so `src.*` resolves when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import settings  # noqa: E402
from src.repositories import pgsync  # noqa: E402
from src.services.applications.status import replay_status, stage_for_status  # noqa: E402


def rebuild(conn: pgsync.Connection, *, apply: bool = False) -> dict[str, Any]:
    """Replay every application; return ``{checked, changed, changes}``. Writes
    the differing rows only when ``apply`` is true."""
    apps = conn.execute("SELECT id, status, stage FROM applications ORDER BY id").fetchall()
    events: dict[int, list[dict[str, Any]]] = {}
    for r in conn.execute(
        "SELECT id, application_id, event_type, corrects_event_id, recorded_at, occurred_at "
        "FROM application_events"
    ).fetchall():
        row = dict(r)
        events.setdefault(int(row["application_id"]), []).append(row)

    changes: list[dict[str, Any]] = []
    for a in apps:
        app = dict(a)
        new_status = replay_status(events.get(int(app["id"]), []))
        new_stage = stage_for_status(new_status) or app["stage"]
        if new_status == app["status"] and new_stage == app["stage"]:
            continue
        changes.append(
            {
                "application_id": int(app["id"]),
                "status": (app["status"], new_status),
                "stage": (app["stage"], new_stage),
            }
        )
        if apply:
            conn.execute(
                "UPDATE applications SET status = ?, stage = ? WHERE id = ?",
                (new_status, new_stage, int(app["id"])),
            )
    if apply:
        conn.commit()
    return {"checked": len(apps), "changed": len(changes), "changes": changes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true", help="print what would change (the default)")
    mode_group.add_argument("--apply", action="store_true", help="write the differences")
    args = parser.parse_args(argv)
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        result = rebuild(conn, apply=args.apply)
    mode = "APPLIED" if args.apply else "DRY RUN (nothing written; pass --apply to write)"
    print(f"{mode}: {result['checked']} applications checked, {result['changed']} differ")
    for c in result["changes"]:
        print(
            f"  application {c['application_id']}: status {c['status'][0]} -> {c['status'][1]}, "
            f"stage {c['stage'][0]} -> {c['stage'][1]}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

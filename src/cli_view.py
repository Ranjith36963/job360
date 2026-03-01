"""Rich terminal view with time-bucketed job tables."""

import argparse
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.config.settings import DB_PATH, MIN_MATCH_SCORE
from src.utils.time_buckets import (
    BUCKETS,
    bucket_jobs,
    bucket_summary_counts,
    format_relative_time,
    score_color_name,
)

console = Console()


def _load_jobs_sync(db_path: str | None = None, days: int = 7, min_score: int = 30) -> list[dict]:
    """Load recent jobs from SQLite synchronously."""
    path = db_path or str(DB_PATH)
    if not Path(path).exists():
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = conn.execute(
            "SELECT * FROM jobs WHERE first_seen >= ? AND match_score >= ? ORDER BY date_found DESC",
            (cutoff, min_score),
        )
        return [dict(row) for row in cursor.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def _format_salary(job: dict) -> str:
    """Format salary for terminal display."""
    smin = job.get("salary_min")
    smax = job.get("salary_max")
    if smin and smax:
        return f"\u00a3{int(smin):,}-\u00a3{int(smax):,}"
    if smin:
        return f"\u00a3{int(smin):,}+"
    if smax:
        return f"Up to \u00a3{int(smax):,}"
    return "N/A"


def _build_bucket_table(jobs: list[dict], bucket_idx: int) -> Table:
    """Build a Rich table for one time bucket."""
    label, _, rich_emoji, _, _ = BUCKETS[bucket_idx]
    table = Table(
        title=f"{rich_emoji} {label} ({len(jobs)} jobs)",
        show_lines=False,
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Score", justify="right", style="bold", width=6)
    table.add_column("Title", style="cyan", min_width=25)
    table.add_column("Company", min_width=15)
    table.add_column("Location", min_width=12)
    table.add_column("Salary", min_width=12)
    table.add_column("Posted", min_width=12)
    table.add_column("Source", min_width=8)
    table.add_column("Visa", justify="center", width=5)

    for job in jobs:
        score = job.get("match_score", 0)
        color = score_color_name(score)
        visa = "[green]\u2713[/green]" if job.get("visa_flag") else ""
        posted = format_relative_time(job.get("date_found", ""))
        table.add_row(
            f"[{color}]{score}[/{color}]",
            job.get("title", ""),
            job.get("company", ""),
            job.get("location", ""),
            _format_salary(job),
            posted,
            job.get("source", ""),
            visa,
        )
    return table


def display_jobs(
    hours: int = 168,
    min_score: int = 30,
    source: str | None = None,
    visa_only: bool = False,
    db_path: str | None = None,
):
    """Load jobs, apply filters, bucket them, and print to terminal."""
    days = max(hours / 24, 1)
    jobs = _load_jobs_sync(db_path=db_path, days=int(days), min_score=min_score)

    if not jobs:
        console.print(Panel("No jobs found in the database.", title="Job360", style="yellow"))
        return

    # Apply filters
    if source:
        jobs = [j for j in jobs if j.get("source", "").lower() == source.lower()]
    if visa_only:
        jobs = [j for j in jobs if j.get("visa_flag")]

    # Filter by hours (more precise than days)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    filtered = []
    for j in jobs:
        from src.utils.time_buckets import parse_date_safe
        dt = parse_date_safe(j.get("date_found", "")) or parse_date_safe(j.get("first_seen", ""))
        if dt is None or dt >= cutoff:
            filtered.append(j)
    jobs = filtered

    bucketed = bucket_jobs(jobs, min_score=min_score)
    counts = bucket_summary_counts(bucketed)

    # Summary panel
    summary = (
        f"[bold]Total:[/bold] {counts['total']} jobs | "
        f"[red]24h:[/red] {counts['last_24h']} | "
        f"[dark_orange]24-48h:[/dark_orange] {counts['24_48h']} | "
        f"[yellow]48-72h:[/yellow] {counts['48_72h']} | "
        f"[blue]3-7d:[/blue] {counts['3_7d']}"
    )
    console.print(Panel(summary, title="Job360 \u2014 Time-Bucketed View", style="bold blue"))

    # Print each bucket table
    for idx in range(4):
        bucket_list = bucketed.get(idx, [])
        if bucket_list:
            table = _build_bucket_table(bucket_list, idx)
            console.print(table)
            console.print()
        else:
            label, _, rich_emoji, _, _ = BUCKETS[idx]
            console.print(f"  {rich_emoji} {label}: [dim]No jobs in this period[/dim]\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job360 CLI View")
    parser.add_argument("--hours", type=int, default=168, help="Show jobs from last N hours")
    parser.add_argument("--min-score", type=int, default=MIN_MATCH_SCORE, help="Minimum match score")
    parser.add_argument("--source", default=None, help="Filter by source name")
    parser.add_argument("--visa-only", action="store_true", help="Show only visa-flagged jobs")
    parser.add_argument("--db-path", default=None, help="Override database path")
    args = parser.parse_args()
    display_jobs(
        hours=args.hours,
        min_score=args.min_score,
        source=args.source,
        visa_only=args.visa_only,
        db_path=args.db_path,
    )

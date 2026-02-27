import asyncio
import logging

import aiohttp

from src.models import Job
from src.config.settings import DISCORD_WEBHOOK_URL

logger = logging.getLogger("job360.discord")


def is_discord_configured() -> bool:
    return bool(DISCORD_WEBHOOK_URL)


def _format_salary(job: Job) -> str:
    if job.salary_min and job.salary_max:
        return f"£{int(job.salary_min):,}-£{int(job.salary_max):,}"
    if job.salary_min:
        return f"£{int(job.salary_min):,}+"
    if job.salary_max:
        return f"Up to £{int(job.salary_max):,}"
    return "N/A"


def _build_embeds(jobs: list[Job], stats: dict) -> dict:
    total = stats.get("total_found", 0)
    new = stats.get("new_jobs", 0)
    per_source = stats.get("per_source", {})
    source_summary = ", ".join(f"{k}: {v}" for k, v in per_source.items()) if per_source else "N/A"

    sorted_jobs = sorted(jobs, key=lambda j: j.match_score, reverse=True)
    top_jobs = sorted_jobs[:10]

    # Build job listing text
    lines = []
    for job in top_jobs:
        score = job.match_score
        emoji = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
        visa = " 🛂" if job.visa_flag else ""
        salary = _format_salary(job)
        lines.append(
            f"{emoji} **[{score}]** [{job.title}]({job.apply_url})\n"
            f"*{job.company}* | {job.location} | {salary}{visa}"
        )

    description = "\n\n".join(lines)
    if len(sorted_jobs) > 10:
        description += f"\n\n*...and {len(sorted_jobs) - 10} more jobs. Check email or dashboard for full list.*"

    embed = {
        "title": f"🔍 Job360: {new} new AI/ML jobs found",
        "description": description,
        "color": 0x1A73E8,
        "fields": [
            {"name": "Total Scanned", "value": str(total), "inline": True},
            {"name": "New Jobs", "value": str(new), "inline": True},
            {"name": "Sources", "value": str(len(per_source)), "inline": True},
        ],
        "footer": {"text": f"Sources: {source_summary}"},
    }

    return {"embeds": [embed]}


async def send_discord(jobs: list[Job], stats: dict):
    if not is_discord_configured():
        logger.info("Discord not configured, skipping")
        return
    if not jobs:
        logger.info("No new jobs for Discord")
        return

    payload = _build_embeds(jobs, stats)

    async with aiohttp.ClientSession() as session:
        async with session.post(DISCORD_WEBHOOK_URL, json=payload) as resp:
            if resp.status in (200, 204):
                logger.info(f"Discord notification sent ({len(jobs)} jobs)")
            else:
                body = await resp.text()
                logger.error(f"Discord webhook failed ({resp.status}): {body}")

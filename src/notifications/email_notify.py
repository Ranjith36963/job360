import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timezone
from pathlib import Path

from src.models import Job
from src.config.settings import SMTP_HOST, SMTP_PORT, SMTP_EMAIL, SMTP_PASSWORD, NOTIFY_EMAIL
from src.notifications.report_generator import generate_html_report

logger = logging.getLogger("job360.email")


def is_email_configured() -> bool:
    return bool(SMTP_EMAIL and SMTP_PASSWORD and NOTIFY_EMAIL)


def _build_email(jobs: list[Job], stats: dict, csv_path: str | None = None) -> MIMEMultipart:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Job360: {len(jobs)} new AI/ML jobs found - {now}"
    msg["From"] = SMTP_EMAIL
    msg["To"] = NOTIFY_EMAIL

    html = generate_html_report(jobs, stats)
    msg.attach(MIMEText(html, "html"))

    if csv_path and Path(csv_path).exists():
        with open(csv_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f"attachment; filename=job360_report_{now}.csv",
            )
            msg.attach(part)

    return msg


def _send_sync(msg: MIMEMultipart):
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.send_message(msg)


async def send_email(jobs: list[Job], stats: dict, csv_path: str | None = None):
    if not is_email_configured():
        logger.warning("Email not configured, skipping notification")
        return
    if not jobs:
        logger.info("No new jobs to email")
        return
    msg = _build_email(jobs, stats, csv_path)
    await asyncio.to_thread(_send_sync, msg)
    logger.info(f"Email sent to {NOTIFY_EMAIL} with {len(jobs)} jobs")

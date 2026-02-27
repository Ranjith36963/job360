import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "jobs.db"
EXPORTS_DIR = DATA_DIR / "exports"
REPORTS_DIR = DATA_DIR / "reports"
LOGS_DIR = DATA_DIR / "logs"

# API Keys (Group A)
REED_API_KEY = os.getenv("REED_API_KEY", "")
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
JSEARCH_API_KEY = os.getenv("JSEARCH_API_KEY", "")

# Email
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")

# Slack / Discord webhooks
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# Search
MIN_MATCH_SCORE = 30
MAX_RESULTS_PER_SOURCE = 100
MAX_DAYS_OLD = 7

# Rate limits (requests per second)
RATE_LIMITS = {
    "reed": {"concurrent": 1, "delay": 2.0},
    "adzuna": {"concurrent": 1, "delay": 2.0},
    "jsearch": {"concurrent": 1, "delay": 3.0},
    "arbeitnow": {"concurrent": 2, "delay": 1.0},
    "remoteok": {"concurrent": 1, "delay": 2.0},
    "jobicy": {"concurrent": 2, "delay": 1.0},
    "himalayas": {"concurrent": 2, "delay": 1.0},
    "greenhouse": {"concurrent": 2, "delay": 1.5},
    "lever": {"concurrent": 2, "delay": 1.5},
    "workable": {"concurrent": 2, "delay": 1.5},
    "ashby": {"concurrent": 2, "delay": 1.5},
    "findajob": {"concurrent": 1, "delay": 3.0},
}

# Retry
MAX_RETRIES = 3
RETRY_BACKOFF = [1, 2, 4]

# HTTP
REQUEST_TIMEOUT = 30
USER_AGENT = "Job360/1.0 (UK Job Search Aggregator)"

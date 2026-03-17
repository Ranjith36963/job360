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
JOOBLE_API_KEY = os.getenv("JOOBLE_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
CAREERJET_AFFID = os.getenv("CAREERJET_AFFID", "")
FINDWORK_API_KEY = os.getenv("FINDWORK_API_KEY", "")

# GitHub (optional — for higher rate limits on profile enrichment)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

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

# Target salary range (GBP, annual) — used for tiebreaker sorting, not scoring
TARGET_SALARY_MIN = int(os.getenv("TARGET_SALARY_MIN", "40000"))
TARGET_SALARY_MAX = int(os.getenv("TARGET_SALARY_MAX", "120000"))

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
    "remotive": {"concurrent": 2, "delay": 1.0},
    "jooble": {"concurrent": 1, "delay": 2.0},
    "linkedin": {"concurrent": 1, "delay": 3.0},
    "smartrecruiters": {"concurrent": 2, "delay": 1.5},
    "pinpoint": {"concurrent": 2, "delay": 1.5},
    "recruitee": {"concurrent": 2, "delay": 1.5},
    "indeed": {"concurrent": 1, "delay": 3.0},
    "glassdoor": {"concurrent": 1, "delay": 3.0},
    "workday": {"concurrent": 2, "delay": 1.5},
    "google_jobs": {"concurrent": 1, "delay": 2.0},
    "devitjobs": {"concurrent": 2, "delay": 1.0},
    "landingjobs": {"concurrent": 2, "delay": 1.0},
    "aijobs": {"concurrent": 2, "delay": 1.0},
    "themuse": {"concurrent": 1, "delay": 2.0},
    "hackernews": {"concurrent": 2, "delay": 1.0},
    "careerjet": {"concurrent": 1, "delay": 2.0},
    "findwork": {"concurrent": 1, "delay": 2.0},
    "nofluffjobs": {"concurrent": 2, "delay": 1.5},
    # New sources (Phase 4)
    "hn_jobs": {"concurrent": 3, "delay": 0.5},
    "yc_companies": {"concurrent": 1, "delay": 1.0},
    "jobs_ac_uk": {"concurrent": 1, "delay": 2.0},
    "nhs_jobs": {"concurrent": 1, "delay": 2.0},
    "personio": {"concurrent": 1, "delay": 3.0},
    "workanywhere": {"concurrent": 1, "delay": 5.0},
    "weworkremotely": {"concurrent": 1, "delay": 2.0},
    "realworkfromanywhere": {"concurrent": 1, "delay": 2.0},
    "biospace": {"concurrent": 1, "delay": 2.0},
    "jobtensor": {"concurrent": 1, "delay": 3.0},
    "climatebase": {"concurrent": 1, "delay": 3.0},
    "eightykhours": {"concurrent": 1, "delay": 2.0},
    "bcs_jobs": {"concurrent": 1, "delay": 3.0},
    "uni_jobs": {"concurrent": 1, "delay": 2.0},
    "successfactors": {"concurrent": 1, "delay": 2.0},
    "aijobs_global": {"concurrent": 2, "delay": 1.0},
    "aijobs_ai": {"concurrent": 1, "delay": 2.0},
    "nomis": {"concurrent": 1, "delay": 5.0},
}

# Retry
MAX_RETRIES = 3
RETRY_BACKOFF = [1, 2, 4]

# HTTP
REQUEST_TIMEOUT = 30
USER_AGENT = "Job360/1.0 (UK Job Search Aggregator)"

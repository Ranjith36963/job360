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

# LLM providers for CV analysis (free tiers)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")

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

# Pillar 2 Batch 2.2 — gate-pass scoring
# A job must clear BOTH the title gate AND the skill gate to receive a linear
# score; otherwise the score is suppressed to max(10, (title+skill)*0.25) so
# location/recency alone can no longer inflate a non-matching job. The gates
# are expressed as fractions of the component max (TITLE_WEIGHT / SKILL_WEIGHT
# in skill_matcher.py — both 40). Default 0.15 → absolute threshold of 6.
MIN_TITLE_GATE = float(os.getenv("MIN_TITLE_GATE", "0.15"))
MIN_SKILL_GATE = float(os.getenv("MIN_SKILL_GATE", "0.15"))

# Pillar 2 Batch 2.9 — multi-dimensional scoring weights.
# JobScorer.score() adds these on top of the legacy 4-component formula
# (title + skill + location + recency). The sum (legacy 100 + 30 new)
# is clamped to 100 at the call site, so individual weights can be tuned
# via env vars without changing the ceiling.
SALARY_WEIGHT = int(os.getenv("SALARY_WEIGHT", "10"))
SENIORITY_WEIGHT = int(os.getenv("SENIORITY_WEIGHT", "8"))
VISA_WEIGHT = int(os.getenv("VISA_WEIGHT", "6"))
WORKPLACE_WEIGHT = int(os.getenv("WORKPLACE_WEIGHT", "6"))

# Pillar 2 Batch 2.6 — semantic stack feature flag.
# When false (default), embeddings + ChromaDB + ESCO normalisation all skip.
# When true, callers that check this flag activate the semantic retrieval path.
SEMANTIC_ENABLED = os.getenv("SEMANTIC_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}

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
    # Batch 3 additions — published rate-limits cited in each source's tests
    "teaching_vacancies": {"concurrent": 1, "delay": 2.0},   # no stated cap, polite
    "gov_apprenticeships": {"concurrent": 1, "delay": 2.0},  # 150 req / 5 min
    "nhs_jobs_xml": {"concurrent": 1, "delay": 2.0},         # feed XML, 15-min tier
    "rippling": {"concurrent": 2, "delay": 1.5},             # ATS, 60s tier
    "comeet": {"concurrent": 2, "delay": 1.5},               # ATS, 60s tier
}

# Retry
MAX_RETRIES = 3
RETRY_BACKOFF = [1, 2, 4]

# HTTP
REQUEST_TIMEOUT = 30
USER_AGENT = "Job360/1.0 (UK Job Search Aggregator)"

# Greenhouse boards: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
# Verified Feb 2026 via web research
GREENHOUSE_COMPANIES = [
    "deepmind", "monzo", "deliveroo", "darktracelimited",
    "stabilityai", "anthropic", "graphcore", "wayve",
    "polyai", "synthesia", "transferwise", "snyk",
    "stripe", "cloudflare", "databricks", "dataiku",
    "ocadotechnology", "tractable", "paddle", "harnessinc",
]

# Lever boards: https://api.lever.co/v0/postings/{slug}?mode=json
# Verified Feb 2026
LEVER_COMPANIES = [
    "mistral", "healx", "palantir", "spotify", "joinzoe",
]

# Workable boards: https://apply.workable.com/api/v2/accounts/{slug}/jobs
# Verified Feb 2026
WORKABLE_COMPANIES = [
    "benevolentai", "exscientia", "oxa", "cervest",
]

# Ashby boards: https://api.ashbyhq.com/posting-api/job-board/{slug}
# Verified Feb 2026
ASHBY_COMPANIES = [
    "anthropic", "cohere", "openai", "improbable",
    "Synthesia", "multiverse",
]

# Slug → display name overrides (when slug doesn't match company name)
COMPANY_NAME_OVERRIDES = {
    "darktracelimited": "Darktrace",
    "transferwise": "Wise",
    "ocadotechnology": "Ocado Technology",
    "harnessinc": "Harness",
    "joinzoe": "ZOE",
    "Synthesia": "Synthesia",
}

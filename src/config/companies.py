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
    "tractable", "helsing", "secondmind", "mosaic-ml", "faculty",
]

# Workable boards: https://apply.workable.com/api/v2/accounts/{slug}/jobs
# Verified Feb 2026
WORKABLE_COMPANIES = [
    "benevolentai", "exscientia", "oxa", "cervest",
    "huggingface", "labelbox", "runway", "adept",
]

# Ashby boards: https://api.ashbyhq.com/posting-api/job-board/{slug}
# Verified Feb 2026
ASHBY_COMPANIES = [
    "anthropic", "cohere", "openai", "improbable",
    "Synthesia", "multiverse",
]

# SmartRecruiters boards: https://api.smartrecruiters.com/v1/companies/{slug}/postings
# Verified Feb 2026
SMARTRECRUITERS_COMPANIES = [
    "wise", "revolut", "checkout", "astrazeneca",
    "samsung-r-and-d-institute-uk", "booking",
]

# Pinpoint boards: https://{slug}.pinpointhq.com/postings.json
# Verified Feb 2026
PINPOINT_COMPANIES = [
    "moneysupermarket", "bulb", "starling-bank",
    "octopus-energy", "faculty", "arm", "sky", "tesco-technology",
]

# Recruitee boards: https://{slug}.recruitee.com/api/offers/
# Verified Feb 2026
RECRUITEE_COMPANIES = [
    "peak-ai", "satalia", "speech-graphics",
    "signal-ai", "eigen-technologies", "causaly", "kheiron-medical", "polyai",
]

# Workday boards: POST https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
# Verified Mar 2026 via API testing
WORKDAY_COMPANIES = [
    {"tenant": "astrazeneca", "wd": "wd3", "site": "Careers", "name": "AstraZeneca"},
    {"tenant": "nvidia", "wd": "wd5", "site": "NVIDIAExternalCareerSite", "name": "NVIDIA"},
    {"tenant": "shell", "wd": "wd3", "site": "ShellCareers", "name": "Shell"},
    {"tenant": "roche", "wd": "wd3", "site": "roche-ext", "name": "Roche"},
    {"tenant": "novartis", "wd": "wd3", "site": "Novartis_Careers", "name": "Novartis"},
    {"tenant": "cisco", "wd": "wd5", "site": "Cisco_Careers", "name": "Cisco"},
    {"tenant": "dell", "wd": "wd1", "site": "External", "name": "Dell"},
    {"tenant": "intel", "wd": "wd1", "site": "External", "name": "Intel"},
    {"tenant": "unilever", "wd": "wd3", "site": "Unilever_Experienced_Professionals", "name": "Unilever"},
]

# Slug → display name overrides (when slug doesn't match company name)
COMPANY_NAME_OVERRIDES = {
    "darktracelimited": "Darktrace",
    "transferwise": "Wise",
    "ocadotechnology": "Ocado Technology",
    "harnessinc": "Harness",
    "joinzoe": "ZOE",
    "Synthesia": "Synthesia",
    "huggingface": "Hugging Face",
    # SmartRecruiters
    "samsung-r-and-d-institute-uk": "Samsung R&D UK",
    "checkout": "Checkout.com",
    # Pinpoint
    "moneysupermarket": "MoneySuperMarket",
    "starling-bank": "Starling Bank",
    "octopus-energy": "Octopus Energy",
    # Recruitee
    "peak-ai": "Peak AI",
    "speech-graphics": "Speech Graphics",
    "signal-ai": "Signal AI",
    "eigen-technologies": "Eigen Technologies",
    "kheiron-medical": "Kheiron Medical",
    # Lever
    "mosaic-ml": "MosaicML",
    # Pinpoint
    "tesco-technology": "Tesco Technology",
}

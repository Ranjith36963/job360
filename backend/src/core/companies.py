# Greenhouse boards: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
# Verified Feb 2026 via web research
GREENHOUSE_COMPANIES = [
    "deepmind", "monzo", "deliveroo", "darktracelimited",
    "stabilityai", "anthropic", "graphcore", "wayve",
    "polyai", "synthesia", "transferwise", "snyk",
    "stripe", "cloudflare", "databricks", "dataiku",
    "ocadotechnology", "tractable", "paddle", "harnessinc",
    "isomorphiclabs", "speechmatics", "onfido", "oxfordnanopore", "bloomberg",
]

# Lever boards: https://api.lever.co/v0/postings/{slug}?mode=json
# Verified Feb 2026
LEVER_COMPANIES = [
    "mistral", "healx", "palantir", "spotify", "joinzoe",
    "tractable", "helsing", "secondmind", "mosaic-ml", "faculty",
    "dyson", "fiveai",
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
    "synthesia", "multiverse",
    "elevenlabs", "perplexity", "anyscale",
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
    {"tenant": "hsbc", "wd": "wd3", "site": "HSBC_Careers", "name": "HSBC"},
    {"tenant": "barclays", "wd": "wd3", "site": "Barclays_Careers", "name": "Barclays"},
    {"tenant": "lloydsbankinggroup", "wd": "wd3", "site": "LloydsBankingGroupCareers", "name": "Lloyds Banking Group"},
    {"tenant": "rollsroyce", "wd": "wd3", "site": "Careers", "name": "Rolls-Royce"},
    {"tenant": "gaborsk", "wd": "wd5", "site": "GSKCareers", "name": "GSK"},
    {"tenant": "jaguarlandrover", "wd": "wd1", "site": "JLR_Careers", "name": "Jaguar Land Rover"},
]

# Slug → display name overrides (when slug doesn't match company name)
COMPANY_NAME_OVERRIDES = {
    "darktracelimited": "Darktrace",
    "transferwise": "Wise",
    "ocadotechnology": "Ocado Technology",
    "harnessinc": "Harness",
    "joinzoe": "ZOE",
    "synthesia": "Synthesia",
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
    "fiveai": "Five AI",
    # Pinpoint
    "tesco-technology": "Tesco Technology",
    # Greenhouse (new)
    "isomorphiclabs": "Isomorphic Labs",
    "oxfordnanopore": "Oxford Nanopore Technologies",
}

# Personio ATS boards: https://{slug}.jobs.personio.de/xml?language=en
# UK companies using Personio
PERSONIO_COMPANIES = [
    "celonis", "trade-republic", "sennder", "contentful",
    "personio", "forto", "taxfix", "wonderkind",
    "airfocus", "heydata",
]

# SAP SuccessFactors career site sitemaps
# UK defence/enterprise companies
SUCCESSFACTORS_COMPANIES = [
    {"name": "BAE Systems", "sitemap_url": "https://jobs.baesystems.com/sitemap.xml"},
    {"name": "QinetiQ", "sitemap_url": "https://careers.qinetiq.com/sitemap.xml"},
    {"name": "Thales UK", "sitemap_url": "https://careers.thalesgroup.com/sitemap.xml"},
    # MBDA removed: careers.mbda-systems.com DNS resolution fails
]


# Rippling ATS public board: https://ats.rippling.com/api/board/{slug}/jobs
# Added in Batch 3 — slug list is a starter set of UK-facing companies
# known to use Rippling for hiring. Expand via the Feashliaa repo later.
RIPPLING_COMPANIES = [
    "rippling",         # Rippling itself (primary test fixture)
    "checkr",           # Global reach
    "figma",            # Engineering hires in London
    "scalepath",        # UK AI/ML
    "linear",           # Tooling, UK eng
]


# Comeet ATS public board: https://www.comeet.co/careers-api/2.0/company/{slug}/positions
# Added in Batch 3 — same UK-facing starter set as Rippling.
COMEET_COMPANIES = [
    "celonis-process-mining",
    "riskified",
    "lightricks",
    "fiverr",
    "placer-ai",
]

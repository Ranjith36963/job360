# Greenhouse boards: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
# Verified Feb 2026 + expanded Batch 3 (Apr 2026) with UK-facing slugs.
# Unknown slugs gracefully no-op via BaseJobSource retry (return None -> []).
GREENHOUSE_COMPANIES = [
    # Original verified Feb 2026 (25)
    "deepmind", "monzo", "deliveroo", "darktracelimited",
    "stabilityai", "anthropic", "graphcore", "wayve",
    "polyai", "synthesia", "transferwise", "snyk",
    "stripe", "cloudflare", "databricks", "dataiku",
    "ocadotechnology", "tractable", "paddle", "harnessinc",
    "isomorphiclabs", "speechmatics", "onfido", "oxfordnanopore", "bloomberg",
    # Batch 3 additions (55) — UK finance, AI, fintech, healthcare, climate
    "starlingbank", "revolutpeople", "wiseeurope", "gocardless",
    "checkoutcom", "zilchtechnology", "octopusenergy", "bulb",
    "thg", "deliverooengineering", "babylonhealth", "babylon",
    "cera", "huma", "clinithink", "benevolent",
    "improbableworlds", "palantirtechnologies",
    "snapuk", "peakuk", "gympass", "multiverse",
    "snykukltd", "lendinvest", "nested", "habito",
    "marshmallow", "zego", "pensionbee", "stepstonegroup",
    "trussle", "moneyhub", "moneyfarm", "nutmeg",
    "curve", "currencycloud", "hometree", "yolt",
    "tide", "youlend",
    "citymapper", "skyscanner", "trainline",
    "learnlight", "mindgym", "healx",
    "secondmindltd", "faculty",
    "pagerduty", "zendeskeurope", "atlassianuk",
    "miro", "asana", "notionlabs",
    "figma", "canva", "linearapp",
]

# Lever boards: https://api.lever.co/v0/postings/{slug}?mode=json
# Verified Feb 2026 + expanded Batch 3 (Apr 2026).
LEVER_COMPANIES = [
    # Original verified (12)
    "mistral", "healx", "palantir", "spotify", "joinzoe",
    "tractable", "helsing", "secondmind", "mosaic-ml", "faculty",
    "dyson", "fiveai",
    # Batch 3 additions (23)
    "tessian", "hubspot", "twilio", "segment",
    "netflix", "shopify", "lyft", "doordash",
    "scaleai", "ramp", "gongio", "lucid",
    "mural", "vercel", "supabase", "planetscale",
    "retool", "airtable", "coda", "writer",
    "glean", "cresta", "adept",
]

# Workable boards: https://apply.workable.com/api/v2/accounts/{slug}/jobs
# Verified Feb 2026 + expanded Batch 3 (Apr 2026).
WORKABLE_COMPANIES = [
    # Original verified (8)
    "benevolentai", "exscientia", "oxa", "cervest",
    "huggingface", "labelbox", "runway", "adept",
    # Batch 3 additions (17) — UK/EU startups using Workable
    "typeform", "livinglens", "phrasee", "signal",
    "legalandgeneral", "vorboss", "gusto", "welcometothejungle",
    "wunderman", "farfetch", "bumble", "trustpilot",
    "papa", "upscale", "rateit",
    "mindlabs", "flo",
]

# Ashby boards: https://api.ashbyhq.com/posting-api/job-board/{slug}
# Verified Feb 2026 + expanded Batch 3 (Apr 2026).
ASHBY_COMPANIES = [
    # Original verified (9)
    "anthropic", "cohere", "openai", "improbable",
    "synthesia", "multiverse",
    "elevenlabs", "perplexity", "anyscale",
    # Batch 3 additions (16) — AI/ML UK+EU
    "deepgram", "abnormal", "ramp", "benchling",
    "notion", "datadog", "airtable", "discord",
    "modal", "replit", "databricks", "langchain",
    "character", "pika", "11x", "writer",
]

# SmartRecruiters boards: https://api.smartrecruiters.com/v1/companies/{slug}/postings
# Verified Feb 2026 + expanded Batch 3 (Apr 2026).
SMARTRECRUITERS_COMPANIES = [
    # Original verified (6)
    "wise", "revolut", "checkout", "astrazeneca",
    "samsung-r-and-d-institute-uk", "booking",
    # Batch 3 additions (9)
    "capgemini", "costa-coffee", "publicisgroup", "unilever",
    "allianz", "visa", "ikea", "sodexo", "hilton",
]

# Pinpoint boards: https://{slug}.pinpointhq.com/postings.json
# Verified Feb 2026 + expanded Batch 3 (Apr 2026).
PINPOINT_COMPANIES = [
    # Original verified (8)
    "moneysupermarket", "bulb", "starling-bank",
    "octopus-energy", "faculty", "arm", "sky", "tesco-technology",
    # Batch 3 additions (7)
    "m-and-s", "john-lewis-partnership", "asda-stores",
    "british-airways", "specsavers", "morrisons", "boots",
]

# Recruitee boards: https://{slug}.recruitee.com/api/offers/
# Verified Feb 2026 + expanded Batch 3 (Apr 2026).
RECRUITEE_COMPANIES = [
    # Original verified (8)
    "peak-ai", "satalia", "speech-graphics",
    "signal-ai", "eigen-technologies", "causaly", "kheiron-medical", "polyai",
    # Batch 3 additions (12)
    "contentstack", "hootsuite", "deezer", "criteo",
    "klarna", "n26", "backbase", "mollie",
    "ada", "productboard", "wonderkind", "tresorit",
]

# Workday boards: POST https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
# Verified Mar 2026 via API testing + expanded Batch 3 (Apr 2026).
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
    # Batch 3 additions (5)
    {"tenant": "sainsburys", "wd": "wd3", "site": "SainsburysCareers", "name": "Sainsbury's"},
    {"tenant": "natwest", "wd": "wd3", "site": "NatWestExternalCareers", "name": "NatWest"},
    {"tenant": "standardchartered", "wd": "wd3", "site": "StandardCharteredCareers", "name": "Standard Chartered"},
    {"tenant": "santander", "wd": "wd3", "site": "SantanderCareers", "name": "Santander"},
    {"tenant": "vodafone", "wd": "wd3", "site": "VodafoneExternalCareers", "name": "Vodafone"},
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
    "starlingbank": "Starling Bank",
    "revolutpeople": "Revolut",
    "wiseeurope": "Wise",
    "gocardless": "GoCardless",
    "checkoutcom": "Checkout.com",
    "zilchtechnology": "Zilch",
    "octopusenergy": "Octopus Energy",
    "thg": "THG",
    "babylonhealth": "Babylon Health",
    "benevolent": "BenevolentAI",
    "improbableworlds": "Improbable",
    "palantirtechnologies": "Palantir",
    "peakuk": "Peak",
    "deliverooengineering": "Deliveroo",
    "snykukltd": "Snyk",
    "moneyhub": "Moneyhub",
    "moneyfarm": "Moneyfarm",
    "pensionbee": "PensionBee",
    "stepstonegroup": "StepStone",
    "currencycloud": "Currencycloud",
    "youlend": "YouLend",
    "citymapper": "Citymapper",
    "skyscanner": "Skyscanner",
    "trainline": "Trainline",
    "learnlight": "Learnlight",
    "mindgym": "MindGym",
    "healx": "Healx",
    "secondmindltd": "SecondMind",
    "faculty": "Faculty AI",
    "zendeskeurope": "Zendesk",
    "atlassianuk": "Atlassian",
    "linearapp": "Linear",
    # SmartRecruiters
    "samsung-r-and-d-institute-uk": "Samsung R&D UK",
    "checkout": "Checkout.com",
    "costa-coffee": "Costa Coffee",
    "publicisgroup": "Publicis",
    # Pinpoint
    "moneysupermarket": "MoneySuperMarket",
    "starling-bank": "Starling Bank",
    "octopus-energy": "Octopus Energy",
    "m-and-s": "Marks & Spencer",
    "john-lewis-partnership": "John Lewis Partnership",
    "asda-stores": "Asda",
    "british-airways": "British Airways",
    "tesco-technology": "Tesco Technology",
    # Recruitee
    "peak-ai": "Peak AI",
    "speech-graphics": "Speech Graphics",
    "signal-ai": "Signal AI",
    "eigen-technologies": "Eigen Technologies",
    "kheiron-medical": "Kheiron Medical",
    "contentstack": "Contentstack",
    "productboard": "Productboard",
    # Lever
    "mosaic-ml": "MosaicML",
    "fiveai": "Five AI",
    "scaleai": "Scale AI",
    "gongio": "Gong",
    # Greenhouse (new)
    "isomorphiclabs": "Isomorphic Labs",
    "oxfordnanopore": "Oxford Nanopore Technologies",
    # Ashby (new)
    "11x": "11x",
    # Workable (new)
    "legalandgeneral": "Legal & General",
    "welcometothejungle": "Welcome to the Jungle",
}

# Personio ATS boards: https://{slug}.jobs.personio.de/xml?language=en
# UK/EU companies using Personio. Verified Feb 2026 + expanded Batch 3.
PERSONIO_COMPANIES = [
    # Original (10)
    "celonis", "trade-republic", "sennder", "contentful",
    "personio", "forto", "taxfix", "wonderkind",
    "airfocus", "heydata",
    # Batch 3 additions (8)
    "getyourguide", "omio", "finn", "choco",
    "scalable", "raisin", "flink", "lanamedical",
]

# SAP SuccessFactors career site sitemaps
# UK defence/enterprise companies. Unchanged in Batch 3 (slugs are dicts
# with sitemap_url so expansion requires per-site discovery).
SUCCESSFACTORS_COMPANIES = [
    {"name": "BAE Systems", "sitemap_url": "https://jobs.baesystems.com/sitemap.xml"},
    {"name": "QinetiQ", "sitemap_url": "https://careers.qinetiq.com/sitemap.xml"},
    {"name": "Thales UK", "sitemap_url": "https://careers.thalesgroup.com/sitemap.xml"},
    # MBDA removed: careers.mbda-systems.com DNS resolution fails
]


# Rippling ATS public board: https://ats.rippling.com/api/board/{slug}/jobs
# Added in Batch 3 — starter set of UK-facing slugs; expand via the
# Feashliaa repo parse in a follow-up batch.
RIPPLING_COMPANIES = [
    "rippling",
    "checkr",
    "figma",
    "scalepath",
    "linear",
]


# Comeet ATS public board: https://www.comeet.co/careers-api/2.0/company/{slug}/positions
# QUARANTINED 2026-06-11: the API now requires a per-company token (HTTP 400
# "Token is missing" without one). Probe results for the original Batch 3 set:
#   celonis-process-mining, placer-ai  -> HTTP 404 (slug gone)
#   riskified, lightricks, fiverr      -> HTTP 400 token gate
#   riskified + lightricks have moved to Greenhouse (zero UK locations there).
# Only the canary slug remains — ComeetSource probes it once per run and
# resumes full fetching automatically if the gate is ever lifted.
COMEET_COMPANIES = [
    "fiverr",
]

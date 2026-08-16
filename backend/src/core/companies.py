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

# Workable boards: POST https://apply.workable.com/api/v2/accounts/{slug}/jobs
# RE-VERIFIED LIVE 2026-08-10 — 38 slugs checked (all 18 configured + 20 new).
# The old 18-slug list yielded ZERO usable jobs: 17 returned HTTP 200 with
# total=0 (drained boards) and were dropped; only "huggingface" still had
# postings (7), and all 7 are US/FR so _is_uk_or_remote drops them — kept
# anyway because it is a real board that can repopulate.
# The 20 slugs below were each observed TWICE on separate runs: HTTP 200 AND
# total >= 1, with page-1 UK-or-remote yield measured through the real
# _is_uk_or_remote from sources/base.py.
# TRAP: this endpoint returns HTTP 200 with total=0 for accounts that do NOT
# exist, so the status code alone never proves a slug is real. Require
# total >= 1 before adding anything here.
# KNOWN GAP (not fixed here): workable.py reads only data["results"] and never
# follows the nextPage cursor, so the API's 10-per-page cap means big boards
# (abm-careers 168, charity-link 161, indra-uk 53) contribute at most 10 jobs
# per run. ~600 postings across this list stay unread until that is fixed.
# CHURN RISK: warden-ai (1 job) and gener8 (3) are most likely to empty next;
# abm-careers, charity-link, indra-uk and icmp-6 are the durable core.
WORKABLE_COMPANIES = [
    # Survivor of the pre-2026-08-10 list (1)
    "huggingface",
    # Verified live 2026-08-10 (20) — tech / AI
    "appvia", "suade", "yapily", "warden-ai",
    "tomoro-ai", "gener8", "ngeneration", "indra-uk",
    # facilities / property / construction
    "abm-careers", "workman-llp", "statom", "london-energy-1",
    "peopleworth",
    # retail / marketing / agency
    "debenhamsgroup", "n2o-1", "soar-with-us",
    # recruitment / education / medical / charity
    "lafosse", "icmp-6", "costello-medical", "charity-link",
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
# RE-VERIFIED LIVE 2026-08-10 — 53 slugs checked (all 15 configured + 38 new).
# 14 of the 15 configured slugs returned HTTP 404 with an identical
# 11,684-byte error page — the same bytes three nonsense control slugs got, so
# the tenants genuinely do not exist — and were dropped. Only "arm" survives
# (HTTP 200, 4 jobs). Each of the 38 below was curled twice: HTTP 200 with
# >= 1 real posting; 1,391 postings total across them on 2026-08-10.
#
# *** DO NOT ADD A PINPOINT SLUG ON "HTTP 200 + >= 1 job" ALONE. ***
# Pinpoint serves HTTP 200 with FAKE SEED JOBS on trial/sandbox tenants. The
# signature is this verbatim posting set, byte-identical across unrelated
# companies: "Head of DEI - Belfast" / "- US" / "- UK", "Marketing Manager"
# (Paris), "Customer Service Rep" (New York). Such a tenant would ingest
# cleanly and inject fabricated jobs into the shared catalog. Confirmed
# poisoned on 2026-08-10, BANNED: grantthornton, nspcc, saga, soenergy,
# simplyhealth, proximie, schroders, macmillan, rsmuk, mha, scope, esure,
# azets, admiralgroup, hcone, cazoo, rightmove, travelperk, elder, sonovate,
# thoughtmachine. Every slug below was checked against that signature.
#
# BOUNDS: this is not an enumeration of Pinpoint's customers — CT-log mining
# is impossible (customer boards sit behind a wildcard *.pinpointhq.com cert),
# so the list is what search + ~280 guess-and-verify probes could prove.
PINPOINT_COMPANIES = [
    # Survivor of the pre-2026-08-10 list (1)
    "arm",
    # Verified live 2026-08-10 (38), highest posting count first
    "priorygroup", "davies", "networkplus", "natcen",
    "sumer", "cfc", "lush", "ogier",
    "pxlimited", "djh", "rowdentech", "made-tech",
    "sandpiperci", "goodenergy", "bluecross", "lvcaregroup",
    "digitalscience", "stint", "thyssenkrupp", "guernseyelectricity",
    "whiteswandata", "zencargo", "wearemapp", "nmc",
    "wfs", "snapanalytics", "collascrill", "gordonmurraygroup",
    "harpercollins", "jerseywater", "bedellcristin", "fundapps",
    "ports", "jerseypost", "jtglobal", "wealthwizards",
    "accenture", "workwithus",
]

# Recruitee boards: https://{slug}.recruitee.com/api/offers/
# RE-VERIFIED LIVE 2026-08-10 — 50 slugs checked (all 20 configured + 30 new).
# 19 of the 20 configured slugs returned HTTP 404 with an empty body (board
# gone) and were dropped; only "wonderkind" still returns HTTP 200 (2 offers),
# but both offers fail _is_uk_or_remote, so the old list produced literally
# nothing. wonderkind is kept because the board is real and can repopulate.
# Each of the 30 below returned HTTP 200 with >= 1 offer AND >= 1 offer passing
# the source's own _is_uk_or_remote (imported from sources/base.py, not a
# lookalike heuristic); counts reproduced identically on two probe runs.
# NOTE: "causalens" (causaLens) is NOT a respelling of the dropped "causaly"
# (Causaly) — different companies. Causaly's board is gone.
# BOUNDS: guessing famous UK tech names does not work on Recruitee (29 such
# guesses all 404'd — they are on Greenhouse/Workday). These came from
# harvesting live *.recruitee.com career pages, then curl-verifying each.
# Several are retail/logistics/hospitality rather than tech (dckgroup,
# itxuklimited, theentouragegroup, kikomilano) — high UK volume, not tech-heavy.
RECRUITEE_COMPANIES = [
    # Survivor of the pre-2026-08-10 list (1)
    "wonderkind",
    # Verified live 2026-08-10 (30), highest UK-or-remote yield first
    "dckgroup", "transperfect", "itxuklimited", "theentouragegroup",
    "customssupport", "framestore", "bcngroup", "deephealth",
    "kikomilano", "bitfinex", "gevgroupltd", "theconfigteamcareers",
    "signode", "sfors", "hybrid", "grid",
    "natilik", "avenircollective", "junolegal", "talentheroesclientats",
    "bespokebritannia", "gain", "jeranexbp", "medica",
    "metyisag", "wmreplyjobs", "fastned", "causalens",
    "digitickets", "zeotap",
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
    # Pinpoint (2026-08-10: the 14 dead slugs' overrides were removed with them)
    "ports": "Ports of Jersey",
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
    # Workable — the "-1"/"-6" tails are Workable's own uniqueness suffixes,
    # not part of the company name. Slugs whose real trading name I could not
    # verify are intentionally left to the slug.title() fallback rather than
    # guessed (2026-08-10).
    "london-energy-1": "London Energy",
    "n2o-1": "N2O",
    # Personio — "AG" is a German legal-form suffix, stripped for display
    "exnaton-ag": "Exnaton",
    "astormueller-ag": "Astormueller",
}

# Personio ATS boards: https://{slug}.jobs.personio.de/xml?language=en
# RE-VERIFIED LIVE 2026-08-10 — 43 slugs checked (all 18 configured + 25 new).
# 16 of the 18 configured slugs returned HTTP 307 -> https://personio.com (that
# subdomain no longer exists) and "finn" returned an empty <workzag-jobs/> feed
# — all 17 dropped. Only "personio" itself still serves a feed (1 position,
# Berlin/Munich, so it yields no UK jobs); kept as the vendor liveness canary.
# Each of the 25 below returned HTTP 200 with >= 1 <position>, and was
# re-parsed through the real _is_uk_or_remote from sources/base.py.
# NOTE: a 307 is NOT a vendor shutdown — the XML board is alive.
# {slug}.jobs.personio.com is an alias of the same board, so switching domain
# does not revive a dead slug.
# DELIBERATELY EXCLUDED: high-volume DACH-only boards that only "pass" because
# German city names are absent from FOREIGN_INDICATORS and fall through the
# unknown-location -> keep branch (skalbach-gmbh 168, matrix42 28,
# holidaycheck, recup, coinmerce, the-usual, ...). Volume, not UK relevance.
# COST NOTE: personio.py sleeps 3s between companies, so 26 slugs = ~75s per
# run before any HTTP time. It also breaks out after 2 consecutive empty
# responses — with the old all-dead list that fired immediately, which is why
# this source was silently contributing nothing.
PERSONIO_COMPANIES = [
    # Survivor of the pre-2026-08-10 list (1)
    "personio",
    # Verified live 2026-08-10 (25) — genuine UK offices first
    "flatpay", "stark", "merantix", "intigriti",
    "herdify", "olio", "exnaton-ag",
    # remote-first / remote-bearing boards that pass the UK-or-remote filter
    "war-child-alliance", "moore-tk", "holidu", "index-soft",
    "gridx", "hahnair", "gnosis", "astormueller-ag",
    "metycle", "tracify", "maltego", "super-ai",
    "otonomee", "k15t", "ling", "edgeless-systems",
    "friendsurance", "helloinside",
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

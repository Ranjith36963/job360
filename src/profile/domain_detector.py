"""Detect professional domain(s) from a user profile.

Reads CV data + preferences and returns detected domains. Users can be
multi-domain (e.g., "Healthcare + Technology"). Detection enhances display
and synonym matching but NEVER limits search.
"""

from __future__ import annotations

from src.profile.models import UserProfile

# Domain signals: domain_name -> (title_keywords, skill_keywords)
# A domain is detected if enough signals match.
_DOMAIN_SIGNALS: dict[str, tuple[set[str], set[str]]] = {
    "Technology": (
        {"engineer", "developer", "devops", "sre", "architect", "programmer",
         "fullstack", "frontend", "backend", "software", "platform"},
        {"python", "javascript", "java", "react", "docker", "kubernetes",
         "aws", "azure", "gcp", "sql", "node.js", "typescript", "git",
         "ci/cd", "terraform", "linux", "api", "microservices"},
    ),
    "Data & AI": (
        {"data", "ml", "ai", "machine", "learning", "scientist", "analytics",
         "nlp", "deep", "llm", "genai"},
        {"pytorch", "tensorflow", "pandas", "scikit-learn", "spark", "airflow",
         "snowflake", "dbt", "bigquery", "langchain", "rag", "hugging face",
         "computer vision", "nlp", "machine learning", "deep learning"},
    ),
    "Healthcare": (
        {"nurse", "nursing", "clinical", "medical", "health", "nhs",
         "physiotherap", "pharmacy", "doctor", "gp", "midwife", "care",
         "paramedic", "therapist", "dental"},
        {"nhs", "cqc", "gmp", "ehr", "emr", "patient care", "clinical trials",
         "pharmacy", "nursing", "physiotherapy", "occupational therapy",
         "safeguarding", "clinical assessment"},
    ),
    "Finance": (
        {"accountant", "financial", "finance", "banking", "audit", "treasury",
         "actuari", "compliance", "underwriter", "tax"},
        {"acca", "cima", "cfa", "ifrs", "gaap", "aml", "kyc", "fp&a",
         "bookkeeping", "sap", "xero", "sage", "vat", "paye",
         "financial modelling", "audit", "budgeting", "forecasting"},
    ),
    "Legal": (
        {"solicitor", "lawyer", "barrister", "paralegal", "legal", "counsel",
         "conveyancer", "litigat"},
        {"gdpr", "sra", "lpc", "conveyancing", "litigation", "m&a",
         "corporate law", "employment law", "intellectual property",
         "compliance", "due diligence", "contract law"},
    ),
    "Marketing": (
        {"marketing", "brand", "content", "copywriter", "seo", "digital",
         "social media", "communications", "pr", "creative"},
        {"seo", "sem", "ppc", "google analytics", "hubspot", "salesforce",
         "crm", "content marketing", "digital marketing", "google ads",
         "social media", "branding", "copywriting"},
    ),
    "Human Resources": (
        {"hr", "recruitment", "talent", "people", "cipd", "l&d",
         "employee relations", "payroll"},
        {"cipd", "recruitment", "talent acquisition", "ats", "payroll",
         "employee relations", "l&d", "dei", "onboarding", "hris"},
    ),
    "Education": (
        {"teacher", "lecturer", "tutor", "professor", "academic",
         "education", "teaching", "curriculum"},
        {"qts", "pgce", "sen", "send", "cpd", "safeguarding", "curriculum",
         "differentiation", "ofsted", "pastoral"},
    ),
    "Engineering": (
        {"mechanical", "electrical", "civil", "structural", "chemical",
         "building", "construction", "hvac", "manufacturing"},
        {"cad", "bim", "hvac", "plc", "scada", "autocad", "solidworks",
         "revit", "pcb", "lean", "six sigma", "iso"},
    ),
    "Project Management": (
        {"project", "programme", "program", "delivery", "pmo", "scrum master",
         "agile coach", "change"},
        {"pmp", "prince2", "agile", "scrum", "kanban", "jira", "itil",
         "stakeholder management", "risk management", "governance"},
    ),
}

# Minimum signals needed to detect a domain
_MIN_TITLE_SIGNALS = 1
_MIN_SKILL_SIGNALS = 2


def detect_domains(profile: UserProfile) -> list[str]:
    """Detect professional domains from user profile.

    Returns a list of detected domain names, e.g. ["Technology", "Data & AI"].
    Multi-domain users get multiple entries.
    """
    # Collect all signals from profile
    all_titles: set[str] = set()
    for t in profile.preferences.target_job_titles:
        all_titles.update(w.lower() for w in t.split())
    for t in profile.cv_data.job_titles:
        all_titles.update(w.lower() for w in t.split())
    for pos in profile.cv_data.linkedin_positions:
        title = pos.get("title", "")
        if title:
            all_titles.update(w.lower() for w in title.split())

    all_skills: set[str] = set()
    for s in profile.preferences.additional_skills:
        all_skills.add(s.lower())
    for s in profile.cv_data.skills:
        all_skills.add(s.lower())
    for s in profile.cv_data.linkedin_skills:
        all_skills.add(s.lower())
    for s in profile.cv_data.github_skills_inferred:
        all_skills.add(s.lower())

    # Also check raw CV text for skill keywords
    cv_lower = profile.cv_data.raw_text.lower()

    detected: list[str] = []
    for domain, (title_kws, skill_kws) in _DOMAIN_SIGNALS.items():
        title_hits = sum(1 for kw in title_kws if kw in all_titles)
        skill_hits = sum(1 for kw in skill_kws
                         if kw in all_skills or kw in cv_lower)
        if title_hits >= _MIN_TITLE_SIGNALS and skill_hits >= _MIN_SKILL_SIGNALS:
            detected.append(domain)
        elif skill_hits >= _MIN_SKILL_SIGNALS + 2:
            # Strong skill signal alone can indicate domain
            detected.append(domain)

    return detected

"""Ten realistic-but-FAKE technical user profiles for the engine ablation.

Every profile is invented (no real person) but modelled on the real user-info
shape: a full CV (titles + skills + summary + work history), GitHub signal
(languages/topics/frameworks), LinkedIn positions, and preferences. They span
the technical job market so the engine eval is stress-tested across roles, not
tuned to one CV.

Used by scripts/eval_fake_profiles.py. `build_user_profile(spec)` turns a spec
into a real `UserProfile` (cv_data + preferences) so the live engines score it.
"""
from __future__ import annotations

# Each spec fills the SCORING-relevant corners: cv (titles/skills/summary/work),
# github, linkedin, and preferences. Kept as plain data so it's reproducible.
PROFILES: list[dict] = [
    {
        "id": "data_engineer",
        "role": "Data Engineer",
        "name": "Alex Rivera",
        "headline": "Data Engineer — batch + streaming pipelines",
        "location": "Manchester, UK",
        "summary": "Mid-level data engineer (4 yrs) building reliable batch and streaming "
        "pipelines. Owns ingestion, warehousing and orchestration; turns messy source data "
        "into trusted, query-ready tables for analytics and ML teams.",
        "job_titles": ["Data Engineer", "Analytics Engineer", "ETL Developer"],
        "skills": ["Python", "SQL", "Apache Spark", "Apache Airflow", "dbt", "Kafka", "Snowflake",
                   "BigQuery", "AWS (S3, Glue, EMR, Redshift)", "Databricks", "Data Modeling",
                   "ETL/ELT", "Data Warehousing", "Pandas", "Terraform", "Docker", "CI/CD",
                   "Delta Lake", "Parquet", "Data Quality", "Great Expectations"],
        "work": [("Data Engineer", "Northwind Analytics", "2022", "now"),
                 ("Junior Data Engineer", "BluePeak Retail", "2020", "2022")],
        "github_username": "alexr-data",
        "github_languages": {"Python": 60, "SQL": 25, "Scala": 10, "HCL": 5},
        "github_topics": ["data-engineering", "airflow", "spark", "etl", "dbt"],
        "github_frameworks": ["Airflow", "Spark", "dbt", "Pandas"],
        "career_domain": "data_engineering",
        "prefs": {"experience_level": "mid", "salary_min": 55000, "salary_max": 85000,
                  "workplace": "hybrid", "needs_visa": False, "locations": ["UK", "Remote UK"]},
    },
    {
        "id": "ai_engineer",
        "role": "AI Engineer",
        "name": "Priya Nair",
        "headline": "AI Engineer — LLM apps, RAG, agents",
        "location": "London, UK",
        "summary": "AI engineer (3 yrs) shipping LLM-powered products: retrieval-augmented "
        "generation, agentic workflows, fine-tuning and evals. Comfortable across the stack "
        "from prompt design to vector search to production deployment.",
        "job_titles": ["AI Engineer", "Machine Learning Engineer", "LLM Engineer"],
        "skills": ["Python", "PyTorch", "LangChain", "LlamaIndex", "Hugging Face Transformers",
                   "OpenAI API", "RAG", "Vector Databases", "ChromaDB", "FAISS", "Pinecone",
                   "Prompt Engineering", "Fine-Tuning", "LoRA", "Embeddings", "FastAPI",
                   "Agents", "Semantic Search", "Model Evaluation", "AWS Bedrock", "Docker"],
        "work": [("AI Engineer", "Cortex Labs", "2023", "now"),
                 ("ML Engineer", "DataForge", "2022", "2023")],
        "github_username": "priya-ai",
        "github_languages": {"Python": 80, "TypeScript": 12, "Jupyter Notebook": 8},
        "github_topics": ["llm", "rag", "langchain", "agents", "nlp"],
        "github_frameworks": ["PyTorch", "LangChain", "FastAPI", "Transformers"],
        "career_domain": "ai_ml",
        "prefs": {"experience_level": "mid", "salary_min": 65000, "salary_max": 100000,
                  "workplace": "remote", "needs_visa": True, "locations": ["UK", "Remote"]},
    },
    {
        "id": "forward_deployed",
        "role": "Forward-Deployed Engineer",
        "name": "Tom Becker",
        "headline": "Forward-Deployed Engineer — customer data solutions",
        "location": "London, UK",
        "summary": "Forward-deployed / solutions engineer (5 yrs) embedding with enterprise "
        "customers to build and ship data + AI solutions end to end. Bridges product and "
        "customer: scoping, prototyping in Python/SQL, and productionising on cloud platforms.",
        "job_titles": ["Forward Deployed Engineer", "Solutions Engineer", "Sales Engineer",
                       "Customer Engineer"],
        "skills": ["Python", "SQL", "Spark", "Databricks", "Snowflake", "REST APIs",
                   "Data Pipelines", "Solution Architecture", "Customer-Facing", "Pre-Sales",
                   "Stakeholder Management", "AWS", "Docker", "Tableau", "Prototyping",
                   "Requirements Gathering", "Cloud Migration", "Demos"],
        "work": [("Forward Deployed Engineer", "Palantir-style Co", "2021", "now"),
                 ("Solutions Engineer", "CloudData Inc", "2019", "2021")],
        "github_username": "tbecker-fde",
        "github_languages": {"Python": 65, "SQL": 20, "JavaScript": 15},
        "github_topics": ["solutions-engineering", "data-platform", "demos"],
        "github_frameworks": ["FastAPI", "Streamlit", "Spark"],
        "career_domain": "solutions_engineering",
        "prefs": {"experience_level": "senior", "salary_min": 70000, "salary_max": 110000,
                  "workplace": "hybrid", "needs_visa": False, "locations": ["UK", "London"]},
    },
    {
        "id": "data_analyst",
        "role": "Data Analyst",
        "name": "Sofia Costa",
        "headline": "Data Analyst — dashboards, insight, SQL",
        "location": "Birmingham, UK",
        "summary": "Data analyst (2 yrs) turning business questions into SQL, dashboards and "
        "clear recommendations. Strong with BI tools and stakeholder storytelling; some Python "
        "for deeper analysis. Early-career, eager to grow into analytics engineering.",
        "job_titles": ["Data Analyst", "Business Intelligence Analyst", "Insight Analyst"],
        "skills": ["SQL", "Excel", "Tableau", "Power BI", "Looker", "Python", "Pandas",
                   "Data Visualization", "Statistics", "A/B Testing", "Dashboards",
                   "Stakeholder Reporting", "Google Analytics", "DAX", "Data Cleaning",
                   "Storytelling", "KPIs"],
        "work": [("Data Analyst", "MarketPulse", "2023", "now"),
                 ("Junior Analyst", "RetailIQ", "2022", "2023")],
        "github_username": "sofia-analytics",
        "github_languages": {"Python": 45, "R": 20, "SQL": 35},
        "github_topics": ["data-analysis", "tableau", "sql", "visualization"],
        "github_frameworks": ["Pandas", "Matplotlib", "Plotly"],
        "career_domain": "data_analytics",
        "prefs": {"experience_level": "junior", "salary_min": 32000, "salary_max": 50000,
                  "workplace": "hybrid", "needs_visa": False, "locations": ["UK"]},
    },
    {
        "id": "data_scientist",
        "role": "Data Scientist",
        "name": "Daniel Okafor",
        "headline": "Data Scientist — modelling, experimentation, ML",
        "location": "Edinburgh, UK",
        "summary": "Data scientist (4 yrs) building predictive models and running experiments "
        "that drive product decisions. Strong stats + ML foundations, ships models to "
        "production, communicates results to non-technical stakeholders.",
        "job_titles": ["Data Scientist", "Machine Learning Scientist", "Applied Scientist"],
        "skills": ["Python", "scikit-learn", "Pandas", "NumPy", "Statistics", "Machine Learning",
                   "XGBoost", "Experimentation", "A/B Testing", "Causal Inference", "SQL",
                   "PyTorch", "Feature Engineering", "MLflow", "Bayesian Methods", "Forecasting",
                   "NLP", "Jupyter", "Model Deployment", "AWS SageMaker"],
        "work": [("Data Scientist", "Insight Genie", "2021", "now"),
                 ("Junior Data Scientist", "QuantEdge", "2020", "2021")],
        "github_username": "danokafor-ds",
        "github_languages": {"Python": 75, "R": 15, "Jupyter Notebook": 10},
        "github_topics": ["data-science", "machine-learning", "statistics", "forecasting"],
        "github_frameworks": ["scikit-learn", "XGBoost", "PyTorch", "MLflow"],
        "career_domain": "data_science",
        "prefs": {"experience_level": "mid", "salary_min": 55000, "salary_max": 85000,
                  "workplace": "remote", "needs_visa": False, "locations": ["UK", "Remote"]},
    },
    {
        "id": "cyber_security",
        "role": "Cyber Security Engineer",
        "name": "Hana Yilmaz",
        "headline": "Security Engineer — cloud, detection, DevSecOps",
        "location": "Bristol, UK",
        "summary": "Cyber security engineer (4 yrs) hardening cloud platforms and building "
        "detection. Threat modelling, vulnerability management, SIEM and security automation; "
        "shifts security left into CI/CD. CISSP in progress.",
        "job_titles": ["Security Engineer", "Cyber Security Engineer", "Cloud Security Engineer",
                       "DevSecOps Engineer"],
        "skills": ["Python", "SIEM", "Splunk", "Threat Detection", "Incident Response",
                   "Vulnerability Management", "AWS Security", "IAM", "Penetration Testing",
                   "SOC", "MITRE ATT&CK", "Kubernetes Security", "Terraform", "Network Security",
                   "Cryptography", "DevSecOps", "Burp Suite", "Nessus", "Zero Trust", "SAST/DAST"],
        "work": [("Security Engineer", "ShieldOps", "2022", "now"),
                 ("Security Analyst", "SafeHarbor", "2020", "2022")],
        "github_username": "hana-sec",
        "github_languages": {"Python": 55, "Go": 25, "Shell": 20},
        "github_topics": ["security", "devsecops", "cloud-security", "detection"],
        "github_frameworks": ["Terraform", "Falco", "Trivy"],
        "career_domain": "cyber_security",
        "prefs": {"experience_level": "mid", "salary_min": 55000, "salary_max": 90000,
                  "workplace": "hybrid", "needs_visa": True, "locations": ["UK"]},
    },
    {
        "id": "soc_analyst",
        "role": "SOC Analyst",
        "name": "Marcus Webb",
        "headline": "SOC Analyst — monitoring, triage, threat hunting",
        "location": "Leeds, UK",
        "summary": "SOC analyst (2 yrs) on a 24/7 blue team: monitoring SIEM alerts, triaging "
        "incidents, and threat hunting. Strong on log analysis and playbooks; growing into "
        "detection engineering. Early-career, security-cleared eligible.",
        "job_titles": ["SOC Analyst", "Security Operations Analyst", "Threat Analyst",
                       "Cyber Security Analyst"],
        "skills": ["SIEM", "Splunk", "Microsoft Sentinel", "Incident Response", "Triage",
                   "Threat Hunting", "Log Analysis", "MITRE ATT&CK", "EDR", "CrowdStrike",
                   "Phishing Analysis", "Network Traffic Analysis", "Wireshark", "Python",
                   "Playbooks", "SOAR", "IOCs", "Malware Analysis (basic)"],
        "work": [("SOC Analyst (Tier 2)", "GuardTower MSSP", "2023", "now"),
                 ("SOC Analyst (Tier 1)", "GuardTower MSSP", "2022", "2023")],
        "github_username": "mwebb-soc",
        "github_languages": {"Python": 60, "PowerShell": 30, "Shell": 10},
        "github_topics": ["soc", "blue-team", "threat-hunting", "detection"],
        "github_frameworks": ["Sigma", "YARA"],
        "career_domain": "cyber_security",
        "prefs": {"experience_level": "junior", "salary_min": 30000, "salary_max": 48000,
                  "workplace": "onsite", "needs_visa": False, "locations": ["UK"]},
    },
    {
        "id": "business_analyst",
        "role": "Business Analyst (Technical)",
        "name": "Grace Lindqvist",
        "headline": "Technical Business Analyst — requirements, data, delivery",
        "location": "London, UK",
        "summary": "Technical business analyst (5 yrs) bridging business and engineering: "
        "elicits requirements, writes specs and user stories, models processes, and validates "
        "delivery. SQL-literate, works closely with data and product teams in Agile delivery.",
        "job_titles": ["Business Analyst", "Technical Business Analyst", "Product Analyst",
                       "Systems Analyst"],
        "skills": ["Requirements Gathering", "User Stories", "SQL", "Process Mapping", "BPMN",
                   "Stakeholder Management", "Agile", "Scrum", "JIRA", "Confluence", "UAT",
                   "Data Analysis", "Excel", "Wireframing", "Gap Analysis", "Documentation",
                   "Power BI", "Workshops", "Backlog Management"],
        "work": [("Technical Business Analyst", "Finovate Bank", "2021", "now"),
                 ("Business Analyst", "LogiServe", "2019", "2021")],
        "github_username": "grace-ba",
        "github_languages": {"SQL": 60, "Python": 40},
        "github_topics": ["business-analysis", "requirements", "process"],
        "github_frameworks": ["Pandas"],
        "career_domain": "business_analysis",
        "prefs": {"experience_level": "mid", "salary_min": 50000, "salary_max": 75000,
                  "workplace": "hybrid", "needs_visa": False, "locations": ["UK", "London"]},
    },
    {
        "id": "fullstack",
        "role": "Full-Stack Software Engineer",
        "name": "Leo Marsh",
        "headline": "Full-Stack Software Engineer — web apps end to end",
        "location": "Remote, UK",
        "summary": "Mid-level full-stack software engineer (4 yrs) building web apps from "
        "frontend to backend to deploy. Comfortable owning a feature end to end: React UI, "
        "Node/Python APIs, Postgres, and shipping on AWS with CI/CD.",
        "job_titles": ["Full Stack Software Engineer", "Software Engineer", "Backend Engineer",
                       "Frontend Engineer"],
        "skills": ["JavaScript", "TypeScript", "React", "Next.js", "Node.js", "Express",
                   "Python", "Django", "FastAPI", "REST APIs", "GraphQL", "PostgreSQL",
                   "MongoDB", "Redis", "Docker", "Kubernetes", "AWS", "CI/CD", "Microservices",
                   "System Design", "Tailwind CSS"],
        "work": [("Software Engineer", "AppFoundry", "2022", "now"),
                 ("Junior Developer", "WebWorks", "2020", "2022")],
        "github_username": "leomarsh-dev",
        "github_languages": {"TypeScript": 45, "JavaScript": 30, "Python": 25},
        "github_topics": ["fullstack", "react", "nodejs", "web"],
        "github_frameworks": ["React", "Next.js", "Express", "FastAPI"],
        "career_domain": "software_engineering",
        "prefs": {"experience_level": "mid", "salary_min": 50000, "salary_max": 90000,
                  "workplace": "remote", "needs_visa": True, "locations": ["UK", "Remote"]},
    },
    {
        "id": "devops_sre",
        "role": "DevOps / SRE",
        "name": "Ivan Petrov",
        "headline": "DevOps / SRE — Kubernetes, IaC, reliability",
        "location": "Remote, UK",
        "summary": "DevOps / SRE engineer (6 yrs) keeping platforms reliable and shippable: "
        "Kubernetes, infrastructure-as-code, CI/CD, observability and on-call. Automates toil "
        "and drives reliability with SLOs. Senior, deeply hands-on.",
        "job_titles": ["DevOps Engineer", "Site Reliability Engineer", "Platform Engineer",
                       "Cloud Engineer"],
        "skills": ["Kubernetes", "Terraform", "AWS", "GCP", "Docker", "CI/CD", "GitHub Actions",
                   "ArgoCD", "Helm", "Prometheus", "Grafana", "Observability", "Linux", "Python",
                   "Go", "Ansible", "SLOs", "Incident Management", "Infrastructure as Code",
                   "Networking", "Service Mesh"],
        "work": [("Site Reliability Engineer", "ScaleOps", "2021", "now"),
                 ("DevOps Engineer", "CloudNine", "2018", "2021")],
        "github_username": "ipetrov-sre",
        "github_languages": {"Go": 40, "Python": 30, "HCL": 20, "Shell": 10},
        "github_topics": ["devops", "kubernetes", "sre", "terraform", "observability"],
        "github_frameworks": ["Terraform", "Helm", "Prometheus", "ArgoCD"],
        "career_domain": "devops",
        "prefs": {"experience_level": "senior", "salary_min": 70000, "salary_max": 115000,
                  "workplace": "remote", "needs_visa": False, "locations": ["UK", "Remote"]},
    },
]


def build_user_profile(spec: dict):
    """Turn a fake-profile spec into a real ``UserProfile`` (cv_data + prefs).

    Fills the scoring-relevant CV corners (titles/skills/summary/raw_text +
    LinkedIn work history + GitHub signal) and the preferences, so the live
    engines score it exactly as they would a real parsed profile.
    """
    from src.services.profile.models import CVData, UserPreferences, UserProfile  # noqa: PLC0415

    p = spec["prefs"]
    raw = (
        f"{spec['name']} — {spec['headline']}\n{spec['summary']}\n"
        f"Skills: {', '.join(spec['skills'])}\n"
        f"Experience: " + "; ".join(f"{t} at {c} ({s}-{e})" for t, c, s, e in spec["work"])
    )
    cv = CVData(
        job_titles=list(spec["job_titles"]),
        skills=list(spec["skills"]),
        summary=spec["summary"],
        raw_text=raw,
        name=spec["name"],
        headline=spec["headline"],
        location=spec["location"],
        linkedin_positions=[
            {"title": t, "company": c, "start": s, "end": e, "description": ""}
            for (t, c, s, e) in spec["work"]
        ],
        linkedin_industry=spec["role"],
        github_languages=dict(spec["github_languages"]),
        github_topics=list(spec["github_topics"]),
        github_frameworks=list(spec["github_frameworks"]),
        career_domain=spec.get("career_domain"),
    )
    prefs = UserPreferences(
        target_job_titles=list(spec["job_titles"]),
        additional_skills=list(spec["skills"]),
        preferred_locations=list(p.get("locations", [])),
        salary_min=p.get("salary_min"),
        salary_max=p.get("salary_max"),
        work_arrangement=p.get("workplace", ""),
        experience_level=p.get("experience_level", ""),
        needs_visa=bool(p.get("needs_visa", False)),
        github_username=spec.get("github_username", ""),
        about_me=spec["summary"],
    )
    return UserProfile(cv_data=cv, preferences=prefs)

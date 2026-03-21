"""Controlled skill inference — infer related skills from existing ones."""

from __future__ import annotations

# Raw relationships: skill -> [(related_skill, confidence)]
# Confidence 0.0-1.0. Only inferred if >= threshold (default 0.7).
_RAW_RELATIONSHIPS: dict[str, list[tuple[str, float]]] = {
    # Cloud platforms
    "AWS": [("Azure", 0.7), ("GCP", 0.7), ("Cloud Computing", 0.8)],
    "Azure": [("AWS", 0.7), ("GCP", 0.7), ("Cloud Computing", 0.8)],
    "GCP": [("AWS", 0.7), ("Azure", 0.7), ("Cloud Computing", 0.8)],

    # Containers & orchestration
    "Docker": [("Kubernetes", 0.8), ("Containers", 0.9), ("Docker Compose", 0.8)],
    "Kubernetes": [("Docker", 0.8), ("Helm", 0.7), ("Containers", 0.9)],
    "Helm": [("Kubernetes", 0.8)],

    # IaC
    "Terraform": [("Infrastructure as Code", 0.9), ("AWS", 0.7), ("CloudFormation", 0.7)],
    "CloudFormation": [("AWS", 0.8), ("Terraform", 0.7)],
    "Ansible": [("Infrastructure as Code", 0.8), ("Puppet", 0.7), ("Chef", 0.7)],
    "Puppet": [("Ansible", 0.7), ("Chef", 0.7)],
    "Chef": [("Ansible", 0.7), ("Puppet", 0.7)],
    "Pulumi": [("Terraform", 0.7), ("Infrastructure as Code", 0.9)],

    # CI/CD
    "Jenkins": [("CI/CD", 0.9), ("GitHub Actions", 0.7), ("GitLab CI", 0.7)],
    "GitHub Actions": [("CI/CD", 0.9), ("Jenkins", 0.7)],
    "GitLab CI": [("CI/CD", 0.9), ("Jenkins", 0.7)],
    "CircleCI": [("CI/CD", 0.9), ("Jenkins", 0.7)],
    "ArgoCD": [("CI/CD", 0.8), ("Kubernetes", 0.8), ("GitOps", 0.9)],

    # Python ecosystem
    "Python": [("pip", 0.8), ("pytest", 0.7)],
    "Django": [("Python", 0.9), ("REST API", 0.8), ("PostgreSQL", 0.7)],
    "Flask": [("Python", 0.9), ("REST API", 0.8)],
    "FastAPI": [("Python", 0.9), ("REST API", 0.9), ("async", 0.8)],
    "Celery": [("Python", 0.8), ("Redis", 0.7), ("RabbitMQ", 0.7)],
    "pandas": [("Python", 0.9), ("NumPy", 0.8), ("Data Analysis", 0.8)],
    "NumPy": [("Python", 0.9), ("pandas", 0.8), ("SciPy", 0.7)],
    "SciPy": [("Python", 0.8), ("NumPy", 0.8)],

    # JavaScript/TypeScript ecosystem
    "JavaScript": [("TypeScript", 0.8), ("Node.js", 0.7)],
    "TypeScript": [("JavaScript", 0.9), ("Node.js", 0.7)],
    "React": [("Next.js", 0.7), ("JavaScript", 0.8), ("TypeScript", 0.7), ("Redux", 0.7)],
    "Next.js": [("React", 0.9), ("TypeScript", 0.7), ("Vercel", 0.7)],
    "Vue.js": [("Nuxt.js", 0.7), ("JavaScript", 0.8)],
    "Nuxt.js": [("Vue.js", 0.9)],
    "Angular": [("TypeScript", 0.9), ("RxJS", 0.8)],
    "Node.js": [("JavaScript", 0.9), ("Express", 0.8), ("npm", 0.8)],
    "Express": [("Node.js", 0.9), ("REST API", 0.8)],

    # Databases
    "PostgreSQL": [("SQL", 0.9), ("MySQL", 0.7)],
    "MySQL": [("SQL", 0.9), ("PostgreSQL", 0.7)],
    "MongoDB": [("NoSQL", 0.9), ("Mongoose", 0.7)],
    "Redis": [("Caching", 0.8), ("NoSQL", 0.7)],
    "Elasticsearch": [("Kibana", 0.8), ("ELK Stack", 0.9)],
    "Cassandra": [("NoSQL", 0.9), ("Distributed Systems", 0.7)],
    "DynamoDB": [("AWS", 0.8), ("NoSQL", 0.9)],
    "SQL Server": [("SQL", 0.9), (".NET", 0.7)],

    # AI/ML
    "PyTorch": [("Deep Learning", 0.9), ("Python", 0.8), ("Neural Networks", 0.8)],
    "TensorFlow": [("Deep Learning", 0.9), ("Python", 0.8), ("Keras", 0.8)],
    "Keras": [("TensorFlow", 0.8), ("Deep Learning", 0.8)],
    "scikit-learn": [("Machine Learning", 0.9), ("Python", 0.8), ("pandas", 0.7)],
    "XGBoost": [("Machine Learning", 0.8), ("scikit-learn", 0.7)],
    "Hugging Face": [("NLP", 0.9), ("Transformers", 0.9), ("PyTorch", 0.7)],
    "OpenCV": [("Computer Vision", 0.9), ("Python", 0.7)],
    "spaCy": [("NLP", 0.9), ("Python", 0.8)],
    "NLTK": [("NLP", 0.8), ("Python", 0.8)],

    # LLM/GenAI
    "LangChain": [("LLM", 0.9), ("RAG", 0.8), ("Python", 0.7), ("OpenAI", 0.7)],
    "LlamaIndex": [("LLM", 0.9), ("RAG", 0.9), ("Python", 0.7)],
    "OpenAI": [("LLM", 0.9), ("GPT", 0.9), ("API", 0.7)],
    "RAG": [("LLM", 0.9), ("Vector Database", 0.8), ("Embeddings", 0.8)],
    "Prompt Engineering": [("LLM", 0.9), ("GPT", 0.7)],

    # Vector databases
    "Pinecone": [("Vector Database", 0.9), ("Embeddings", 0.8), ("RAG", 0.7)],
    "Weaviate": [("Vector Database", 0.9), ("Embeddings", 0.8)],
    "ChromaDB": [("Vector Database", 0.9), ("RAG", 0.8)],
    "Milvus": [("Vector Database", 0.9), ("Embeddings", 0.8)],
    "FAISS": [("Vector Database", 0.8), ("Embeddings", 0.8), ("Python", 0.7)],

    # Data engineering
    "Apache Spark": [("PySpark", 0.9), ("Big Data", 0.9), ("Hadoop", 0.7)],
    "PySpark": [("Apache Spark", 0.9), ("Python", 0.8)],
    "Kafka": [("Event Streaming", 0.9), ("Apache Spark", 0.7), ("Distributed Systems", 0.7)],
    "Airflow": [("Data Engineering", 0.9), ("Python", 0.8), ("ETL", 0.8)],
    "dbt": [("Data Engineering", 0.8), ("SQL", 0.9), ("Analytics Engineering", 0.9)],
    "Snowflake": [("Data Warehouse", 0.9), ("SQL", 0.8), ("dbt", 0.7)],
    "BigQuery": [("GCP", 0.8), ("Data Warehouse", 0.9), ("SQL", 0.8)],
    "Redshift": [("AWS", 0.8), ("Data Warehouse", 0.9), ("SQL", 0.8)],
    "Hadoop": [("Big Data", 0.9), ("HDFS", 0.8), ("MapReduce", 0.8)],

    # Observability
    "Prometheus": [("Grafana", 0.9), ("Monitoring", 0.9), ("Kubernetes", 0.7)],
    "Grafana": [("Prometheus", 0.8), ("Monitoring", 0.9), ("Dashboards", 0.8)],
    "Datadog": [("Monitoring", 0.9), ("APM", 0.8), ("Logging", 0.7)],
    "New Relic": [("Monitoring", 0.9), ("APM", 0.8)],
    "Splunk": [("Logging", 0.9), ("SIEM", 0.8), ("Monitoring", 0.7)],
    "ELK Stack": [("Elasticsearch", 0.9), ("Kibana", 0.8), ("Logstash", 0.8)],

    # Testing
    "pytest": [("Python", 0.8), ("TDD", 0.7)],
    "Jest": [("JavaScript", 0.8), ("TypeScript", 0.7), ("React", 0.7)],
    "Cypress": [("E2E Testing", 0.9), ("JavaScript", 0.7)],
    "Selenium": [("E2E Testing", 0.8), ("Test Automation", 0.9)],
    "JUnit": [("Java", 0.9), ("TDD", 0.7)],

    # Java/.NET/Ruby/Go
    "Java": [("Spring", 0.8), ("Maven", 0.7), ("JUnit", 0.7)],
    "Spring": [("Java", 0.9), ("Spring Boot", 0.9), ("REST API", 0.8)],
    "Spring Boot": [("Spring", 0.9), ("Java", 0.9), ("Microservices", 0.8)],
    ".NET": [("C#", 0.9), ("ASP.NET", 0.8), ("SQL Server", 0.7)],
    "C#": [(".NET", 0.9), ("ASP.NET", 0.7)],
    "Ruby": [("Ruby on Rails", 0.8)],
    "Ruby on Rails": [("Ruby", 0.9), ("REST API", 0.8), ("PostgreSQL", 0.7)],
    "Go": [("Microservices", 0.7), ("gRPC", 0.7), ("Kubernetes", 0.7)],
    "Rust": [("Systems Programming", 0.8), ("WebAssembly", 0.7)],
    "Scala": [("Apache Spark", 0.8), ("JVM", 0.8), ("Functional Programming", 0.8)],

    # Mobile
    "React Native": [("React", 0.8), ("Mobile Development", 0.9), ("JavaScript", 0.7)],
    "Flutter": [("Dart", 0.9), ("Mobile Development", 0.9)],
    "Swift": [("iOS", 0.9), ("Mobile Development", 0.8)],
    "Kotlin": [("Android", 0.9), ("Java", 0.7), ("Mobile Development", 0.8)],

    # Methodologies
    "Agile": [("Scrum", 0.8), ("Kanban", 0.7), ("Jira", 0.7)],
    "Scrum": [("Agile", 0.9), ("Sprint Planning", 0.8), ("Jira", 0.7)],
    "DevOps": [("CI/CD", 0.9), ("Docker", 0.7), ("Kubernetes", 0.7), ("Terraform", 0.7)],
    "SRE": [("DevOps", 0.8), ("Monitoring", 0.8), ("Incident Management", 0.8)],
    "Microservices": [("API", 0.8), ("Docker", 0.7), ("Kubernetes", 0.7)],

    # ── Healthcare ──
    "Nursing": [("Patient Care", 0.9), ("Clinical Assessment", 0.8), ("NHS", 0.7)],
    "NHS": [("Healthcare", 0.9), ("CQC", 0.7)],
    "Physiotherapy": [("Rehabilitation", 0.9), ("Musculoskeletal", 0.8)],
    "Occupational Therapy": [("Rehabilitation", 0.8), ("Patient Care", 0.8)],
    "Pharmacy": [("Dispensing", 0.9), ("GMP", 0.7)],
    "Clinical Trials": [("GCP", 0.8), ("Regulatory Affairs", 0.8), ("CRO", 0.7)],

    # ── Finance & Accounting ──
    "ACCA": [("Financial Reporting", 0.9), ("IFRS", 0.8), ("Audit", 0.8)],
    "CIMA": [("Management Accounting", 0.9), ("Budgeting", 0.8), ("Forecasting", 0.8)],
    "CFA": [("Investment Analysis", 0.9), ("Portfolio Management", 0.8)],
    "FP&A": [("Budgeting", 0.9), ("Forecasting", 0.9), ("Financial Modelling", 0.8)],
    "AML": [("KYC", 0.9), ("Financial Crime", 0.9), ("Compliance", 0.8)],
    "KYC": [("AML", 0.9), ("Due Diligence", 0.8)],
    "Bookkeeping": [("Accounts Payable", 0.8), ("Accounts Receivable", 0.8), ("VAT", 0.7)],
    "SAP": [("ERP", 0.9), ("Finance", 0.7)],
    "Xero": [("Bookkeeping", 0.8), ("Accounting", 0.8)],
    "Sage": [("Bookkeeping", 0.8), ("Payroll", 0.7)],

    # ── Legal ──
    "Conveyancing": [("Property Law", 0.9), ("Land Registry", 0.8)],
    "Litigation": [("Dispute Resolution", 0.9), ("Civil Procedure", 0.8)],
    "Corporate Law": [("M&A", 0.8), ("Company Law", 0.9), ("Due Diligence", 0.7)],
    "GDPR": [("Data Protection", 0.9), ("Privacy", 0.8), ("Compliance", 0.8)],
    "Employment Law": [("HR", 0.7), ("Tribunal", 0.8)],

    # ── Marketing ──
    "SEO": [("SEM", 0.8), ("Google Analytics", 0.8), ("Content Marketing", 0.7)],
    "PPC": [("Google Ads", 0.9), ("SEM", 0.9), ("Digital Marketing", 0.8)],
    "CRM": [("Salesforce", 0.7), ("HubSpot", 0.7), ("Customer Engagement", 0.8)],
    "Google Analytics": [("Web Analytics", 0.9), ("SEO", 0.7), ("Tag Manager", 0.7)],
    "HubSpot": [("CRM", 0.8), ("Marketing Automation", 0.9), ("Inbound Marketing", 0.8)],
    "Salesforce": [("CRM", 0.9), ("Sales Operations", 0.7)],

    # ── Human Resources ──
    "CIPD": [("HR", 0.9), ("Employee Relations", 0.8), ("L&D", 0.7)],
    "Recruitment": [("Talent Acquisition", 0.9), ("ATS", 0.8), ("Interviewing", 0.8)],
    "Payroll": [("PAYE", 0.8), ("Pension Administration", 0.7), ("HR", 0.7)],

    # ── Education ──
    "QTS": [("Teaching", 0.9), ("Curriculum", 0.8), ("Safeguarding", 0.7)],
    "SEN": [("Inclusion", 0.8), ("EHCP", 0.8), ("Differentiation", 0.7)],

    # ── Engineering (non-software) ──
    "CAD": [("SolidWorks", 0.7), ("AutoCAD", 0.8), ("Technical Drawing", 0.8)],
    "BIM": [("Revit", 0.9), ("Construction", 0.8), ("CAD", 0.7)],
    "HVAC": [("Building Services", 0.9), ("Mechanical Engineering", 0.8)],
    "PLC": [("SCADA", 0.8), ("Automation", 0.9), ("Control Systems", 0.8)],

    # ── Project Management ──
    "PMP": [("Project Management", 0.9), ("Risk Management", 0.8), ("Stakeholder Management", 0.7)],
    "PRINCE2": [("Project Management", 0.9), ("Governance", 0.8)],
    "ITIL": [("Service Management", 0.9), ("Incident Management", 0.8), ("Change Management", 0.7)],
    "Six Sigma": [("Lean", 0.8), ("Process Improvement", 0.9), ("Quality Management", 0.8)],
    "Lean": [("Six Sigma", 0.8), ("Continuous Improvement", 0.9), ("Kaizen", 0.7)],

    # ══════════════════════════════════════════════════════════════
    # ESCO-derived skill relationships — cross-domain transferability
    # ══════════════════════════════════════════════════════════════

    # ── Cybersecurity ──
    "CISSP": [("Information Security", 0.9), ("Security Architecture", 0.8), ("Risk Management", 0.7)],
    "SOC": [("SIEM", 0.9), ("Incident Response", 0.9), ("Threat Detection", 0.8)],
    "Penetration Testing": [("Vulnerability Assessment", 0.9), ("OWASP", 0.8), ("Information Security", 0.8)],
    "OWASP": [("Application Security", 0.9), ("Penetration Testing", 0.8)],
    "IAM": [("SSO", 0.8), ("LDAP", 0.7), ("Active Directory", 0.7)],
    "SIEM": [("Splunk", 0.8), ("SOC", 0.9), ("Log Analysis", 0.8)],

    # ── Data & Visualization ──
    "Tableau": [("Data Visualization", 0.9), ("Business Intelligence", 0.8), ("SQL", 0.7)],
    "Power BI": [("Data Visualization", 0.9), ("Business Intelligence", 0.8), ("DAX", 0.8)],
    "Looker": [("Data Visualization", 0.8), ("GCP", 0.7), ("SQL", 0.8)],
    "RPA": [("Process Automation", 0.9), ("UiPath", 0.7), ("Blue Prism", 0.7)],
    "UiPath": [("RPA", 0.9), ("Process Automation", 0.8)],
    "Blue Prism": [("RPA", 0.9), ("Process Automation", 0.8)],

    # ── Construction & Trades ──
    "NEBOSH": [("Health and Safety", 0.9), ("Risk Assessment", 0.8), ("IOSH", 0.7)],
    "IOSH": [("Health and Safety", 0.9), ("NEBOSH", 0.7)],
    "CSCS": [("Construction", 0.9), ("Site Safety", 0.8)],
    "CDM": [("Construction", 0.9), ("Health and Safety", 0.8), ("Project Management", 0.7)],
    "SMSTS": [("Site Management", 0.9), ("Construction", 0.8), ("Health and Safety", 0.8)],
    "Quantity Surveying": [("Cost Management", 0.9), ("NRM", 0.8), ("Construction", 0.8)],
    "Revit": [("BIM", 0.9), ("CAD", 0.7), ("Architecture", 0.7)],
    "AutoCAD": [("CAD", 0.9), ("Technical Drawing", 0.8), ("Engineering", 0.7)],
    "SolidWorks": [("CAD", 0.9), ("3D Modelling", 0.8), ("Mechanical Engineering", 0.7)],

    # ── Logistics & Supply Chain ──
    "WMS": [("Warehouse Management", 0.9), ("Inventory Management", 0.8), ("Supply Chain", 0.7)],
    "3PL": [("Logistics", 0.9), ("Supply Chain", 0.8), ("Distribution", 0.8)],
    "SCM": [("Procurement", 0.8), ("Logistics", 0.8), ("Inventory Management", 0.7)],

    # ── Real Estate & Property ──
    "RICS": [("Surveying", 0.9), ("Property Valuation", 0.8), ("Real Estate", 0.8)],
    "Conveyancing": [("Property Law", 0.9), ("Land Registry", 0.8), ("Legal", 0.7)],

    # ── Insurance & Actuarial ──
    "CII": [("Insurance", 0.9), ("Underwriting", 0.8), ("Claims", 0.7)],
    "IFoA": [("Actuarial Science", 0.9), ("Risk Modelling", 0.8), ("Statistics", 0.7)],
    "Underwriting": [("Insurance", 0.9), ("Risk Assessment", 0.8)],

    # ── Social Work & Care ──
    "Safeguarding": [("Child Protection", 0.9), ("DBS", 0.7), ("Vulnerable Adults", 0.8)],
    "Social Work": [("Safeguarding", 0.8), ("Mental Health", 0.7), ("Case Management", 0.8)],
    "DBS": [("Safeguarding", 0.8), ("Vetting", 0.7)],

    # ── Science & Pharma ──
    "GLP": [("Laboratory Management", 0.9), ("Quality Assurance", 0.8), ("Regulatory", 0.7)],
    "HPLC": [("Analytical Chemistry", 0.9), ("Laboratory", 0.8), ("GC", 0.7)],
    "GC": [("Analytical Chemistry", 0.9), ("HPLC", 0.7), ("Mass Spectrometry", 0.7)],
    "PCR": [("Molecular Biology", 0.9), ("Genetics", 0.8), ("Laboratory", 0.7)],
    "MHRA": [("Regulatory Affairs", 0.9), ("GMP", 0.8), ("Pharmacovigilance", 0.7)],
    "GMP": [("Manufacturing", 0.8), ("Quality Assurance", 0.9), ("MHRA", 0.7)],

    # ── Environmental & Sustainability ──
    "ESG": [("Sustainability", 0.9), ("Corporate Governance", 0.8), ("CSR", 0.8)],
    "BREEAM": [("Sustainability", 0.8), ("Construction", 0.7), ("Environmental Assessment", 0.9)],
    "LEED": [("Sustainability", 0.8), ("Green Building", 0.9), ("BREEAM", 0.7)],
    "EIA": [("Environmental Management", 0.9), ("Planning", 0.7), ("Ecology", 0.7)],

    # ── Procurement ──
    "CIPS": [("Procurement", 0.9), ("Supply Chain", 0.8), ("Contract Management", 0.7)],
    "Procurement": [("Contract Management", 0.8), ("Vendor Management", 0.8), ("Negotiation", 0.7)],

    # ── Quality & Manufacturing ──
    "QMS": [("Quality Assurance", 0.9), ("ISO 9001", 0.8), ("Audit", 0.7)],
    "CAPA": [("Quality Assurance", 0.9), ("Root Cause Analysis", 0.8), ("GMP", 0.7)],
    "SOP": [("Quality Assurance", 0.8), ("Process Documentation", 0.9), ("Compliance", 0.7)],
    "FMEA": [("Risk Assessment", 0.9), ("Quality Engineering", 0.8), ("Six Sigma", 0.7)],
    "CNC": [("Manufacturing", 0.9), ("Machining", 0.9), ("CAD", 0.7)],
    "FEA": [("Structural Analysis", 0.9), ("CAD", 0.7), ("Engineering", 0.8)],
    "CFD": [("Fluid Dynamics", 0.9), ("Engineering", 0.8), ("Simulation", 0.8)],
    "HAZOP": [("Process Safety", 0.9), ("Risk Assessment", 0.8), ("Chemical Engineering", 0.7)],

    # ── Hospitality & Food ──
    "HACCP": [("Food Safety", 0.9), ("Quality Assurance", 0.8), ("Compliance", 0.7)],
    "Food Safety": [("HACCP", 0.9), ("Hygiene", 0.8), ("Compliance", 0.7)],

    # ── Telecom & IoT ──
    "IoT": [("Embedded Systems", 0.8), ("Sensors", 0.7), ("MQTT", 0.7)],
    "VoIP": [("SIP", 0.9), ("Telecommunications", 0.9), ("Networking", 0.7)],
    "SDN": [("Networking", 0.9), ("Cloud Computing", 0.7), ("Automation", 0.7)],

    # ── Education (extended) ──
    "EYFS": [("Early Years", 0.9), ("Teaching", 0.8), ("Child Development", 0.8)],
    "Ofsted": [("Education", 0.8), ("School Inspection", 0.9), ("Safeguarding", 0.7)],
    "DSL": [("Safeguarding", 0.9), ("Child Protection", 0.8), ("Education", 0.7)],

    # ── Healthcare (extended) ──
    "NMC": [("Nursing", 0.9), ("Registration", 0.8), ("Clinical Standards", 0.7)],
    "GMC": [("Medical Practice", 0.9), ("Registration", 0.8), ("Clinical Standards", 0.7)],
    "HCPC": [("Allied Health", 0.9), ("Registration", 0.8)],
    "IPC": [("Infection Control", 0.9), ("Clinical Governance", 0.8), ("NHS", 0.7)],
    "Clinical Governance": [("Patient Safety", 0.9), ("Quality Improvement", 0.8), ("NHS", 0.7)],

    # ── Finance (extended) ──
    "VaR": [("Risk Management", 0.9), ("Quantitative Finance", 0.8), ("Statistics", 0.7)],
    "FX": [("Trading", 0.8), ("Capital Markets", 0.8), ("Bloomberg", 0.7)],
    "Bloomberg": [("Financial Data", 0.9), ("Trading", 0.7), ("Capital Markets", 0.7)],
    "Private Equity": [("M&A", 0.8), ("Financial Modelling", 0.9), ("Due Diligence", 0.8)],
    "Venture Capital": [("Startup Investment", 0.9), ("Due Diligence", 0.8), ("Financial Analysis", 0.7)],
}


def _build_bidirectional() -> dict[str, list[tuple[str, float]]]:
    """Build bidirectional lookup from raw relationships."""
    result: dict[str, list[tuple[str, float]]] = {}
    for skill, relations in _RAW_RELATIONSHIPS.items():
        key = skill.lower()
        if key not in result:
            result[key] = []
        for related, conf in relations:
            result[key].append((related, conf))
            # Add reverse relationship
            related_key = related.lower()
            if related_key not in result:
                result[related_key] = []
            result[related_key].append((skill, conf))
    return result


SKILL_RELATIONSHIPS = _build_bidirectional()


def infer_skills(existing_skills: list[str], threshold: float = 0.7) -> list[str]:
    """Infer new skills from existing ones. Only returns skills NOT already in the list."""
    existing_lower = {s.lower() for s in existing_skills}
    inferred: dict[str, float] = {}  # skill_name -> best confidence

    for skill in existing_skills:
        key = skill.lower()
        if key not in SKILL_RELATIONSHIPS:
            continue
        for related, confidence in SKILL_RELATIONSHIPS[key]:
            if confidence < threshold:
                continue
            if related.lower() in existing_lower:
                continue
            rl = related.lower()
            if rl not in inferred or confidence > inferred[rl]:
                inferred[rl] = confidence

    # Return the original-case names from relationships
    name_map: dict[str, str] = {}
    for skill in existing_skills:
        key = skill.lower()
        if key in SKILL_RELATIONSHIPS:
            for related, conf in SKILL_RELATIONSHIPS[key]:
                if related.lower() in inferred:
                    name_map[related.lower()] = related

    return [name_map.get(k, k) for k in sorted(inferred.keys())]


def get_inference_details(existing_skills: list[str], threshold: float = 0.7) -> list[dict]:
    """Get detailed inference info: skill, confidence, inferred_from."""
    existing_lower = {s.lower() for s in existing_skills}
    details: dict[str, dict] = {}

    for skill in existing_skills:
        key = skill.lower()
        if key not in SKILL_RELATIONSHIPS:
            continue
        for related, confidence in SKILL_RELATIONSHIPS[key]:
            if confidence < threshold:
                continue
            if related.lower() in existing_lower:
                continue
            rl = related.lower()
            if rl not in details or confidence > details[rl]["confidence"]:
                details[rl] = {
                    "skill": related,
                    "confidence": confidence,
                    "inferred_from": skill,
                }

    return sorted(details.values(), key=lambda x: (-x["confidence"], x["skill"]))

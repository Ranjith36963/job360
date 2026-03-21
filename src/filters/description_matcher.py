"""Synonym-enhanced skill matching for job descriptions."""

from __future__ import annotations

import re
from functools import lru_cache

# Groups of equivalent terms. Each group is a set of synonyms.
SYNONYM_GROUPS: list[set[str]] = [
    # Languages
    {"JavaScript", "JS"},
    {"TypeScript", "TS"},
    {"Python", "Py"},
    {"C#", "CSharp", "C Sharp"},
    {"C++", "CPP"},
    {"Golang", "Go"},

    # AI/ML
    {"Machine Learning", "ML"},
    {"Deep Learning", "DL"},
    {"Artificial Intelligence", "AI"},
    {"Natural Language Processing", "NLP"},
    {"Computer Vision"},
    {"Large Language Model", "LLM", "Large Language Models", "LLMs"},
    {"Retrieval Augmented Generation", "RAG"},
    {"Generative AI", "GenAI", "Gen AI"},
    {"Reinforcement Learning", "RL"},
    {"Convolutional Neural Network", "CNN", "CNNs"},
    {"Recurrent Neural Network", "RNN", "RNNs"},
    {"Transformer", "Transformers"},
    {"GPT", "Generative Pre-trained Transformer"},

    # Cloud
    {"Amazon Web Services", "AWS"},
    {"Google Cloud Platform", "GCP", "Google Cloud"},
    {"Microsoft Azure", "Azure"},
    {"Infrastructure as Code", "IaC"},
    {"Continuous Integration", "CI"},
    {"Continuous Deployment", "CD"},
    {"CI/CD", "CI CD", "CICD"},

    # Containers & orchestration
    {"Kubernetes", "K8s", "k8s"},
    {"Docker Compose", "docker-compose"},

    # Databases
    {"PostgreSQL", "Postgres"},
    {"MongoDB", "Mongo"},
    {"Elasticsearch", "ES", "Elastic Search"},
    {"DynamoDB", "Dynamo DB"},
    {"SQL Server", "MSSQL", "MS SQL"},
    {"Microsoft SQL Server", "MSSQL"},

    # Data engineering
    {"Apache Spark", "Spark"},
    {"Apache Kafka", "Kafka"},
    {"Apache Airflow", "Airflow"},
    {"Extract Transform Load", "ETL"},
    {"Apache Hadoop", "Hadoop"},
    {"Apache Flink", "Flink"},
    {"Apache Beam", "Beam"},

    # Frameworks
    {"Ruby on Rails", "Rails", "RoR"},
    {"ASP.NET", "ASPNET"},
    {"Spring Boot", "SpringBoot"},
    {"React Native", "ReactNative"},
    {"Vue.js", "Vue", "VueJS"},
    {"Angular.js", "AngularJS"},
    {"Next.js", "NextJS"},
    {"Nuxt.js", "NuxtJS"},
    {"Node.js", "NodeJS", "Node"},
    {"Express.js", "Express", "ExpressJS"},
    {"scikit-learn", "sklearn", "scikit learn"},
    {"Hugging Face", "HuggingFace"},

    # Tools & platforms
    {"GitHub Actions", "GH Actions"},
    {"GitLab CI", "GitLab CICD"},
    {"Visual Studio Code", "VS Code", "VSCode"},
    {"Amazon S3", "S3"},
    {"Amazon EC2", "EC2"},
    {"Amazon ECS", "ECS"},
    {"Amazon EKS", "EKS"},
    {"Amazon Lambda", "AWS Lambda", "Lambda"},
    {"Google Kubernetes Engine", "GKE"},
    {"Azure Kubernetes Service", "AKS"},
    {"Elastic Container Service", "ECS"},

    # Methodologies & practices
    {"Test Driven Development", "TDD"},
    {"Behaviour Driven Development", "BDD"},
    {"Object Oriented Programming", "OOP"},
    {"Functional Programming", "FP"},
    {"Domain Driven Design", "DDD"},
    {"Representational State Transfer", "REST"},
    {"REST API", "RESTful API", "REST APIs"},
    {"GraphQL", "GQL"},
    {"Remote Procedure Call", "RPC", "gRPC"},
    {"Software Development Life Cycle", "SDLC"},
    {"Site Reliability Engineering", "SRE"},
    {"Application Programming Interface", "API"},

    # Data science
    {"Business Intelligence", "BI"},
    {"Data Science", "DS"},
    {"Data Analysis", "Data Analytics"},
    {"Exploratory Data Analysis", "EDA"},

    # ── Healthcare ──
    {"Registered Nurse", "RN"},
    {"Licensed Practical Nurse", "LPN"},
    {"Nurse Practitioner", "NP"},
    {"Electronic Health Record", "EHR", "Electronic Medical Record", "EMR"},
    {"Health and Safety", "H&S"},
    {"Occupational Therapy", "OT"},
    {"Physiotherapy", "Physical Therapy", "PT"},
    {"Speech and Language Therapy", "SLT", "Speech Therapy"},
    {"Cognitive Behavioural Therapy", "CBT"},
    {"General Practitioner", "GP"},
    {"National Health Service", "NHS"},
    {"Care Quality Commission", "CQC"},
    {"Good Manufacturing Practice", "GMP"},
    {"Clinical Research Organisation", "CRO"},

    # ── Finance & Accounting ──
    {"Chartered Accountant", "CA", "ACA"},
    {"Association of Chartered Certified Accountants", "ACCA"},
    {"Chartered Institute of Management Accountants", "CIMA"},
    {"Certified Public Accountant", "CPA"},
    {"Financial Planning and Analysis", "FP&A"},
    {"Accounts Payable", "AP"},
    {"Accounts Receivable", "AR"},
    {"International Financial Reporting Standards", "IFRS"},
    {"Generally Accepted Accounting Principles", "GAAP"},
    {"Anti Money Laundering", "AML"},
    {"Know Your Customer", "KYC"},
    {"Financial Conduct Authority", "FCA"},
    {"Chartered Financial Analyst", "CFA"},
    {"Enterprise Resource Planning", "ERP"},
    {"Profit and Loss", "P&L", "PnL"},

    # ── Legal ──
    {"Solicitors Regulation Authority", "SRA"},
    {"Legal Practice Course", "LPC"},
    {"Intellectual Property", "IP"},
    {"General Data Protection Regulation", "GDPR"},
    {"Non-Disclosure Agreement", "NDA"},
    {"Mergers and Acquisitions", "M&A"},
    {"Power of Attorney", "POA"},

    # ── Marketing & Digital ──
    {"Search Engine Optimisation", "SEO"},
    {"Search Engine Marketing", "SEM"},
    {"Pay Per Click", "PPC"},
    {"Cost Per Acquisition", "CPA"},
    {"Customer Relationship Management", "CRM"},
    {"Key Performance Indicator", "KPI", "KPIs"},
    {"Return on Investment", "ROI"},
    {"Content Management System", "CMS"},
    {"Social Media Marketing", "SMM"},
    {"User Experience", "UX"},
    {"User Interface", "UI"},
    {"Conversion Rate Optimisation", "CRO"},

    # ── Human Resources ──
    {"Chartered Institute of Personnel and Development", "CIPD"},
    {"Human Resources", "HR"},
    {"Human Resource Management", "HRM"},
    {"Diversity Equity and Inclusion", "DEI", "DE&I"},
    {"Employee Relations", "ER"},
    {"Learning and Development", "L&D"},
    {"Applicant Tracking System", "ATS"},

    # ── Education ──
    {"Qualified Teacher Status", "QTS"},
    {"Postgraduate Certificate in Education", "PGCE"},
    {"Special Educational Needs", "SEN", "SEND"},
    {"Continuing Professional Development", "CPD"},
    {"Teaching Assistant", "TA"},
    {"Higher Education", "HE"},
    {"Further Education", "FE"},

    # ── Engineering (non-software) ──
    {"Chartered Engineer", "CEng"},
    {"Computer Aided Design", "CAD"},
    {"Computer Aided Manufacturing", "CAM"},
    {"Building Information Modelling", "BIM"},
    {"Heating Ventilation and Air Conditioning", "HVAC"},
    {"Printed Circuit Board", "PCB"},
    {"Programmable Logic Controller", "PLC"},
    {"Supervisory Control and Data Acquisition", "SCADA"},
    {"Health Safety and Environment", "HSE"},

    # ── Project / Programme Management ──
    {"Project Management Professional", "PMP"},
    {"PRINCE2", "Projects in Controlled Environments"},
    {"Programme Management", "Program Management"},
    {"Statement of Work", "SOW"},
    {"Work Breakdown Structure", "WBS"},
    {"Service Level Agreement", "SLA"},
    {"Information Technology Infrastructure Library", "ITIL"},

    # ── Supply Chain & Operations ──
    {"Supply Chain Management", "SCM"},
    {"Just in Time", "JIT"},
    {"Total Quality Management", "TQM"},
    {"Six Sigma", "6 Sigma"},
    {"Key Account Management", "KAM"},

    # ══════════════════════════════════════════════════════════════
    # ESCO-derived synonym groups — curated for UK job postings
    # ══════════════════════════════════════════════════════════════

    # ── Cybersecurity ──
    {"Information Security", "InfoSec"},
    {"Certified Information Systems Security Professional", "CISSP"},
    {"Certified Ethical Hacker", "CEH"},
    {"Security Operations Centre", "SOC"},
    {"Security Information and Event Management", "SIEM"},
    {"Penetration Testing", "Pen Testing", "Pentest"},
    {"Identity and Access Management", "IAM"},
    {"Multi Factor Authentication", "MFA", "2FA"},
    {"Data Loss Prevention", "DLP"},
    {"Intrusion Detection System", "IDS"},
    {"Intrusion Prevention System", "IPS"},
    {"Web Application Firewall", "WAF"},
    {"Open Web Application Security Project", "OWASP"},
    {"Cyber Essentials", "CE"},
    {"CompTIA Security+", "Security+"},

    # ── Data & Analytics (extended) ──
    {"Power BI", "PowerBI"},
    {"Business Process Modelling", "BPM"},
    {"Robotic Process Automation", "RPA"},
    {"Optical Character Recognition", "OCR"},
    {"Data Warehouse", "DWH"},
    {"Online Analytical Processing", "OLAP"},
    {"Online Transaction Processing", "OLTP"},
    {"Master Data Management", "MDM"},
    {"Data Quality", "DQ"},

    # ── Cloud & SaaS (extended) ──
    {"Software as a Service", "SaaS"},
    {"Platform as a Service", "PaaS"},
    {"Infrastructure as a Service", "IaaS"},
    {"Single Sign On", "SSO"},
    {"Software Development Kit", "SDK"},
    {"Application Performance Monitoring", "APM"},
    {"Content Delivery Network", "CDN"},
    {"Mean Time to Recovery", "MTTR"},
    {"Mean Time Between Failures", "MTBF"},
    {"Service Oriented Architecture", "SOA"},
    {"Microservices Architecture", "MSA"},

    # ── Construction & Trades ──
    {"Construction Skills Certification Scheme", "CSCS"},
    {"Site Management Safety Training Scheme", "SMSTS"},
    {"Site Supervisor Safety Training Scheme", "SSSTS"},
    {"National Examination Board in Occupational Safety and Health", "NEBOSH"},
    {"Institution of Occupational Safety and Health", "IOSH"},
    {"Construction Design and Management", "CDM"},
    {"Quantity Surveyor", "QS"},
    {"Mechanical Electrical and Plumbing", "MEP"},
    {"New Rules of Measurement", "NRM"},
    {"Joint Contracts Tribunal", "JCT"},
    {"National Engineering Construction", "NEC"},
    {"Temporary Works", "TW"},

    # ── Logistics & Warehousing ──
    {"Warehouse Management System", "WMS"},
    {"Third Party Logistics", "3PL"},
    {"Electronic Point of Sale", "EPOS"},
    {"Stock Keeping Unit", "SKU"},
    {"Certificate of Professional Competence", "CPC"},
    {"Forklift Truck", "FLT"},
    {"Transport Management System", "TMS"},
    {"Goods In", "Goods Inward"},
    {"Pick Pack and Dispatch", "PPD"},

    # ── Real Estate & Property ──
    {"Royal Institution of Chartered Surveyors", "RICS"},
    {"Energy Performance Certificate", "EPC"},
    {"Association of Residential Letting Agents", "ARLA"},
    {"National Association of Estate Agents", "NAEA"},

    # ── Insurance & Actuarial ──
    {"Professional Indemnity", "PI Insurance"},
    {"Chartered Insurance Institute", "CII"},
    {"Institute and Faculty of Actuaries", "IFoA"},
    {"Solvency Capital Requirement", "SCR"},

    # ── Social Work & Care ──
    {"Disclosure and Barring Service", "DBS"},
    {"Looked After Children", "LAC"},
    {"Education Health and Care Plan", "EHCP"},
    {"Mental Capacity Act", "MCA"},
    {"Deprivation of Liberty Safeguards", "DoLS"},
    {"Social Work England", "SWE"},
    {"Person Centred Care", "PCC"},
    {"Activities of Daily Living", "ADL"},

    # ── Science & Research ──
    {"Good Laboratory Practice", "GLP"},
    {"High Performance Liquid Chromatography", "HPLC"},
    {"Gas Chromatography", "GC"},
    {"Mass Spectrometry", "MS"},
    {"Polymerase Chain Reaction", "PCR"},
    {"Good Clinical Practice", "GCP Pharma"},
    {"Medicines and Healthcare products Regulatory Agency", "MHRA"},
    {"Food and Drug Administration", "FDA"},
    {"European Medicines Agency", "EMA"},
    {"Quality by Design", "QbD"},

    # ── Environmental & Sustainability ──
    {"Environmental Social and Governance", "ESG"},
    {"Corporate Social Responsibility", "CSR"},
    {"Environmental Impact Assessment", "EIA"},
    {"Leadership in Energy and Environmental Design", "LEED"},
    {"Building Research Establishment Environmental Assessment Method", "BREEAM"},
    {"Carbon Dioxide Equivalent", "CO2e"},
    {"Waste Electrical and Electronic Equipment", "WEEE"},
    {"Environmental Management System", "EMS"},
    {"Net Zero Carbon", "Net Zero"},

    # ── Public Sector & Government ──
    {"Freedom of Information", "FOI"},
    {"Data Protection Officer", "DPO"},
    {"Her Majesty's Revenue and Customs", "HMRC"},
    {"Ministry of Defence", "MoD"},
    {"Public Sector Bodies Accessibility Regulations", "PSBAR"},

    # ── Procurement ──
    {"Chartered Institute of Procurement and Supply", "CIPS"},
    {"Request for Proposal", "RFP"},
    {"Request for Quotation", "RFQ"},
    {"Invitation to Tender", "ITT"},
    {"Purchase Order", "PO"},
    {"Vendor Management", "VM"},

    # ── Quality & Standards ──
    {"Quality Management System", "QMS"},
    {"Corrective and Preventive Action", "CAPA"},
    {"Standard Operating Procedure", "SOP"},
    {"Failure Mode and Effects Analysis", "FMEA"},
    {"Statistical Process Control", "SPC"},
    {"Non Conformance Report", "NCR"},

    # ── Hospitality & Food Safety ──
    {"Food and Beverage", "F&B"},
    {"Hazard Analysis Critical Control Points", "HACCP"},
    {"Food Safety Management System", "FSMS"},
    {"Revenue Per Available Room", "RevPAR"},

    # ── Telecommunications ──
    {"Voice over Internet Protocol", "VoIP"},
    {"Session Initiation Protocol", "SIP"},
    {"Internet of Things", "IoT"},
    {"Radio Frequency", "RF"},
    {"Local Area Network", "LAN"},
    {"Wide Area Network", "WAN"},
    {"Virtual Private Network", "VPN"},
    {"Software Defined Networking", "SDN"},

    # ── Media & Creative ──
    {"Adobe Creative Suite", "Creative Suite", "Adobe CC"},
    {"Digital Asset Management", "DAM"},
    {"User Generated Content", "UGC"},
    {"Search Engine Results Page", "SERP"},

    # ── General Business & Executive ──
    {"Chief Executive Officer", "CEO"},
    {"Chief Financial Officer", "CFO"},
    {"Chief Technology Officer", "CTO"},
    {"Chief Operating Officer", "COO"},
    {"Chief Information Officer", "CIO"},
    {"Chief Information Security Officer", "CISO"},
    {"Vice President", "VP"},
    {"Managing Director", "MD"},
    {"Full Time Equivalent", "FTE"},
    {"Year on Year", "YoY"},
    {"Month on Month", "MoM"},
    {"Objective and Key Results", "OKR", "OKRs"},
    {"Annual Recurring Revenue", "ARR"},
    {"Monthly Recurring Revenue", "MRR"},
    {"Total Addressable Market", "TAM"},
    {"Subject Matter Expert", "SME"},
    {"Business to Business", "B2B"},
    {"Business to Consumer", "B2C"},

    # ── Healthcare (extended ESCO) ──
    {"Clinical Commissioning Group", "CCG"},
    {"Integrated Care System", "ICS"},
    {"Integrated Care Board", "ICB"},
    {"Primary Care Network", "PCN"},
    {"Mental Health Act", "MHA"},
    {"Nursing and Midwifery Council", "NMC"},
    {"General Medical Council", "GMC"},
    {"Health Care Professions Council", "HCPC"},
    {"Medicines Management", "MM"},
    {"Clinical Governance", "CG"},
    {"Infection Prevention and Control", "IPC"},
    {"Personal Protective Equipment", "PPE"},

    # ── Finance (extended ESCO) ──
    {"Basel III", "Basel 3"},
    {"Capital Adequacy", "CAR"},
    {"Value at Risk", "VaR"},
    {"Prudential Regulation Authority", "PRA"},
    {"Payment Card Industry Data Security Standard", "PCI DSS"},
    {"Straight Through Processing", "STP"},
    {"Over the Counter", "OTC"},
    {"Fixed Income", "FI"},
    {"Foreign Exchange", "FX", "Forex"},
    {"Venture Capital", "VC"},
    {"Private Equity", "PE"},

    # ── Legal (extended ESCO) ──
    {"Bar Standards Board", "BSB"},
    {"Alternative Dispute Resolution", "ADR"},
    {"Terms and Conditions", "T&C", "T&Cs"},

    # ── Education (extended ESCO) ──
    {"Early Years Foundation Stage", "EYFS"},
    {"Key Stage", "KS"},
    {"National Curriculum", "NC"},
    {"Office for Standards in Education", "Ofsted"},
    {"Designated Safeguarding Lead", "DSL"},

    # ── Engineering (extended ESCO) ──
    {"Finite Element Analysis", "FEA"},
    {"Computational Fluid Dynamics", "CFD"},
    {"Computer Numerical Control", "CNC"},
    {"Piping and Instrumentation Diagram", "P&ID"},
    {"Hazard and Operability Study", "HAZOP"},
    {"Safety Integrity Level", "SIL"},
    {"Failure Modes Effects and Criticality Analysis", "FMECA"},
    {"Design for Manufacture", "DFM"},
    {"Design for Assembly", "DFA"},
]


def _build_synonym_lookup() -> dict[str, set[str]]:
    """Build bidirectional synonym lookup: term.lower() -> set of all synonyms."""
    lookup: dict[str, set[str]] = {}
    for group in SYNONYM_GROUPS:
        lower_group = {term.lower() for term in group}
        for term in group:
            key = term.lower()
            if key not in lookup:
                lookup[key] = set()
            lookup[key].update(lower_group)
            lookup[key].discard(key)  # don't include self
    return lookup


SKILL_SYNONYMS = _build_synonym_lookup()


def get_synonyms(term: str) -> set[str]:
    """Return all known synonyms for a term (lowercased)."""
    return SKILL_SYNONYMS.get(term.lower(), set())


@lru_cache(maxsize=512)
def _synonym_pattern(term: str) -> re.Pattern:
    """Build a compiled regex that matches term OR any of its synonyms with word boundaries."""
    terms = [term]
    synonyms = get_synonyms(term)
    terms.extend(synonyms)
    # Sort by length descending so longer matches are tried first
    terms.sort(key=len, reverse=True)
    escaped = [re.escape(t) for t in terms]
    pattern = r'\b(?:' + '|'.join(escaped) + r')(?:\b|(?=[^a-zA-Z0-9]))'
    return re.compile(pattern, re.IGNORECASE)


def text_contains_with_synonyms(text: str, term: str) -> bool:
    """Check if term or any of its synonyms appear as whole words in text."""
    return bool(_synonym_pattern(term).search(text))

"""Keyword configuration for Job360.

This module contains:
- UK_LOCATIONS: UK locations for scoring (domain-agnostic)
- VISA_KEYWORDS: Visa-related terms (domain-agnostic)
- KNOWN_SKILLS: Multi-domain skill database for CV parsing
- KNOWN_TITLE_PATTERNS: Multi-domain job titles for CV parsing
- KNOWN_LOCATIONS: Comprehensive location database for CV parsing

No hard-coded domain-specific keywords exist here. All job-specific
keywords (titles, skills, relevance terms, negative keywords) come
from the user's profile via SearchConfig.
"""

# ---------------------------------------------------------------------------
# UK locations for scoring — domain-agnostic, applies to all professions
# ---------------------------------------------------------------------------

LOCATIONS = [
    "UK",
    "United Kingdom",
    "London",
    "Greater London",
    "City of London",
    "Cambridge",
    "Manchester",
    "Edinburgh",
    "Birmingham",
    "Bristol",
    "Hertfordshire",
    "Hatfield",
    "Leeds",
    "Glasgow",
    "Belfast",
    "Oxford",
    "Reading",
    "Southampton",
    "Nottingham",
    "Sheffield",
    "Liverpool",
    "England",
    "Scotland",
    "Wales",
    "Remote",
    "Hybrid",
]

# ---------------------------------------------------------------------------
# Visa-related keywords — domain-agnostic, applies to all professions
# ---------------------------------------------------------------------------

VISA_KEYWORDS = [
    "visa sponsorship",
    "sponsorship",
    "right to work",
    "work permit",
    "visa",
    "sponsored",
    "tier 2",
    "skilled worker visa",
]

# ---------------------------------------------------------------------------
# Comprehensive multi-domain skill database
# Used by cv_parser to recognise skills from ANY CV
# ---------------------------------------------------------------------------

KNOWN_SKILLS = {
    # --- Programming Languages ---
    "Python", "Java", "JavaScript", "TypeScript", "C", "C++", "C#",
    "Go", "Golang", "Rust", "Ruby", "PHP", "Swift", "Kotlin", "Scala",
    "R", "MATLAB", "Perl", "Haskell", "Lua", "Dart", "Elixir",
    "Clojure", "Groovy", "Objective-C", "Assembly", "COBOL", "Fortran",
    "Visual Basic", "VBA", "Shell", "Bash", "PowerShell", "SQL", "PL/SQL",
    "T-SQL", "Solidity",

    # --- Web Frontend ---
    "React", "React.js", "Angular", "Vue", "Vue.js", "Svelte",
    "Next.js", "Nuxt.js", "Gatsby", "HTML", "CSS", "SCSS", "Sass",
    "Less", "Tailwind CSS", "Bootstrap", "Material UI", "jQuery",
    "Webpack", "Vite", "Babel", "Redux", "MobX", "Zustand",
    "Styled Components", "Storybook", "Three.js", "D3.js",
    "WebGL", "WebAssembly", "HTMX",

    # --- Web Backend ---
    "Node.js", "Express", "Express.js", "Nest.js", "NestJS", "Deno",
    "Django", "Flask", "FastAPI", "Spring", "Spring Boot",
    "ASP.NET", ".NET", ".NET Core", "Rails", "Ruby on Rails",
    "Laravel", "Symfony", "Gin", "Echo", "Fiber",
    "Phoenix", "Actix", "Rocket",

    # --- Mobile ---
    "React Native", "Flutter", "SwiftUI", "Jetpack Compose",
    "Xamarin", "Ionic", "Capacitor", "Cordova",
    "Android", "iOS", "Mobile Development",

    # --- Databases ---
    "MySQL", "PostgreSQL", "MongoDB", "SQLite", "Oracle",
    "SQL Server", "MariaDB", "Cassandra", "CouchDB", "DynamoDB",
    "Firebase", "Firestore", "Supabase", "Neo4j", "InfluxDB",
    "TimescaleDB", "CockroachDB", "Redis", "Memcached",
    "Elasticsearch", "OpenSearch", "pgvector",

    # --- Cloud & DevOps ---
    "AWS", "Azure", "Google Cloud", "GCP", "DigitalOcean", "Heroku",
    "Vercel", "Netlify", "Cloudflare",
    "Docker", "Kubernetes", "Terraform", "Ansible", "Puppet", "Chef",
    "Vagrant", "Packer", "Helm",
    "Jenkins", "GitHub Actions", "GitLab CI", "CircleCI", "Travis CI",
    "ArgoCD", "Flux",
    "S3", "EC2", "Lambda", "ECS", "EKS", "SageMaker", "Bedrock",
    "CloudWatch", "CloudFormation", "CDK",
    "Nginx", "Apache", "HAProxy", "Traefik",
    "Prometheus", "Grafana", "Datadog", "New Relic", "Splunk",
    "PagerDuty", "ELK Stack", "Logstash", "Kibana",

    # --- AI / ML / Data Science ---
    "PyTorch", "TensorFlow", "Keras", "Scikit-learn",
    "LangChain", "LlamaIndex", "RAG", "LLM",
    "Generative AI", "GenAI", "Hugging Face", "Transformers",
    "OpenAI", "NLP", "Deep Learning", "Neural Networks",
    "Computer Vision", "Prompt Engineering", "Agentic AI",
    "Machine Learning", "Reinforcement Learning",
    "FAISS", "ChromaDB", "Pinecone", "Weaviate", "Milvus",
    "MLflow", "Kubeflow", "Weights & Biases", "W&B",
    "Feature Engineering", "Data Pipelines", "ETL",
    "Pandas", "NumPy", "SciPy", "Matplotlib", "Seaborn",
    "Plotly", "Jupyter", "Spark", "PySpark", "Hadoop",
    "Airflow", "Dagster", "Prefect", "dbt",
    "Tableau", "Power BI", "Looker", "Metabase",
    "SPSS", "SAS", "Stata",
    "LLM fine-tuning", "Fine-tuning", "RLHF", "LoRA", "QLoRA",
    "Gemini",

    # --- Data Engineering ---
    "Kafka", "RabbitMQ", "Apache Pulsar", "Apache Flink",
    "Apache Beam", "Snowflake", "BigQuery", "Redshift",
    "Databricks", "Delta Lake", "Apache Iceberg",
    "dbt", "Great Expectations", "Apache NiFi",

    # --- Testing ---
    "Selenium", "Cypress", "Playwright", "Puppeteer",
    "Jest", "Mocha", "Chai", "Jasmine", "Vitest",
    "Pytest", "Unittest", "JUnit", "TestNG", "xUnit",
    "Postman", "SoapUI", "JMeter", "k6", "Locust",
    "Cucumber", "Robot Framework",

    # --- Security ---
    "OWASP", "Penetration Testing", "Burp Suite", "Nmap",
    "Wireshark", "Metasploit", "SIEM", "SOC",
    "Firewall", "VPN", "IAM", "OAuth", "SAML", "JWT",
    "SSL/TLS", "Encryption", "PKI", "HashiCorp Vault",

    # --- Version Control ---
    "Git", "GitHub", "GitLab", "Bitbucket", "SVN",
    "Mercurial",

    # --- Project & Collaboration ---
    "Jira", "Confluence", "Trello", "Asana",
    "Slack", "Microsoft Teams", "Notion",
    "Figma", "Sketch", "Adobe XD", "InVision",

    # --- Methodologies ---
    "Agile", "Scrum", "Kanban", "DevOps", "SRE",
    "CI/CD", "TDD", "BDD", "DDD",
    "Microservices", "REST", "RESTful", "GraphQL", "gRPC",
    "Event-Driven Architecture", "CQRS", "Serverless",
    "SOA", "API Design",

    # --- Operating Systems ---
    "Linux", "Ubuntu", "CentOS", "RHEL", "Debian",
    "Windows Server", "macOS",

    # --- Other Tools ---
    "n8n", "Zapier", "Make",
    "RPA", "UiPath", "Automation Anywhere",
    "SAP", "Salesforce", "ServiceNow",
    "Twilio", "SendGrid", "Stripe",
    "WordPress", "Shopify", "Magento",

    # --- Networking ---
    "TCP/IP", "DNS", "HTTP", "HTTPS", "WebSocket",
    "Load Balancing", "CDN", "BGP", "VLAN",

    # --- Finance / Accounting (non-tech) ---
    "Excel", "Financial Modelling", "Bloomberg",
    "QuickBooks", "Xero", "Sage",

    # --- Design ---
    "Photoshop", "Illustrator", "After Effects",
    "Premiere Pro", "Blender", "AutoCAD",
    "SolidWorks", "CATIA",

    # --- Embedded / IoT ---
    "Arduino", "Raspberry Pi", "FPGA", "VHDL", "Verilog",
    "RTOS", "Embedded C", "ARM", "STM32",
    "MQTT", "Zigbee", "LoRaWAN",

    # --- Blockchain ---
    "Ethereum", "Solana", "Web3", "Smart Contracts",
    "Hardhat", "Truffle", "IPFS",

    # --- Game Development ---
    "Unity", "Unreal Engine", "Godot",
    "OpenGL", "DirectX", "Vulkan",
}

# ---------------------------------------------------------------------------
# Comprehensive job title patterns (for open-ended CV extraction)
# ---------------------------------------------------------------------------

KNOWN_TITLE_PATTERNS = [
    # Engineering
    "Software Engineer", "Software Developer", "Full Stack Developer",
    "Full-Stack Developer", "Frontend Developer", "Front-End Developer",
    "Backend Developer", "Back-End Developer", "Web Developer",
    "Mobile Developer", "iOS Developer", "Android Developer",
    "DevOps Engineer", "Site Reliability Engineer", "SRE",
    "Platform Engineer", "Infrastructure Engineer",
    "Cloud Engineer", "Cloud Architect", "Solutions Architect",
    "Systems Engineer", "Network Engineer",
    "Security Engineer", "Cybersecurity Engineer", "Penetration Tester",
    "Embedded Engineer", "Firmware Engineer", "Hardware Engineer",
    "QA Engineer", "Test Engineer", "SDET",
    "Release Engineer", "Build Engineer",
    # AI/ML/Data
    "AI Engineer", "ML Engineer", "Machine Learning Engineer",
    "GenAI Engineer", "Generative AI Engineer", "LLM Engineer",
    "NLP Engineer", "Data Scientist", "MLOps Engineer",
    "AI/ML Engineer", "Deep Learning Engineer",
    "Computer Vision Engineer", "RAG Engineer",
    "AI Solutions Engineer", "AI Research Engineer",
    "Applied ML Engineer", "Python AI Developer",
    "Data Engineer", "Data Analyst", "Business Analyst",
    "Analytics Engineer", "BI Developer", "BI Analyst",
    "Database Administrator", "DBA",
    # Management
    "Engineering Manager", "Technical Lead", "Tech Lead",
    "Team Lead", "CTO", "VP of Engineering",
    "Product Manager", "Program Manager", "Project Manager",
    "Scrum Master", "Delivery Manager",
    # Design
    "UX Designer", "UI Designer", "UX/UI Designer",
    "Product Designer", "Graphic Designer", "Visual Designer",
    "UX Researcher", "Interaction Designer",
    # Other tech
    "Technical Writer", "Developer Advocate",
    "Developer Relations", "Support Engineer",
    "IT Administrator", "System Administrator",
    # Non-tech
    "Accountant", "Financial Analyst", "Marketing Manager",
    "Sales Manager", "HR Manager", "Operations Manager",
    "Consultant", "Business Consultant", "Management Consultant",
    "Teacher", "Lecturer", "Professor", "Researcher",
    "Nurse", "Doctor", "Pharmacist",
    "Lawyer", "Solicitor", "Paralegal",
    "Civil Engineer", "Mechanical Engineer", "Electrical Engineer",
    "Chemical Engineer", "Structural Engineer",
]

# ---------------------------------------------------------------------------
# Comprehensive location database (cities, countries, regions)
# ---------------------------------------------------------------------------

KNOWN_LOCATIONS = {
    # UK
    "UK", "United Kingdom", "England", "Scotland", "Wales", "Northern Ireland",
    "London", "Manchester", "Birmingham", "Leeds", "Glasgow", "Edinburgh",
    "Liverpool", "Bristol", "Sheffield", "Newcastle", "Nottingham", "Leicester",
    "Coventry", "Belfast", "Cardiff", "Brighton", "Southampton", "Oxford",
    "Cambridge", "Reading", "Milton Keynes", "Hertfordshire", "Hatfield",
    "Swindon", "York", "Bath", "Exeter", "Norwich", "Aberdeen",
    # Work modes
    "Remote", "Hybrid", "On-site", "Onsite",
    "Work from Home", "WFH", "Fully Remote",
}

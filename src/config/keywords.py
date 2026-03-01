JOB_TITLES = [
    "AI Engineer",
    "ML Engineer",
    "Machine Learning Engineer",
    "GenAI Engineer",
    "Generative AI Engineer",
    "LLM Engineer",
    "NLP Engineer",
    "Data Scientist",
    "MLOps Engineer",
    "AI/ML Engineer",
    "Deep Learning Engineer",
    "Computer Vision Engineer",
    "RAG Engineer",
    "AI Solutions Engineer",
    "AI Research Engineer",
    "Applied ML Engineer",
    "Python AI Developer",
    "AI Researcher",
    "ML Scientist",
    "Machine Learning Scientist",
    "AI Platform Engineer",
    "AI Infrastructure Engineer",
    "Conversational AI Engineer",
    "Applied Scientist",
    "Research Scientist",
]

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

# Skills with weights: primary=3, secondary=2, tertiary=1
PRIMARY_SKILLS = [
    "Python",
    "PyTorch",
    "TensorFlow",
    "LangChain",
    "RAG",
    "LLM",
    "Generative AI",
    "Hugging Face",
    "Transformers",
    "OpenAI",
    "NLP",
    "Deep Learning",
    "Neural Networks",
    "Computer Vision",
    "Prompt Engineering",
]

SECONDARY_SKILLS = [
    "Scikit-learn",
    "Keras",
    "AWS",
    "SageMaker",
    "Bedrock",
    "Docker",
    "Kubernetes",
    "FastAPI",
    "ChromaDB",
    "FAISS",
    "OpenSearch",
    "Redis",
    "pgvector",
    "Gemini",
    "Agentic AI",
    "LLM fine-tuning",
    "Fine-tuning",
]

TERTIARY_SKILLS = [
    "CI/CD",
    "MLflow",
    "Git",
    "Linux",
    "n8n",
    "Data Pipelines",
    "ETL",
    "Feature Engineering",
    "S3",
    "CloudWatch",
    "Machine Learning",
]

# Visa-related keywords to flag
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

# Keywords to filter relevance (job must contain at least one)
RELEVANCE_KEYWORDS = [
    "ai", "ml", "machine learning", "deep learning", "nlp",
    "natural language", "computer vision", "data scien",
    "generative", "llm", "large language", "neural",
    "pytorch", "tensorflow", "python", "rag", "langchain",
    "transformers", "hugging face",
    "mlops", "genai", "openai", "anthropic", "bedrock", "sagemaker",
    "prompt engineer", "agentic", "vector database", "embeddings",
    "fine-tun", "chatgpt", "gpt-4", "claude", "gemini", "diffusion",
    "reinforcement learning",
]

NEGATIVE_TITLE_KEYWORDS = [
    # Original entries
    "sales engineer", "account manager", "marketing", "recruiter",
    "accountant", "hr manager", "graphic designer", "copywriter",
    "customer support", "civil engineer", "mechanical engineer",
    "nurse", "pharmacist", "teaching assistant", "lecturer",
    # Sales / business
    "sales specialist", "sales representative", "business development",
    "account executive",
    # IT ops
    "site reliability", "sre ", "support desk", "help desk",
    "service desk", "network engineer", "systems administrator",
    "desktop support", "it support",
    # Unrelated research
    "quantum", "virology", "bioinformatics", "genomics",
    # Creative
    "model artist", "3d artist", "3d modeler", "animator",
    # Enterprise platforms
    "power platform", "dynamics 365", "sharepoint", "sap ",
    "oracle erp", "salesforce admin",
    # Legacy
    "mainframe", "cobol", "rpg developer",
    # Hardware
    "embedded firmware", "hvac",
    # Non-tech engineering
    "electrical engineer", "chemical engineer",
    # Healthcare
    "doctor", "physician", "dentist",
    # Legal
    "legal counsel", "solicitor", "paralegal", "barrister",
    # Finance
    "auditor", "tax ", "bookkeeper",
    # Education
    "teacher",
]

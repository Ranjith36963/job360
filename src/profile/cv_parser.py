"""PDF/DOCX text extraction and CV section parsing."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from src.profile.models import CVData

logger = logging.getLogger("job360.profile.cv_parser")

# Section header patterns (case-insensitive)
_SECTION_PATTERNS = {
    "skills": re.compile(
        r'^(?:(?:core|technical|key|professional)?\s*skills|competencies|technologies|tools)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "experience": re.compile(
        r'^(?:(?:professional\s+|work\s+)?experience|employment|career\s+history)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "education": re.compile(
        r'^(?:education|qualifications|academic|degrees)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "certifications": re.compile(
        r'^(?:licenses?\s*(?:&|and|,)\s*certifications?|certifications?\s*(?:&|and|,)\s*licenses?|certifications?|certificates?|accreditations?|licenses?)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    "summary": re.compile(
        r'^(?:(?:professional\s+|executive\s+|career\s+)?summary|profile|objective|about\s+me|personal\s+statement|overview)\s*[:\-]?\s*$',
        re.IGNORECASE | re.MULTILINE,
    ),
}

# Pattern to detect "Title at Company" or "Company | Date" in experience sections
_TITLE_AT_COMPANY = re.compile(
    r'^(.+?)\s+(?:at|@|-|–|,)\s+(.+?)$',
    re.MULTILINE,
)

# Pattern for "Company | Date | Location" style headers
_COMPANY_DATE_LINE = re.compile(
    r'^(.+?)\s*\|\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{4}',
    re.IGNORECASE | re.MULTILINE,
)

# Pattern for standalone role titles (e.g., "AI Solutions Engineer – R&D Department")
_ROLE_TITLE_LINE = re.compile(
    r'^([A-Z][A-Za-z\s/]+(?:Engineer|Developer|Scientist|Analyst|Manager|Intern|Lead|Architect|Consultant|Designer|Specialist)(?:\s*[–\-]\s*.+)?)\s*$',
    re.MULTILINE,
)

# Primary delimiters — bullet points that separate skill entries
_BULLET_DELIMITERS = re.compile(r'[•·▪\ufffd]')

# Category header pattern: "Category Name:" at the start of a bullet entry
_CATEGORY_HEADER = re.compile(r'^[A-Za-z/&\s]+:\s*')

# Valid single-character programming language names
SINGLE_CHAR_SKILLS = {"R", "C"}

# Pattern for capitalized tool/technology names (fallback extraction)
_TECH_NAME = re.compile(r'\b[A-Z][a-zA-Z0-9+#.]*(?:\s+[A-Z][a-zA-Z0-9+#.]*){0,2}\b')


def extract_text_from_pdf(file_path: str) -> str:
    """Extract text from a PDF file using pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed. Run: pip install pdfplumber")
        return ""

    text_parts = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
    except Exception as e:
        logger.error("Failed to read PDF %s: %s", file_path, e)
        return ""
    return "\n".join(text_parts)


def extract_text_from_docx(file_path: str) -> str:
    """Extract text from a DOCX file using python-docx."""
    try:
        import docx
    except ImportError:
        logger.error("python-docx not installed. Run: pip install python-docx")
        return ""

    try:
        doc = docx.Document(file_path)
        return "\n".join(para.text for para in doc.paragraphs if para.text.strip())
    except Exception as e:
        logger.error("Failed to read DOCX %s: %s", file_path, e)
        return ""


def extract_text(file_path: str) -> str:
    """Extract text from PDF or DOCX based on file extension."""
    path = Path(file_path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext == ".docx":
        return extract_text_from_docx(file_path)
    elif ext == ".doc":
        logger.warning("Legacy .doc format not supported. Please convert to .docx: %s", file_path)
        return ""
    else:
        logger.warning("Unsupported file type: %s", ext)
        return ""


def _find_sections(text: str) -> dict[str, str]:
    """Split CV text into named sections."""
    # Find all section headers and their positions
    matches = []
    for section_name, pattern in _SECTION_PATTERNS.items():
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), section_name))

    if not matches:
        return {"full_text": text}

    matches.sort(key=lambda x: x[0])
    sections = {}
    for i, (start, end, name) in enumerate(matches):
        next_start = matches[i + 1][0] if i + 1 < len(matches) else len(text)
        sections[name] = text[end:next_start].strip()

    sections["full_text"] = text
    return sections


def _extract_skills_from_text(text: str) -> list[str]:
    """Extract skills from a skills section.

    Handles: PDF line-wrapping, category headers (e.g., "Cloud & MLOps:"),
    parenthetical groups (e.g., "AWS (Bedrock, SageMaker, S3)"), and
    bullet-delimited skill lists.
    """
    has_bullets = bool(_BULLET_DELIMITERS.search(text))

    if has_bullets:
        # Bullet-delimited CV (most common) — rejoin broken lines, split on bullets
        joined = re.sub(r'(?<![:\n])\n(?!\s*[•·▪\ufffd])', ' ', text)
        entries = _BULLET_DELIMITERS.split(joined)
    elif ';' in text:
        # Semicolon-delimited list
        entries = text.split(';')
    else:
        # Newline-delimited or comma-delimited list
        entries = text.split('\n')

    skills = []
    for entry in entries:
        entry = entry.strip().strip("-•·▪\ufffd ")
        if not entry:
            continue

        # Step 3: Strip category headers like "AI/ML & GenAI Systems: "
        entry = _CATEGORY_HEADER.sub('', entry).strip()
        if not entry:
            continue

        # Step 4: If the entry still contains internal bullet-like separators
        # (some CVs use "Skill1 • Skill2 • Skill3" inline), those were already
        # split above. But if it has parenthetical content, keep it together.
        # Only split on bare commas if there are NO parentheses.
        if '(' not in entry:
            # Safe to split on commas — no parenthetical groups
            sub_items = [s.strip() for s in entry.split(',') if s.strip()]
        else:
            # Has parentheses — keep the whole entry as-is, or split carefully
            # Protect parenthetical groups, then split remaining commas
            sub_items = _split_preserving_parens(entry)

        for item in sub_items:
            cleaned = item.strip().strip("-•·▪\ufffd ")
            if cleaned and (len(cleaned) > 1 or cleaned in SINGLE_CHAR_SKILLS) and len(cleaned) < 80:
                skills.append(cleaned)

    return skills


def _split_preserving_parens(text: str) -> list[str]:
    """Split on commas but keep parenthetical groups together.

    E.g., "AWS (Bedrock, SageMaker, S3), Docker, Python (Pandas, NumPy)"
    → ["AWS (Bedrock, SageMaker, S3)", "Docker", "Python (Pandas, NumPy)"]
    """
    results = []
    depth = 0
    current = []
    for char in text:
        if char == '(':
            depth += 1
            current.append(char)
        elif char == ')':
            depth = max(0, depth - 1)
            current.append(char)
        elif char == ',' and depth == 0:
            part = ''.join(current).strip()
            if part:
                results.append(part)
            current = []
        else:
            current.append(char)
    part = ''.join(current).strip()
    if part:
        results.append(part)
    return results


def _extract_titles_from_experience(text: str) -> list[str]:
    """Extract company names and job titles from experience section text.

    Returns entries like "Company | Date" and "Role Title" for display.
    """
    entries = []
    seen = set()

    # Method 1: "Company | Date | Location" header lines
    for match in _COMPANY_DATE_LINE.finditer(text):
        line = match.group(0).strip()
        if line.lower() not in seen:
            entries.append(line)
            seen.add(line.lower())

    # Method 2: Standalone role title lines
    for match in _ROLE_TITLE_LINE.finditer(text):
        title = match.group(1).strip()
        if title.lower() not in seen:
            entries.append(title)
            seen.add(title.lower())

    # Method 3: "Title at/- Company" pattern → extract the title part
    for match in _TITLE_AT_COMPANY.finditer(text):
        title = match.group(1).strip()
        if 3 < len(title) < 80 and title.lower() not in seen:
            entries.append(title)
            seen.add(title.lower())

    return entries


def _extract_tech_names(text: str) -> list[str]:
    """Fallback: extract capitalized technology names from full text."""
    matches = _TECH_NAME.findall(text)
    # Filter out common non-tech words
    noise = {
        "The", "This", "That", "With", "From", "Have", "Been", "Will",
        "About", "Summary", "Education", "Experience", "Skills",
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
        "University", "College", "School", "London", "Manchester",
    }
    return [m for m in matches if m not in noise and len(m) > 1]


def _extract_deep_skills(full_text: str, existing_skills: list[str]) -> list[str]:
    """Deep-scan the ENTIRE CV for professional terms a recruiter would notice.

    Goes beyond the CORE SKILLS section to find:
    - Technology + context phrases (AWS Bedrock, Docker deployment)
    - Achievement metrics (95% accuracy, reducing latency by 35%)
    - Domain expertise phrases (fraud detection models, predictive maintenance)
    - Methodology terms (multi-agent workflow, hyperparameter optimisation)
    - Compound tool/platform names (OpenAI API, Gemini API)
    """
    extra = []
    existing_lower = {s.lower() for s in existing_skills}
    existing_text = " ".join(existing_skills).lower()

    # --- Pattern 1: Specific technology compound phrases ---
    # Only match "TechPrefix + Noun" patterns that are real professional terms
    tech_compounds = re.compile(
        r'\b((?:AWS|Docker|Redis|Cloud|RAG|LLM|ML|AI|Data|CI/CD|Vector|Neural|'
        r'Deep|Machine|Transfer|Reinforcement|Computer|Speech|Audio|Video|'
        r'Multi-agent|Hyperparameter|Feature|Model|NLP|Fraud|Predictive|Traffic)'
        r'\s+[A-Za-z]+(?:\s+[A-Za-z]+)?)\b',
        re.IGNORECASE,
    )
    # Filter: only keep if the phrase ends with a meaningful noun
    meaningful_suffixes = {
        'pipeline', 'pipelines', 'deployment', 'infrastructure', 'engineering',
        'architecture', 'automation', 'detection', 'prediction', 'recognition',
        'processing', 'generation', 'optimisation', 'optimization', 'caching',
        'monitoring', 'versioning', 'learning', 'networks', 'vision',
        'models', 'model', 'systems', 'system', 'solutions', 'assistant',
        'workflow', 'workflows', 'agents', 'maintenance', 'scheduling',
        'analysis', 'understanding', 'embedding', 'embeddings',
        'specialist', 'mlops', 'bedrock', 'sagemaker', 'cloudwatch',
    }

    for match in tech_compounds.finditer(full_text):
        phrase = match.group(1).strip()
        last_word = phrase.split()[-1].lower()
        if last_word in meaningful_suffixes and phrase.lower() not in existing_lower:
            if phrase.lower() not in existing_text and len(phrase) < 50:
                extra.append(phrase)
                existing_lower.add(phrase.lower())

    # --- Pattern 2: Achievement metrics ---
    # "achieving 90% accuracy", "reducing query latency by 35%"
    achievement_pattern = re.compile(
        r'(?:achieving|delivered|improved|reducing|accelerated|cutting|enhancing|increasing)\s+'
        r'[^.]{5,80}?\d+%[^.]{0,40}',
        re.IGNORECASE,
    )
    for match in achievement_pattern.finditer(full_text):
        phrase = match.group(0).strip().rstrip('.')
        if phrase.lower() not in existing_lower and len(phrase) < 120:
            extra.append(phrase)
            existing_lower.add(phrase.lower())

    # --- Pattern 3: Domain expertise phrases (noun + professional noun) ---
    # "fraud detection", "predictive maintenance", "traffic prediction"
    # Only match adjective/noun + domain-noun patterns — no verbs as prefix
    domain_phrases = re.compile(
        r'\b([A-Za-z]+\s+(?:detection|prediction|recognition|processing|generation|'
        r'optimisation|optimization|deployment|monitoring|engineering|'
        r'infrastructure|automation|management|analysis|understanding|'
        r'classification|maintenance|scheduling|caching|pipelines?|'
        r'workflows?|intelligence))\b',
        re.IGNORECASE,
    )
    # Words that should NOT start a domain phrase (verbs, articles, etc.)
    bad_prefixes = {
        'the', 'a', 'an', 'this', 'that', 'my', 'our', 'their', 'its',
        'and', 'or', 'for', 'with', 'from', 'into', 'by', 'to',
        'evaluated', 'streamline', 'optimise', 'optimize', 'improve',
        'built', 'designed', 'developed', 'integrated', 'collected',
        'conducted', 'ensuring', 'enabling', 'performing', 'achieving',
    }
    for match in domain_phrases.finditer(full_text):
        phrase = match.group(1).strip()
        first_word = phrase.split()[0].lower()
        pl = phrase.lower()
        if first_word in bad_prefixes:
            continue
        if pl not in existing_lower and pl not in existing_text and len(phrase) > 8:
            extra.append(phrase)
            existing_lower.add(pl)

    # --- Pattern 4: Headline role from CV header ---
    # First 5 lines often contain the person's stated role
    header_lines = full_text.split('\n')[:5]
    role_keywords = [
        'engineer', 'scientist', 'developer', 'analyst', 'architect',
        'specialist', 'consultant', 'manager', 'lead', 'director',
        'researcher', 'designer', 'intern',
    ]
    for line in header_lines:
        line_stripped = line.strip()
        if any(kw in line_stripped.lower() for kw in role_keywords):
            if line_stripped.lower() not in existing_lower:
                extra.append(line_stripped)
                existing_lower.add(line_stripped.lower())

    return extra


def parse_cv(file_path: str) -> CVData:
    """Parse a CV file and extract structured data."""
    raw_text = extract_text(file_path)
    if not raw_text:
        return CVData()

    sections = _find_sections(raw_text)
    cv = CVData(raw_text=raw_text)

    # Extract skills from skills section
    if "skills" in sections:
        cv.skills = _extract_skills_from_text(sections["skills"])

    # Also scan experience, summary, education for skills mentioned inline
    # (e.g., "built using TensorFlow and PyTorch" in experience bullets)
    # Only add skills not already covered by existing entries (even inside parens)
    extra_sections = ["experience", "summary", "education", "certifications"]
    extra_text = " ".join(sections.get(s, "") for s in extra_sections)
    if extra_text.strip():
        from src.config.keywords import KNOWN_SKILLS
        # Build a set of all text already in skills (including parenthetical contents)
        existing_text = " ".join(cv.skills).lower()
        for known in sorted(KNOWN_SKILLS, key=len, reverse=True):
            kl = known.lower()
            # Skip if this skill (or its text) is already present in existing skills
            if kl in existing_text:
                continue
            # Skip single-char matches (too many false positives like "R", "C")
            if len(known) <= 1:
                continue
            # Word-boundary match in the extra text
            pattern = re.compile(r'\b' + re.escape(known) + r'\b', re.IGNORECASE)
            if pattern.search(extra_text):
                cv.skills.append(known)
                existing_text += " " + kl

    if not cv.skills:
        # Fallback: try to find tech names from full text
        cv.skills = _extract_tech_names(raw_text)[:30]

    # Deep scan: extract technology phrases, achievements, domain terms
    # from the ENTIRE CV — not just the skills section
    # Use line-joined text to avoid newline-broken phrases
    joined_text = re.sub(r'\n(?!\s*[A-Z•·▪\ufffd])', ' ', raw_text)
    deep_extras = _extract_deep_skills(joined_text, cv.skills)

    # Clean: remove entries with newlines, URLs, "of" prefixes, and short junk
    cleaned = []
    seen_lower = {s.lower() for s in cv.skills}
    for entry in deep_extras:
        entry = entry.strip()
        # Skip entries with newlines, URLs, or starting with prepositions
        if '\n' in entry or 'http' in entry or 'linkedin' in entry.lower():
            continue
        if entry.lower().startswith(('of ', 'in ', 'on ', 'at ', 'to ', 'by ')):
            continue
        # Skip if too long (sentence fragments from achievements)
        if len(entry) > 55:
            continue
        # Skip generic academic phrases
        if entry.lower() in ('science engineering', 'flow analysis', 'error analysis',
                              'agent workflow', 'training workflows', 'preprocessing pipelines'):
            continue
        # Skip if a shorter version already exists (dedup "RAG pipelines" vs "RAG pipeline")
        base = entry.lower().rstrip('s')
        if base in seen_lower or (base + 's') in seen_lower:
            continue
        cleaned.append(entry)
        seen_lower.add(entry.lower())

    cv.skills.extend(cleaned)

    # Extract job titles from experience
    if "experience" in sections:
        cv.job_titles = _extract_titles_from_experience(sections["experience"])

    # Extract education (full section, no truncation)
    if "education" in sections:
        lines = [l.strip() for l in sections["education"].split("\n") if l.strip()]
        cv.education = lines

    # Extract certifications
    if "certifications" in sections:
        lines = [l.strip() for l in sections["certifications"].split("\n") if l.strip()]
        cv.certifications = lines

    # Extract summary
    if "summary" in sections:
        cv.summary = sections["summary"][:1000]

    # Store experience section text for display
    if "experience" in sections:
        cv.experience_text = sections["experience"]

    return cv


def parse_cv_from_bytes(content: bytes, filename: str) -> CVData:
    """Parse CV from in-memory bytes (for Streamlit file_uploader)."""
    import tempfile
    import os

    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        return parse_cv(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass  # File locked or already removed

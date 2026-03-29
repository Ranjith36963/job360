import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from src.models import Job
from src.config.keywords import LOCATIONS
from src.config.settings import TARGET_SALARY_MIN, TARGET_SALARY_MAX
from src.filters.description_matcher import text_contains_with_synonyms

logger = logging.getLogger("job360.scorer")

# Weights for scoring components (total = 100) — legacy, kept for backward compat
TITLE_WEIGHT = 40
SKILL_WEIGHT = 40
LOCATION_WEIGHT = 10
RECENCY_WEIGHT = 10

# ── Multi-dimensional weights (total = 100) ──
DIM_ROLE = 20          # was 25 — reduced after title-matching precision fix (1E)
DIM_SKILL = 25
DIM_SENIORITY = 10
DIM_EXPERIENCE = 10
DIM_CREDENTIALS = 5
DIM_LOCATION = 10
DIM_RECENCY = 10
DIM_SEMANTIC = 10      # was 5 — doubled to leverage embedding infrastructure (1E)


@dataclass
class ScoreBreakdown:
    """Per-dimension scoring breakdown for a job match."""
    role: int = 0           # 0-20: title/role match
    skill: int = 0          # 0-25: skill overlap
    seniority: int = 0      # 0-10: seniority alignment
    experience: int = 0     # 0-10: experience years match
    credentials: int = 0    # 0-5:  qualification match
    location: int = 0       # 0-10: location match
    recency: int = 0        # 0-10: posting freshness
    semantic: int = 0       # 0-10: embedding similarity + keyword overlap
    penalty: int = 0        # subtracted from total (negative titles + excluded skills)
    total: int = 0          # final 0-100 score

    # Match explanation lists (for Phase 2B display)
    matched_skills: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    missing_preferred: list[str] = field(default_factory=list)
    transferable_skills: list[str] = field(default_factory=list)

# Points per skill match
PRIMARY_POINTS = 3
SECONDARY_POINTS = 2
TERTIARY_POINTS = 1
SKILL_CAP = SKILL_WEIGHT

# Location aliases — map variants to canonical form
LOCATION_ALIASES = {
    "greater london": "london",
    "city of london": "london",
    "england": "uk",
    "scotland": "uk",
    "wales": "uk",
    "gb": "uk",
    "great britain": "uk",
    "united kingdom": "uk",
}

REMOTE_TERMS = {"remote", "anywhere", "work from home", "wfh"}

# Countries, major non-UK cities, and US state abbreviations that indicate non-UK jobs
FOREIGN_INDICATORS = {
    # Countries
    "united states", "usa", "canada", "australia", "india", "germany", "france",
    "spain", "italy", "netherlands", "sweden", "norway", "denmark", "finland",
    "switzerland", "austria", "belgium", "ireland", "singapore", "japan",
    "china", "brazil", "mexico", "south korea", "korea", "israel", "poland",
    "portugal", "czech", "romania", "turkey", "south africa", "new zealand",
    "philippines", "malaysia", "taiwan", "morocco", "serbia", "hungary",
    "thailand", "vietnam", "indonesia", "argentina", "colombia", "chile",
    "ukraine", "greece", "croatia", "luxembourg", "qatar", "saudi arabia",
    # Major non-UK cities
    "new york", "san francisco", "los angeles", "chicago", "seattle", "austin",
    "boston", "denver", "toronto", "vancouver", "montreal", "sydney", "melbourne",
    "berlin", "munich", "paris", "amsterdam", "stockholm", "copenhagen", "oslo",
    "helsinki", "zurich", "dubai", "bangalore", "bengaluru", "hyderabad",
    "mumbai", "pune", "chennai", "tokyo", "tel aviv", "ottawa", "palo alto",
    "stuttgart", "warsaw", "warszawa", "belgrade", "casablanca", "taipei",
    "shanghai", "beijing", "hong kong", "kuala lumpur", "kulim", "prague",
    "budapest", "bucharest", "lisbon", "barcelona", "madrid", "milan", "rome",
    "athens", "cairo", "nairobi", "lagos", "cape town", "johannesburg",
    "buenos aires", "bogota", "santiago", "lima", "sao paulo",
    "pistoia", "tuscany", "florence", "naples", "turin",
    # US patterns (" us" and "us-" catch "Remote - US", "US-Remote", etc.)
    " us", "us-",
    # US/Canadian state/province names
    "illinois", "maryland", "virginia", "washington, d.c.", "british columbia",
    "california", "massachusetts", "colorado", "georgia", "north carolina",
    "florida", "pennsylvania", "ohio", "new jersey", "oregon", "arizona",
    "minnesota", "connecticut", "michigan", "tennessee", "texas",
    ", ca", ", ny", ", tx", ", wa", ", ma", ", co", ", il", ", ga", ", nc", ", va",
    ", fl", ", pa", ", oh", ", nj", ", or", ", az", ", mn", ", ct", ", md",
}

# Terms that confirm UK / remote (checked before foreign indicators)
UK_TERMS = {
    "uk", "united kingdom", "london", "manchester", "birmingham", "bristol",
    "cambridge", "oxford", "edinburgh", "glasgow", "belfast", "leeds",
    "liverpool", "nottingham", "sheffield", "southampton", "reading",
    "hatfield", "hertfordshire", "england", "scotland", "wales",
    "greater london", "city of london", "gb", "great britain",
}

# Experience level patterns
_EXPERIENCE_PATTERNS = {
    "intern": re.compile(r'\b(intern|internship)\b', re.IGNORECASE),
    "junior": re.compile(r'\b(junior|jr|graduate|entry[\s-]?level)\b', re.IGNORECASE),
    "mid": re.compile(r'\b(mid[\s-]?level|intermediate)\b', re.IGNORECASE),
    "senior": re.compile(r'\b(senior|sr)\b', re.IGNORECASE),
    "lead": re.compile(r'\b(lead|team\s*lead)\b', re.IGNORECASE),
    "staff": re.compile(r'\bstaff\b', re.IGNORECASE),
    "principal": re.compile(r'\bprincipal\b', re.IGNORECASE),
    "head": re.compile(r'\b(head\s+of|director|vp)\b', re.IGNORECASE),
}


@lru_cache(maxsize=512)
def _word_boundary_pattern(term: str) -> re.Pattern:
    """Build a compiled word-boundary regex for a skill term."""
    return re.compile(r'\b' + re.escape(term) + r'\b', re.IGNORECASE)


def _text_contains(text: str, term: str) -> bool:
    """Check if term appears as a whole word in text."""
    return bool(_word_boundary_pattern(term).search(text))


def _location_score(location: str) -> int:
    loc_lower = location.lower()
    # Check remote terms first
    for term in REMOTE_TERMS:
        if term in loc_lower:
            return LOCATION_WEIGHT - 2
    # Apply aliases then check against LOCATIONS
    normalized = loc_lower
    for alias, canonical in LOCATION_ALIASES.items():
        if alias in normalized:
            normalized = normalized.replace(alias, canonical)
    for target in LOCATIONS:
        target_lower = target.lower()
        if target_lower in ("remote", "hybrid"):
            continue
        if target_lower in loc_lower or target_lower in normalized:
            return LOCATION_WEIGHT
    return 0


def _recency_score(date_found: str) -> int:
    """Score based on job posting age. Recent jobs score higher."""
    if not date_found:
        return 0
    try:
        posted = datetime.fromisoformat(date_found)
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        days_old = (datetime.now(timezone.utc) - posted).days
    except (ValueError, TypeError):
        return 0
    if days_old <= 1:
        return RECENCY_WEIGHT
    if days_old <= 3:
        return 8
    if days_old <= 5:
        return 6
    if days_old <= 7:
        return 4
    if days_old <= 14:
        return 2
    if days_old <= 21:
        return 1
    return 0


def is_foreign_only(location: str) -> bool:
    """Return True if the location is foreign-only (no UK mention).

    Rules:
    - Empty/unknown location → False (benefit of doubt, might be UK)
    - UK term present → False (keep regardless of other countries)
    - Foreign indicator present → True (remove, even if "remote" is mentioned)
    - Pure remote (no foreign indicator) → False (keep)
    - No indicators at all → False (benefit of doubt)
    """
    if not location:
        return False
    loc_lower = location.lower()
    # UK terms always win — if UK is mentioned, keep the job
    for term in UK_TERMS:
        if term in loc_lower:
            return False
    # Foreign indicators override "remote" — "Remote - US" is still foreign
    for indicator in FOREIGN_INDICATORS:
        if indicator in loc_lower:
            return True
    # Pure remote with no foreign qualifier → keep
    for term in REMOTE_TERMS:
        if term in loc_lower:
            return False
    return False


def detect_experience_level(title: str) -> str:
    """Parse a job title and return the experience level string."""
    for level, pattern in _EXPERIENCE_PATTERNS.items():
        if pattern.search(title):
            return level
    return ""


def salary_in_range(job: Job) -> bool:
    """Check if a job's salary overlaps with the target range."""
    if job.salary_min is None and job.salary_max is None:
        return False
    job_min = job.salary_min or 0
    job_max = job.salary_max or float("inf")
    return job_max >= TARGET_SALARY_MIN and job_min <= TARGET_SALARY_MAX


class JobScorer:
    """Score jobs using dynamic keyword sets from a SearchConfig."""

    def __init__(self, config):
        """Accept a SearchConfig (from src.profile.models)."""
        self._config = config
        self._profile_embedding = None  # Lazy-computed on first use
        self._embedding_attempted = False

    def _title_score(self, job_title: str) -> int:
        title_lower = job_title.lower()
        best = 0
        for target in self._config.job_titles:
            target_lower = target.lower()
            if target_lower == title_lower:
                return TITLE_WEIGHT  # Exact match
            # Word-overlap scoring (replaces substring matching)
            target_words = set(re.findall(r'\w+', target_lower))
            title_words = set(re.findall(r'\w+', title_lower))
            shared = target_words & title_words
            if not shared:
                continue
            max_len = max(len(target_words), len(title_words))
            ratio = len(shared) / max_len if max_len else 0
            # Require at least one core domain word for >50% credit
            core_in_shared = shared & self._config.core_domain_words
            if core_in_shared:
                score = int(ratio * TITLE_WEIGHT)
            else:
                score = min(int(ratio * TITLE_WEIGHT), TITLE_WEIGHT // 4)
            best = max(best, score)
        if best > 0:
            return best
        # Fallback: domain word matching
        title_words = set(re.findall(r'\w+', title_lower))
        core_overlap = title_words & self._config.core_domain_words
        if not core_overlap:
            return 0
        support_overlap = title_words & self._config.supporting_role_words
        return min(len(core_overlap) * 5 + len(support_overlap) * 3, TITLE_WEIGHT // 4)

    def _skill_score(self, text: str) -> int:
        points = 0
        for skill in self._config.primary_skills:
            if text_contains_with_synonyms(text, skill):
                points += PRIMARY_POINTS
        for skill in self._config.secondary_skills:
            if text_contains_with_synonyms(text, skill):
                points += SECONDARY_POINTS
        for skill in self._config.tertiary_skills:
            if text_contains_with_synonyms(text, skill):
                points += TERTIARY_POINTS
        return min(points, SKILL_CAP)

    def _negative_penalty(self, job_title: str, description: str = "") -> int:
        title_lower = job_title.lower()
        for kw in self._config.negative_title_keywords:
            if kw.lower() in title_lower:
                return 30  # Title match — strongest signal
        if description:
            desc_lower = description.lower()
            for kw in self._config.negative_title_keywords:
                if kw.lower() in desc_lower:
                    return 15  # Description-only match — weaker signal
        return 0

    def _excluded_penalty(self, text: str) -> int:
        """Penalty for jobs containing skills the user explicitly excluded."""
        if not hasattr(self._config, 'excluded_skills') or not self._config.excluded_skills:
            return 0
        text_lower = text.lower()
        hits = sum(1 for skill in self._config.excluded_skills
                   if skill.lower() in text_lower)
        return min(hits * 5, 15)  # -5 per match, capped at -15

    def score(self, job: Job) -> int:
        text = f"{job.title} {job.description}"
        title_pts = self._title_score(job.title)
        skill_pts = self._skill_score(text)
        location_pts = _location_score(job.location)
        recency_pts = _recency_score(job.date_found)
        penalty = self._negative_penalty(job.title, job.description)
        penalty += self._excluded_penalty(text)
        total = title_pts + skill_pts + location_pts + recency_pts - penalty
        return min(max(total, 0), 100)

    def check_visa_flag(self, job: Job) -> bool:
        text = f"{job.title} {job.description}".lower()
        return any(kw.lower() in text for kw in self._config.visa_keywords)

    # ── Multi-dimensional scoring ─────────────────────────────────────

    def score_detailed(self, job: Job,
                       parsed_jd: Optional[object] = None,
                       cv_data: Optional[object] = None) -> ScoreBreakdown:
        """Score a job across 8 dimensions, returning a full breakdown.

        Args:
            job: The job to score.
            parsed_jd: Optional ParsedJD from jd_parser.parse_jd().
            cv_data: Optional CVData with structured fields.

        Returns ScoreBreakdown with per-dimension scores and skill match lists.
        """
        bd = ScoreBreakdown()
        text = f"{job.title} {job.description}"

        # 1. Role (0-25)
        bd.role = self._dim_role(job.title)

        # 2. Skill (0-25) + match lists
        bd.skill, bd.matched_skills, bd.missing_required, bd.missing_preferred = \
            self._dim_skill(text, parsed_jd)

        # 3. Seniority (0-10)
        bd.seniority = self._dim_seniority(job.title, parsed_jd, cv_data)

        # 4. Experience (0-10)
        bd.experience = self._dim_experience(parsed_jd, cv_data)

        # 5. Credentials (0-5)
        bd.credentials = self._dim_credentials(parsed_jd, cv_data)

        # 6. Location (0-10) — with work arrangement preference
        bd.location = _location_score(job.location)
        if hasattr(self._config, 'work_arrangement') and self._config.work_arrangement:
            loc_lower = job.location.lower() if job.location else ""
            text_lower = text.lower()
            pref = self._config.work_arrangement.lower()
            if pref == "remote":
                if any(t in loc_lower or t in text_lower
                       for t in ("remote", "work from home", "wfh")):
                    bd.location = min(bd.location + 2, DIM_LOCATION)
                elif "onsite" in loc_lower or "on-site" in loc_lower:
                    bd.location = max(bd.location - 2, 0)
            elif pref in ("onsite", "on-site"):
                if "remote" in loc_lower and "onsite" not in loc_lower:
                    bd.location = max(bd.location - 2, 0)

        # 7. Recency (0-10)
        bd.recency = _recency_score(job.date_found)

        # 8. Semantic (0-10) — embedding similarity + keyword overlap
        bd.semantic = self._dim_semantic(text, job_embedding_str=job.embedding)

        # Penalty (negative keywords + excluded skills)
        bd.penalty = self._negative_penalty(job.title, job.description)
        bd.penalty += self._excluded_penalty(text)

        # Transferable skills (from skill graph)
        bd.transferable_skills = self._find_transferable(
            bd.missing_required, parsed_jd, cv_data
        )

        bd.total = min(max(
            bd.role + bd.skill + bd.seniority + bd.experience +
            bd.credentials + bd.location + bd.recency + bd.semantic -
            bd.penalty, 0), 100)

        logger.debug(
            '"%s @ %s" R=%d S=%d Sen=%d Exp=%d Cred=%d Loc=%d Rec=%d Sem=%d P=%d T=%d',
            job.title[:40], job.company[:20],
            bd.role, bd.skill, bd.seniority, bd.experience,
            bd.credentials, bd.location, bd.recency, bd.semantic,
            bd.penalty, bd.total,
        )

        return bd

    def _dim_role(self, job_title: str) -> int:
        """Score role/title match (0-20)."""
        title_lower = job_title.lower()
        best = 0
        for target in self._config.job_titles:
            target_lower = target.lower()
            if target_lower == title_lower:
                return DIM_ROLE  # Exact match
            # Word-overlap scoring (replaces substring matching)
            target_words = set(re.findall(r'\w+', target_lower))
            title_words = set(re.findall(r'\w+', title_lower))
            shared = target_words & title_words
            if not shared:
                continue
            max_len = max(len(target_words), len(title_words))
            ratio = len(shared) / max_len if max_len else 0
            core_in_shared = shared & self._config.core_domain_words
            if core_in_shared:
                score = int(ratio * DIM_ROLE)
            else:
                score = min(int(ratio * DIM_ROLE), DIM_ROLE // 4)
            best = max(best, score)
        if best > 0:
            return best
        # Fallback: domain word matching
        title_words = set(re.findall(r'\w+', title_lower))
        core_overlap = title_words & self._config.core_domain_words
        if not core_overlap:
            return 0
        support_overlap = title_words & self._config.supporting_role_words
        return min(len(core_overlap) * 4 + len(support_overlap) * 2, DIM_ROLE * 3 // 4)

    def _dim_skill(self, text: str,
                   parsed_jd: Optional[object] = None,
                   ) -> tuple[int, list[str], list[str], list[str]]:
        """Score skill match (0-25) and build match lists."""
        all_user_skills = set()
        for s in self._config.primary_skills:
            all_user_skills.add(s.lower())
        for s in self._config.secondary_skills:
            all_user_skills.add(s.lower())
        for s in self._config.tertiary_skills:
            all_user_skills.add(s.lower())

        matched: list[str] = []
        missing_req: list[str] = []
        missing_pref: list[str] = []

        # If we have a parsed JD, use its required/preferred classification
        # Skills in parsed_jd are already confirmed to exist in the text
        # (by _extract_skills or _enrich_with_user_skills) — no need to re-check
        if parsed_jd and hasattr(parsed_jd, 'required_skills') and parsed_jd.required_skills:
            for skill in parsed_jd.required_skills:
                if skill.lower() in all_user_skills:
                    matched.append(skill)
                else:
                    missing_req.append(skill)
            for skill in parsed_jd.preferred_skills:
                if skill.lower() in all_user_skills:
                    matched.append(skill)
                else:
                    missing_pref.append(skill)
            # Score: each required match = 3pts, preferred match = 1pt
            points = len([s for s in matched if s in (parsed_jd.required_skills or [])]) * 3
            points += len([s for s in matched if s in (parsed_jd.preferred_skills or [])]) * 1
        else:
            # Fallback to tier-based scoring (no ParsedJD)
            points = 0
            matched_lower: set[str] = set()
            for skill in self._config.primary_skills:
                if text_contains_with_synonyms(text, skill):
                    points += PRIMARY_POINTS
                    matched.append(skill)
                    matched_lower.add(skill.lower())
            for skill in self._config.secondary_skills:
                if text_contains_with_synonyms(text, skill):
                    points += SECONDARY_POINTS
                    matched.append(skill)
                    matched_lower.add(skill.lower())
            for skill in self._config.tertiary_skills:
                if text_contains_with_synonyms(text, skill):
                    points += TERTIARY_POINTS
                    matched.append(skill)
                    matched_lower.add(skill.lower())

            # When direct matches are sparse (title-only text), try two extras:
            # 1. Component-word matching: "Generative AI" → check "AI" alone
            # 2. Skill graph: title mentions related skill → partial credit
            if points < DIM_SKILL // 2:
                text_lower = text.lower()
                # 1. Component words from multi-word skills (1pt each, primary only)
                for skill in self._config.primary_skills:
                    if skill.lower() in matched_lower:
                        continue
                    words = skill.split()
                    if len(words) >= 2:
                        for w in words:
                            if len(w) >= 2 and re.search(r'\b' + re.escape(w.lower()) + r'\b', text_lower):
                                points += 1
                                matched.append(f"{skill}~")
                                matched_lower.add(skill.lower())
                                break

                # 2. Skill graph: if title mentions a skill related to user's skill
                try:
                    from src.profile.skill_graph import SKILL_RELATIONSHIPS
                    title_words = set(re.findall(r'\w+', text_lower))
                    for user_skill in list(self._config.primary_skills)[:10]:
                        if user_skill.lower() in matched_lower:
                            continue
                        key = user_skill.lower()
                        if key in SKILL_RELATIONSHIPS:
                            for related, conf in SKILL_RELATIONSHIPS[key]:
                                if conf >= 0.7 and related.lower() in text_lower:
                                    points += 1
                                    matched.append(f"{user_skill}~graph")
                                    matched_lower.add(user_skill.lower())
                                    break
                except ImportError:
                    pass

        return min(points, DIM_SKILL), matched, missing_req, missing_pref

    def _dim_seniority(self, job_title: str,
                       parsed_jd: Optional[object] = None,
                       cv_data: Optional[object] = None) -> int:
        """Score seniority alignment (0-10)."""
        # Determine JD seniority
        jd_level = ""
        if parsed_jd and hasattr(parsed_jd, 'seniority_signal'):
            jd_level = parsed_jd.seniority_signal
        if not jd_level:
            jd_level = detect_experience_level(job_title)
            # Map to standard levels
            level_map = {"intern": "entry", "junior": "entry", "staff": "senior",
                         "principal": "lead", "head": "executive"}
            jd_level = level_map.get(jd_level, jd_level)

        # Determine user seniority — prefer explicit preference over CV-inferred
        cv_level = ""
        if hasattr(self._config, 'target_experience_level') and self._config.target_experience_level:
            cv_level = self._config.target_experience_level
        elif cv_data and hasattr(cv_data, 'computed_seniority'):
            cv_level = cv_data.computed_seniority

        if not jd_level or not cv_level:
            return 3  # Unknown = low credit (prevents irrelevant jobs reaching threshold)

        levels = ["entry", "mid", "senior", "lead", "executive"]
        try:
            jd_idx = levels.index(jd_level)
            cv_idx = levels.index(cv_level)
        except ValueError:
            return DIM_SENIORITY // 2

        gap = abs(jd_idx - cv_idx)
        if gap == 0:
            return DIM_SENIORITY
        elif gap == 1:
            return DIM_SENIORITY * 3 // 4  # 7-8 points
        elif gap == 2:
            return DIM_SENIORITY // 4       # 2 points
        return 0

    def _dim_experience(self, parsed_jd: Optional[object] = None,
                        cv_data: Optional[object] = None) -> int:
        """Score experience years alignment (0-10)."""
        jd_years = None
        if parsed_jd and hasattr(parsed_jd, 'experience_years'):
            jd_years = parsed_jd.experience_years

        cv_months = 0
        if cv_data and hasattr(cv_data, 'total_experience_months'):
            cv_months = cv_data.total_experience_months

        if jd_years is None:
            return 3  # No requirement stated = low credit (prevents score inflation)

        cv_years = cv_months / 12
        if cv_years >= jd_years:
            return DIM_EXPERIENCE
        elif cv_years >= jd_years - 2:
            return DIM_EXPERIENCE * 3 // 4  # Within 2 years
        elif cv_years >= jd_years - 4:
            return DIM_EXPERIENCE // 4
        return 0

    def _dim_credentials(self, parsed_jd: Optional[object] = None,
                         cv_data: Optional[object] = None) -> int:
        """Score qualification/credential match (0-5)."""
        if not parsed_jd or not hasattr(parsed_jd, 'qualifications'):
            return 0
        jd_quals = {q.lower() for q in (parsed_jd.qualifications or [])}
        if not jd_quals:
            return 0

        cv_quals: set[str] = set()
        if cv_data:
            if hasattr(cv_data, 'certifications'):
                cv_quals.update(c.lower() for c in (cv_data.certifications or []))
            if hasattr(cv_data, 'structured_education'):
                for edu in (cv_data.structured_education or []):
                    if hasattr(edu, 'degree') and edu.degree:
                        cv_quals.add(edu.degree.lower())

        if not cv_quals:
            return 0

        matches = jd_quals & cv_quals
        if matches:
            return min(len(matches) * 2, DIM_CREDENTIALS)
        return 0

    def _ensure_profile_embedding(self):
        """Lazy-compute profile embedding on first use."""
        if self._embedding_attempted:
            return
        self._embedding_attempted = True
        try:
            from src.filters.embeddings import is_available, build_profile_embedding
            if is_available():
                about_me = ""
                if hasattr(self._config, 'about_me'):
                    about_me = self._config.about_me
                self._profile_embedding = build_profile_embedding(
                    job_titles=self._config.job_titles,
                    primary_skills=self._config.primary_skills,
                    secondary_skills=self._config.secondary_skills,
                    relevance_keywords=self._config.relevance_keywords,
                    about_me=about_me,
                )
        except Exception:
            self._profile_embedding = None

    def _dim_semantic(self, text: str, job_embedding_str: str = "") -> int:
        """Score semantic relevance (0-10) using embeddings or keyword fallback."""
        # Try embeddings first
        self._ensure_profile_embedding()
        base_score = 0
        if self._profile_embedding is not None:
            try:
                from src.filters.embeddings import score_semantic_similarity
                from src.filters.hybrid_retriever import deserialize_embedding
                pre_emb = deserialize_embedding(job_embedding_str) if job_embedding_str else None
                base_score = score_semantic_similarity(
                    self._profile_embedding, text, max_points=DIM_SEMANTIC,
                    job_embedding=pre_emb,
                )
            except Exception:
                pass  # Fall through to keyword overlap

        if base_score == 0:
            # Fallback: keyword overlap
            if self._config.relevance_keywords:
                text_lower = text.lower()
                hits = sum(1 for kw in self._config.relevance_keywords if kw in text_lower)
                ratio = hits / len(self._config.relevance_keywords)
                base_score = min(int(ratio * DIM_SEMANTIC * 2), DIM_SEMANTIC)

        # Phase 3D: industry mention bonus (+2, within DIM_SEMANTIC cap)
        if hasattr(self._config, 'industries') and self._config.industries:
            text_lower = text.lower()
            if any(ind.lower() in text_lower for ind in self._config.industries):
                base_score = min(base_score + 2, DIM_SEMANTIC)

        return base_score

    def _find_transferable(self, missing: list[str],
                           parsed_jd: Optional[object] = None,
                           cv_data: Optional[object] = None) -> list[str]:
        """Find user skills that are transferable to missing requirements."""
        if not missing:
            return []
        try:
            from src.profile.skill_graph import SKILL_RELATIONSHIPS
        except ImportError:
            return []

        all_user_skills = set()
        for s in self._config.primary_skills:
            all_user_skills.add(s.lower())
        for s in self._config.secondary_skills:
            all_user_skills.add(s.lower())
        for s in self._config.tertiary_skills:
            all_user_skills.add(s.lower())

        transferable: list[str] = []
        for missing_skill in missing:
            key = missing_skill.lower()
            if key in SKILL_RELATIONSHIPS:
                for related, conf in SKILL_RELATIONSHIPS[key]:
                    if related.lower() in all_user_skills and conf >= 0.7:
                        transferable.append(f"{related} → {missing_skill}")
                        break
        return transferable

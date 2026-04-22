import re

from src.models import Job

# Seniority prefixes to strip for fuzzy title matching
_SENIORITY_RE = re.compile(
    r'^(senior|sr\.?|junior|jr\.?|lead|principal|staff|head\s+of)\s+',
    re.IGNORECASE,
)

# Trailing job codes like "- 12345" or "/ REQ-123"
_TRAILING_CODE_RE = re.compile(r'\s*[-/]\s*[A-Z0-9]{2,}[-_]?\d+\s*$', re.IGNORECASE)

# Parentheticals like "(London)" or "(Remote)"
_PAREN_RE = re.compile(r'\s*\([^)]*\)\s*$')


def _normalize_title(title: str) -> str:
    """Normalize a job title for dedup grouping.

    NOTE: This is intentionally MORE aggressive than Job.normalized_key().
    The DB UNIQUE constraint uses normalized_key() (company suffix + lowercase only),
    while dedup uses this function (also strips seniority, job codes, parentheticals).
    This means dedup groups are wider than DB unique keys — by design:
    - Dedup merges "Senior ML Engineer" and "ML Engineer" within a single run
    - DB preserves them as separate records across runs
    Do NOT unify these without a full DB migration (see CLAUDE.md Rule 1).
    """
    t = title.strip()
    t = _TRAILING_CODE_RE.sub('', t)
    t = _PAREN_RE.sub('', t)
    t = _SENIORITY_RE.sub('', t)
    return t.strip().lower()


def _completeness(job: Job) -> int:
    score = 0
    if job.salary_min is not None:
        score += 10
    if job.salary_max is not None:
        score += 10
    if job.description:
        score += min(len(job.description), 20)
    if job.location:
        score += 5
    return score


def _enrichment_bonus(job: Job, enrichments: dict | None) -> int:
    """Pillar 2 Batch 2.5 tiebreaker — reward jobs whose `id` has an LLM
    enrichment row, so when two candidates in a dedup group tie on
    match_score + completeness, the enriched one wins and carries the
    structured fields downstream (scoring, embeddings).

    `enrichments` is a ``dict[int, object]`` (or any truthy mapping) passed
    in by callers that have already loaded enrichments for the candidate
    set. Callers who don't opt in pass ``None`` and this returns 0 —
    preserves pre-Batch-2.5 ordering exactly.
    """
    if not enrichments:
        return 0
    job_id = getattr(job, "id", None)
    return 5 if job_id is not None and job_id in enrichments else 0


def deduplicate(
    jobs: list[Job],
    enrichments: dict | None = None,
    *,
    enable_fuzzy: bool = True,
    enable_tfidf: bool = True,
    enable_embedding_repost: bool = False,
    embedding_lookup: "dict | None" = None,
) -> list[Job]:
    """Group jobs by normalized (company, title) and keep the best per group.

    Pillar 2 Batch 2.10 — four-layer dedup:
      Layer 1 (always on): exact normalized (company, title) key.
      Layer 2 (`enable_fuzzy`): RapidFuzz token_set_ratio ≥ 80 on titles
        AND `ratio` ≥ 85 on companies AND same normalized location.
      Layer 3 (`enable_tfidf`): TF-IDF cosine ≥ 0.85 on
        (company + title + description[:200]).
      Layer 4 (`enable_embedding_repost`): same-company embedding cosine
        ≥ 0.92 → flagged as a repost. Requires `embedding_lookup` dict
        mapping job_id → vector.

    Ranking (high → low):
      1. `match_score` — the original Pillar-1 primary key.
      2. enrichment bonus — if `enrichments` is provided and the job has a
         row in it, +5. Encourages the enriched candidate to win a tie.
      3. `_completeness` — salary/description/location fullness.
    """
    if not jobs:
        return []

    # --- Layer 1: exact normalized (company, title) key --------------------
    groups: dict[tuple[str, str], list[Job]] = {}
    for job in jobs:
        company, _ = job.normalized_key()
        title = _normalize_title(job.title)
        key = (company, title)
        groups.setdefault(key, []).append(job)

    def _rank_key(j: Job):
        return (
            j.match_score,
            _enrichment_bonus(j, enrichments),
            _completeness(j),
        )

    # Pick the representative of each Layer-1 group.
    layer1 = [max(group, key=_rank_key) for group in groups.values()]

    # --- Layer 2: RapidFuzz fuzzy merge ------------------------------------
    if enable_fuzzy and len(layer1) > 1:
        layer1 = _merge_fuzzy(layer1, rank_key=_rank_key)

    # --- Layer 3: TF-IDF cosine merge --------------------------------------
    if enable_tfidf and len(layer1) > 1:
        layer1 = _merge_tfidf(layer1, rank_key=_rank_key)

    # --- Layer 4: embedding-based repost detection within same company ----
    if enable_embedding_repost and embedding_lookup and len(layer1) > 1:
        layer1 = _merge_embedding_reposts(
            layer1, rank_key=_rank_key, embedding_lookup=embedding_lookup,
        )

    return layer1


# ---------------------------------------------------------------------------
# Layer 2 — RapidFuzz
# ---------------------------------------------------------------------------


def _norm_location(loc: str) -> str:
    return (loc or "").strip().lower()


def _merge_fuzzy(jobs: list[Job], *, rank_key) -> list[Job]:
    """Collapse fuzzy duplicates using RapidFuzz.

    Lazy-imports `rapidfuzz` so Layer-1-only callers don't pay the import
    cost. Jobs are considered duplicates when:
      - `token_set_ratio(title_i, title_j) >= 80` AND
      - `ratio(company_i, company_j) >= 85` AND
      - normalized location matches.
    """
    try:
        from rapidfuzz import fuzz   # type: ignore
    except ImportError:
        # Degraded — keep Layer-1 result.
        return jobs

    kept: list[Job] = []
    for job in jobs:
        matched_index: int | None = None
        for i, keeper in enumerate(kept):
            if _norm_location(job.location) != _norm_location(keeper.location):
                continue
            company_ratio = fuzz.ratio(job.company.lower(), keeper.company.lower())
            if company_ratio < 85:
                continue
            title_ratio = fuzz.token_set_ratio(job.title.lower(), keeper.title.lower())
            if title_ratio < 80:
                continue
            matched_index = i
            break
        if matched_index is None:
            kept.append(job)
        else:
            # Replace the keeper if the new candidate ranks higher.
            if rank_key(job) > rank_key(kept[matched_index]):
                kept[matched_index] = job
    return kept


# ---------------------------------------------------------------------------
# Layer 3 — TF-IDF cosine
# ---------------------------------------------------------------------------


_TFIDF_THRESHOLD = 0.85


def _tfidf_doc(job: Job) -> str:
    """Documents combine company + title + first 200 chars of description."""
    desc = (job.description or "")[:200]
    return f"{job.company} {job.title} {desc}".lower()


def _merge_tfidf(jobs: list[Job], *, rank_key) -> list[Job]:
    """Collapse near-duplicates using TF-IDF cosine on
    company + title + description[:200]. Lazy-imports scikit-learn."""
    if len(jobs) <= 1:
        return jobs
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer   # type: ignore
        from sklearn.metrics.pairwise import cosine_similarity         # type: ignore
    except ImportError:
        return jobs

    docs = [_tfidf_doc(j) for j in jobs]
    try:
        vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=2000)
        matrix = vectorizer.fit_transform(docs)
    except ValueError:
        # All docs empty after stop-word filtering — nothing to compare.
        return jobs

    sim = cosine_similarity(matrix)
    # Group jobs whose pairwise similarity >= threshold via union-find.
    n = len(jobs)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] >= _TFIDF_THRESHOLD:
                union(i, j)

    # Pick the highest-ranked job per cluster.
    clusters: dict[int, list[Job]] = {}
    for i, job in enumerate(jobs):
        clusters.setdefault(find(i), []).append(job)
    return [max(group, key=rank_key) for group in clusters.values()]


# ---------------------------------------------------------------------------
# Layer 4 — embedding-based repost detection within same company
# ---------------------------------------------------------------------------


_EMBEDDING_REPOST_THRESHOLD = 0.92


def _cosine(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = sum(a * a for a in v1) ** 0.5
    n2 = sum(a * a for a in v2) ** 0.5
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


def _merge_embedding_reposts(
    jobs: list[Job],
    *,
    rank_key,
    embedding_lookup: dict,
) -> list[Job]:
    """Within each company, collapse jobs whose embedding cosine ≥ 0.92.
    Chosen survivor keeps the earliest ``first_seen_at`` to preserve the
    original posting date (plan §4 Batch 2.10)."""
    by_company: dict[str, list[Job]] = {}
    for job in jobs:
        company = (job.company or "").strip().lower()
        by_company.setdefault(company, []).append(job)

    kept: list[Job] = []
    for company, group in by_company.items():
        company_kept: list[Job] = []
        for job in group:
            job_id = getattr(job, "id", None)
            if job_id is None:
                company_kept.append(job)
                continue
            vec = embedding_lookup.get(job_id)
            if vec is None:
                company_kept.append(job)
                continue

            matched_index: int | None = None
            for i, keeper in enumerate(company_kept):
                keeper_id = getattr(keeper, "id", None)
                keeper_vec = (embedding_lookup.get(keeper_id) if keeper_id is not None
                              else None)
                if not keeper_vec:
                    continue
                if _cosine(vec, keeper_vec) >= _EMBEDDING_REPOST_THRESHOLD:
                    matched_index = i
                    break
            if matched_index is None:
                company_kept.append(job)
            else:
                existing = company_kept[matched_index]
                winner = max((existing, job), key=rank_key)
                # Preserve the earliest first_seen_at across the merged pair.
                earliest = min(
                    (j for j in (existing, job) if getattr(j, "first_seen_at", None)),
                    key=lambda j: j.first_seen_at,
                    default=None,
                )
                if earliest is not None:
                    winner.first_seen_at = earliest.first_seen_at
                company_kept[matched_index] = winner
        kept.extend(company_kept)
    return kept

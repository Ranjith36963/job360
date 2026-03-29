import logging
import re
from difflib import SequenceMatcher
from typing import Optional

import numpy as np

from src.models import Job

logger = logging.getLogger("job360.dedup")

# Minimum description similarity ratio for pass-2 dedup (lowered from 0.85)
_DESC_SIMILARITY_THRESHOLD = 0.80
# Minimum description length to attempt similarity comparison
_MIN_DESC_LEN = 50

# Embedding cosine thresholds for fast dedup
_COSINE_AUTO_DUP = 0.92     # Above this → auto-duplicate (skip SequenceMatcher)
_COSINE_AUTO_SKIP = 0.75    # Below this → definitely not duplicate

# HTML tag stripping pattern
_HTML_TAG_RE = re.compile(r'<[^>]+>')
_WHITESPACE_RE = re.compile(r'\s+')


def _normalize_title(title: str) -> str:
    """Normalize a job title for dedup grouping.

    Strips trailing job codes and parentheticals but preserves seniority
    prefixes — "Senior Data Engineer" and "Junior Data Engineer" are
    distinct roles and must NOT collapse into the same dedup bucket.
    """
    from src.models import _TRAILING_CODE_RE, _PAREN_RE
    t = title.strip()
    t = _TRAILING_CODE_RE.sub('', t)
    t = _PAREN_RE.sub('', t)
    return t.strip().lower()


def _completeness(job: Job) -> int:
    score = 0
    if job.salary_min is not None:
        score += 10
    if job.salary_max is not None:
        score += 10
    if job.description:
        score += min(len(job.description) // 50, 20)
    if job.location:
        score += 5
    return score


def _normalize_description(text: str) -> str:
    """Strip HTML tags and collapse whitespace for fair comparison."""
    text = _HTML_TAG_RE.sub(' ', text)
    text = _WHITESPACE_RE.sub(' ', text)
    return text.strip().lower()


def _description_similar(a: str, b: str) -> bool:
    """Return True if two descriptions are similar enough to be duplicates."""
    if len(a) < _MIN_DESC_LEN or len(b) < _MIN_DESC_LEN:
        return False
    na = _normalize_description(a)
    nb = _normalize_description(b)
    return SequenceMatcher(None, na, nb).ratio() >= _DESC_SIMILARITY_THRESHOLD


def _description_similar_fast(na: str, nb: str) -> bool:
    """Fast similarity check using pre-normalized descriptions.

    Uses two pre-filters before the expensive SequenceMatcher.ratio():
    1. Length filter — if lengths differ by >40%, can't be 80% similar
    2. quick_ratio() — cheap upper bound, skip if below threshold
    """
    len_a, len_b = len(na), len(nb)
    if len_a < _MIN_DESC_LEN or len_b < _MIN_DESC_LEN:
        return False
    # Pre-filter 1: length difference
    shorter, longer = min(len_a, len_b), max(len_a, len_b)
    if shorter / longer < _DESC_SIMILARITY_THRESHOLD:
        return False
    # Pre-filter 2: quick_ratio (O(n) vs O(n²) for full ratio)
    sm = SequenceMatcher(None, na, nb)
    if sm.quick_ratio() < _DESC_SIMILARITY_THRESHOLD:
        return False
    return sm.ratio() >= _DESC_SIMILARITY_THRESHOLD


def _batch_cosine_matrix(jobs: list[Job]) -> Optional[np.ndarray]:
    """Compute pairwise cosine similarity matrix for jobs with embeddings.

    Returns (n, n) similarity matrix. Pairs where either job lacks an
    embedding are marked with -2.0 (sentinel below any real similarity).
    Returns None if fewer than 2 jobs have embeddings.
    """
    from src.filters.hybrid_retriever import deserialize_embedding

    vecs = []
    has_emb = []
    for job in jobs:
        if job.embedding:
            vec = deserialize_embedding(job.embedding)
            vecs.append(vec)
            has_emb.append(vec is not None)
        else:
            vecs.append(None)
            has_emb.append(False)

    valid_count = sum(has_emb)
    if valid_count < 2:
        return None

    dim = 384
    mat = np.zeros((len(jobs), dim), dtype=np.float32)
    for i, vec in enumerate(vecs):
        if vec is not None and has_emb[i]:
            mat[i] = vec

    # Pairwise cosine: mat @ mat.T (vectors are L2-normalized)
    sim_matrix = mat @ mat.T

    # Mask out pairs where either job lacks an embedding
    for i in range(len(jobs)):
        if not has_emb[i]:
            sim_matrix[i, :] = -2.0
            sim_matrix[:, i] = -2.0

    return sim_matrix


def deduplicate(jobs: list[Job], stats_out: dict | None = None) -> list[Job]:
    if not jobs:
        return []

    # Pass 1: Group by normalized (company, title)
    groups: dict[tuple[str, str], list[Job]] = {}
    for job in jobs:
        company, _ = job.normalized_key()
        title = _normalize_title(job.title)
        key = (company, title)
        groups.setdefault(key, []).append(job)
    pass1: list[Job] = []
    for group in groups.values():
        best = max(group, key=lambda j: (j.match_score, _completeness(j)))
        pass1.append(best)

    _removed_by_key = len(jobs) - len(pass1)
    logger.debug("Dedup pass1: %d groups, %d removed by normalized key", len(groups), _removed_by_key)

    # Pass 2: Same company + similar description → merge
    # Pre-normalize all descriptions once (avoids repeated normalization)
    norm_cache: dict[int, str] = {}
    for job in pass1:
        norm_cache[id(job)] = _normalize_description(job.description) if job.description else ""

    company_groups: dict[str, list[Job]] = {}
    for job in pass1:
        company, _ = job.normalized_key()
        company_groups.setdefault(company, []).append(job)

    _removed_by_similarity = 0
    result: list[Job] = []
    for company_jobs in company_groups.values():
        if len(company_jobs) <= 1:
            result.extend(company_jobs)
            continue

        # Pre-compute cosine similarity matrix for this company group
        sim_matrix = _batch_cosine_matrix(company_jobs)

        kept: list[Job] = []
        kept_indices: list[int] = []

        for i, job in enumerate(company_jobs):
            is_dup = False
            dup_method = ""
            na = norm_cache[id(job)]

            for ki, orig_idx in enumerate(kept_indices):
                existing = kept[ki]

                # Three-tier comparison using embeddings + SequenceMatcher
                if sim_matrix is not None:
                    cos_sim = float(sim_matrix[i, orig_idx])
                    if cos_sim > -1.5:  # both have valid embeddings
                        if cos_sim >= _COSINE_AUTO_DUP:
                            is_dup = True
                            dup_method = "cosine_auto"
                        elif cos_sim < _COSINE_AUTO_SKIP:
                            continue  # definitely not a duplicate
                        else:
                            # Ambiguous zone — confirm with SequenceMatcher
                            nb = norm_cache[id(existing)]
                            if _description_similar_fast(na, nb):
                                is_dup = True
                                dup_method = "cosine+seq"
                    else:
                        # Missing embedding — fall back to text comparison
                        nb = norm_cache[id(existing)]
                        if _description_similar_fast(na, nb):
                            is_dup = True
                            dup_method = "seq_only"
                else:
                    # No embedding matrix — original behavior
                    nb = norm_cache[id(existing)]
                    if _description_similar_fast(na, nb):
                        is_dup = True
                        dup_method = "seq_only"

                if is_dup:
                    _removed_by_similarity += 1
                    logger.debug(
                        "Dedup: '%s @ %s' dup of '%s @ %s' (%s)",
                        job.title[:30], job.company[:15],
                        existing.title[:30], existing.company[:15],
                        dup_method,
                    )
                    # Keep the one with higher score / more data
                    if (job.match_score, _completeness(job)) > (
                        existing.match_score, _completeness(existing)
                    ):
                        kept[ki] = job
                        kept_indices[ki] = i
                    break

            if not is_dup:
                kept.append(job)
                kept_indices.append(i)

        result.extend(kept)

    if stats_out is not None:
        stats_out["removed_by_key"] = _removed_by_key
        stats_out["removed_by_similarity"] = _removed_by_similarity

    return result

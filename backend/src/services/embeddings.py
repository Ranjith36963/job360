"""Pillar 2 Batch 2.6 — job embeddings.

Encodes a Job + its enrichment into a 384-dim vector using
`sentence-transformers/all-MiniLM-L6-v2`. Handles the short-profile →
long-description asymmetry the research report flags: when the
`requirements_summary` exceeds 300 tokens, split into 300-token windows
with 50-token overlap and store `max(chunk_similarities)` as the
job-level score.

CLAUDE.md rule #11 compliance — `sentence_transformers` is imported
lazily inside the functions that use it. Tests inject an
`encoder_factory` that returns a fake encoder so no real 80 MB model
is downloaded during pytest.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable
from typing import Any, Callable, Optional, cast

logger = logging.getLogger("job360.services.embeddings")


def _semantic_enabled() -> bool:
    """Read ``SEMANTIC_ENABLED`` at call time so tests can monkey-patch the env."""

    return os.getenv("SEMANTIC_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

# Chunking policy — plan §4 Batch 2.6 calls for 300-token windows with
# 50-token overlap on `requirements_summary`. Implemented here as word-count
# splits so we avoid shipping a dedicated tokenizer inside library code.
_CHUNK_SIZE_WORDS = 300
_CHUNK_OVERLAP_WORDS = 50


# Loaded on first call, reused thereafter. Tests override via the
# ``encoder_factory`` parameter, never touching this cache.
_ENCODER: Optional[object] = None


def _load_encoder() -> object:
    """Lazy-load the sentence-transformers encoder. CLAUDE.md rule #11."""
    global _ENCODER
    if _ENCODER is not None:
        return _ENCODER
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise RuntimeError(
            "sentence-transformers is not installed — run " "`pip install '.[semantic]'` and retry."
        ) from e
    _ENCODER = SentenceTransformer(MODEL_NAME)
    return _ENCODER


def _encode_text(text: str, encoder: Any) -> list[float]:
    """Wrap a single-text .encode() call. Returns a list[float] for JSON safety."""
    import numpy as np  # Local import — same CLAUDE.md rule pattern.

    vec = encoder.encode(text)
    if isinstance(vec, np.ndarray):
        vec = vec.tolist()
    elif isinstance(vec, list) and vec and isinstance(vec[0], np.ndarray):
        vec = vec[0].tolist()
    # The encoder is duck-typed (real model or a test stub); every branch above
    # lands on a plain list of floats.
    return cast("list[float]", vec)


def _chunk_words(text: str, size: int, overlap: int) -> list[str]:
    """Split a string on word boundaries into overlapping windows."""
    words = text.split()
    if len(words) <= size:
        return [text]
    chunks: list[str] = []
    step = size - overlap
    if step <= 0:
        step = size
    for start in range(0, len(words), step):
        window = words[start : start + size]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + size >= len(words):
            break
    return chunks


def _pool_chunk_vectors(vectors: Iterable[list[float]]) -> list[float]:
    """Element-wise max-pool — the research report's recommendation for
    matching a short query against long documents. Preserves the
    strongest signal per dimension so a single chunk's relevance doesn't
    wash out in a mean."""
    vecs = list(vectors)
    if not vecs:
        return []
    if len(vecs) == 1:
        return list(vecs[0])
    dim = len(vecs[0])
    out = [float("-inf")] * dim
    for v in vecs:
        for i, x in enumerate(v):
            if x > out[i]:
                out[i] = x
    return out


def profile_to_embedding_text(profile: Any) -> str:
    """Build the CANDIDATE-side semantic text from the WHOLE profile.

    WHY THIS EXISTS (2026-08-06). The hybrid search's query vector was built
    from ``job_titles + cv.skills[:40] + summary`` only. But ``cv.skills``
    receives CV-derived skills exclusively (two_pass.py merges only
    ``llm_cv.skills`` / the deterministic CV pass into it) — LinkedIn and
    GitHub skills live in their OWN fields (``linkedin_skills``,
    ``github_llm_skills``, ``about_me_inferred_skills``), which
    ``skill_tiering`` reads separately. So the semantic side of the engine
    silently searched on a CV-only, 40-skill-truncated view of the user,
    discarding exactly the LinkedIn/GitHub signal the profile pipeline works
    hardest to extract. The old docstring even claimed the opposite
    ("LinkedIn/GitHub data is already merged into cv_data") — true for the
    blob, false for ``cv.skills``.

    Included here: target + held job titles, EVERY skill source, the CV
    summary and raw experience text, LinkedIn positions (title + company +
    description), LinkedIn/GitHub projects, GitHub repo names + descriptions
    + README excerpts (what the user actually BUILDS), education,
    certifications, and the stated preferences.

    NOTHING IS CAPPED OR TRUNCATED — deliberate. There is exactly ONE profile
    per user, and ``encode_job`` chunks long text into 300-word windows and
    MAX-pools the chunk vectors, so length costs a few milliseconds, never
    signal. Truncation here is the same failure this function exists to fix:
    a cap at 40 skills silently dropped everything a rich profile knew. If a
    field describes the candidate, it belongs in the vector.
    """
    cv = getattr(profile, "cv_data", None)
    prefs = getattr(profile, "preferences", None)
    if cv is None and prefs is None:
        return ""

    parts: list[str] = []

    def _add_all(values: Any) -> None:
        for v in values or []:
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())

    if prefs is not None:
        _add_all(getattr(prefs, "target_job_titles", []))
        _add_all(getattr(prefs, "additional_skills", []))
    if cv is not None:
        _add_all(getattr(cv, "job_titles", []))
        _add_all(getattr(cv, "companies", []))
        # EVERY skill source — the CV-derived list is only one of five.
        for field in (
            "skills",
            "linkedin_skills",
            "github_llm_skills",
            "github_skills_inferred",
            "github_frameworks",
            "about_me_inferred_skills",
            "cv_skills_esco",
        ):
            _add_all(getattr(cv, field, []))
        # Facts about the candidate that had a shelf but no consumer until
        # 2026-08-09: where they have worked (cv_industries), which human
        # languages they speak (cv_languages — a real requirement on UK ads),
        # and the domain the CV pass classified them into (career_domain, whose
        # own comment claimed for months that a scorer read it; none did).
        _add_all(getattr(cv, "cv_industries", []))
        _add_all(getattr(cv, "cv_languages", []))
        domain = getattr(cv, "career_domain", "") or ""
        if isinstance(domain, str) and domain.strip():
            parts.append(domain.strip())
        for text_field in (
            "summary", "experience_text", "linkedin_summary",
            "linkedin_headline",
        ):
            v = getattr(cv, text_field, "") or ""
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
        _add_all(getattr(cv, "education", []))
        _add_all(getattr(cv, "certifications", []))
        _add_all(getattr(cv, "achievements", []))
        # Roles actually held — title + company + what they did there.
        for pos in getattr(cv, "linkedin_positions", []) or []:
            if isinstance(pos, dict):
                bits = [str(pos.get(k) or "").strip()
                        for k in ("title", "company", "description")]
                blob = " ".join(b for b in bits if b)
                if blob:
                    parts.append(blob)
        # TWO DIFFERENTLY-SHAPED LISTS, SO TWO KEY SETS. The CV pass writes
        # {name, description, technologies}; the LinkedIn pass writes
        # {title, description}. Reading one shared key set across both meant a
        # CV project contributed only its description — its NAME and its whole
        # technology list, the most matchable part of a project, reached the
        # vector as nothing at all. Concatenating the two lists and reading
        # "title" is exactly the shape of bug this batch keeps finding: one
        # hand-written assumption covering two things that are not the same.
        for proj in getattr(cv, "cv_projects", []) or []:
            if not isinstance(proj, dict):
                continue
            techs = proj.get("technologies")
            tech_txt = (
                " ".join(str(t) for t in techs if isinstance(t, str))
                if isinstance(techs, list)
                else ""
            )
            blob = " ".join(
                b
                for b in (
                    str(proj.get("name") or "").strip(),
                    str(proj.get("description") or "").strip(),
                    tech_txt.strip(),
                )
                if b
            ).strip()
            if blob:
                parts.append(blob)
        for proj in getattr(cv, "linkedin_projects", []) or []:
            if isinstance(proj, dict):
                blob = f"{proj.get('title') or ''} {proj.get('description') or ''}".strip()
                if blob:
                    parts.append(blob)
        # What the user BUILDS — repo prose carries domain signal a skill list
        # cannot ("human-in-the-loop agent", "fraud detection").
        for repo in getattr(cv, "github_repos_brief", []) or []:
            if isinstance(repo, dict):
                name = (repo.get("name") or "").replace("-", " ").strip()
                topics = " ".join(str(t) for t in (repo.get("topics") or []))
                blob = " ".join(b for b in (
                    name, repo.get("description") or "", topics,
                    repo.get("readme_excerpt") or "",
                ) if b).strip()
                if blob:
                    parts.append(blob)
        for gh_text in ("github_bio", "github_profile_readme"):
            v = getattr(cv, gh_text, "") or ""
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
    if prefs is not None:
        _add_all(getattr(prefs, "industries", []))
        _add_all(getattr(prefs, "preferred_locations", []))
        # preferred_workplace dropped: it is derived from work_arrangement, so it
        # only duplicated a token already in the vector text.
        for attr in ("experience_level", "work_arrangement"):
            v = getattr(prefs, attr, None)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
        about = getattr(prefs, "about_me", "") or ""
        if about:
            parts.append(about)

    # De-dup while preserving order — repeated skills add no semantic signal
    # but do eat the chunker's budget.
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return " ".join(out).strip()


def encode_job(
    job: Any,
    enrichment: Any,
    *,
    encoder_factory: Optional[Callable[[], object]] = None,
) -> list[float]:
    """Produce a 384-dim embedding for (job, enrichment).

    Base text: ``title + " | " + requirements_summary + " | " +
    " ".join(required_skills)``. When the full job description exceeds
    ``_CHUNK_SIZE_WORDS`` words, chunk the description with overlap and
    max-pool the per-chunk vectors — the research report's asymmetric
    search trick (short query, long document).

    Args:
        job: a `Job` dataclass — `title` and `description` are read.
        enrichment: a `JobEnrichment` — may be None (degraded mode:
            encode the title alone).
        encoder_factory: optional callable returning a sentence-transformers-
            compatible encoder (anything with `.encode(str) -> list[float]`).
            Tests inject a fake here to avoid the 80 MB model download.

    Returns:
        A `list[float]` of length 384 (plain Python to keep the DB write
        path JSON-safe).
    """
    started_ns = time.perf_counter_ns()
    try:
        encoder = encoder_factory() if encoder_factory else _load_encoder()

        title = (getattr(job, "title", None) or "").strip()
        description = (getattr(job, "description", "") or "").strip()

        if enrichment is None:
            # Degraded mode: title + description (chunked if long), no enriched
            # fields to round things out.
            if not description or len(description.split()) <= _CHUNK_SIZE_WORDS:
                return _encode_text(
                    (f"{title} | {description}").strip(" |") or "job",
                    encoder,
                )
            chunks = _chunk_words(description, _CHUNK_SIZE_WORDS, _CHUNK_OVERLAP_WORDS)
            return _pool_chunk_vectors([_encode_text(f"{title} | {c}", encoder) for c in chunks])

        summary = (getattr(enrichment, "requirements_summary", "") or "").strip()
        required = getattr(enrichment, "required_skills", []) or []
        required_joined = " ".join(required)

        base_text = f"{title} | {summary} | {required_joined}".strip(" |")

        # Chunk the long description (unbounded) rather than the 250-char summary.
        desc_words = description.split()
        if not description or len(desc_words) <= _CHUNK_SIZE_WORDS:
            return _encode_text(base_text or title or "job", encoder)

        chunks = _chunk_words(description, _CHUNK_SIZE_WORDS, _CHUNK_OVERLAP_WORDS)
        chunk_texts = [f"{title} | {summary} | {required_joined} | {c}".strip(" |") for c in chunks]
        chunk_vecs = [_encode_text(t, encoder) for t in chunk_texts]
        return _pool_chunk_vectors(chunk_vecs)
    finally:
        # Step-1 S3 — record encode duration. Stays inert when SEMANTIC_ENABLED
        # is false (CLAUDE.md rule #18). Local import keeps the path cheap.
        if _semantic_enabled():
            from src.utils.telemetry import embeddings_telemetry

            tel = embeddings_telemetry()
            tel.encode_duration_ms += int(max(0, (time.perf_counter_ns() - started_ns) // 1_000_000))


def reset_cache_for_testing() -> None:
    """Discard the cached encoder — tests that swap models call this."""
    global _ENCODER
    _ENCODER = None

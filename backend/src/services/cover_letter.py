"""Tailored cover-letter generation — copy_roadmap.md Tier-2 #4 (core).

Reuses the existing profile LLM provider chain (Gemini->Groq->Cerebras via
``llm_extract``) — the same "expensive half already built" leverage the roadmap
calls out — to generate a per-job cover letter from a candidate summary. This is
the discovery-tool's first step into the *apply* layer (Sprout's wedge,
``References.md`` 1.7).

Scope: the generation core + its service contract. The FastAPI endpoint, the
profile->candidate_summary assembly, and CV (not just cover-letter) tailoring
are the remaining surface — deliberately out of this core so the LLM-facing
logic can be unit-tested with the provider mocked (no live calls).
"""
from __future__ import annotations

from src.models import Job

_SYSTEM = (
    "You are an expert career writer. Write concise, specific, non-generic cover "
    "letters that connect the candidate's real experience to the role. Never "
    "invent facts. Return JSON only."
)

_PROMPT = """\
Write a tailored cover letter for this job application.

ROLE: {title}
COMPANY: {company}
JOB DESCRIPTION:
{description}

CANDIDATE SUMMARY:
{candidate}

Requirements:
- At most {max_words} words.
- Reference specific, relevant points from the candidate summary against the
  job description. Do not invent experience the candidate summary does not state.
- Professional but human tone; no clichés ("I am writing to apply...").
- Return strictly: {{"cover_letter": "<the letter text>"}}
"""


async def generate_cover_letter(
    job: Job,
    candidate_summary: str,
    *,
    max_words: int = 250,
) -> str:
    """Generate a tailored cover letter for ``job`` given ``candidate_summary``.

    Raises ``ValueError`` for missing inputs (job title/company, or an empty
    candidate summary) and ``RuntimeError`` if the LLM returns no usable text.
    The provider chain itself raises ``RuntimeError`` when every provider fails.
    """
    # Job.__post_init__ guarantees a non-empty company ("Unknown" fallback), so
    # only title can legitimately arrive empty.
    if not (job.title or "").strip():
        raise ValueError("job must have a title")
    candidate_summary = (candidate_summary or "").strip()
    if not candidate_summary:
        raise ValueError("candidate_summary must be non-empty")

    # Lazy import mirrors cv_parser — keeps provider SDK imports out of module load.
    from src.services.profile.llm_provider import llm_extract

    prompt = _PROMPT.format(
        title=job.title.strip(),
        company=job.company.strip(),
        description=(job.description or "").strip()[:3000] or "(no description provided)",
        candidate=candidate_summary[:3000],
        max_words=max_words,
    )
    result = await llm_extract(prompt, system=_SYSTEM)
    text = ""
    if isinstance(result, dict):
        text = (result.get("cover_letter") or "").strip()
    if not text:
        raise RuntimeError("LLM returned no cover_letter text")
    return text

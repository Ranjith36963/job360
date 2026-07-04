"""Generation orchestration for tailored CVs / cover letters.

Pure and testable: it takes the already-fetched inputs (CV text, job, fit reason,
per-user examples, universal patterns) and returns the generated document. The
route/storage layer does the DB I/O and passes data in. The LLM call is injectable
(``llm_extract_fn``) so tests never touch a real provider.
"""

from __future__ import annotations

import os
from typing import Awaitable, Callable

from pydantic import BaseModel

from src.services.profile.llm_provider import llm_extract
from src.services.tailoring.integrity import repair_proper_nouns
from src.services.tailoring.prompts import SYSTEM_BY_KIND, build_user_prompt

DOC_KINDS = ("cv", "cover_letter")

# (prompt, system) -> {"document": "..."} ; matches llm_provider.llm_extract.
LlmFn = Callable[[str, str], Awaitable[dict]]


class GeneratedDoc(BaseModel):
    doc_kind: str
    document: str
    model: str = "auto"
    # Proper nouns in the output that match nothing in the source — a possible
    # fabrication for the user to review (guardrail #2). Empty on a clean run.
    flagged_terms: list[str] = []


class EmptyCVError(ValueError):
    """The user has no CV text stored — nothing truthful to reshape."""


async def generate_document(
    *,
    doc_kind: str,
    cv_text: str,
    job_title: str,
    company: str,
    job_description: str,
    fit_reason: str = "",
    user_examples: list[str] | None = None,
    universal_patterns: str = "",
    llm_extract_fn: LlmFn | None = None,
) -> GeneratedDoc:
    """Generate ONE tailored document (``doc_kind`` = 'cv' | 'cover_letter').

    Raises ``ValueError`` on a bad ``doc_kind`` and ``EmptyCVError`` when there is no
    CV text to reshape (guardrail #2 — we never invent a CV from nothing).
    """
    if doc_kind not in DOC_KINDS:
        raise ValueError(f"unknown doc_kind {doc_kind!r}; expected one of {DOC_KINDS}")
    if not (cv_text or "").strip():
        raise EmptyCVError("no CV text on file — upload a CV before tailoring")

    fn: LlmFn = llm_extract_fn or llm_extract
    system = SYSTEM_BY_KIND[doc_kind]
    user_prompt = build_user_prompt(
        doc_kind=doc_kind,
        cv_text=cv_text,
        job_title=job_title,
        company=company,
        job_description=job_description,
        fit_reason=fit_reason,
        user_examples=user_examples,
        universal_patterns=universal_patterns,
    )

    raw = await fn(user_prompt, system)
    document = (raw.get("document") if isinstance(raw, dict) else None) or ""
    document = document.strip()
    if not document:
        raise RuntimeError("LLM returned an empty tailored document")

    # Guardrail #2 (deterministic enforcement): the model can misspell a real name
    # while rewriting ("Monzo" -> "Monox"). Re-spell near-misses back to the source
    # spelling and flag any proper noun that matches nothing in the source at all.
    source_text = "\n".join(p for p in (cv_text, job_title, company, job_description) if p)
    document, flagged = repair_proper_nouns(document, source_text)

    model = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")
    return GeneratedDoc(doc_kind=doc_kind, document=document, model=model, flagged_terms=flagged)

"""Locked prompts for CV / cover-letter tailoring.

Guardrail #2 (spec §4) — **never fabricate** — lives here. The system prompts
forbid inventing anything and are the single source of the feature's trust. Do NOT
loosen them without a deliberate review; a fabricated CV gets a user rejected or
fired.

Every generation returns JSON ``{"document": "<plain text>"}`` so it rides the
existing JSON-mode LLM providers (llm_provider forces ``response_format=json_object``).
"""

from __future__ import annotations

# ── System prompts — the guardrail. Reshape TRUE content only; never invent. ──

_ATS_RULES = (
    "Output ATS-friendly PLAIN TEXT only: no tables, no columns, no text boxes, no "
    "graphics, no markdown symbols for layout. Use simple lines and standard section "
    "headers (e.g. SUMMARY, EXPERIENCE, SKILLS, EDUCATION). Applicant-tracking "
    "software must parse it cleanly."
)

_NEVER_FABRICATE = (
    "ABSOLUTE RULE — NEVER FABRICATE. You may ONLY reorder, rephrase, tighten, and "
    "emphasise facts that are EXPLICITLY present in the candidate's source CV. You "
    "MUST NOT invent, add, or exaggerate any skill, employer, job title, date, "
    "degree, certification, metric, or achievement. If the job wants something the "
    "candidate does not have, DO NOT add it — leave it out. Reshape the truth to fit "
    "the job; never manufacture it."
)

CV_SYSTEM = (
    "You are an expert CV editor. You tailor an existing CV to one specific job by "
    "reordering and rewording the candidate's REAL experience so the most relevant "
    "parts come first and use the job's language.\n\n"
    f"{_NEVER_FABRICATE}\n\n"
    f"{_ATS_RULES}\n\n"
    "Keep it truthful, concise, and in the candidate's voice. Return JSON: "
    '{"document": "<the full tailored CV as plain text>"}.'
)

COVER_LETTER_SYSTEM = (
    "You are an expert cover-letter writer. You write a short, specific cover letter "
    "for one job, grounded ONLY in the candidate's real CV and the job description.\n\n"
    f"{_NEVER_FABRICATE}\n\n"
    "3-4 tight paragraphs, warm but professional, no clichés, no invented enthusiasm "
    "about facts that aren't there. Address why the candidate's REAL background fits "
    "THIS job.\n\n"
    f"{_ATS_RULES}\n\n"
    'Return JSON: {"document": "<the full cover letter as plain text>"}.'
)

SYSTEM_BY_KIND = {"cv": CV_SYSTEM, "cover_letter": COVER_LETTER_SYSTEM}


def build_user_prompt(
    *,
    doc_kind: str,
    cv_text: str,
    job_title: str,
    company: str,
    job_description: str,
    fit_reason: str = "",
    user_examples: list[str] | None = None,
    universal_patterns: str = "",
) -> str:
    """Assemble the user prompt.

    Layer 2 (per-user, spec §6): ``user_examples`` are the user's own past KEPT
    polished docs of this kind — few-shot "write like me" signal. Layer 1
    (universal): ``universal_patterns`` is a privacy-scrubbed structural summary
    used mainly for cold-start (no per-user history yet).
    """
    what = "CV" if doc_kind == "cv" else "cover letter"
    parts: list[str] = [
        f"Tailor a {what} for this job, using ONLY what is true in the candidate's "
        f"source CV below.",
        "",
        f"=== JOB: {job_title} at {company} ===",
        (job_description or "(no description provided)").strip(),
    ]
    if fit_reason:
        # The judge (E4) already computed *why* this job fits — tells us what to lead with.
        parts += ["", "=== WHY THIS CANDIDATE FITS (emphasise these) ===", fit_reason.strip()]

    parts += ["", "=== CANDIDATE'S SOURCE CV (the ONLY facts you may use) ===",
              (cv_text or "").strip()]

    if universal_patterns:
        parts += ["", "=== GENERAL GOOD-TAILORING PATTERNS (structure guidance only) ===",
                  universal_patterns.strip()]

    if user_examples:
        # Layer 2 — the user's own voice. Match their style, not the content.
        parts += ["", "=== HOW THIS USER WRITES THEIR " + what.upper() +
                  " (match their tone/style, not their old content) ==="]
        for i, ex in enumerate(user_examples, 1):
            snippet = (ex or "").strip()
            if len(snippet) > 2000:  # keep few-shot within weak-model context windows
                snippet = snippet[:2000] + "…"
            parts += [f"--- example {i} ---", snippet]

    parts += ["", "Now produce the tailored " + what + ". Remember: reshape the "
              "truth, never invent. Return JSON {\"document\": \"...\"}."]
    return "\n".join(parts)

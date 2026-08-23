"""Per-User AI CV & Cover Letter tailoring (docs/product/peruser_cv_coverletter.md).

Turns a user's ONE stored CV + a job's description + the judge's fit reason into a
CV and cover letter tailored to that job, then learns from the user's edits.

Guardrails (spec §4) are enforced here:
  - #2 never fabricate  -> locked system prompts in ``prompts.py``
  - #5 ATS-friendly     -> plain-text output + ``pdf.py`` renders a machine-readable PDF
  - §6 2-layer learning -> per-user few-shot (Layer 2) + universal patterns (Layer 1)
  - §7 privacy          -> universal layer stores structural PATTERNS only, no content
"""

from src.services.tailoring.generator import (
    DOC_KINDS,
    GeneratedDoc,
    generate_document,
)

__all__ = ["DOC_KINDS", "GeneratedDoc", "generate_document"]

"""Rendering and checking of a tailored CV / cover letter.

Decision 28 (2026-09-21, slice A): Job360 has no brain of its own. The user's
agent writes the tailored CV and cover letter and saves the text with
``save_artifact``; this package never calls an LLM. What is left is the part a
browser cannot do for itself:

  - ``docx.py`` / ``pdf.py``  — ATS-friendly rendering of saved text
  - ``provenance.py``        — which lines are grounded in the user's own CV
  - ``integrity.py``         — proper nouns that match nothing in the source
  - ``patterns.py``          — structure only (no content), the §7 privacy line

``generator.py`` and ``prompts.py`` were deleted with the tailor's LLM.
"""

# The two document kinds the tailor routes render. A subset of
# ``settings.APPLICATION_ARTIFACT_KINDS`` — "answers" and "outreach" are saved
# and versioned like any artifact, but nothing renders them as a document.
DOC_KINDS = ("cv", "cover_letter")

__all__ = ["DOC_KINDS"]

"""The fit record (feedback slice 5): validation of what the assistant saves
with ``save_fit`` and the ONE builder of the ``fit`` dict every reader returns.

Job360 stores the assistant's verdict; it never computes one (VISION rule 4).
A score always carries a reason (``verdict``) and a source label; an unscored
fit stays NULL, never 0 - career-ops ``rank-pipeline.mjs:8-13, :53, :103``
(a score without a usable reason is not written; ADAPTED: refuse with a 422
because the caller is live and can fix it) and ``modes/pipeline.md:108-113``
(an unranked row simply has no usable annotation).

Pure functions, no DB. Imports ``SpineError`` from spine; spine imports this
module lazily inside functions (the same pattern as ``visa``).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from src.core import settings
from src.services.applications.spine import SpineError


def clean_items(raw: Optional[list[Any]], *, field: str, max_items: int) -> list[dict[str, Any]]:
    """Normalise a gaps / jd_requirements list to ``[{"text", "kind"}]``.

    An item is a plain string (kind None - old callers keep working; not
    length-capped, empty strings dropped as before) or a dict with exactly
    ``text`` (1..FIT_GAP_TEXT_MAX_CHARS after strip) and an optional ``kind``
    (None or a member of FIT_GAP_KINDS). Anything else is a 422.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SpineError(422, f"{field} must be a list")
    if len(raw) > max_items:
        raise SpineError(422, f"{field}: at most {max_items} items")
    kinds = list(settings.FIT_GAP_KINDS)
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            if item.strip():
                out.append({"text": item, "kind": None})
            continue
        if not isinstance(item, dict):
            raise SpineError(422, f'{field}[{i}]: must be a string or {{"text", "kind"}} (kind one of {kinds})')
        extra = sorted(set(item) - {"text", "kind"})
        if extra:
            raise SpineError(422, f"{field}[{i}]: unknown key {extra[0]!r}; allowed: text, kind")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise SpineError(422, f"{field}[{i}]: text must be a non-empty string")
        text = text.strip()
        if len(text) > settings.FIT_GAP_TEXT_MAX_CHARS:
            raise SpineError(422, f"{field}[{i}]: text is at most {settings.FIT_GAP_TEXT_MAX_CHARS} characters")
        kind = item.get("kind")
        if kind is not None and kind not in settings.FIT_GAP_KINDS:
            raise SpineError(422, f"{field}[{i}]: kind must be one of {kinds} or left out")
        out.append({"text": text, "kind": kind})
    return out


def validate_fit_record(
    *,
    score: Optional[int],
    verdict: Optional[str],
    score_source: Optional[str],
    gaps: Optional[list[Any]],
    jd_requirements: Optional[list[Any]],
    archetype: Optional[str],
    remote_policy: Optional[str],
    team_size: Optional[int],
    via: Optional[str],
) -> dict[str, Any]:
    """Check one save_fit call; first failure wins. Returns the clean values
    (``gaps`` / ``jd_requirements`` as item dicts, ``archetype`` stripped or
    None). Raises ``SpineError(422)``. Nothing is written here."""
    cap = settings.FIT_SCORE_REASON_MAX_CHARS
    sources = tuple(settings.FIT_SCORE_SOURCES)
    if score is not None:
        if verdict is None or not verdict.strip():
            raise SpineError(
                422, f"a score needs a reason: pass verdict, one line of 1-{cap} characters",
            )
        if len(verdict.strip()) > cap:
            raise SpineError(422, f"with a score, verdict (the reason) is at most {cap} characters")
        if score_source not in sources:
            raise SpineError(422, f"a score needs score_source: one of {sources}")
    elif score_source is not None:
        raise SpineError(422, "score_source needs a score; leave both out for an unscored fit")
    if remote_policy is not None and remote_policy not in settings.FIT_REMOTE_POLICIES:
        raise SpineError(422, f"remote_policy must be one of {tuple(settings.FIT_REMOTE_POLICIES)}")
    if via is not None and via not in settings.FIT_VIA:
        raise SpineError(422, f"via must be one of {tuple(settings.FIT_VIA)}")
    clean_archetype: Optional[str] = None
    if archetype is not None:
        clean_archetype = archetype.strip() or None
        if clean_archetype is not None and len(clean_archetype) > settings.FIT_ARCHETYPE_MAX_CHARS:
            raise SpineError(422, f"archetype is at most {settings.FIT_ARCHETYPE_MAX_CHARS} characters")
    if team_size is not None:
        if isinstance(team_size, bool) or not isinstance(team_size, int):
            raise SpineError(422, "team_size must be a whole number of people")
        if team_size < 1 or team_size > settings.FIT_TEAM_SIZE_MAX:
            raise SpineError(422, f"team_size must be between 1 and {settings.FIT_TEAM_SIZE_MAX}")
    return {
        "gaps": clean_items(gaps, field="gaps", max_items=50),
        "jd_requirements": clean_items(
            jd_requirements, field="jd_requirements", max_items=settings.FIT_JD_REQUIREMENTS_MAX,
        ),
        "archetype": clean_archetype,
        "remote_policy": remote_policy,
        "team_size": team_size,
        "via": via,
    }


def _loads_list(raw: Any) -> list[Any]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return val if isinstance(val, list) else []


def fit_view(app_row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """THE builder of the ``fit`` dict (None when no fit was ever saved).
    ``gaps`` stays a list of texts for old readers; ``gaps_detail`` carries
    the kinds (a fit saved before migration 0064 reads back from ``fit_gaps``
    with kind None)."""
    if not app_row.get("fit_recorded_at"):
        return None
    gaps = _loads_list(app_row.get("fit_gaps"))
    detail_raw = app_row.get("fit_gaps_detail")
    if detail_raw:
        gaps_detail = _loads_list(detail_raw)
    else:
        gaps_detail = [{"text": g, "kind": None} for g in gaps]
    return {
        "score": app_row.get("fit_score"),
        "verdict": app_row.get("fit_verdict"),
        "score_source": app_row.get("fit_score_source"),
        "gaps": gaps,
        "gaps_detail": gaps_detail,
        "jd_requirements": _loads_list(app_row.get("fit_jd_requirements")),
        "reasoning": app_row.get("fit_reasoning"),
        "axes": _loads_list(app_row.get("fit_axes")),
        "archetype": app_row.get("fit_archetype"),
        "remote_policy": app_row.get("fit_remote_policy"),
        "team_size": app_row.get("fit_team_size"),
        "via": app_row.get("fit_via"),
        "recorded_by": app_row.get("fit_recorded_by") or "",
        "recorded_at": app_row.get("fit_recorded_at") or "",
    }

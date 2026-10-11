"""Refused calls: a CLOSED reason code for the audit trail, and the self-correcting message.

Owner decision 2026-10-10 ("never lose an answer"):

* every refused ``update_profile`` / ``save_fit`` / ``record_event`` is audited with a
  closed ``reason`` code - the path and the code, NEVER the value;
* a refused ``update_profile`` says ``Nothing was saved.``, then per bad path the rule and
  a valid example (from ``shapes.SHAPES``, never the caller's value), then the full list of
  paths to send again - because a retry that leaves out the good paths loses them.
"""
from __future__ import annotations

from typing import Any, Iterable

from src.services.profile import shapes

REASONS: frozenset[str] = frozenset({
    "unknown_key", "bad_type", "bad_value", "out_of_range", "retired_path",
    "setup_round_missing_answers", "years_order", "too_long", "validation_error",
    "rate_limited",
})

# Pydantic error `type` -> reason.
_PYDANTIC_REASONS: dict[str, str] = {
    "extra_forbidden": "unknown_key",
    "string_too_long": "too_long", "too_long": "too_long",
    "greater_than": "out_of_range", "greater_than_equal": "out_of_range", "less_than": "out_of_range",
    "less_than_equal": "out_of_range", "string_too_short": "out_of_range", "too_short": "out_of_range",
    "literal_error": "bad_value", "enum": "bad_value", "value_error": "bad_value",
    "string_pattern_mismatch": "bad_value",
}


def reason_for_pydantic(errors: Iterable[Any]) -> str:
    """The reason for the FIRST pydantic error (``missing`` and ``*_type`` -> bad_type)."""
    for err in errors:
        kind = str(err.get("type", ""))
        if kind in _PYDANTIC_REASONS:
            return _PYDANTIC_REASONS[kind]
        if kind == "missing" or kind.endswith("_type") or kind.endswith("_parsing"):
            return "bad_type"
        return "validation_error"
    return "validation_error"


def reason_for_message(detail: str) -> str:
    """Classify a refusal message by its wording (the validators' own phrases)."""
    text = detail.lower()
    if "retired" in text:
        return "retired_path"
    if "unknown key" in text or "not an editable path" in text or "extra inputs" in text:
        return "unknown_key"
    if "must not be above" in text:
        return "years_order"
    if "limit" in text or "exceeds" in text or "at most" in text or "too long" in text:
        return "too_long"
    if "must be a whole number from" in text or "between" in text or "must be >=" in text or "from 0 to" in text:
        return "out_of_range"
    if " must be a " in text or " must be an " in text or "got " in text:
        return "bad_type"
    if "must be" in text or "not one of" in text or "contradicts" in text or "needs " in text:
        return "bad_value"
    return "validation_error"


def reason_for_http(detail: Any, explicit: str = "", status: int = 422) -> str:
    """``explicit`` if it is a known code, else 429 -> rate_limited, else classify ``detail``."""
    if explicit in REASONS:
        return explicit
    if status == 429:
        return "rate_limited"
    return reason_for_message(detail if isinstance(detail, str) else str(detail))


_VERBS = frozenset({"is", "must", "exceeds", "encodes", "needs", "may", "looks"})


def _rule_text(path: str, detail: str) -> str:
    """The validator's sentence, without the leading path (it is printed once already)."""
    for prefix in (f"{path}: ", f"{path} "):
        if detail.startswith(prefix):
            detail = detail[len(prefix):]
            if detail.split(" ", 1)[0] in _VERBS:  # "is not one of..." reads as "value is not one of..."
                detail = f"value {detail}"
            break
    return detail.rstrip(". ")


def self_correcting_message(
    all_paths: list[str], problems: list[tuple[str, str]], *, extra_example: dict[str, str] | None = None
) -> str:
    """The 422 body for a refused multi-path call.

    ``all_paths`` = every path in the call (good and bad, call order, no repeats);
    ``problems`` = ``[(path, rule sentence)]`` for the bad ones. Nothing here quotes a submitted value.
    """
    lines = ["Nothing was saved."]
    for path, detail in problems:
        line = f"{path}: {_rule_text(path, detail)}."
        example = (extra_example or {}).get(path) or shapes.example_json(path)
        if example is not None:
            line += f" Example: {example}"
            note = (shapes.shape_for(path) or {}).get("note")
            if note:
                line += f" ({note})"
        lines.append(line)
    seen: list[str] = []
    for path in all_paths:
        if path not in seen:
            seen.append(path)
    lines.append(f"Fix it and resend ALL {len(seen)} paths in one call: {', '.join(seen)}")
    return "\n".join(lines)

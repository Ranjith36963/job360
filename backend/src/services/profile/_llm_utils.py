"""Shared defensive coercion helpers for LLM JSON output.

Weaker models (Cerebras llama3.1-8b, Groq llama-3.3-70b) occasionally
return strings, None, dicts, or mixed-type lists for fields that were
requested as list[str]. These helpers normalise such output without
raising, so any parser layered on top of llm_provider.llm_extract()
can rely on clean, typed values.
"""

from __future__ import annotations

from typing import Any


def coerce_str_list(value: Any) -> list[str]:
    """Coerce LLM output to a clean list[str]. Never raises."""
    if value is None:
        return []
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    if isinstance(value, dict):
        return [str(v) for v in value.values() if v]
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    result.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("name") or item.get("skill") or item.get("title")
                if name:
                    result.append(str(name))
            elif item is not None:
                result.append(str(item))
        return result
    return []


def coerce_str(value: Any) -> str:
    """Coerce LLM output to a string. Returns '' for wrong types."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        return ""
    return str(value)

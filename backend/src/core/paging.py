"""Small paging helpers."""
from __future__ import annotations

from typing import Sequence, TypeVar

T = TypeVar("T")


def page_window(items: Sequence[T], page: int, per_page: int) -> list[T]:
    """Return the items on a 1-based ``page`` of size ``per_page``."""
    if page < 1 or per_page < 1:
        return []
    start = (page - 1) * per_page
    end = start + per_page - 1
    return list(items[start:end])

"""One request's shared reads for the Needs-you badge (``GET /api/needs-you-count``, 2026-11-17).

On a large account the badge redid the same profile work per open question row, per fill and per queued
country: ``kit.compute_answers`` (b_derive, b_flags and answer_once each ran it per country), the saved-answer
reading ``ready._approved_record`` (b_derive, no memo) and ``ask_once.memory_question`` (the whole question
catalog normalised for every row). They all read ONE profile, so :func:`scope` holds their answers for the
life of ONE request: a ``ContextVar`` set by ``needs_you_count`` and reset when it returns (the same idea as
``utils.server_timing.TIMINGS``), so nothing survives into the next request and every caller outside it
(MCP tools, the card, check_form) computes exactly as before.

Safe because: results are keyed on the IDENTITY of the profile / overlay objects (the entry keeps a reference
to both, so an ``id()`` can never be reused for another object while the scope lives) and no reader of them
mutates them (read-only: ``ready._answer_rows`` / ``_user_check``, ``common_questions._row``,
``kit._memory_answer`` build new dicts; pinned by ``tests/test_needs_you_derive_memo.py``).
It holds the loaded profile in memory for the request only and logs nothing.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

ENABLED = True  # tests flip it to compare with the un-memoised reading


@dataclass
class Memo:
    # (id(profile), id(overlay), country, company_key, job_country_only) -> (profile, overlay, result)
    answers: dict[tuple[Any, ...], tuple[Any, Any, Any]] = field(default_factory=dict)
    # id(profile) -> (profile, ready._approved_record's memo dict for that one base profile)
    approved: dict[int, tuple[Any, dict[Any, Any]]] = field(default_factory=dict)
    # (key, question) -> ask_once.memory_question's answer (an immutable tuple)
    memory_q: dict[tuple[Optional[str], str], tuple[bool, Optional[str]]] = field(default_factory=dict)
    # blockers' one profile read of this request: (profile or None, overlay rows); None = it read none
    loaded: Optional[tuple[Any, list[dict[str, Any]]]] = None


MEMO: ContextVar[Optional[Memo]] = ContextVar("needs_you_derive_memo", default=None)


def current() -> Optional[Memo]:
    """The active request's memo; None outside :func:`scope` (or with :data:`ENABLED` off)."""
    return MEMO.get() if ENABLED else None


@contextmanager
def scope() -> Iterator[Memo]:
    memo = Memo()
    token = MEMO.set(memo)
    try:
        yield memo
    finally:
        MEMO.reset(token)


def answers_for(
    profile: Any, overlay: Any, country: Optional[str], company_key: Optional[str], job_country_only: bool,
    compute: Callable[[], Any],
) -> Any:
    """``compute()`` (a real ``kit.compute_answers`` run) at most once per profile / overlay / place in a scope."""
    memo = current()
    if memo is None:
        return compute()
    key = (id(profile), id(overlay), country, company_key, job_country_only)
    hit = memo.answers.get(key)
    if hit is None:
        hit = memo.answers[key] = (profile, overlay, compute())
    return hit[2]


def approved_memo(profile: Any) -> Optional[dict[Any, Any]]:
    """The ``memo=`` for ``ready._approved_record`` over ONE base profile (None outside a scope: no memo)."""
    memo = current()
    if memo is None:
        return None
    hit = memo.approved.get(id(profile))
    if hit is None:
        hit = memo.approved[id(profile)] = (profile, {})
    return hit[1]


def memory_question(key: Optional[str], question: str) -> tuple[bool, Optional[str]]:
    """``ask_once.memory_question`` read once per distinct (key, question) in a scope: each real call
    normalises the whole question catalog."""
    from src.services.profile.ask_once import memory_question as real  # noqa: PLC0415 - profile imports spine

    memo = current()
    if memo is None:
        return real(key, question)
    pair = (key, question)
    if pair not in memo.memory_q:
        memo.memory_q[pair] = real(key, question)
    return memo.memory_q[pair]

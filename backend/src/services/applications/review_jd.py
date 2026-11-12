"""The ad's requirements in a ``cv_reviewed`` (CV benchmark, owner 2026-11-10): the reviewer judges meaning, Job360
checks only what needs no understanding.

The reviewer sends an optional ``jd`` list ``[{term, must, have, evidence, n}]``: each requirement of the frozen ad
(``applications.job_description_snapshot``), whether it is a must-have, whether the PROFILE supports it (its judgment),
0-3 profile quotes that show it, and the review line numbers (``review.lines``, from 1) where the CV names it. Per
item (number ``i`` from 1, stored in ``where``), with :class:`cv_coverage.Tokens` (literal, no regex on caller text):

* ``jd_term_not_in_ad``: the term is not word for word in the ad (nothing else is checked for it);
* have=true: ``jd_evidence_not_in_profile`` unless there is evidence and every quote lies in ONE profile field or
  line (``cv_review.quote_in_profile``); ``jd_term_not_in_cv`` when it names lines and none says the term. It is
  COVERED when both hold and it names a line; ``n: []`` = had but not used (honest, it only lowers coverage);
* have=false: ``jd_gap_claimed`` when the CV names it anywhere (:meth:`Tokens.claims`, looser than ``find``: a CV
  never claims a requirement the profile lacks); else it is a gap, stored in the ad's own words for ``stats``.

Coverage = covered must-haves / had must-haves; below ``settings.CV_REVIEW_JD_BAR`` (0.8) = ``jd_coverage_low``.
Ad text but no ``jd`` (or ``[]``) = ``jd_not_judged`` (weak). No ad text = ``jd_no_ad``: stored, never weak, so the
caller can ask the user for the ad.
"""
from __future__ import annotations

from typing import Any

from src.core import settings

JD_KEYS = frozenset({"term", "must", "have", "evidence", "n"})
CODES = ("jd_not_judged", "jd_term_not_in_ad", "jd_evidence_not_in_profile", "jd_term_not_in_cv", "jd_gap_claimed",
         "jd_coverage_low")
JD_MAX_ITEMS, TERM_MAX_CHARS, QUOTES_MAX, QUOTE_MAX_CHARS, LINES_MAX = 40, 80, 3, 300, 5
_SHAPE = "{term, must, have, evidence, n}"


def shape_problems(jd: Any, max_line: int) -> list[str]:
    """Every shape problem of an optional ``jd`` (422); a well-formed one is judged, never refused."""
    if jd is None:
        return []
    if not isinstance(jd, list) or len(jd) > JD_MAX_ITEMS:
        return [f"jd must be a list of at most {JD_MAX_ITEMS} {_SHAPE}"]
    problems: list[str] = []
    for i, item in enumerate(jd):
        if not isinstance(item, dict) or set(item) != JD_KEYS:
            problems.append(f"jd[{i}] takes exactly {_SHAPE}")
            continue
        term, quotes, ns = item["term"], item["evidence"], item["n"]
        if not isinstance(term, str) or not 1 <= len(term.strip()) <= TERM_MAX_CHARS:
            problems.append(f"jd[{i}].term must be the ad's words (1-{TERM_MAX_CHARS} characters)")
        if not isinstance(item["must"], bool) or not isinstance(item["have"], bool):
            problems.append(f"jd[{i}].must and .have must be true or false")
        if not isinstance(quotes, list) or len(quotes) > QUOTES_MAX or not all(
                isinstance(q, str) and 3 <= len(q.strip()) <= QUOTE_MAX_CHARS for q in quotes):
            problems.append(f"jd[{i}].evidence must be 0-{QUOTES_MAX} profile quotes of 3-{QUOTE_MAX_CHARS} characters")
        if not isinstance(ns, list) or len(ns) > LINES_MAX or not all(
                isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= max_line for n in ns):
            problems.append(f"jd[{i}].n must be 0-{LINES_MAX} review.lines numbers (1-{max_line})")
    return problems


def judge(jd: Any, ad_text: str, lines: list[str], cv_text: str, profile: Any) -> tuple[dict[str, list[Any]],
                                                                                      dict[str, Any]]:
    """``(where, stored)``: the codes with their jd item numbers, and the event's ``jd`` block (state, counts, gaps;
    never CV or profile text, only the ad's own words for a gap)."""
    from src.services.applications import cv_review  # noqa: PLC0415 - cv_review imports this module lazily
    from src.services.applications.cv_coverage import Tokens  # noqa: PLC0415

    where: dict[str, list[Any]] = {}
    if not ad_text.strip():
        return where, {"state": "jd_no_ad"}
    if not jd:
        return {"jd_not_judged": []}, {"state": "jd_not_judged"}
    ad, cv = Tokens(ad_text), Tokens(cv_text)
    source, source_words = cv_review.quote_sources(profile)
    must = covered = 0
    gaps: list[str] = []
    for i, item in enumerate(jd, 1):
        term = item["term"]
        ad_words = ad.find(term)
        if not ad_words:
            where.setdefault("jd_term_not_in_ad", []).append(i)
            continue
        if not item["have"]:
            if cv.claims(term):
                where.setdefault("jd_gap_claimed", []).append(i)
            if ad_words not in gaps:
                gaps.append(ad_words)
            continue
        quotes = item["evidence"]
        shown = bool(quotes) and all(cv_review.quote_in_profile(q, source, source_words) for q in quotes)
        if not shown:
            where.setdefault("jd_evidence_not_in_profile", []).append(i)
        named = [n for n in item["n"] if n <= len(lines) and Tokens(lines[n - 1]).find(term)]
        if item["n"] and not named:
            where.setdefault("jd_term_not_in_cv", []).append(i)
        if item["must"]:
            must += 1
            covered += int(shown and bool(named))
    bar = settings.CV_REVIEW_JD_BAR
    if must and covered < bar * must - 1e-9:
        where["jd_coverage_low"] = []
    return where, {"state": "judged", "must": must, "covered": covered, "bar": bar, "gaps": gaps}

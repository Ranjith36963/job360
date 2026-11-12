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
Checker 2026-11-12: one item per requirement (a duplicate term, :func:`cv_coverage.same_term`, is a 422, so copies of
a covered term cannot dilute a miss); no must-have item = ``jd_no_must``; fewer than min(3, save_fit's
``jd_requirements``) items = ``jd_too_few``; terms compare with :meth:`Tokens.match` (a plural / split term folds).
Skill words (production 2026-11-12, owner: "Python coding and Python language are the same capability"): reviewers
copied whole ad lines ("Python – Strong knowledge"), so a CV saying "Python" covered nothing. The ad check stays on the
FULL term; the CV checks (named line, gap claimed), duplicates and the stored gap use :func:`core`, the term cut at a
spaced dash, "(" or ":" with closed :data:`FILLER` words trimmed at its ends; a core listing several skills ("RAG and
Vector Databases", "FastAPI / REST API") is in a CV line when ANY of them is (:func:`parts`). An empty core or one of
more than 4 words is a 422. The judged list is stored as ``terms`` ``[{term: core, must}]`` (the ad's words); a
re-review of the application must list every earlier must-term again as a must, else ``jd_terms_dropped`` (apps 793,
150 shrank the list to pass). Ad text but no ``jd`` (or ``[]``) = ``jd_not_judged`` (weak). No ad text =
``jd_no_ad``: stored, never weak, so the caller can ask the user for the ad.
"""
from __future__ import annotations

from typing import Any

from src.core import settings

JD_KEYS = frozenset({"term", "must", "have", "evidence", "n"})
CODES = ("jd_not_judged", "jd_no_must", "jd_too_few", "jd_term_not_in_ad", "jd_evidence_not_in_profile",
         "jd_term_not_in_cv", "jd_gap_claimed",
         "jd_coverage_low", "jd_terms_dropped")
JD_MAX_ITEMS, TERM_MAX_CHARS, QUOTES_MAX, QUOTE_MAX_CHARS, LINES_MAX = 40, 80, 3, 300, 5
MIN_ITEMS = 3  # with requirements saved by save_fit: at least min(this, how many were saved)
_SHAPE = "{term, must, have, evidence, n}"
CORE_MAX_WORDS = 4
FILLER = frozenset((
    "experience", "experienced", "with", "in", "of", "on", "for", "knowledge", "strong", "good", "solid", "deep",
    "hands-on", "hands", "working", "proficiency", "proficient", "familiarity", "familiar", "understanding",
    "expertise", "skills", "skill", "coding", "programming", "language", "languages", "development", "preferred",
    "required", "ability", "able", "to", "a", "an", "the", "using", "use"))
_DASHES = frozenset(("-", "–", "—"))
_JOINS = frozenset(("and", "or", "&", "/"))
_SENTENCE = "jd[{i}] is a sentence, not one requirement: give the skill's own words from the ad (1-4 words)"


def core(term: Any) -> str:
    """The term's skill words: cut at the first spaced dash, "(" or ":", then the :data:`FILLER` words trimmed at
    both ends ("Python – Strong knowledge" / "Strong Python programming skills" -> "Python"). Word by word."""
    words: list[str] = []
    for w in (term if isinstance(term, str) else "").split():
        if w in _DASHES:
            break
        cut = min((k for k in (w.find("("), w.find(":")) if k >= 0), default=-1)
        if cut >= 0:
            if w[:cut]:
                words.append(w[:cut])
            break
        words.append(w)

    def filler(w: str) -> bool:
        return w.casefold().strip(".,;!?") in FILLER

    while words and filler(words[0]):
        words.pop(0)
    while words and filler(words[-1]):
        words.pop()
    return " ".join(words)


def parts(core_term: str) -> list[str]:
    """The skills a core lists ("RAG and Vector Databases" -> RAG, Vector Databases): split at and / or / & / "/"
    and a trailing comma; one skill is itself."""
    out: list[list[str]] = [[]]
    for w in core_term.replace("/", " / ").split():
        bare = w.rstrip(",;")
        if bare.casefold() in _JOINS:
            out.append([])
            continue
        if bare:
            out[-1].append(bare)
        if bare != w:
            out.append([])
    return [" ".join(p) for p in out if p]


def _words(core_term: str) -> int:
    return sum(1 for w in core_term.split() if any(c.isalnum() for c in w))


def shape_problems(jd: Any, max_line: int) -> list[str]:
    """Every shape problem of an optional ``jd`` (422); a well-formed one is judged, never refused."""
    if jd is None:
        return []
    if not isinstance(jd, list) or len(jd) > JD_MAX_ITEMS:
        return [f"jd must be a list of at most {JD_MAX_ITEMS} {_SHAPE}"]
    from src.services.applications.cv_coverage import same_term  # noqa: PLC0415

    problems: list[str] = []
    terms: list[tuple[int, str]] = []
    for i, item in enumerate(jd):
        if not isinstance(item, dict) or set(item) != JD_KEYS:
            problems.append(f"jd[{i}] takes exactly {_SHAPE}")
            continue
        term, quotes, ns = item["term"], item["evidence"], item["n"]
        if not isinstance(term, str) or not 1 <= len(term.strip()) <= TERM_MAX_CHARS:
            problems.append(f"jd[{i}].term must be the ad's words (1-{TERM_MAX_CHARS} characters)")
        elif not 1 <= _words(core(term)) <= CORE_MAX_WORDS:  # 2026-11-12: a whole ad line is no requirement
            problems.append(_SENTENCE.format(i=i))
        else:  # checker D1: a copy of a covered term would dilute a miss; one item per requirement (by core)
            first = next((j for j, seen in terms if same_term(seen, core(term))), None)
            if first is None:
                terms.append((i, core(term)))
            else:
                problems.append(f"jd[{i}] is a duplicate of jd[{first}] (one item per requirement)")
        if not isinstance(item["must"], bool) or not isinstance(item["have"], bool):
            problems.append(f"jd[{i}].must and .have must be true or false")
        if not isinstance(quotes, list) or len(quotes) > QUOTES_MAX or not all(
                isinstance(q, str) and 3 <= len(q.strip()) <= QUOTE_MAX_CHARS for q in quotes):
            problems.append(f"jd[{i}].evidence must be 0-{QUOTES_MAX} profile quotes of 3-{QUOTE_MAX_CHARS} characters")
        if not isinstance(ns, list) or len(ns) > LINES_MAX or not all(
                isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= max_line for n in ns):
            problems.append(f"jd[{i}].n must be 0-{LINES_MAX} review.lines numbers (1-{max_line})")
    return problems


def judge(jd: Any, ad_text: str, lines: list[str], cv_text: str, profile: Any, saved: int = 0,
          earlier: Any = None) -> tuple[dict[str, list[Any]], dict[str, Any]]:
    """``(where, stored)``: the codes with their jd item numbers, and the event's ``jd`` block (state, counts, gaps,
    terms; never CV or profile text, only the ad's own words). ``saved`` = how many requirements save_fit holds;
    ``earlier`` = the ``terms`` of this application's earlier reviews (each must-term must come back as a must)."""
    from src.services.applications import cv_review  # noqa: PLC0415 - cv_review imports this module lazily
    from src.services.applications.cv_coverage import Tokens, same_term  # noqa: PLC0415

    where: dict[str, list[Any]] = {}
    if not ad_text.strip():
        return where, {"state": "jd_no_ad"}
    if not jd:
        return {"jd_not_judged": []}, {"state": "jd_not_judged"}
    items: list[tuple[int, dict[str, Any]]] = []
    for i, item in enumerate(jd, 1):  # each distinct term counts once (shape_problems refuses a duplicate)
        if not any(same_term(core(seen["term"]), core(item["term"])) for _, seen in items):
            items.append((i, item))
    if not any(item["must"] for _, item in items):  # checker D2: the reviewer names the ad's must-haves
        where["jd_no_must"] = []
    need = min(MIN_ITEMS, max(saved, 0))
    if len(items) < need:
        where["jd_too_few"] = [f"{len(items)} items, at least {need} (save_fit's jd_requirements)"]
    ad, cv = Tokens(ad_text), Tokens(cv_text)
    source, source_words = cv_review.quote_sources(profile)
    must = covered = 0
    gaps: list[str] = []
    terms: list[dict[str, Any]] = []
    for i, item in items:
        term = item["term"]
        if not ad.match(term):
            where.setdefault("jd_term_not_in_ad", []).append(i)
            continue
        skill = core(term) or term
        ad_words, skills = ad.match(skill) or skill, parts(skill) or [skill]
        terms.append({"term": ad_words, "must": bool(item["must"])})
        if not item["have"]:
            if any(cv.claims(s) for s in skills):
                where.setdefault("jd_gap_claimed", []).append(i)
            if ad_words not in gaps:
                gaps.append(ad_words)
            continue
        quotes = item["evidence"]
        shown = bool(quotes) and all(cv_review.quote_in_profile(q, source, source_words) for q in quotes)
        if not shown:
            where.setdefault("jd_evidence_not_in_profile", []).append(i)
        named = [n for n in item["n"] if n <= len(lines) and any(Tokens(lines[n - 1]).match(s) for s in skills)]
        if item["n"] and not named:
            where.setdefault("jd_term_not_in_cv", []).append(i)
        if item["must"]:
            must += 1
            covered += int(shown and bool(named))
    bar = settings.CV_REVIEW_JD_BAR
    if must and covered < bar * must - 1e-9:
        where["jd_coverage_low"] = []
    musts = [t["term"] for t in terms if t["must"]]
    for old in earlier if isinstance(earlier, list) else []:  # D (apps 793, 150): a re-review never sheds a must
        name = old.get("term") if isinstance(old, dict) and old.get("must") is True else None
        dropped = where.get("jd_terms_dropped") or []
        if isinstance(name, str) and not any(same_term(name, t) for t in [*musts, *dropped]):
            where.setdefault("jd_terms_dropped", []).append(name)
    return where, {"state": "judged", "must": must, "covered": covered, "bar": bar, "gaps": gaps, "terms": terms}

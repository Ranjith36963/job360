"""Judge one ``cv_reviewed``'s rows (CV benchmark S2, owner 2026-11-10: wording is free and human; facts are exact;
the reviewer is the semantic judge, this code checks only what needs no understanding).

A row ``{n, verdict, evidence}`` names line ``n`` of ``review.lines`` (1-based), the reviewer's verdict and 0-3
verbatim profile quotes. The wording need not share its quotes' words; its facts must be in them. Codes, per line:
``line_uncovered`` (no row), ``line_unsupported`` (the reviewer said no), ``no_evidence`` (none for a line the profile
does not hold itself), ``quote_not_in_profile`` (not inside ONE field / line), ``quote_too_thin`` (< 3 words),
``evidence_wrong_record`` (outside the job / project the line sits under, ``cv_records``), ``claim_not_in_quote`` (a
number, all-caps, ``+``/``#`` or mid-sentence capitalised word, a :data:`NUMBER_WORDS` word, a :data:`ROLE_FAMILIES`
word, a word listed with a name, a list label word that is no generic skills word, or a profile name opening the line,
missing from its quotes), ``evidence_reused``
(one quote the only evidence of more than ``CV_REVIEW_QUOTE_REUSE_MAX`` lines). Every other opening word is free.
Word by word, no regex on caller text; inputs capped by ``cv_review.shape_problems`` and the 64 KB payload cap.
"""
from __future__ import annotations

from typing import Any, Optional

from src.core import settings
from src.services.applications import cv_checks, cv_records, cv_review, evidence

# Closed: words that claim a role or ownership. Wherever one stands in a line (opening it or mid-sentence), a word of
# its family must be in the row's quotes: "led the migration" needs a quote that says it led. Every other word is free.
ROLE_FAMILIES = tuple(frozenset(f.split()) for f in (
    "lead leads led leading leader leaders", "manage manages managed managing manager managers",
    "mentor mentors mentored mentoring", "founded founder founders founding co-founded co-founder cofounder cofounded",
    "owned owner owners owns ownership", "head heads headed", "directed director directors directs",
    "supervised supervises supervising supervisor supervisors", "spearheaded spearheads spearheading",
    "chair chairs chaired chairing chairman chairperson", "hired hires hiring", "promoted promotion",
    "principal", "senior", "chief", "architect architects architected architecting",
))
_ROLE_WORDS = {w: fam for fam in ROLE_FAMILIES for w in fam}
# Closed English function words: never a name listed with a tool ("in Python and then ...").
_FUNCTION_WORDS = frozenset("""
a an the over across with using for as from in on by to while through after before during within without and also
both each every this that these those then now
""".split())
# Closed: a number written as a word is a hard fact too ("twelve engineers"). "one" is left out (a pronoun).
NUMBER_WORDS = frozenset("""
two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen
nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred hundreds thousand thousands million millions
billion billions dozen dozens twice double doubled triple tripled quadrupled half halved tenfold
""".split())
_OUTER = ".,;:!?()[]{}\"'‘’“”"


def _at(where: dict[str, list[Any]], code: str, n: int) -> None:
    got = where.setdefault(code, [])
    if n not in got:
        got.append(n)


_LIST_JOINERS = frozenset({"and", "or", "&", "/"})


def _listed_with_names(body: str) -> list[str]:
    """A lowercase word joined to a name by "and" / "or" / "&" / "/" is a name too: "in Python and kubernetes" names a
    tool (never a function word, a verb form ending -ed / -ing, or a word of 3 letters or fewer: "in Python and
    deployed it")."""
    names = {t.casefold() for t in evidence.claim_tokens(body)}
    toks = [t.strip(_OUTER) for t in body.split()]
    out: list[str] = []
    for i in range(2, len(toks)):
        w = toks[i].casefold()
        if (toks[i - 1].casefold() in _LIST_JOINERS and toks[i - 2].casefold() in names and len(w) > 3
                and w not in _FUNCTION_WORDS and not w.endswith(("ed", "ing"))):
            out.append(w)
    return out


def hard_facts(body: str, quotes: list[str], names: set[str], free_labels: frozenset[str]) -> bool:
    """A hard fact of the line is missing from its own quotes (``claim_not_in_quote``, module doc). ``names``: the
    profile's names (one-word skill items, mid-sentence capitalised words, company / project words), a fact even
    opening the line; ``free_labels``: list label words that tie items to nothing."""
    bank = " ".join(cv_review._quote_bank(q) for q in quotes)
    bank_words = set(evidence._words(bank))
    words = evidence._words(body)
    first = words[0] if words else ""
    parts = cv_review.list_parts(body)  # "MSc: Python, SQL": a list's label ties its items to that name
    label = cv_review._label_words(parts[0]) - {"and"} if parts else frozenset()
    if label - bank_words - free_labels:
        return True
    return bool(evidence.unsupported_tokens(body, bank) or any(w in NUMBER_WORDS and w not in bank_words for w in words)
                or any(w in _ROLE_WORDS and not _ROLE_WORDS[w] & bank_words for w in words)
                or any(w not in bank_words for w in _listed_with_names(body))
                or (first in names and first not in bank_words))


def _proper_nouns(text: str) -> set[str]:
    """Words the profile capitalises mid-sentence, line by line: a line's opening word, after any bullet glyph (the
    PDF's "(cid:127)", "•"), is grammar, never a name."""
    out: set[str] = set()
    for line in text.splitlines():
        toks = line.split()
        while toks and not toks[0][:1].isalnum():
            toks.pop(0)
        out |= {t.casefold() for t in evidence.claim_tokens(" ".join(toks))}
    return out


def judge(text: str, rows: list[dict[str, Any]], profile: Any) -> tuple[dict[str, list[Any]], list[str], int]:
    """``(where, content lines, held count)``: each code with the line numbers it came from, in line order. Rows whose
    ``n`` is past ``review.lines`` are the caller's 422 (``cv_review.verify``), never judged here."""
    content = cv_review.content_lines(text, profile)
    lines = content[: cv_review.REVIEW_LINES_CAP]
    recs = cv_records.line_records(text, profile)
    by_n = {int(r["n"]): r for r in rows}
    where: dict[str, list[Any]] = {}
    for n in range(1, len(content) + 1):
        if n not in by_n:
            _at(where, "line_uncovered", n)
    source, source_words = cv_review.quote_sources(profile)
    book, records = cv_review.skill_book(profile), cv_review.profile_records(profile)
    cv = getattr(profile, "cv_data", None)
    names = {i for i in book.items if " " not in i} | _proper_nouns(cv_checks.profile_source_text(profile)) | set(
        evidence._words(" ".join(
            str(r.get("company") or r.get("name") or "") for f in cv_review._RECORD_FIELDS
            for r in getattr(cv, f, None) or [] if isinstance(r, dict))))
    # a generic label word ("Skills:") is free unless the profile names its own group with it ("Agents & LLMs:")
    free_labels = cv_review.GENERIC_LABEL_WORDS - frozenset().union(*(k for k, _ in book.groups))
    texts = cv_records.record_texts(profile)
    own: dict[frozenset[str], tuple[str, str]] = {}
    alone: dict[str, list[int]] = {}  # a quote -> the lines it is the ONLY evidence of
    held = 0
    for n in sorted(k for k in by_n if k <= len(lines)):
        row, body = by_n[n], lines[n - 1].strip().lstrip(cv_review._BULLETS + " \t")
        if row["verdict"] != "supported":
            _at(where, "line_unsupported", n)
        if cv_review.list_line_holds(body, book) or cv_review.record_line_holds(body, records):
            held += 1
            continue
        quotes: list[str] = row["evidence"]
        if not quotes:
            _at(where, "no_evidence", n)
            continue
        line_recs: Optional[frozenset[str]] = recs[n - 1] if n <= len(recs) else None
        if line_recs is not None and line_recs not in own:
            own[line_recs] = cv_review.sources_from([s for k in sorted(line_recs) for s in texts.get(k, [])])
        for q in quotes:
            if not cv_review.quote_in_profile(q, source, source_words):
                _at(where, "quote_not_in_profile", n)
            if len(set(evidence._words(q))) < 3:
                _at(where, "quote_too_thin", n)
            if line_recs is not None and not cv_review.quote_in_profile(q, *own[line_recs]):
                _at(where, "evidence_wrong_record", n)
        if hard_facts(body, quotes, names, free_labels):
            _at(where, "claim_not_in_quote", n)
        if len(quotes) == 1:
            alone.setdefault(cv_review._norm(quotes[0]), []).append(n)
    for ns in alone.values():
        if len(ns) > settings.CV_REVIEW_QUOTE_REUSE_MAX:
            for n in ns:
                _at(where, "evidence_reused", n)
    return {k: sorted(v) for k, v in where.items()}, content, held

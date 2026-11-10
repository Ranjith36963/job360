"""Judge one ``cv_reviewed``'s rows (CV benchmark S2, owner 2026-11-10: wording is free and human; facts are exact).

A row ``{n, verdict, evidence}`` names line ``n`` of ``review.lines`` (1-based), the reviewer's verdict and 0-3
verbatim profile quotes. The wording need not share its quotes' words; its facts must be in them. Codes, per line:
``line_uncovered`` (no row), ``line_unsupported`` (the reviewer said no), ``no_evidence`` (none for a line the profile
does not hold itself), ``quote_not_in_profile`` (not inside ONE field / line), ``quote_too_thin`` (< 3 words),
``evidence_wrong_record`` (outside the job / project the line sits under, ``cv_records``), ``claim_not_in_quote`` (a
number, all-caps, ``+``/``#`` or mid-sentence capitalised word, a :data:`NUMBER_WORDS` word, a word listed with a name,
or a capitalised profile word opening the line, missing from its quotes), ``first_word_unknown`` (an opening word that
is no :data:`ACTION_VERBS` / :data:`OPENING_WORDS` word and no profile word), ``evidence_unrelated`` (no shared 4+
letter word), ``evidence_reused`` (one quote the only evidence of more than ``CV_REVIEW_QUOTE_REUSE_MAX`` lines).
Word by word, no regex on caller text; inputs capped by ``cv_review.shape_problems`` and the 64 KB payload cap.
"""
from __future__ import annotations

from typing import Any, Optional

from src.core import settings
from src.services.applications import cv_checks, cv_records, cv_review, evidence

# Closed, generic English verbs a CV line may open with though its quotes never say them. Never a skill, tool, title
# or employer, nor a verb that claims a role ("led", "managed", "mentored", "founded", "owned"): those are facts.
ACTION_VERBS = frozenset("""
accelerated achieved added advised analysed analyzed architected assessed automated balanced boosted built calibrated
collaborated combined completed configured consolidated contributed converted coordinated created cut
debugged decreased defined delivered deployed designed developed devised diagnosed documented doubled drafted
drove eliminated enabled engineered enhanced established evaluated expanded extended facilitated fixed formed
gathered generated grew halved handled helped identified implemented improved increased initiated integrated
introduced investigated kept launched lifted maintained measured merged migrated modelled modeled
monitored moved optimised optimized orchestrated organised organized overhauled partnered piloted planned
produced profiled programmed prototyped published raised ran rebuilt redesigned reduced refactored released replaced
researched resolved restructured retired reworked saved scaled secured set shipped simplified solved sped
standardised standardized started stored streamlined strengthened structured supported tested took trained
transformed tuned turned unified upgraded used validated wrote
applied assembled audited authored brought captured cleaned coded communicated compiled conducted connected
containerised containerized crafted curated delegated demonstrated drew earned edited embedded explored exposed
extracted forecast found framed gave held hosted indexed interviewed labelled labeled lowered made mapped met
minimised minimized negotiated operated packaged parsed performed presented prioritised prioritized processed
provided provisioned reached read recruited refined reported reviewed routed scheduled scoped scraped sent served
shaped shortened showed sold sourced spent split steered studied summarised summarized surfaced synced tracked
translated triaged troubleshot visualised visualized won wired worked
runs makes builds writes keeps holds takes gives brings finds sends sells
""".split())
# Closed English function words and sentence openers ("Over three years ...", "End-to-end ..."), never a fact.
OPENING_WORDS = frozenset("""
a an the over across with using for as from in on by to while through after before during within without and also
both each every this that these those my our end-to-end hands-on currently now then today recently
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


def _opens_freely(word: str) -> bool:
    w = word.casefold()
    stems = [w[:-2], w[:-1]] if w.endswith("es") else [w[:-1]] if w.endswith("s") else []
    return w in ACTION_VERBS or w in OPENING_WORDS or any(s + e in ACTION_VERBS for s in stems for e in ("ed", "d"))


_LIST_JOINERS = frozenset({"and", "or", "&", "/"})


def _listed_with_names(body: str) -> list[str]:
    """A lowercase word joined to a name by "and" / "or" / "&" / "/" is a name too: "in Python and kubernetes" names a
    tool (an action or opening word, or a word of 3 letters or fewer, never: "in Python and deployed it")."""
    names = {t.casefold() for t in evidence.claim_tokens(body)}
    toks = [t.strip(_OUTER) for t in body.split()]
    out: list[str] = []
    for i in range(2, len(toks)):
        w = toks[i].casefold()
        if (toks[i - 1].casefold() in _LIST_JOINERS and toks[i - 2].casefold() in names and len(w) > 3
                and not _opens_freely(w)):
            out.append(w)
    return out


def hard_facts(body: str, quotes: list[str], known: set[str]) -> list[str]:
    """The codes a line's hard facts earn against its own quotes (module doc): claim_not_in_quote,
    first_word_unknown, evidence_unrelated."""
    bank = " ".join(cv_review._quote_bank(q) for q in quotes)
    bank_words = set(evidence._words(bank))
    codes: list[str] = []
    words = evidence._words(body)
    if (evidence.unsupported_tokens(body, bank) or any(w in NUMBER_WORDS and w not in bank_words for w in words)
            or any(w not in bank_words for w in _listed_with_names(body))):
        codes.append("claim_not_in_quote")
    toks = body.split()
    first = toks[0].strip(_OUTER) if toks else ""
    if first[:1].isupper() and first.casefold() not in bank_words and not _opens_freely(first):
        codes.append("claim_not_in_quote" if first.casefold() in known else "first_word_unknown")
    rest = {w for w in evidence._words(" ".join(toks[1:])) if len(w) >= 4}
    if rest and not rest & bank_words:
        codes.append("evidence_unrelated")
    return codes


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
    known = set(evidence._words(cv_checks.profile_source_text(profile)))
    book, records = cv_review.skill_book(profile), cv_review.profile_records(profile)
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
        for code in hard_facts(body, quotes, known):
            _at(where, code, n)
        if len(quotes) == 1:
            alone.setdefault(cv_review._norm(quotes[0]), []).append(n)
    for ns in alone.values():
        if len(ns) > settings.CV_REVIEW_QUOTE_REUSE_MAX:
            for n in ns:
                _at(where, "evidence_reused", n)
    return {k: sorted(v) for k, v in where.items()}, content, held

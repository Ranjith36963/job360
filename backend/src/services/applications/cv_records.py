"""Which profile record a CV line sits under (CV benchmark S1, owner 2026-11-10).

A true fact moved to another job is a lie: Northwind's "40%" written under Tidewater, quoting Northwind's bullet, was
stored strong under rules 3. A CV line's evidence must come from the job, project or raw block it sits under:

* :func:`record_texts`: each record's OWN text, keyed as ``cv_review.keyed_records`` keys its pieces. A role or
  project is every value of it (bullets and description too); a raw header line's block runs from that line to the
  next raw header line or section heading.
* :func:`line_records`: for each ``cv_review.content_lines`` line, the record ids it sits under. A section heading
  resets to ``None`` (no constraint: SUMMARY, SKILLS, a line before any heading). In an experience / projects
  section (:data:`RECORD_SECTION_WORDS`), a header-shaped line (``cv_review._raw_header``) sets the ids of the records
  that hold it, and of the roles / projects it names (:func:`_named`), for the lines below it; one held and named by
  none sets ``frozenset()``: no quote can come from it. A raw block and the role / project its header names are one
  job. A header line itself is ``None``; outside such a section nothing is constrained (a held headline is no job).
* :func:`top_lines`: the CV's TOP lines (rules 7, owner 2026-11-12): its headline and summary, the content lines
  before the first job / project header line, bullet or heading other than a summary one. They pitch the whole
  career, so ``review_rows.judge`` asks no quote for them, only that each hard fact is somewhere in the profile.
Education and certification entries never constrain (their lines are often listed without headers). Word by word,
no regex on caller text; bounded by the CV's and profile's line counts.
"""
from __future__ import annotations

from typing import Any, Optional

from src.services.applications import cv_checks, cv_review, evidence

# A heading naming one of these words opens a section whose header lines must be held by a record.
RECORD_SECTION_WORDS = frozenset({"experience", "employment", "projects", "portfolio", "work", "career", "history"})
_FREE_PREFIXES = ("education", "cv_education_details", "certifications")
# Closed: a heading made of these words (one of _TOP_SECTION_WORDS among them) keeps the CV's top open: "PROFESSIONAL
# SUMMARY", "About me". Any other heading (EXPERIENCE, SKILLS, EDUCATION ...) ends it.
_TOP_SECTION_WORDS = frozenset({"summary", "profile", "about", "objective", "overview", "statement"})
_TOP_HEADING_WORDS = _TOP_SECTION_WORDS | {"professional", "personal", "career", "executive", "me", "and"}


def record_texts(profile: Any) -> dict[str, list[str]]:
    """``{record id: its own strings}`` for every role, project, one-line entry and raw header block."""
    cv = getattr(profile, "cv_data", None)
    out: dict[str, list[str]] = {}
    for name in cv_review._RECORD_FIELDS:
        for i, r in enumerate(getattr(cv, name, None) or []):
            if isinstance(r, dict):
                out[f"{name}[{i}]"] = cv_checks._flat(r)
    for name in cv_review._ONE_LINE_FIELDS:
        for i, s in enumerate(cv_checks._flat(getattr(cv, name, None))):
            out[f"{name}[{i}]"] = [s]
    for name in ("raw_text", "linkedin_raw_text"):
        block: Optional[list[str]] = None
        for i, ln in enumerate(str(getattr(cv, name, "") or "").splitlines()):
            if cv_review._raw_header(ln):
                block = out[f"{name}:{i}"] = [ln]
            elif block is not None and cv_review.is_heading(ln):
                block = None
            elif block is not None and ln.strip():
                block.append(ln)
    return out


def _run(words: list[str], value: Any) -> bool:
    """``value``'s words (dashes aside) are a contiguous run of ``words``."""
    want = [w for w in evidence._words(str(value or "")) if not set(w) <= cv_review._DASH_TOKENS]
    n = len(want)
    return bool(want) and any(words[i:i + n] == want for i in range(len(words) - n + 1))


def _named(profile: Any, line: str, words: Optional[list[str]] = None) -> frozenset[str]:
    """Structured roles / projects this header NAMES: the role's company (narrowed by its title when two roles share
    the company) or the project's name, as a run of the header's words. The PDF's "Co | Title, R&D Jun 2025 – ..."
    holds only its raw record, yet it is the role whose bullets the CV quotes (production CV 1060). A lie in the header
    itself is the header line's own claim, judged on its own row. ``words``: a prose line's own words instead."""
    cv = getattr(profile, "cv_data", None)
    if words is None:
        words = [w for p in cv_review.header_pieces(line) or [] for w in p if not set(w) <= cv_review._DASH_TOKENS]
    found: dict[str, bool] = {}  # id -> its title is in the header too
    for name in cv_review._RECORD_FIELDS:
        for i, r in enumerate(getattr(cv, name, None) or []):
            if isinstance(r, dict) and _run(words, r.get("company") if "positions" in name else r.get("name")):
                found[f"{name}[{i}]"] = _run(words, r.get("title"))
    titled = {k for k, t in found.items() if t}
    return frozenset(titled or found)


def line_records(cv_text: str, profile: Any) -> list[Optional[frozenset[str]]]:
    """The record ids each ``content_lines`` line sits under (module doc), in the same order."""
    content = cv_review.content_lines(cv_text, profile)
    keyed = [(k, r) for k, r in cv_review.keyed_records(profile) if not k.startswith(_FREE_PREFIXES)]
    raw_named = {f"{name}:{i}": _named(profile, ln) for name in ("raw_text", "linkedin_raw_text")
                 for i, ln in enumerate(str(getattr(profile.cv_data, name, "") or "").splitlines())
                 if cv_review._raw_header(ln)}  # a raw block and the role / project it names are one job
    out: list[Optional[frozenset[str]]] = []
    current: Optional[frozenset[str]] = None
    in_section = False
    for line in (ln.strip() for ln in str(cv_text or "").splitlines()):
        if not line:
            continue
        is_content = len(out) < len(content) and line == content[len(out)]
        if cv_review.is_heading(line):
            words = cv_review._and_words(evidence._words(line.strip("#*: \t").replace("/", " ")))
            in_section, current = bool(words & RECORD_SECTION_WORDS), None
            if is_content:
                out.append(None)
            continue
        header = cv_review._raw_header(line)
        if is_content and not header and current is None:  # B4: a free line that names a job is that job's line
            named = _named(profile, line, [w for w in evidence._words(line) if not set(w) <= cv_review._DASH_TOKENS])
            out.append(_one_job(named, raw_named) or None)
        elif is_content:
            out.append(None if header else current)
        if header:
            got = cv_review.header_pieces(line) or []
            held = frozenset(k for k, r in keyed if cv_review._runs_in(got, r)) | _named(profile, line)
            current = _one_job(held, raw_named) if in_section else None
    return out


def _one_job(held: frozenset[str], raw_named: dict[str, frozenset[str]]) -> frozenset[str]:
    """``held`` with the raw blocks and structured records that are the same job (a raw header names the role)."""
    held = held | {s for k in held for s in raw_named.get(k, ())}
    return held | {k for k, named in raw_named.items() if named & held}


def top_lines(cv_text: str, profile: Any) -> tuple[frozenset[int], Optional[int]]:
    """``(numbers, headline)``: the ``content_lines`` numbers (1-based) of the CV's TOP lines (module doc), never a
    heading, a skills list or a line naming a job (those keep their own rules); ``headline`` is the first one when no
    heading stands above it (else ``None``). The top ends at a header line a profile record holds or names, or a
    heading that is no summary heading."""
    content = cv_review.content_lines(cv_text, profile)
    keyed = [r for k, r in cv_review.keyed_records(profile) if not k.startswith(_FREE_PREFIXES)]
    recs = line_records(cv_text, profile)  # B4: a summary line naming a job is that job's line, never a top line
    out: list[int] = []
    headline: Optional[int] = None
    seen_heading = False
    n = 0
    for line in (ln.strip() for ln in str(cv_text or "").splitlines()):
        if not line or n >= len(content):
            continue
        is_content = line == content[n]
        heading = cv_review.is_heading(line)
        if not heading and _marked(line):  # a bullet is a record's line, never the headline or summary
            break
        words = cv_review._and_words(evidence._words(line.strip("#*: \t").replace("/", " "))) if heading else set()
        if heading and words <= _TOP_HEADING_WORDS and words & _TOP_SECTION_WORDS:
            seen_heading = True
            if is_content:  # "PROFESSIONAL SUMMARY" kept as a line: a top line, never the headline
                out.append(n + 1)
        elif heading and not (is_content and not out and not seen_heading):
            break  # "EXPERIENCE", "HIGHLIGHTS": the top is over (an all-caps first line is the headline)
        elif cv_review._raw_header(line) and (
                any(cv_review._runs_in(cv_review.header_pieces(line) or [], r) for r in keyed)
                or _named(profile, line)):
            break
        elif is_content and not _skills_list(line, first=not out and not seen_heading) and recs[n] is None:
            out.append(n + 1)
            if len(out) == 1 and not seen_heading:
                headline = n + 1
        n += is_content
    return frozenset(out), headline


def _marked(line: str) -> bool:
    """A line opening with a list marker: any symbol ("-", "·", "➤", ">"), "o ", or a number like "1." / "2)"."""
    first = line.split(maxsplit=1)[0]
    return (not line[0].isalnum() or first == "o"
            or (len(first) > 1 and first[:-1].isdigit() and first[-1] in ".)"))


def _skills_list(line: str, first: bool = False) -> bool:
    """A labelled or comma list ("Skills: Python, SQL", "Python, SQL, Docker"); a bar-joined unlabelled line ("AI
    Engineer | Agentic Systems") is a headline, and so is any unlabelled ``first`` line ("AI Engineer, Agentic
    Systems")."""
    parts = cv_review.list_parts(line)
    return parts is not None and (bool(parts[0]) or not (first or cv_review._HEADER_BARS.intersection(line.split())))

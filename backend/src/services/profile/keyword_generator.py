"""Convert a UserProfile into a SearchConfig for dynamic keyword-driven search."""

from __future__ import annotations

import re

from src.core.keywords import LOCATIONS, VISA_KEYWORDS
from src.core.skill_synonyms import canonicalize_skill
from src.services.profile.models import CVData, SearchConfig, UserProfile
from src.services.profile.skill_tiering import (
    collect_evidence_from_profile,
    tier_skills_by_evidence,
)


def _canonicalize_skill_list(skills: list[str]) -> list[str]:
    """Pillar 2 Batch 2.3 — collapse a skill list to canonical forms while
    preserving order and dedup. E.g. ['JS', 'JavaScript'] → ['javascript'],
    ['K8S'] → ['kubernetes']. Unknown entries pass through with case/whitespace
    normalisation only, so domain-specific CV terms aren't discarded."""
    out: list[str] = []
    seen: set[str] = set()
    for s in skills:
        canonical = canonicalize_skill(s)
        if canonical and canonical not in seen:
            out.append(canonical)
            seen.add(canonical)
    return out


# Words to ignore when building relevance keywords
_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "to", "for", "with", "on",
    "at", "by", "is", "it", "as", "be", "was", "are", "from", "that",
    "this", "have", "has", "had", "not", "but", "its", "can", "will",
    "do", "does", "did",
}

# Common role words that support but don't define a domain
_ROLE_WORDS = {
    "engineer", "developer", "architect", "analyst", "consultant",
    "manager", "specialist", "lead", "head", "director", "scientist",
    "researcher", "designer", "coordinator", "administrator", "officer",
    "technician", "associate", "assistant", "intern", "trainee",
}

# ---------------------------------------------------------------------------
# SEARCH TITLES — what we are willing to send to a job board.
#
# THE DEFECT (measured on live prod, 2026-08-13). `SearchConfig.job_titles` was
# doing two incompatible jobs: it is the EVIDENCE the scorer matches against,
# and it was also the QUERY list Reed/Adzuna/Jooble/Workday/JobSpy put in an
# HTTP request. So raw CV strings went straight to job boards. One live profile
# sent eight queries, including:
#     "AI Solutions Engineer – R&D Department UK"   (a department suffix)
#     "Software Development Engineer in Test (SDET) UK"  (a parenthetical)
#     "Intern UK"                                    (a rank, not a job)
# No posting on any board carries those as its title, so those requests came
# back near-empty. Everything downstream — scoring, ranking, the LLM judge — is
# capped by what these queries fetch, so this is the first lever, not a detail.
#
# THE FIX is a split, not a filter: `job_titles` keeps everything (the scorer is
# untouched, byte for byte) and a NEW `search_titles` carries the cleaned,
# ranked, capped subset. Five structural steps, no hand-typed junk vocabulary:
#   1 SPLIT     one raw title -> 1-2 candidates (dash clause cut, parenthetical
#               promoted to its own candidate — "(SDET)" is a real search term)
#   2 NORMALISE collapse whitespace, strip edge punctuation
#   3 ADMIT     keep only titles that name a DOMAIN, not just a rank
#   4 DEDUP     drop a title that is a kept title + rank words (redundant under
#               AND-of-terms matching: the broader query already returns it)
#   5 ORDER+CAP typed targets first (never dropped, never capped), then history
#
# Rule #28 note: steps 3 and 4 reuse `_ROLE_WORDS` above — vocabulary that
# already existed in this file for `supporting_role_words`. Nothing new is
# hand-typed, and this module is post-extraction search plumbing, not
# `profile/` extraction.
# ---------------------------------------------------------------------------
# Total search titles emitted. 6 matches Reed's own derived per-run title
# budget, and 6 titles x 2 locations = 12 <= the existing 16-query cap.
# Typed targets are exempt — a user who types 8 gets 8.
_MAX_SEARCH_TITLES = 6

# Strips a trailing/inline parenthetical from a title: "Software Development
# Engineer in Test (SDET)" -> "Software Development Engineer in Test". Pure
# text hygiene — no vocabulary, nothing hand-typed (rule #28 safe).
_PARENTHETICAL_RE = re.compile(r"\s*\([^)]*\)")
# The inner text of each parenthetical — an ALTERNATIVE NAME for the same role,
# so it earns its own candidate instead of being thrown away.
_PARENTHETICAL_INNER_RE = re.compile(r"\(([^)]*)\)")

# An ACRONYM parenthetical is an alternative NAME for the same role, so it earns
# its own query: "Software Development Engineer in Test (SDET)" -> "SDET".
# Anything else in brackets is a grade, team or contract note — "(Band 5)",
# "(Growth)", "(Contract)" — and promoting those manufactures nonsense queries
# like "Band 5 UK" that no job board can answer.
# Structural test, not a vocabulary (rule #28): ONE token, all-caps, 2-6 chars.
_ACRONYM_RE = re.compile(r"[A-Z][A-Z0-9]{1,5}")
# Dash-like separators. A CV title says "<role> – <team/department>"; the part
# after the dash is org structure, which no job board indexes.
#
# U+FFFD is included on purpose. It is the Unicode REPLACEMENT CHARACTER — by
# definition a byte sequence that could not be decoded, so it is never part of
# a real word and can never be a meaningful search term. In this codebase it is
# what a mangled en-dash decodes to, i.e. exactly the separator above. Live
# Postgres was checked (2026-08-13) and stores clean en-dashes — the U+FFFD in
# the bug report came from a Windows console print, not the data. This is belt
# and braces so a future decode failure cannot leak into an HTTP query string.
_DASH_SPLIT_RE = re.compile("[-–—\ufffd]")
# Punctuation that can only be noise at the edge of a query string.
_EDGE_PUNCT = " -–—,/&:·|\ufffd"


def _title_tokens(title: str) -> frozenset[str]:
    """The meaningful word set of a title (same tokenisation the core-domain
    build below uses: \\w+, minus stopwords and 1-char junk)."""
    return frozenset(
        w
        for w in re.findall(r"\w+", title.lower())
        if w not in _STOPWORDS and len(w) > 1
    )


def _history_titles(cv: CVData) -> list[str]:
    """Every job title on the profile's work history, in source order
    (CV first, then LinkedIn), deduped case-insensitively.

    LIST ORDER, not date order — measured, not preferred. Sorting by date was
    tried and rejected: on the live profile that motivated this work,
    ``cv_positions`` exists for only 3 of 9 titles, and the two AI roles that
    matter carry NO position record at all, so a date sort promoted a 2024
    blockchain internship and dropped both AI roles. A rule that behaves worst
    on the case it was written for is not a rule. CVs conventionally list roles
    most-recent-first, and list order covers every title.

    (Improvement path, deliberately NOT done here: make CV extraction emit a
    ``cv_positions`` entry for EVERY title it emits, then revisit date order.)
    """
    titles: list[str] = []
    seen: set[str] = set()
    for t in list(cv.job_titles) + [
        str(pos.get("title", "") or "") for pos in cv.linkedin_positions
    ]:
        t = (t or "").strip()
        if t and t.lower() not in seen:
            titles.append(t)
            seen.add(t.lower())
    return titles


def _normalise_title(text: str) -> str:
    """STEP 2 — collapse whitespace and strip edge punctuation."""
    return re.sub(r"\s+", " ", text).strip(_EDGE_PUNCT)


def _split_title(raw: str) -> list[str]:
    """STEP 1 — turn one raw CV title into 1-2 searchable candidates.

    * ``outside`` = the title with parentheticals removed.
    * ``head``    = ``outside`` up to the first dash-like separator.
    * primary     = ``head`` when it still has >= 2 meaningful words, else
      ``outside``. That guard is why "Engineer - Data Platform" does not
      collapse to the useless "Engineer", and why "E-commerce Manager"
      survives a hyphen inside a word.
    * an ACRONYM parenthetical becomes its own candidate — it is an alternative
      name for the same role ("(SDET)"). Any other bracketed text is a grade,
      team or contract note ("(Band 5)", "(Growth)", "(Contract)") and is
      dropped, because promoting it invents a query like "Band 5 UK".
    """
    outside = _normalise_title(_PARENTHETICAL_RE.sub(" ", raw))
    head = _normalise_title(_DASH_SPLIT_RE.split(outside, maxsplit=1)[0])
    primary = head if len(_title_tokens(head)) >= 2 else outside

    aliases = [
        inner
        for inner in _PARENTHETICAL_INNER_RE.findall(raw)
        if _ACRONYM_RE.fullmatch(inner.strip())
    ]

    candidates: list[str] = []
    seen: set[str] = set()
    for cand in [primary, *aliases]:
        cand = _normalise_title(cand)
        if cand and cand.lower() not in seen:
            seen.add(cand.lower())
            candidates.append(cand)
    return candidates


def _names_a_domain(tokens: frozenset[str]) -> bool:
    """STEP 3 — a query must name a DOMAIN, not just a rank.

    "Intern", "Associate", "Manager" alone describe where someone sits, not
    what they do; every board returns an unusable everything-list for them.
    "Blockchain Engineer Intern" keeps 'blockchain' and is admitted.

    Deliberately the WEAK gate. The stronger ``core_domain_words`` gate was
    tested and over-drops: a user whose one title is "Actuarial Analyst" with
    no corroborating skill would get ZERO queries and an empty catalog.
    Rank aggressively, drop conservatively.
    """
    return bool(tokens - _ROLE_WORDS)


def _is_redundant(tokens: frozenset[str], kept: list[frozenset[str]]) -> bool:
    """STEP 4 — drop a candidate that an already-kept query would return anyway.

    Redundant means: some kept title's words are a SUBSET of this one, and the
    only extra words are rank words. "AI/ML Engineer Intern" is "AI/ML
    Engineer" plus 'intern', so under AND-of-terms keyword matching the broader
    query already returns every posting the narrower one could.

    This is a REDUNDANCY argument, not a seniority judgement — nothing here
    decides that junior is worse than senior, so rule #29 is untouched.
    """
    return any(k <= tokens and (tokens - k) <= _ROLE_WORDS for k in kept)


def _build_search_titles(typed: list[str], history: list[str]) -> list[str]:
    """STEP 5 — the ordered, capped query list.

    ``typed`` (``preferences.target_job_titles``) comes first, in the user's
    own order, and is NEVER dropped for lack of a domain word: the user typed
    it, so second-guessing it would be exactly the rule-#29 mistake. Only then
    does work history fill the remaining budget.

    WHY A HISTORY FALLBACK IS NOT A RULE-#29 GUESS. Rule #29 says an empty
    preference means "don't care" — never a penalty, never a default. Four
    reasons this stays inside it:
      1. It is not a scoring input. ``search_titles`` is read only by
         ``fetch_jobs()``. ``job_titles``, ``core_domain_words``,
         ``relevance_keywords``, ``locations`` — everything the scorer reads —
         are byte-identical with or without this fallback, so re-ranking the
         same catalog moves nothing. That is rule #29's own test, passed.
      2. Silence has a floor here. For every other field silence costs a
         neutral constant; for a search query it costs an EMPTY CATALOG, the
         one outcome strictly worse than a wrong job.
      3. It reads a FACT, it does not guess a want. Past job titles are already
         written on the CV. Same operation ``seniority.py`` documents for
         ``experience_level_inferred`` — the precedent is in-repo.
      4. The fallback never writes into ``preferences``, never marks the field
         filled, and is fully overridden the moment the user types one title.

    Never returns empty when the profile has ANY title (last resort below), so
    the search always has something to send.
    """
    kept: list[str] = []
    kept_tokens: list[frozenset[str]] = []
    seen: set[str] = set()

    def _admit(candidate: str, *, require_domain: bool) -> bool:
        key = candidate.lower()
        if not candidate or key in seen:
            return False
        tokens = _title_tokens(candidate)
        if not tokens:
            return False
        if require_domain and not _names_a_domain(tokens):
            return False
        if _is_redundant(tokens, kept_tokens):
            return False
        seen.add(key)
        kept.append(candidate)
        kept_tokens.append(tokens)
        return True

    for raw in typed:
        for candidate in _split_title(raw):
            _admit(candidate, require_domain=False)

    # Typed targets are exempt from the total cap; history fills what is left.
    # ONE cap governs: the number of QUERIES (`budget`). There used to be a
    # second, per-role cap as well, and it bound first — a history-only profile
    # stopped after 4 roles and produced 5 queries out of an allowed 6, silently
    # spending its last slot on nothing. Measured on live user 88e7d907's real
    # 9-role history: 'QA Engineer' and 'Blockchain Developer' — both ordinary,
    # high-yield board queries — were never even considered, while the narrower
    # 'SDET' was. Two caps meant the wrong one decided the catalog.
    budget = max(_MAX_SEARCH_TITLES, len(kept))
    for raw in history:
        if len(kept) >= budget:
            break
        for candidate in _split_title(raw):
            if len(kept) >= budget:
                break
            _admit(candidate, require_domain=True)

    if not kept:
        # LAST RESORT — every title was rank-only ("Intern" and nothing else).
        # "Intern UK" is a poor query; NO query is a worse one (empty catalog).
        for raw in list(typed) + list(history):
            for candidate in _split_title(raw):
                if _admit(candidate, require_domain=False):
                    return kept
    return kept


def generate_search_config(profile: UserProfile) -> SearchConfig:
    """Generate a SearchConfig from a UserProfile."""
    prefs = profile.preferences
    cv = profile.cv_data

    # --- Job titles (EVIDENCE — the scorer's input) ---
    # Unfiltered and uncapped, on purpose: everything the profile knows. Query
    # hygiene happens in `search_titles` below, so nothing here changes ranking.
    titles = list(prefs.target_job_titles)
    seen = {t.lower() for t in titles}
    for t in cv.job_titles:
        if t.lower() not in seen:
            titles.append(t)
            seen.add(t.lower())

    # LinkedIn position titles
    for pos in cv.linkedin_positions:
        title = pos.get("title", "")
        if title and title.lower() not in seen:
            titles.append(title)
            seen.add(title.lower())

    # --- Search titles (QUERIES — the only titles a job board ever sees) ---
    typed_targets = [t for t in prefs.target_job_titles if t and t.strip()]
    search_titles = _build_search_titles(typed_targets, _history_titles(cv))

    # --- Skills (Batch 1.3a — evidence-based tiering by source + frequency) ---
    # Replaces the naive position-based thirds split (pillar_1_report #1).
    # Primary tier requires either a user-declared skill OR ≥2 supporting
    # sources (e.g. cv_explicit + linkedin). See ``skill_tiering`` for the
    # weight table. ``all_skills`` is still emitted for the relevance
    # keyword build below — it's the full deduped union.
    evidence = collect_evidence_from_profile(profile)
    primary, secondary, tertiary = tier_skills_by_evidence(evidence)
    all_skills = [e.name for e in evidence]

    # --- Relevance keywords ---
    rel_set: set[str] = set()
    for title in titles:
        for word in re.findall(r'\w+', title.lower()):
            if word not in _STOPWORDS and len(word) > 1:
                rel_set.add(word)
    for skill in all_skills:
        rel_set.add(skill.lower())

    # LinkedIn industry words
    if cv.linkedin_industry:
        for word in re.findall(r'\w+', cv.linkedin_industry.lower()):
            if word not in _STOPWORDS and len(word) > 1:
                rel_set.add(word)

    relevance_keywords = sorted(rel_set)

    # --- Negative title keywords ---
    negatives = list(prefs.negative_keywords)

    # --- Locations ---
    locations = list(LOCATIONS)  # Start with UK defaults
    for loc in prefs.preferred_locations:
        if loc not in locations:
            locations.append(loc)
    if prefs.work_arrangement:
        arrangement = prefs.work_arrangement.capitalize()
        if arrangement not in locations:
            locations.append(arrangement)

    # --- Core domain words & supporting role words ---
    # Dead-title-lever fix (funnel redesign 2026-08-05). Titles pulled from a CV
    # are hyper-specific ("AI Solutions Engineer - R&D Department"): real
    # postings never equal or contain them, so title scoring falls back to this
    # vocabulary — which used to be raw title tokens, i.e. tiny AND polluted by
    # junk like 'department'. Measured on prod: no job scored above 8/40 on
    # title, capping every match_score at ~69 and quietly making LOCATION the
    # feed's sort key. All corroboration below is evidence from the profile
    # itself (titles, skills) — no hardcoded vocabulary (rule #28 spirit).
    #
    # A title token joins core only with corroboration:
    #   - it appears in >=2 different titles (cross-title evidence), or
    #   - it appears inside the user's skill tokens (skill evidence), or
    #   - it is a short acronym token (<=3 chars: 'ai', 'ml', 'nlp' — domain
    #     acronyms; 1-char junk like the 'r'/'d' of "R&D" is already dropped).
    # Skill tokens seen across >=2 skills join core too, so "Machine Learning
    # Engineer" postings match a profile whose titles never say 'learning'.
    support_words: set[str] = set()
    title_token_titles: dict[str, int] = {}  # token -> number of DISTINCT titles
    seen_token_sets: set[frozenset[str]] = set()
    for title in titles:
        seen_in_title: set[str] = set()
        for word in re.findall(r'\w+', title.lower()):
            if word in _STOPWORDS or len(word) <= 1:
                continue
            if word in _ROLE_WORDS:
                support_words.add(word)
            else:
                seen_in_title.add(word)
        # Near-duplicate titles ("… - R&D Department" vs "… – R&D Department",
        # or the same title arriving from both CV and LinkedIn) must count as
        # ONE piece of evidence, or their junk tokens fake cross-title
        # corroboration. Identity = the token set, not the raw string.
        fs = frozenset(seen_in_title)
        if not fs or fs in seen_token_sets:
            continue
        seen_token_sets.add(fs)
        for word in seen_in_title:
            title_token_titles[word] = title_token_titles.get(word, 0) + 1

    skill_token_freq: dict[str, int] = {}  # token -> number of skills it appears in
    for skill in primary + secondary:
        for word in set(re.findall(r'\w+', skill.lower())):
            if word in _STOPWORDS or word in _ROLE_WORDS or len(word) <= 1:
                continue
            skill_token_freq[word] = skill_token_freq.get(word, 0) + 1

    core_words: set[str] = {
        tok for tok, n_titles in title_token_titles.items()
        if n_titles >= 2 or tok in skill_token_freq or len(tok) <= 3
    }
    core_words |= {tok for tok, freq in skill_token_freq.items() if freq >= 2}
    if not core_words:
        # Thin-profile safety net (one title, no skills): an empty vocabulary
        # would kill title scoring entirely — keep the old all-tokens behaviour.
        core_words = set(title_token_titles)

    # --- Search queries (search titles x top 2 locations) ---
    # Built from `search_titles`, NOT `job_titles` — that swap is the whole
    # point of the split above. The " UK" suffix stays for the sources that
    # send no separate location param (jsearch, 80k Hours); sources that DO
    # pass a location read `search_titles` directly and never see the suffix.
    #
    # An empty `preferred_locations` defaults to "UK" rather than to nothing:
    # rule #30 already refuses non-UK jobs at ingestion, so a UK default cannot
    # narrow the catalog below what the gate would accept anyway.
    search_locations = prefs.preferred_locations[:2] if prefs.preferred_locations else ["UK"]
    queries = []
    for title in search_titles:
        for loc in search_locations:
            queries.append(f"{title} {loc}")
    queries = queries[:16]

    return SearchConfig(
        job_titles=titles,
        search_titles=search_titles,
        primary_skills=_canonicalize_skill_list(primary),
        secondary_skills=_canonicalize_skill_list(secondary),
        tertiary_skills=_canonicalize_skill_list(tertiary),
        relevance_keywords=relevance_keywords,
        negative_title_keywords=negatives,
        locations=locations,
        visa_keywords=list(VISA_KEYWORDS),
        core_domain_words=core_words,
        supporting_role_words=support_words,
        search_queries=queries,
    )

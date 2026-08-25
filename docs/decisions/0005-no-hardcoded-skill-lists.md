# ADR-0005 — Profile extraction contains zero hand-typed skill lists
<!-- doc: LIVING -->

**Status:** Accepted (owner rule, non-negotiable) · **Date:** backfilled
2026-08-25 from hard rule #28 and `docs/product/PILLAR1_EXTRACTION_AUDIT.md`

## Decision

Nothing under `src/services/profile/` may contain a skill-keyword dictionary,
a `*_SKILL_TERMS` map, a prose→skill lookup, or a denylist.

Extraction is the LLM plus **structural** passes: CV headings, dependency
manifests, GitHub language and topic statistics.

## The problem it solved

A hand-typed skill map is written by reading one or two CVs. It then works
beautifully on those CVs and quietly mis-extracts everyone else's — and the
failure is invisible, because the extracted profile looks plausible either way.

Mapping prose to skills is exactly what a language model is good at, and
exactly what a dictionary is bad at: the dictionary cannot generalise past the
examples its author happened to think of.

## Alternatives considered

- **An ontology (ESCO).** Scaffolding for it exists in the tree and is **inert
  — never built, never shipped**. Reviving it means producing artefacts, not
  flipping a flag. It must never be cited as "how extraction works", because it
  is not consulted at all.
- **A small "obvious skills" list as a fallback.** Rejected: the fallback
  becomes the path of least resistance and grows.

## Consequences

- Extraction depends on an LLM being available. `require_llm_key()` fails fast
  and loudly when no provider key is set, rather than silently degrading to a
  worse extractor — a silent degradation here would be indistinguishable from a
  weak CV.
- Quality varies with the provider. Accepted.

## Carve-out

`core/skill_synonyms.py` is **retained** and is not a violation: it is scoring
and search vocabulary, and it reads no CV input. The rule is about extraction.

## Still valid?

Yes. Verified 2026-08-11 that no ontology is consulted anywhere in the
extraction path; the absence chain is documented in
`docs/product/PILLAR1_EXTRACTION_AUDIT.md`.

# mypy strict backlog

This file catalogues the strict-mode mypy errors that remain after the
Step-0 Tier-C gate landed. **Strict mode is active** in `backend/pyproject.toml`
— all _new_ code is checked against it and must be clean. The errors below are
pre-existing debt; they are documented here so future sessions can chip away
at them incrementally.

## Baseline snapshot

- **Run command:** `cd backend && python -m mypy src/`
- **Baseline count:** 395 errors across 83 files (checked 139 source files)
- **Exit code:** non-zero (1) — gate is configured strict but baseline is
  grandfathered. A future commit can wire `python -m mypy src/` into CI once
  the backlog is drained below a chosen threshold.
- **mypy version:** 1.20.2
- **python_version in config:** `"3.10"` (the codebase already uses `X | Y`
  union syntax pervasively — setting 3.9 produces 62 bogus syntax errors).

## Error category histogram

| Count | Category | What it means |
|---|---|---|
| 138 | `type-arg` | `dict` / `list` used without `[K, V]` / `[T]` — cheap to fix mechanically |
| 112 | `no-untyped-def` | Function has no return / param annotations — bulk of FastAPI route + CLI handlers |
| 41  | `union-attr` | Accessing `.foo` on `Optional[X]` without narrowing — needs real `if x is None:` guards |
| 30  | `no-untyped-call` | Calling an untyped function from a typed one — cascades out of `no-untyped-def` fixes |
| 21  | `call-overload` | Overloaded stdlib / aiohttp call with unexpected arg shape — case-by-case |
| 17  | `no-any-return` | Returning `Any` from a function typed to return a concrete type |
| 10  | `unused-ignore` | Obsolete `# type: ignore` comments left behind by past refactors — cheap to delete |
| 8   | `arg-type` | Passing wrong type to a function — real bugs or bad annotations |
| 5   | `attr-defined` | Accessing an attribute mypy believes doesn't exist |
| 4   | `return-value` | Returning wrong type from a function |
| 3   | `operator` | Using an operator on incompatible types |
| 2   | `var-annotated` | Variable declaration needs explicit annotation |
| 2   | `assignment` | Assigning wrong type to a typed variable |
| 1   | `misc` / `index` | Miscellaneous |

## Hotspots (top 10 files by error count)

| Errors | File |
|---|---|
| 63 | `src/repositories/database.py` |
| 38 | `src/sources/base.py` |
| 19 | `src/services/profile/linkedin_parser.py` |
| 19 | `src/services/deduplicator.py` |
| 11 | `src/services/profile/schemas.py` |
| 11 | `src/main.py` |
| 10 | `src/services/vector_index.py` |
| 10 | `src/services/profile/github_enricher.py` |
| 10 | `src/cli.py` |
| 9  | `src/workers/tasks.py` |

Fixing the top 5 hotspots would clear ~150 errors (~38% of the backlog).

## Suggested chip-away order

1. **Remove the 10 `unused-ignore` comments** — pure cleanup, no risk.
   Locations: `services/vector_index.py:26`, `services/retrieval.py:152`,
   `services/profile/dep_file_parser.py:117`, `services/embeddings.py:44`,
   `services/profile/skill_normalizer.py:98,129`,
   `services/channels/dispatcher.py:29`, `services/deduplicator.py:150,198,199`.
2. **Sweep `type-arg` (138)** — mostly `dict` → `dict[str, Any]` or
   `dict[str, str]`. A half-day of mechanical work.
3. **Add return annotations on FastAPI routes** — drops ~40 of the
   `no-untyped-def` errors in `api/routes/*`. FastAPI routes almost always
   return a Pydantic model or dict, so the annotation is self-documenting.
4. **Tackle `src/repositories/database.py` (63 errors)** — biggest single
   win. Most errors are `Row`-unpacking (untyped tuples from aiosqlite) and
   `Optional` narrowing.
5. **Rest of `sources/base.py` and per-source files** — once `base.py` is
   typed, the 47 source subclasses largely follow.

## Gate behaviour

`backend/pyproject.toml` has `[tool.mypy]` with `strict = true`. The command

```bash
cd backend && python -m mypy src/
```

currently **fails** with 395 errors. Future commits that introduce *new*
strict-mode violations will be visible in the diff between the baseline and
the new run — it's the new violations that matter, not the grandfathered
ones. Once the count is stable-or-decreasing, wire the command into CI with
a failure-count threshold (e.g. `mypy --error-summary | awk '{ exit ($NF > 395) }'`).

## Exclusions / suppressions in place

See the `[[tool.mypy.overrides]]` blocks in `backend/pyproject.toml`.

- Heavy optional deps (sentence-transformers, chromadb, rapidfuzz, sklearn,
  apprise, torch, groq, cerebras, google.generativeai, arq, etc.) are
  silenced via `ignore_missing_imports = true` + `follow_imports = "skip"`.
- Tests under `tests.*` have relaxed rules (no enforcement of
  `disallow_untyped_defs` / `disallow_incomplete_defs` / `check_untyped_defs`).
- No per-file `# type: ignore[...]` suppressions were added as part of the
  gate landing — the full baseline is visible in the error count above.

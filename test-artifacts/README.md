# test-artifacts/
<!-- doc: LIVING -->

Test fixtures and E2E verification artifacts.

| File | Purpose |
|------|---------|
| `sample_cv.pdf` | Minimal PDF fixture used by CV-parser tests (`test_profile.py`, `test_api.py` file-upload scenarios) |

Transient outputs (screenshots, HTML reports) are gitignored by an
**allow-list**, not a glob: `.gitignore:69-71` ignores `test-artifacts/*` and
then re-admits exactly two committed fixtures — this README and
`sample_cv.pdf`.

The old `test-artifacts/*.png` pattern this file used to describe was replaced
because it only caught top-level PNGs, so nested screenshot directories
(`design/`, `tailor/`, `verify-*/`) accumulated untracked and could be swept in
by a `git add -A`. Corrected 2026-08-24.

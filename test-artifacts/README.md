# test-artifacts/
<!-- doc: LIVING -->

Test fixtures and E2E verification artifacts.

| File | Purpose |
|------|---------|
| `sample_cv.pdf` | Minimal PDF fixture used by CV-parser tests (`test_profile.py`, `test_api.py` file-upload scenarios) |

Transient outputs (screenshots, HTML reports) are gitignored by an
**allow-list**, not a glob: `.gitignore` ignores `test-artifacts/*` and then
re-admits exactly the two committed fixtures — this README and `sample_cv.pdf`.
A `*.png` glob would only catch top-level PNGs and let nested screenshot
directories accumulate untracked.

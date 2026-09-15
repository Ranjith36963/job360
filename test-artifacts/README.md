# test-artifacts/
<!-- doc: LIVING -->

Test fixtures and E2E verification artifacts.

| File | Purpose |
|------|---------|
| `sample_cv.pdf` | The CV uploaded by the manual sweep — `.claude/skills/verify-job360/SKILL.md` step 2. No pytest reads it; the tests that need a PDF build their own with fpdf2 |

Transient outputs (screenshots, HTML reports) are gitignored by an
**allow-list**, not a glob: `.gitignore` ignores `test-artifacts/*` and then
re-admits exactly the two committed fixtures — this README and `sample_cv.pdf`.
A `*.png` glob would only catch top-level PNGs and let nested screenshot
directories accumulate untracked.

"""Step-1 gate check: ESCO_SKILL_NORMALISATION_ENABLED=false must not pull heavy deps.

Imports the FastAPI app with ESCO_SKILL_NORMALISATION_ENABLED=false and asserts that
sentence-transformers, numpy, torch, rapidfuzz and pdfplumber are NOT loaded into sys.modules.
Honours CLAUDE.md rules #11 and #16 (lazy-import discipline for heavy deps).

Exits 0 on success, 1 on violation. Used by `make verify-step-1`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def main() -> int:
    os.environ["ESCO_SKILL_NORMALISATION_ENABLED"] = "false"

    # Import the API app — anything pulled in by it is a startup cost.
    from src.api.main import app  # noqa: F401, E402

    forbidden = ("sentence_transformers", "numpy", "torch", "rapidfuzz", "pdfplumber")
    leaks = [name for name in forbidden if name in sys.modules]

    if leaks:
        print(f"FAIL: heavy modules loaded at ESCO_SKILL_NORMALISATION_ENABLED=false: {leaks}")
        return 1

    print("OK: no heavy modules in sys.modules at ESCO_SKILL_NORMALISATION_ENABLED=false")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Guard: every third-party module imported by ``src/`` is a RUNTIME dependency.

The prod image installs with ``pip install .`` (no ``[dev]`` extra), so a module
that only appears under ``[project.optional-dependencies].dev`` imports fine in
tests and CI but raises ImportError in production. This is how ``fpdf2`` shipped
dev-only while ``services/tailoring/pdf.py`` needed it at runtime (PDF download
broken in prod). Lazy imports inside functions count too — this scans every
``import`` node, not just module top level.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # backend supports 3.10+; tomllib is 3.11+
    import tomli as tomllib

BACKEND = Path(__file__).resolve().parent.parent
SRC = BACKEND / "src"

# import name -> distribution name, only where they differ.
IMPORT_TO_DIST = {
    "docx": "python-docx",
    "dotenv": "python-dotenv",
    "multipart": "python-multipart",
    "sentry_sdk": "sentry-sdk",
    "yaml": "pyyaml",
    "jwt": "pyjwt",
    "bs4": "beautifulsoup4",
    "fpdf": "fpdf2",
    "argon2": "argon2-cffi",
}

# Imported directly but legitimately NOT a top-level dependency. Each needs a reason.
ALLOWED_UNDECLARED = {
    "starlette": "ships with fastapi",
    "pydantic": "ships with fastapi (and mcp)",
    "mcp-types": "ships with mcp",
    "psycopg-pool": "psycopg[pool] extra",
    "tomli": "only imported on Python < 3.11 (dep_file_parser.py)",
    "numpy": "optional; import wrapped in try/except (skill_normalizer.py)",
    "sentence-transformers": "optional extra; import wrapped in try/except (skill_normalizer.py)",
}


def _dist(requirement: str) -> str:
    return re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].lower().replace("_", "-")


def _runtime_deps() -> set[str]:
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return {_dist(d) for d in project["dependencies"]}


def _third_party_imports() -> dict[str, str]:
    """Return {distribution: first file importing it} for non-stdlib, non-local imports."""
    local = {p.name.removesuffix(".py") for p in SRC.iterdir()} | {"src", "migrations", "api"}
    found: dict[str, str] = {}
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                if name in sys.stdlib_module_names or name in local:
                    continue
                dist = IMPORT_TO_DIST.get(name, name.lower().replace("_", "-"))
                found.setdefault(dist, str(path.relative_to(BACKEND)))
    return found


def test_every_src_import_is_a_runtime_dependency() -> None:
    missing = {d: f for d, f in _third_party_imports().items() if d not in _runtime_deps() | ALLOWED_UNDECLARED.keys()}
    assert not missing, (
        "imported by src/ but not in [project].dependencies (prod installs without [dev]): "
        f"{missing}"
    )


def test_pdf_renderer_dependency_is_runtime() -> None:
    assert "fpdf2" in _runtime_deps()

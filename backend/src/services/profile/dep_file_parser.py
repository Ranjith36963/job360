"""Parse package-manager manifest files into sets of dependency names.

Batch 1.2 (Pillar 1). One function per manifest format; each returns
an (ecosystem, deps) tuple where ``deps`` is a *set of bare names*
without version specifiers. Version stripping is done here so the
downstream dependency-skill lookup is ecosystem-keyed (``pypi`` /
``npm`` / etc.) and version-agnostic — we care about *which* frameworks
the user has used, not *which version*.

Parsing is intentionally permissive. If a manifest is malformed, we
swallow the error and return the deps we *could* extract; the enricher
treats this as "0 deps for this manifest" and moves on. That matches
the report's recommendation (§report-item-#3 mitigation: "gracefully
skip unparseable manifests").

No network I/O in this module — it takes raw content bytes/strings
and returns names. ``github_enricher.py`` owns fetch.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Tuple

logger = logging.getLogger("job360.profile.deps")


# Public: filename → (ecosystem, parser_fn). Ordered by prevalence so
# the enricher can iterate sensibly.
MANIFEST_FILES: tuple[tuple[str, str], ...] = (
    ("package.json", "npm"),
    ("requirements.txt", "pypi"),
    ("pyproject.toml", "pypi"),
    ("Cargo.toml", "cargo"),
    ("Gemfile", "rubygems"),
    ("go.mod", "go"),
    ("composer.json", "composer"),
)


# ── package.json (npm) ──────────────────────────────────────────────

def parse_package_json(content: str) -> set[str]:
    """Extract names from dependencies + devDependencies + peerDependencies."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(data, dict):
        return set()

    names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            names.update(k for k in section.keys() if isinstance(k, str))
    return names


# ── requirements.txt (pypi) ─────────────────────────────────────────

# Captures the bare name before any version/extras/marker specifiers.
# Examples handled:
#   django==4.2
#   flask >= 2.0, < 3.0
#   uvicorn[standard]>=0.30
#   fastapi; python_version >= "3.9"
_PYPI_LINE_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def parse_requirements_txt(content: str) -> set[str]:
    """Extract bare pkg names from a pip requirements file.

    Skips comments, blank lines, ``-r``/``-c`` includes, ``-e`` editable
    installs (which are URLs we can't map), and lines starting with a URL.
    """
    names: set[str] = set()
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r", "-c", "-e", "--", "git+", "http://", "https://", "file:")):
            continue
        match = _PYPI_LINE_RE.match(line)
        if match:
            names.add(match.group(1).lower())
    return names


# ── pyproject.toml (pypi) ───────────────────────────────────────────

_PEP621_DEPS_RE = re.compile(
    r"^\s*dependencies\s*=\s*\[([^\]]*)\]", re.MULTILINE | re.DOTALL
)
_POETRY_DEPS_RE = re.compile(
    r"^\[tool\.poetry\.dependencies\](.*?)(?:^\[|\Z)",
    re.MULTILINE | re.DOTALL,
)
_POETRY_DEV_DEPS_RE = re.compile(
    r"^\[tool\.poetry\.group\.dev\.dependencies\](.*?)(?:^\[|\Z)",
    re.MULTILINE | re.DOTALL,
)
_OPTIONAL_TABLE_RE = re.compile(
    r"^\[project\.optional-dependencies\](.*?)(?:^\[|\Z)",
    re.MULTILINE | re.DOTALL,
)
_DEP_NAME_RE = re.compile(r"^\s*\"([A-Za-z0-9][A-Za-z0-9._-]*)")
_POETRY_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*=")


def parse_pyproject_toml(content: str) -> set[str]:
    """Extract names from both PEP-621 ([project] deps) and Poetry layouts.

    Deliberately regex-based — avoids adding a ``tomli``/``tomllib``
    dependency (the latter is 3.11+) and stays permissive on malformed
    files. Good-enough: we don't need version semantics.
    """
    names: set[str] = set()

    # PEP 621 — dependencies = ["pkg==1", ...]
    for block in _PEP621_DEPS_RE.findall(content):
        for line in block.splitlines():
            m = _DEP_NAME_RE.match(line)
            if m:
                names.add(m.group(1).lower())

    # PEP 621 — [project.optional-dependencies] groups = { dev = [...], ... }
    for block in _OPTIONAL_TABLE_RE.findall(content):
        for m in re.finditer(r'"([A-Za-z0-9][A-Za-z0-9._-]*)', block):
            names.add(m.group(1).lower())

    # Poetry — [tool.poetry.dependencies] + dev group
    for rx in (_POETRY_DEPS_RE, _POETRY_DEV_DEPS_RE):
        m = rx.search(content)
        if m:
            for line in m.group(1).splitlines():
                pm = _POETRY_NAME_RE.match(line.strip())
                if pm:
                    nm = pm.group(1).lower()
                    if nm != "python":  # poetry's python floor is not a dep
                        names.add(nm)

    return names


# ── Cargo.toml (cargo) ──────────────────────────────────────────────

_CARGO_SECTION_RE = re.compile(
    # Lookahead on terminator so ``findall`` doesn't consume the next
    # ``[`` and miss subsequent sections (caught by dev-dependencies test).
    r"^\[(?:dependencies|dev-dependencies|build-dependencies)\](.*?)(?=^\[|\Z)",
    re.MULTILINE | re.DOTALL,
)
_CARGO_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*=")


def parse_cargo_toml(content: str) -> set[str]:
    names: set[str] = set()
    for block in _CARGO_SECTION_RE.findall(content):
        for line in block.splitlines():
            m = _CARGO_NAME_RE.match(line.strip())
            if m:
                names.add(m.group(1).lower())
    return names


# ── Gemfile (rubygems) ──────────────────────────────────────────────

_GEM_RE = re.compile(r"""^\s*gem\s+['"]([A-Za-z0-9][A-Za-z0-9._-]*)['"]""", re.MULTILINE)


def parse_gemfile(content: str) -> set[str]:
    return {m.group(1).lower() for m in _GEM_RE.finditer(content)}


# ── go.mod (go) ─────────────────────────────────────────────────────

_GOMOD_REQUIRE_BLOCK_RE = re.compile(r"require\s*\((.*?)\)", re.DOTALL)
_GOMOD_SINGLE_REQUIRE_RE = re.compile(r"^\s*require\s+(\S+)\s+v", re.MULTILINE)
_GOMOD_BLOCK_LINE_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9./_-]*)\s+v", re.MULTILINE)


def parse_go_mod(content: str) -> set[str]:
    """Extract module paths from ``require (...)`` blocks and single-line requires.

    Module paths preserve case (``github.com/Gin-Gonic/Gin`` ≠ ``gin-gonic/gin``
    at the module level), so we do NOT lowercase here. The lookup map
    in ``dependency_map.py`` uses the canonical GitHub path casing.
    """
    names: set[str] = set()

    # require ( ... )
    for block in _GOMOD_REQUIRE_BLOCK_RE.findall(content):
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            m = _GOMOD_BLOCK_LINE_RE.match(line)
            if m:
                names.add(m.group(1))

    # require single-line (outside a block)
    for m in _GOMOD_SINGLE_REQUIRE_RE.finditer(content):
        names.add(m.group(1))

    return names


# ── composer.json (composer) ────────────────────────────────────────

def parse_composer_json(content: str) -> set[str]:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(data, dict):
        return set()

    names: set[str] = set()
    for key in ("require", "require-dev"):
        section = data.get(key)
        if isinstance(section, dict):
            names.update(
                k for k in section.keys()
                if isinstance(k, str) and k.lower() != "php"
            )
    return names


# ── Dispatcher ──────────────────────────────────────────────────────

_PARSERS = {
    "package.json": ("npm", parse_package_json),
    "requirements.txt": ("pypi", parse_requirements_txt),
    "pyproject.toml": ("pypi", parse_pyproject_toml),
    "Cargo.toml": ("cargo", parse_cargo_toml),
    "Gemfile": ("rubygems", parse_gemfile),
    "go.mod": ("go", parse_go_mod),
    "composer.json": ("composer", parse_composer_json),
}


def parse_manifest(filename: str, content: str) -> Tuple[str, set[str]]:
    """Dispatch on filename; return ``(ecosystem, deps)``.

    Unknown filenames return ``("unknown", set())``. Swallowed
    exceptions are logged at debug so malformed manifests don't break
    ``fetch_github_profile`` for an unrelated repo.
    """
    entry = _PARSERS.get(filename)
    if not entry:
        return "unknown", set()
    ecosystem, fn = entry
    try:
        return ecosystem, fn(content)
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to parse %s: %s", filename, e)
        return ecosystem, set()

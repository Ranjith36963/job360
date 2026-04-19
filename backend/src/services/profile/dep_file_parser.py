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

# Named-only-from-requirement-specifier regex. Takes a PEP-508 spec
# ("uvicorn[standard]>=0.30.0") and extracts the bare pkg name.
_REQ_SPEC_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_POETRY_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*=")


def _req_name(spec: str) -> str | None:
    """Extract the bare pkg name from a PEP-508 requirement specifier."""
    m = _REQ_SPEC_NAME_RE.match(spec)
    return m.group(1).lower() if m else None


def _parse_pyproject_via_tomllib(content: str) -> set[str] | None:
    """Parse pyproject.toml via ``tomllib`` (3.11+) or ``tomli`` if present.

    Returns ``None`` when neither lib is available, so the caller can
    fall back to the regex path. Returns an empty set if parsing
    succeeds but finds no deps.
    """
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[import-not-found]
        except ImportError:
            return None
    try:
        data = tomllib.loads(content)
    except Exception:  # noqa: BLE001
        return set()

    names: set[str] = set()
    project = data.get("project") or {}
    for spec in project.get("dependencies") or []:
        if isinstance(spec, str):
            nm = _req_name(spec)
            if nm:
                names.add(nm)
    optional = project.get("optional-dependencies") or {}
    if isinstance(optional, dict):
        for group in optional.values():
            if isinstance(group, list):
                for spec in group:
                    if isinstance(spec, str):
                        nm = _req_name(spec)
                        if nm:
                            names.add(nm)

    # Poetry layout (still common in the wild)
    poetry = ((data.get("tool") or {}).get("poetry")) or {}
    for key in ("dependencies", "dev-dependencies"):
        section = poetry.get(key)
        if isinstance(section, dict):
            for k in section.keys():
                if isinstance(k, str) and k.lower() != "python":
                    names.add(k.lower())
    groups = poetry.get("group") or {}
    if isinstance(groups, dict):
        for group in groups.values():
            if isinstance(group, dict):
                deps = group.get("dependencies") or {}
                if isinstance(deps, dict):
                    for k in deps.keys():
                        if isinstance(k, str) and k.lower() != "python":
                            names.add(k.lower())
    return names


def _find_balanced_bracket_span(text: str, start: int) -> tuple[int, int] | None:
    """Given ``[`` at ``text[start]``, return the half-open span that
    contains its matching ``]`` by counting nested brackets and
    skipping over TOML quoted strings.

    Handles ``uvicorn[standard]`` nested brackets and both `"..."` and
    `'...'` single-line strings. Triple-quoted strings are not common
    in ``dependencies = [...]`` lists so are not specially handled.
    """
    assert text[start] == "["
    depth = 1
    i = start + 1
    n = len(text)
    while i < n and depth > 0:
        c = text[i]
        if c in ('"', "'"):
            # Skip quoted string; handle simple escapes.
            quote = c
            j = i + 1
            while j < n and text[j] != quote:
                if text[j] == "\\":
                    j += 2
                else:
                    j += 1
            i = j + 1
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return (start + 1, i)
        i += 1
    return None


def _parse_pyproject_via_regex(content: str) -> set[str]:
    """Fallback parser used when neither ``tomllib`` nor ``tomli`` exists.

    Review fix #2 — the previous regex ``\\[([^\\]]*)\\]`` terminated on
    the first ``]``, silently dropping everything after the first
    dependency with extras like ``uvicorn[standard]``. This version
    bracket-counts to find the real list terminator.
    """
    names: set[str] = set()

    # PEP 621 — dependencies = [...]
    for m in re.finditer(r"^\s*dependencies\s*=\s*\[", content, re.MULTILINE):
        span = _find_balanced_bracket_span(content, m.end() - 1)
        if span is None:
            continue
        body = content[span[0]:span[1]]
        # Each dep is a quoted PEP-508 spec
        for spec_m in re.finditer(r'"([^"]+)"', body):
            nm = _req_name(spec_m.group(1))
            if nm:
                names.add(nm)

    # PEP 621 — [project.optional-dependencies] block.
    # Find the section header, read up to the next top-level ``[``.
    opt_header_re = re.compile(r"^\[project\.optional-dependencies\](.*?)(?=^\[|\Z)",
                               re.MULTILINE | re.DOTALL)
    for block_m in opt_header_re.finditer(content):
        block = block_m.group(1)
        for spec_m in re.finditer(r'"([A-Za-z0-9][A-Za-z0-9._-]*[^"]*)"', block):
            nm = _req_name(spec_m.group(1))
            if nm:
                names.add(nm)

    # Poetry — [tool.poetry.dependencies] / .dev / .group.X.dependencies
    poetry_sections = re.compile(
        r"^\[tool\.poetry(?:\.group\.[A-Za-z0-9_-]+)?\."
        r"(?:dependencies|dev-dependencies)\](.*?)(?=^\[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    for block_m in poetry_sections.finditer(content):
        for line in block_m.group(1).splitlines():
            pm = _POETRY_NAME_RE.match(line.strip())
            if pm:
                nm = pm.group(1).lower()
                if nm != "python":
                    names.add(nm)

    return names


def parse_pyproject_toml(content: str) -> set[str]:
    """Extract names from PEP-621 and Poetry layouts.

    Prefers ``tomllib`` (Python 3.11+ stdlib) so we get correct
    semantics without any regex care; falls back to ``tomli`` if
    present; falls back further to a bracket-counting regex that
    handles nested brackets like ``uvicorn[standard]``.
    """
    toml_result = _parse_pyproject_via_tomllib(content)
    if toml_result is not None:
        return toml_result
    return _parse_pyproject_via_regex(content)


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

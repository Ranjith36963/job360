"""The heavy dependencies never appear at MODULE scope — rules #11 + #16.

WHY A TEST AND NOT A LINT RULE
------------------------------
The obvious mechanisation is ruff's ``flake8-tidy-imports`` banned-api. It is the
wrong tool, and trying it is what proved it (2026-08-11): ``TID251`` flags an
import wherever it appears, so all 12 hits it produced were the **correct** lazy
imports *inside* functions. A guard that fires on the right answer gets
silenced with ``# noqa``, and a codebase trained to add ``# noqa`` to this rule is
worse off than one with no rule at all.

The rule is not "never import it". It is "never import it **at module
scope**". So the guard has to measure module scope — hence an AST walk, where
"top level" is a structural fact rather than a text pattern.

WHAT IT COSTS WHEN IT REGRESSES
-------------------------------
A single top-level import of one of these adds 150 ms - 2 s to *every* pytest
collection and every FastAPI cold start. Nobody attributes that to the commit
that caused it, which is why this is a ratchet: measured 2026-08-11, all are
clean, and this test is what keeps them that way.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"

# CLAUDE.md rules #11 + #16.
HEAVY = {
    "sentence_transformers": "heavy ML import",
    "chromadb": "heavy vector-store import",
    "rapidfuzz": "heavy native import",
    "sklearn": "heavy ML import",
}


def _module_level_heavy_imports(path: Path) -> list[tuple[int, str]]:
    """Heavy imports that execute at import time, i.e. at module scope.

    An import nested in a function/method body does NOT execute on import, which
    is the whole point of the lazy pattern — so we only walk the module's direct
    children, never descend into a FunctionDef.

    ``if TYPE_CHECKING:`` blocks are also fine: they never execute at runtime.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:  # pragma: no cover - a broken file is another test's job
        return []

    hits: list[tuple[int, str]] = []

    def scan(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in HEAVY:
                        hits.append((node.lineno, root))
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in HEAVY:
                    hits.append((node.lineno, root))
            elif isinstance(node, ast.If):
                # `if TYPE_CHECKING:` costs nothing at runtime — skip it. Any
                # other module-level `if` DOES execute, so keep scanning it.
                test = node.test
                is_type_checking = (
                    isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"
                ) or (
                    isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
                )
                if not is_type_checking:
                    scan(node.body)
                    scan(node.orelse)
            elif isinstance(node, (ast.Try, ast.With)):
                # A module-level try/except around an import still executes.
                for attr in ("body", "orelse", "finalbody"):
                    scan(getattr(node, attr, []) or [])
                for handler in getattr(node, "handlers", []) or []:
                    scan(handler.body)
            # ast.FunctionDef / AsyncFunctionDef / ClassDef: deliberately NOT
            # descended. A function-body import is the correct pattern; a class
            # body executes at import time but no heavy import belongs there and
            # descending would re-flag nothing real today.

    scan(tree.body)
    return hits


def test_no_heavy_import_at_module_scope() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for lineno, mod in _module_level_heavy_imports(path):
            rel = path.relative_to(SRC.parent).as_posix()
            offenders.append(f"{rel}:{lineno}  imports {mod!r} — {HEAVY[mod]}")

    assert not offenders, (
        "Heavy dependencies imported at MODULE scope (rules #11 + #16):\n  "
        + "\n  ".join(offenders)
        + "\n\nMove the import INSIDE the function that uses it. Every one of these "
        "executes on import, so it is paid by every pytest collection and every "
        "FastAPI cold start."
    )


@pytest.mark.parametrize("module", sorted(HEAVY))
def test_guard_can_actually_see_a_violation(module: str, tmp_path: Path) -> None:
    """The guard fires when the rule is broken.

    A guard nobody has watched fail is a guard nobody has tested. Three shipped
    broken on 2026-08-11 for exactly that reason, so this drill is part of the
    guard rather than something to remember to do by hand.
    """
    bad = tmp_path / "bad_module.py"
    bad.write_text(f"import {module}\n\n\ndef f():\n    return {module}\n", encoding="utf-8")
    assert _module_level_heavy_imports(bad) == [(1, module)]

    good = tmp_path / "good_module.py"
    good.write_text(f"def f():\n    import {module}\n    return {module}\n", encoding="utf-8")
    assert _module_level_heavy_imports(good) == [], (
        f"The guard flagged a correctly lazy import of {module!r}. That is the "
        "failure mode that made ruff's TID251 unusable here."
    )

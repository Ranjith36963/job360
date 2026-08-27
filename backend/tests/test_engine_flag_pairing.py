"""Hard rule #18, made testable: the legacy engine flags are never read alone.

Rule #18 says the gate is ``ENGINEx_ENABLED OR <legacy flag>`` and ends with
"Test BOTH names". Nothing tested it. The claim lived instead as hand-typed
lists of ``file.py:line`` call sites in five LIVING docs — and by 2026-08-27
every one of those line numbers pointed at a blank line or an unrelated
statement, because line numbers move whenever anything above them is edited.

An enumeration that rots is worse than no enumeration: it reads as verified.
So the enumeration is deleted from the docs and the invariant is pinned here,
where it is re-measured on every run and cannot silently go stale.

What is pinned: every RUNTIME READ of a legacy flag in ``backend/src`` sits in
an ``or`` expression with its ``ENGINEx_ENABLED`` partner. Read via ``ast``, so
comments, docstrings, imports and the flag's own definition are structurally
excluded rather than filtered by regex.

``SEMANTIC_ENABLED`` is deliberately NOT in this table. Engine 3 is asymmetric
on purpose — hybrid retrieval READS are or-paired, but the embedding WRITES are
gated on ``SEMANTIC_ENABLED`` alone, so a blanket pairing rule would be false.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"

# legacy flag -> the ENGINEx_ENABLED switch that must appear beside it
PARTNER = {
    "ENRICHMENT_ENABLED": "ENGINE2_ENABLED",
    "MATCHER_ENABLED": "ENGINE4_ENABLED",
}


def _or_operand_names(node: ast.AST) -> set[str]:
    """Names reachable from `node` through `or`/`not`/parentheses only.

    `A or B`, `not (A or B)` and `(A or B) and C` all count as pairing A with B;
    `A and B` does not, because that is a different gate.
    """
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        names: set[str] = set()
        for value in node.values:
            names |= _or_operand_names(value)
        return names
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _or_operand_names(node.operand)
    return set()


def _unpaired_reads(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, flag) for every read of a legacy flag with no partner."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # Every `or`-expression in the file, mapped to the names it unites.
    or_groups = [
        _or_operand_names(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)
    ]

    bad: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # ast.Name/Load excludes imports (ast.alias), the flag's own
        # assignment (ast.Store) and anything inside a comment or docstring.
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
            continue
        partner = PARTNER.get(node.id)
        if partner is None:
            continue
        if not any(node.id in g and partner in g for g in or_groups):
            bad.append((node.lineno, node.id))
    return bad


def _src_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def test_source_tree_is_discoverable():
    """Guard the guard: an empty sweep must fail, not pass vacuously."""
    files = _src_files()
    assert len(files) > 50, f"only {len(files)} source files found under {SRC}"


@pytest.mark.parametrize("flag,partner", sorted(PARTNER.items()))
def test_legacy_flag_is_actually_read_somewhere(flag: str, partner: str):
    """If a flag stops being read at all, this file is guarding nothing."""
    reads = 0
    for path in _src_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        reads += sum(
            1
            for n in ast.walk(tree)
            if isinstance(n, ast.Name)
            and isinstance(n.ctx, ast.Load)
            and n.id == flag
        )
    assert reads > 0, f"{flag} is no longer read anywhere — delete it or its partner {partner}"


def test_legacy_engine_flags_are_never_read_without_their_partner():
    """Rule #18: `ENGINEx_ENABLED OR <legacy>` at EVERY call site."""
    offenders: list[str] = []
    for path in _src_files():
        for lineno, flag in _unpaired_reads(path):
            rel = path.relative_to(SRC.parent.parent)
            offenders.append(f"{rel}:{lineno} reads {flag} without {PARTNER[flag]}")
    assert not offenders, (
        "Hard rule #18 violated — a legacy engine flag is read alone, so setting "
        "only the ENGINEx_ENABLED name will not switch this path on:\n  "
        + "\n  ".join(offenders)
    )

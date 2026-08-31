"""Hard rule #18, made testable: the legacy engine flags are never read alone.

Rule #18 says the gate is ``ENGINEx_ENABLED OR <legacy flag>`` and required
that both names be tested. Nothing tested it. The claim lived instead as
hand-typed lists of ``file.py:line`` call sites in five LIVING docs — and by
2026-08-27 every one of those line numbers pointed at a blank line or an
unrelated statement, because line numbers move whenever anything above them is
edited. Rule #18 now cites this file as its guard instead of asking a reader to
remember, so do not re-add a quoted phrase here: quoting the rule verbatim is
what made this docstring go stale the first time.

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


def _flag_name(node: ast.AST) -> str | None:
    """The flag this node reads, whether bare or attribute-style, else None.

    `ENRICHMENT_ENABLED` and `settings.ENRICHMENT_ENABLED` are the same read.
    Matching only `ast.Name` would let the second form evade the rule entirely:
    no such read exists today, but a refactor to `from src.core import settings`
    would silently take every call site out of this guard's sight while leaving
    it green — the way a guard stops guarding without anyone noticing.
    """
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        return node.attr
    return None


def _or_operand_names(node: ast.AST) -> set[str]:
    """Names reachable from `node` as top-level operands of an `or`.

    `A or B`, `not (A or B)` and `(A or B) and C` all count as pairing A with B;
    `A and B` does not, because that is a different gate.

    A name under its OWN `not` does not count: `A or not B` is not the rule-#18
    gate — it is on whenever B is off — so it must not satisfy the pairing.
    `not (A or B)` still does, because there the `not` wraps the whole `or` and
    is the BoolOp's PARENT, which the ancestor walk reaches without recursing
    through it here.
    """
    name = _flag_name(node)
    if name is not None:
        return {name}
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        names: set[str] = set()
        for value in node.values:
            names |= _or_operand_names(value)
        return names
    return set()


def _unpaired_reads(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, flag) for every read of a legacy flag with no partner."""
    return _unpaired_reads_in_source(path.read_text(encoding="utf-8"))


def _unpaired_reads_in_source(source: str) -> list[tuple[int, str]]:
    """Return (lineno, flag) for every read of a legacy flag with no partner.

    Each read is bound to ITS OWN enclosing `or` expression, walking up the
    ancestor chain. Collecting every `or` in the file and asking whether any of
    them united the two names would pass an unpaired read in any file that
    happens to contain a paired one elsewhere — `main.py` has two E2 gates, so
    deleting the partner from one would have left this green.

    Split from `_unpaired_reads` so the pairing rules can be asserted against
    small snippets below, instead of only against whatever the tree happens to
    contain today.
    """
    tree = ast.parse(source)

    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    bad: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # Load-context only, which structurally excludes imports (ast.alias),
        # the flag's own assignment (ast.Store), and anything inside a comment
        # or docstring — no regex filtering required.
        flag = _flag_name(node)
        if flag is None:
            continue
        partner = PARTNER.get(flag)
        if partner is None:
            continue
        paired = False
        cur: ast.AST | None = node
        while cur is not None and not paired:
            if isinstance(cur, ast.BoolOp) and isinstance(cur.op, ast.Or):
                names = _or_operand_names(cur)
                # `flag in names` matters: an ancestor `or` may reach the
                # partner down a branch this read does not live on.
                paired = flag in names and partner in names
            cur = parent.get(cur)
        if not paired:
            bad.append((node.lineno, flag))
    return bad


def _src_files() -> list[Path]:
    """Every backend source file the sweep reads, in a stable order."""
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


# --- the pairing rule itself, asserted on snippets --------------------------
#
# These pin what "paired" means, so the rule cannot quietly widen. The
# `or not` case is the one that slipped: `_or_operand_names` used to recurse
# through any `not`, so `ENRICHMENT_ENABLED or not ENGINE2_ENABLED` counted as
# a valid gate — it is on whenever ENGINE2 is OFF, the opposite of rule #18.

ACCEPTED = [
    "if ENGINE2_ENABLED or ENRICHMENT_ENABLED: pass",
    "if not (ENGINE2_ENABLED or ENRICHMENT_ENABLED): pass",
    "if (ENGINE2_ENABLED or ENRICHMENT_ENABLED) and selected: pass",
    "x = ENGINE2_ENABLED or ENRICHMENT_ENABLED",
    "if ENGINE4_ENABLED or MATCHER_ENABLED: pass",
    "if settings.ENGINE2_ENABLED or settings.ENRICHMENT_ENABLED: pass",
]

REJECTED = [
    "if ENRICHMENT_ENABLED: pass",
    "if ENRICHMENT_ENABLED or not ENGINE2_ENABLED: pass",
    "if not ENGINE2_ENABLED or ENRICHMENT_ENABLED: pass",
    "if ENGINE2_ENABLED and ENRICHMENT_ENABLED: pass",
    "if ENGINE2_ENABLED or SOMETHING_ELSE: pass\nif ENRICHMENT_ENABLED: pass",
    "if settings.ENRICHMENT_ENABLED: pass",
]


@pytest.mark.parametrize("source", ACCEPTED)
def test_valid_gates_are_accepted(source: str):
    """Every shape that IS the rule-#18 gate must pass."""
    assert _unpaired_reads_in_source(source) == []


@pytest.mark.parametrize("source", REJECTED)
def test_invalid_gates_are_rejected(source: str):
    """Every shape that is NOT the gate must fail, negations included."""
    assert _unpaired_reads_in_source(source), f"should have been rejected: {source!r}"


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
        # `_flag_name`, not a bare ast.Name check: otherwise a refactor to
        # `settings.<FLAG>` everywhere would trip this guard as "never read"
        # while the sweep above still saw every site.
        reads += sum(1 for n in ast.walk(tree) if _flag_name(n) == flag)
    assert reads > 0, f"{flag} is no longer read anywhere — delete it or its partner {partner}"


def test_legacy_engine_flags_are_never_read_without_their_partner():
    """Rule #18: `ENGINEx_ENABLED OR <legacy>` at every call site in `backend/src`.

    Scope is deliberate, and saying "EVERY call site" without it was an
    overclaim. `backend/scripts/engine_ablation.py` reads `ENRICHMENT_ENABLED`
    alone — correctly, because ablating one engine at a time is the whole point
    of that script. Rule #18 governs what the running system does, so the sweep
    covers `backend/src` and diagnostic tooling stays out of it.
    """
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

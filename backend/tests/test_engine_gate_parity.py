"""Rule #18's second half, pinned: every engine flag is HALF of its gate.

`ENGINE2_ENABLED` / `ENGINE3_ENABLED` / `ENGINE4_ENABLED` each ship next to a
legacy alias (`ENRICHMENT_ENABLED` / `SEMANTIC_ENABLED` / `MATCHER_ENABLED`),
and the effective condition at every call site is ``ENGINEx_ENABLED or
<legacy>`` — so `ENGINE2_ENABLED=true` turns Engine 2 on even with
`ENRICHMENT_ENABLED` false, and setting only the legacy name works too.

This was documented as a hand-maintained LIST of call sites in ARCHITECTURE.md
and README.md, with a file:line for each. Line numbers rot on any edit above
them, and by 2026-09-04 every number in that list pointed at the wrong line
while the invariant itself was still perfectly true. A doc cannot check this;
a test can. The docs now cite this test by name instead of enumerating sites.

Static analysis on purpose: the claim is about EVERY read in the tree,
including the ones on code paths no test exercises (the ARQ worker tasks that
have had no worker to run on since 2026-09-02).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

# flag -> the legacy alias that must appear in the same `or` expression.
LEGACY_ALIAS = {
    "ENGINE2_ENABLED": "ENRICHMENT_ENABLED",
    "ENGINE3_ENABLED": "SEMANTIC_ENABLED",
    "ENGINE4_ENABLED": "MATCHER_ENABLED",
}

# The module that DEFINES the flags reads no gate; skip it.
DEFINING_MODULE = ("core", "settings.py")


def _or_groups(tree: ast.AST) -> list[set[str]]:
    """Every `a or b or …` expression, as the set of bare names it mentions."""
    groups: list[set[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            groups.append(names)
    return groups


def test_every_engine_flag_read_is_ored_with_its_legacy_alias() -> None:
    ungated: list[str] = []
    seen = 0

    for path in sorted(SRC.rglob("*.py")):
        if path.relative_to(SRC).parts == DEFINING_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        groups = _or_groups(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Name) or node.id not in LEGACY_ALIAS:
                continue
            seen += 1
            alias = LEGACY_ALIAS[node.id]
            if not any(node.id in g and alias in g for g in groups):
                ungated.append(
                    f"{path.relative_to(SRC.parent)}:{node.lineno} "
                    f"reads {node.id} without `or {alias}`"
                )

    assert seen, "found no engine-flag reads at all — did the flags get renamed?"
    assert not ungated, (
        "rule #18: each engine flag is only HALF of its gate. "
        "Every read must be `ENGINEx_ENABLED or <legacy alias>`:\n  "
        + "\n  ".join(ungated)
    )


def test_engine_dimensions_fire_on_user_preferences_alone() -> None:
    """Rule #20's gate, pinned as a SHAPE, not a line number.

    The four Batch-2.9 dimensions are gated on `user_preferences` being
    present and on NOTHING else — not on an enrichment lookup, not on a flag.
    `tests/test_scorer.py::test_dims_neutral_not_zero_when_enrichment_missing`
    proves the resulting scores; this proves there is exactly one condition
    guarding them, which is the part a reader keeps re-deriving from a
    file:line that has already moved.
    """
    source = SRC / "services" / "skill_matcher.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    conditions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Attribute)
        and node.test.left.attr == "_user_preferences"
        and any(isinstance(op, (ast.IsNot, ast.Is)) for op in node.test.ops)
    ]
    assert len(conditions) == 1, (
        "the multi-dim path must have exactly ONE `self._user_preferences is "
        f"not None` gate (rule #20); found {len(conditions)}"
    )

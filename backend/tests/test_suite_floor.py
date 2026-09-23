"""The merge-gate floor in CONTRIBUTING.md is checked against a real collection.

WHY THIS EXISTS. ``CONTRIBUTING.md`` states a floor -- "reports 0 failing and
>= N collected" -- that every PR is supposed to clear. Nothing measured it.
``scripts/doc_sync_check.collected_baseline_claims`` deliberately does not:
collecting the suite needs a Postgres and that guard is a fast offline CI step,
so all it can ask is whether two docs quote the SAME number as each other.

So the floor could sit ABOVE reality and every guard stayed green. It did:
1,683 was measured on 2026-09-07, slice B (#608, 2026-09-21) deleted twenty
LLM-only test files, and the documented gate quietly became one no PR could
pass. A doc parser is the wrong instrument for that; a collection is the right
one, and this is the collection.

It asserts one direction only -- floor <= actual. Growing the suite is not a
failure; the floor is a ratchet a human raises deliberately.
"""

import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CONTRIBUTING = BACKEND.parent / "CONTRIBUTING.md"

# Matches the merge-gate wording in CONTRIBUTING.md: "1,586 collected".
_FLOOR = re.compile(r"([\d,]{3,})\s+collected")
# pytest's own `-q --collect-only` summary line: "1586 tests collected in 3.23s".
_COLLECTED = re.compile(r"(\d+)\s+tests? collected")


def documented_floors() -> list[int]:
    """Every collected-count floor CONTRIBUTING.md states, in file order."""
    text = CONTRIBUTING.read_text(encoding="utf-8")
    return [int(m.group(1).replace(",", "")) for m in _FLOOR.finditer(text)]


def collected_now() -> int:
    """Collect the suite in a subprocess and return the count pytest reports.

    A subprocess, not ``request.session.items``: the session only holds what
    THIS run selected, so a single-test invocation would report 1 and fail a
    floor it never contradicted. ``--collect-only`` imports but runs nothing.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=600,
    )
    match = _COLLECTED.search(proc.stdout)
    assert match, (
        "could not read a collected count out of `pytest --collect-only -q`.\n"
        f"exit={proc.returncode}\nstdout tail:\n{proc.stdout[-2000:]}\n"
        f"stderr tail:\n{proc.stderr[-2000:]}"
    )
    return int(match.group(1))


def test_contributing_states_a_floor() -> None:
    """A floor nobody wrote down is a gate nobody can fail."""
    floors = documented_floors()
    assert floors, f"no '<N> collected' floor found in {CONTRIBUTING}"
    assert len(set(floors)) == 1, (
        f"CONTRIBUTING.md quotes disagreeing floors: {floors}. "
        "One number, stated the same everywhere."
    )


def test_documented_floor_is_not_above_reality() -> None:
    """The gate CONTRIBUTING.md documents must be one a green PR can pass."""
    floor = documented_floors()[0]
    actual = collected_now()
    assert floor <= actual, (
        f"CONTRIBUTING.md's merge gate demands >= {floor:,} collected, but the "
        f"suite collects {actual:,}. Either the floor was never lowered after "
        f"tests were deleted, or this branch deleted tests it should not have. "
        f"Lower the floor to {actual:,} in CONTRIBUTING.md only if the deletion "
        f"was intended."
    )

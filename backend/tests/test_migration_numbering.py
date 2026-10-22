"""Migration numbering guard: runs in the required backend job, on PRs and on push to main.

The runner keys on the full stem, so two files sharing a prefix both apply and a
late low number applies out of order. These tests make that a red build.
"""

import re
from collections import defaultdict

from migrations import runner

_STEM = re.compile(r"(\d{4})_.+$")


def _ups() -> dict[int, list[str]]:
    by_prefix: dict[int, list[str]] = defaultdict(list)
    for up in sorted(runner.MIGRATIONS_DIR.glob("*.up.sql")):
        stem = up.name[: -len(".up.sql")]
        m = _STEM.match(stem)
        assert m, f"migration {up.name} does not match NNNN_name"
        by_prefix[int(m.group(1))].append(stem)
    return by_prefix


def test_every_up_has_a_down():
    missing = [
        s for stems in _ups().values() for s in stems if not (runner.MIGRATIONS_DIR / f"{s}.down.sql").exists()
    ]
    assert not missing, f"migration without a .down.sql: {missing}"


def test_migration_prefixes_are_unique():
    for prefix, stems in _ups().items():
        assert len(stems) == 1, f"duplicate migration prefix {prefix:04d}: {' and '.join(stems)}"


def test_migration_prefixes_are_contiguous():
    prefixes = set(_ups())
    gaps = sorted(set(range(max(prefixes) + 1)) - prefixes)
    assert not gaps, f"migration prefix gaps (expected 0000..{max(prefixes):04d}): {gaps}"

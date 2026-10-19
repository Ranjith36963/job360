"""WHICH routes demand a verified email — measured, not grepped.

`require_verified_user` is the only gate that can refuse a fully logged-in
user, so "which routes does it cover?" is a question every verification run and
every auth change has to answer. The answer has lived in prose:
`.claude/skills/verify-job360/SKILL.md` told the reader to
``grep "Depends(require_verified_user)"`` and asserted the result was
`routes/tailor.py` alone.

Measured 2026-10-10, that is TRUE — and true by coincidence. FC-008 also put
the gate on the decorator (``dependencies=VERIFIED_FIRST`` in `api.auth_deps`)
so auth resolves BEFORE the endpoint borrows a pooled DB handle. All five
tailor routes currently carry BOTH forms: the decorator for ordering, the
signature because the endpoint wants the `user` object. Drop the signature from
one of them — a refactor with no intent to change auth — and the grep goes on
reporting a smaller, still-plausible answer. A reader then concludes
"not gated" about a route that is, which is the direction that costs.

So the set is asserted here, off the dependency TREE (which sees both forms),
and the skill cites this test instead of prescribing a search. Changing the
gated surface becomes a deliberate edit to EXPECTED below: widening it locks
users out of a route until they verify, narrowing it drops a gate that was
chosen.

Known blind spot, recorded so the next reader does not have to find it: this
reads the ROUTERS, so a gate applied at the mount
(``app.include_router(..., dependencies=[...])`` in `api.main`) would not be
seen. Nothing does that today — every gate is on the route — but a mount-level
one would have to be asserted somewhere else.

Discovery runs in a SUBPROCESS, for the reason `tests/test_route_auth_coverage.py`
records at length: the suite mutates the route objects' module state, and an
earlier router-reading guard passed alone and failed when run after
`tests/test_api_idor.py`. A clean interpreter makes the verdict independent of
test order. No DB and no network — it imports routers and reads `dependant`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# Every route whose dependency tree reaches ``require_verified_user``, as
# "VERB /api/path". Measured 2026-10-10 at migration head 0052.
#
# All five are the tailor: the web fallback that renders a CV/cover letter.
# Decision 28 removed its LLM, but it still hands a user a generated document,
# which is the thing an unverified throwaway account should not be able to farm.
# Minting an agent token deliberately does NOT need a verified email
# (`tests/test_token_mint_gate.py::test_an_unverified_session_can_still_mint_a_token`),
# and neither does CV upload.
EXPECTED = {
    "GET /api/tailor/{job_id}",
    "GET /api/tailor/{job_id}/{doc_kind}/provenance",
    "PATCH /api/tailor/{job_id}/{doc_kind}",
    "POST /api/tailor/{job_id}/{doc_kind}/keep",
    "POST /api/tailor/{job_id}/{doc_kind}/download",
}

# Route modules are GLOBBED, not listed. `test_route_auth_coverage.py` keeps an
# explicit list on purpose — there, a forgotten module leaves a route
# unclassified and silent. Here the risk runs the other way: a new module that
# gates a route on a verified email must show up without anyone remembering to
# add it, so the filesystem is the source of truth.
_DISCOVER = r"""
import importlib, json, pathlib

def dep_names(route):
    names, dep = set(), getattr(route, "dependant", None)
    if dep is None:
        return names
    stack, seen = [dep], set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        call = getattr(node, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", ""))
        stack.extend(getattr(node, "dependencies", []) or [])
    return names

routes_dir = pathlib.Path("src/api/routes")
# Mirrors src/api/main.py: every API router mounts under "/api"; well_known
# mounts at the site root (a client resolves /.well-known against the origin).
found, modules = [], 0
for f in sorted(routes_dir.glob("*.py")):
    if f.stem == "__init__":
        continue
    mod = importlib.import_module("src.api.routes." + f.stem)
    router = getattr(mod, "router", None)
    if router is None:
        continue
    modules += 1
    prefix = "" if f.stem == "well_known" else "/api"
    for route in router.routes:
        path, methods = getattr(route, "path", None), getattr(route, "methods", None)
        if not path or not methods:
            continue
        if "require_verified_user" not in dep_names(route):
            continue
        for verb in sorted(methods - {"HEAD", "OPTIONS"}):
            found.append(verb + " " + prefix + path)
print(json.dumps({"modules": modules, "gated": sorted(found)}))
"""


def _discover() -> dict:
    """Run the discovery script and return ``{"modules": int, "gated": [str]}``.

    Deliberately NOT cached, unlike the sibling guard's ``lru_cache``: each call
    is a fresh interpreter, which is the whole point above, and two subprocess
    spawns cost a couple of seconds. A non-zero exit is raised as an
    ``AssertionError`` rather than returning an empty table, so a broken import
    fails loudly instead of letting every assertion below pass on nothing.
    """
    proc = subprocess.run(
        [sys.executable, "-c", _DISCOVER],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "Route discovery failed — this guard verifies nothing until it is "
            f"fixed.\nstdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-2000:]}"
        )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_discovery_sees_the_routers() -> None:
    """Guard the guard: too few modules would make the assertion below vacuous."""
    assert _discover()["modules"] >= 10


def test_verified_email_gate_covers_exactly_the_expected_routes() -> None:
    """The gated surface is EXACTLY ``EXPECTED`` — counting both declaration forms."""
    gated = set(_discover()["gated"])
    added, dropped = gated - EXPECTED, EXPECTED - gated
    assert not added, (
        "These routes now require a verified email and are not in EXPECTED: "
        f"{sorted(added)}. An unverified user is locked out of them. If that is "
        "intended, add them to EXPECTED with the reason."
    )
    assert not dropped, (
        f"These routes no longer require a verified email: {sorted(dropped)}. "
        "If the gate was removed on purpose, drop them from EXPECTED; otherwise "
        "an unverified account can now reach them."
    )


def test_both_declaration_forms_are_in_use() -> None:
    """Records WHY the prose recipe this test replaced was fragile.

    The skill's grep searched endpoint signatures. The gate is also declared on
    the decorator, and the dependency tree is the only place both show up. This
    asserts both forms are live in `routes/tailor.py`, so the day one of them
    goes away the reason this test exists is still on the record.
    """
    tailor = (BACKEND / "src/api/routes/tailor.py").read_text(encoding="utf-8")
    assert "dependencies=VERIFIED_FIRST" in tailor, "decorator form gone"
    assert "Depends(require_verified_user)" in tailor, "signature form gone"

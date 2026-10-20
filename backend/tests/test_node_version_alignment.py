"""Every Node major the repo names must be the SAME major (2026-10-20).

WHY THIS TEST EXISTS
--------------------
Until 2026-10-20 CI ran the frontend on Node 22 (`actions/setup-node`) while
`frontend/Dockerfile` shipped `node:20-alpine`. Type-check, lint, vitest and
`next build` all passed on a Node the production image did not run. Node 20
reached end of life 2026-04-30, so the image was also on an unsupported runtime.

This pins the two places a Node major lives in-repo -- every `FROM node:<N>` in
the frontend Dockerfile and every `node-version:` in the workflows -- to one
value. It reads both OUT OF THE FILES, never from a number typed here, so the
next bump (22 -> 24) is a one-line edit in those files and the test follows.

Every `actions/setup-node` step must name a plain major: `lts/*`, `20.x`,
`node-version-file` or no key at all fail the test (each resolves to a Node the
repo does not state). post-merge-watch.yml uses the runner's built-in Node for a
tiny script with no setup-node step; that one is not a build Node and is not
covered.

Not covered: the job NAME `Frontend (Node 20)` in ci.yml. It is a required check
by name; renaming it is an owner ruleset action, so it is excluded on purpose.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "frontend" / "Dockerfile"
WORKFLOWS = REPO / ".github" / "workflows"

_FROM = re.compile(r"^FROM\s+node:(\d+)", re.MULTILINE)
_USES = re.compile(r"^(\s*)(?:-\s+)?uses:\s*actions/setup-node@")
_KEY = re.compile(r"^\s*(node-version(?:-file)?):\s*(.*?)\s*$")


def _plain_major(raw: str) -> str | None:
    """'"22"  # note' -> '22'; anything not a bare integer ('lts/*', '20.x') -> None."""
    value = raw.split(" #", 1)[0].strip().strip("\"'")
    return value if re.fullmatch(r"\d+", value) else None


def _setup_node_problems(path: Path) -> tuple[set[str], list[str]]:
    """(majors, problems) for every actions/setup-node step in one workflow.

    A step must set `node-version` to a plain major. `lts/*`, `20.x`,
    `node-version-file` and a missing key all resolve to a Node the repo does not
    name, so each is a problem rather than a skipped line (they used to be
    ignored, which let the runner default or a floating tag drift unseen).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    majors: set[str] = set()
    problems: list[str] = []
    for i, line in enumerate(lines):
        m = _USES.match(line)
        if not m:
            continue
        # The step ends at the next non-blank line indented no deeper than its `- `.
        step_indent = len(m.group(1)) - (0 if line.lstrip().startswith("-") else 2)
        block: list[str] = []
        for nxt in lines[i + 1 :]:
            if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= step_indent:
                break
            block.append(nxt)
        keys = [k for k in map(_KEY.match, block) if k]
        where = f"{path.name}:{i + 1}"
        if any(k.group(1) == "node-version-file" for k in keys):
            problems.append(f"{where}: node-version-file is not a pinned major")
        versions = [k for k in keys if k.group(1) == "node-version"]
        if not versions:
            problems.append(f"{where}: setup-node sets no node-version (runner default)")
        for k in versions:
            major = _plain_major(k.group(2))
            if major is None:
                problems.append(f"{where}: node-version {k.group(2)!r} is not a plain major")
            else:
                majors.add(major)
    return majors, problems


def _majors() -> tuple[dict[str, set[str]], list[str]]:
    """({source: {majors}}, problems) for the Dockerfile and every workflow."""
    out: dict[str, set[str]] = {}
    problems: list[str] = []
    out[str(DOCKERFILE.relative_to(REPO))] = set(_FROM.findall(DOCKERFILE.read_text(encoding="utf-8")))
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        found, bad = _setup_node_problems(wf)
        problems += bad
        if found:
            out[str(wf.relative_to(REPO))] = found
    return out, problems


def test_dockerfile_names_node_on_every_stage():
    # The Dockerfile has three stages; a stage that dropped `node:` for another
    # base would silently leave this test comparing fewer than it thinks.
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert len(_FROM.findall(text)) == len(re.findall(r"^FROM\s", text, re.MULTILINE)) >= 3


def test_every_node_major_in_the_repo_is_the_same():
    majors, problems = _majors()
    assert not problems, "setup-node steps that do not name a plain major:\n" + "\n".join(problems)
    assert any(k.endswith("ci.yml") for k in majors), "ci.yml sets no node-version"
    distinct = set().union(*majors.values())
    assert len(distinct) == 1, f"Node majors disagree: {majors}"

"""Regenerate the three hashed backend locks from pyproject.toml (2026-10-20).

Run from backend/, with uv (https://docs.astral.sh/uv/) on PATH:

    python scripts/lock_deps.py            # keep existing pins, add what is new
    python scripts/lock_deps.py --upgrade  # move everything to the newest allowed

The locks are resolved FOR the prod image -- CPython 3.12 on x86_64 Linux
(python:3.12-slim) -- whatever machine runs this, so a Windows laptop or a 3.13
venv produces the same pins. Releases younger than COOLDOWN_DAYS are invisible
to the resolver, the same 7-day cooldown .github/dependabot.yml applies.

The runtime lock is compiled first and the dev / browser locks are constrained
to it, so every package they share has the same version and hashes: CI tests,
and the browser image ships, exactly the set the plain image ships.
`python ../scripts/check_py_lock.py` (CI, Backend job) proves they still agree.

uv, not pip-tools: pip-compile 7.6.2 (pip 26.2) downloaded every distribution
file to hash it (3.4 GB for this project) and could only resolve for the
interpreter it ran under. uv reads the index's published hashes and takes the
target as flags. The output is pip-compile's format (pins, hashes, `# via`).
"""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
RUNTIME = "requirements.lock.txt"
EXTRAS = {"requirements-dev.lock.txt": "dev", "requirements-browser.lock.txt": "browser"}
TARGET = ["--python-version", "3.12", "--python-platform", "x86_64-unknown-linux-gnu"]
COOLDOWN_DAYS = 7
COMMAND = "python scripts/lock_deps.py   (from backend/, needs uv; see that file)"


def compile_lock(uv: str, output: str, extra: str | None, upgrade: bool, cutoff: str) -> None:
    cmd = [uv, "pip", "compile", "pyproject.toml", "--quiet", "--generate-hashes", *TARGET,
           "--exclude-newer", cutoff, "--custom-compile-command", COMMAND, "--output-file", output]
    if extra:
        cmd += ["--extra", extra, "--constraints", RUNTIME]
    if upgrade:
        cmd.append("--upgrade")
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=BACKEND, check=True)


def main(argv: list[str]) -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("uv is not on PATH: https://docs.astral.sh/uv/getting-started/installation/")
        return 2
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=COOLDOWN_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    upgrade = "--upgrade" in argv
    compile_lock(uv, RUNTIME, None, upgrade, cutoff)
    for output, extra in EXTRAS.items():
        compile_lock(uv, output, extra, upgrade, cutoff)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

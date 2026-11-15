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
import json
import shutil
import subprocess
import sys
from collections.abc import Collection
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
RUNTIME = "requirements.lock.txt"
EXTRAS = {"requirements-dev.lock.txt": "dev", "requirements-browser.lock.txt": "browser"}
TARGET = ["--python-version", "3.12", "--python-platform", "x86_64-unknown-linux-gnu"]
COOLDOWN_DAYS = 7
# New CVEs are handled automatically: main() runs pip-audit on the current locks and upgrades every
# flagged package that has a fix. Add a package here only if it must skip the cooldown for some
# other reason.
# A package named here ignores the cooldown and is moved to its newest release: a known
# critical advisory outweighs a few days of waiting. Keep the reason, and drop the entry once
# the fixed release is older than COOLDOWN_DAYS (the lock then keeps it on its own).
#   certifi: GHSA-6q48-6c7w-9gw2 (critical, "certifi bundles emSign root CAs with compromised
#   intermediate CA keys"); fixed in 2026.10.19, released 2026-10-20. 2026.7.22 is affected.
#   pillow: eight CVEs (CVE-2026-112395 ... CVE-2026-112899) in 12.3.0, all fixed in 13.0.0,
#   released 2026-10-16 (needs Python >= 3.11; the prod image is 3.12).
#   opentelemetry-api: CVE-2026-126360 in 1.45.1 (pip-audit, a required check, red on every PR
#   2026-11-13); fixed in 1.45.2.
COOLDOWN_EXEMPT = ("certifi", "pillow", "opentelemetry-api")
COMMAND = "python scripts/lock_deps.py   (from backend/, needs uv; see that file)"


LOCKS = (RUNTIME, "requirements-browser.lock.txt", "requirements-dev.lock.txt")


def pip_audit_cmd() -> list[str]:
    """The same audit as .github/workflows/security.yml, plus JSON output."""
    cmd = ["pip-audit", "--progress-spinner", "off", "--require-hashes", "--disable-pip", "-f", "json"]
    for lock in LOCKS:
        cmd += ["-r", lock]
    return cmd


def run_pip_audit() -> str | None:
    """pip-audit's JSON on the current locks, or None (with a warning) if it cannot run."""
    try:
        done = subprocess.run(pip_audit_cmd(), cwd=BACKEND, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:  # not installed, or timed out
        print(f"WARNING: pip-audit could not run ({exc!r}); skipping the CVE step", flush=True)
        return None
    return done.stdout  # exit 1 just means vulns were found; the JSON is still on stdout


def parse_audit(raw: str | None) -> list[tuple[str, str, str, list[str]]]:
    """(package, version, vuln id, fix versions) for every vuln in pip-audit's JSON."""
    try:
        deps = json.loads(raw or "")["dependencies"]
    except (ValueError, KeyError, TypeError):
        print("WARNING: pip-audit gave no usable JSON (network error?); skipping the CVE step", flush=True)
        return []
    return [(d["name"].lower(), d.get("version", "?"), v["id"], list(v.get("fix_versions") or []))
            for d in deps for v in d.get("vulns") or []]


def packages_to_upgrade(findings: list[tuple[str, str, str, list[str]]]) -> set[str]:
    out: set[str] = set()
    for pkg, version, vuln, fixes in findings:
        if fixes:
            print(f"{pkg} {version}: {vuln} -> upgrading (fix {', '.join(fixes)})", flush=True)
            out.add(pkg)
        else:
            print(f"WARNING: {pkg} {version}: {vuln} has no fix version yet; leaving it", flush=True)
    return out


def cve_upgrades() -> set[str]:
    return packages_to_upgrade(parse_audit(run_pip_audit()))


def post_check() -> int:
    """Audit the new locks: non-zero if anything is still vulnerable."""
    findings = parse_audit(run_pip_audit())
    for pkg, version, vuln, fixes in findings:
        print(f"STILL VULNERABLE: {pkg} {version}: {vuln} (fix {', '.join(fixes) or 'none yet'})", flush=True)
    return 1 if findings else 0


def build_compile_cmd(uv: str, output: str, extra: str | None, upgrade: bool, cutoff: str, now: str,
                      cve: Collection[str] = ()) -> list[str]:
    cmd = [uv, "pip", "compile", "pyproject.toml", "--quiet", "--generate-hashes", *TARGET,
           "--exclude-newer", cutoff, "--custom-compile-command", COMMAND, "--output-file", output]
    if extra:
        cmd += ["--extra", extra, "--constraints", RUNTIME]
    if upgrade:
        cmd.append("--upgrade")
    for pkg in (*COOLDOWN_EXEMPT, *sorted(set(cve) - set(COOLDOWN_EXEMPT))):
        cmd += ["--exclude-newer-package", f"{pkg}={now}", "--upgrade-package", pkg]
    return cmd


def compile_lock(uv: str, output: str, extra: str | None, upgrade: bool, cutoff: str,
                 cve: Collection[str] = ()) -> None:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cmd = build_compile_cmd(uv, output, extra, upgrade, cutoff, now, cve)
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=BACKEND, check=True)


def main(argv: list[str]) -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("uv is not on PATH: https://docs.astral.sh/uv/getting-started/installation/")
        return 2
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=COOLDOWN_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    upgrade = "--upgrade" in argv
    cve = cve_upgrades()
    compile_lock(uv, RUNTIME, None, upgrade, cutoff, cve)
    for output, extra in EXTRAS.items():
        compile_lock(uv, output, extra, upgrade, cutoff, cve)
    return post_check()


if __name__ == "__main__":
    sys.exit(main(sys.argv))

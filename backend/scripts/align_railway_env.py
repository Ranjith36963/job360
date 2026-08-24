"""Align Railway service variables without any human or log seeing a secret.

WHY THIS EXISTS
---------------
Fingerprinting every environment on 2026-08-24 (sha256 prefix, never a value)
showed the four surfaces had silently drifted apart:

    variable                      .env    backend   worker
    SERPAPI_KEY                   64ch    65ch      76ch     <- three DIFFERENT keys
    JSEARCH_API_KEY               50ch    EMPTY     50ch
    CAREERJET_AFFID               32ch    32ch      ABSENT
    DFE_APPRENTICESHIPS_API_KEY   32ch    32ch      ABSENT
    FINDWORK_API_KEY              40ch    40ch      ABSENT

The nightly catalog cron runs in the WORKER, so the three ABSENT rows are why
careerjet, gov_apprenticeships and findwork recorded 0 jobs / 0.0s / no error on
three consecutive production runs. Invoked with a key they return 121, 250 and 9
jobs respectively.

WHAT IS DELIBERATELY *NOT* ALIGNED
----------------------------------
"Everything the same everywhere" would be wrong twice over:

  * frontend  — a Next.js service. It holds ONLY NEXT_PUBLIC_* values (PostHog,
                Sentry), which are compiled into the browser bundle. Copying a
                job-board API key here would publish it.
  * GitHub    — Actions secrets serve WORKFLOWS (backup, R2, Semgrep, smoke
                tests). They need no job-source keys, and adding them widens the
                blast radius of a compromised runner for no benefit.

Only backend and worker run the application, so only those two must match.

HOW IT AVOIDS EXPOSING ANYTHING
-------------------------------
Run under `railway run -s <SOURCE>`: Railway injects that service's variables
into this process. The script reads them from os.environ and passes each value to
`railway variables --set` as a subprocess ARGUMENT. The value is never printed,
never logged, and never written to a file. Only names, lengths and fingerprints
are displayed, so the result is auditable without being readable.

USAGE
-----
    # preview (default) — shows what would change, touches nothing
    railway run -s backend python scripts/align_railway_env.py --to worker

    # apply
    railway run -s backend python scripts/align_railway_env.py --to worker --apply

Add --only NAME to move a single variable.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys

# Variables the APPLICATION needs in both runtime services. Names only — the
# values live in Railway, never here.
RUNTIME_CREDENTIALS = [
    # job sources
    "REED_API_KEY",
    "ADZUNA_APP_ID",
    "ADZUNA_APP_KEY",
    "JSEARCH_API_KEY",
    "JOOBLE_API_KEY",
    "SERPAPI_KEY",
    "CAREERJET_AFFID",
    "FINDWORK_API_KEY",
    "DFE_APPRENTICESHIPS_API_KEY",
    # model providers
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "CEREBRAS_API_KEY",
    # delivery + identity
    "RESEND_API_KEY",
    "SMTP_PASSWORD",
    "SESSION_SECRET",
    "CHANNEL_ENCRYPTION_KEY",
    "SENTRY_DSN",
    "GITHUB_PERSONAL_ACCESS_TOKEN",
]


# Variables this script refuses to copy, with the reason. Copying a value is only
# safe when the SOURCE is known good; propagating a broken key just spreads it.
NEEDS_A_HUMAN = {
    "SERPAPI_KEY": (
        "all three environments hold DIFFERENT values (.env 64ch, backend 65ch, "
        "worker 76ch). A SerpApi private key is 64 hex characters, so the .env "
        "one is the only correctly-shaped candidate and backend's would just "
        "spread the 401. Set it by hand from the .env value."
    ),
}


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:8]


def describe(value: str | None) -> str:
    if value is None:
        return "ABSENT"
    if not value.strip():
        return "EMPTY"
    return "%2dch %s" % (len(value.strip()), fingerprint(value))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True, help="target Railway service name")
    ap.add_argument("--only", action="append", help="limit to this variable (repeatable)")
    ap.add_argument("--apply", action="store_true", help="actually write (default: preview)")
    args = ap.parse_args()

    wanted = args.only or RUNTIME_CREDENTIALS
    mode = "APPLY" if args.apply else "PREVIEW (nothing will change)"
    print("aligning -> service %r   [%s]\n" % (args.to, mode))
    print("%-32s %-18s %s" % ("VARIABLE", "SOURCE", "ACTION"))
    print("-" * 74)

    to_write: list[tuple[str, str]] = []
    for name in wanted:
        raw = os.environ.get(name)
        if name in NEEDS_A_HUMAN and not args.only:
            print("%-32s %-18s REFUSED — %s" % (name, describe(raw), NEEDS_A_HUMAN[name]))
            continue
        if raw is None or not raw.strip():
            print("%-32s %-18s skip — nothing to copy" % (name, describe(raw)))
            continue
        # Stripped on the way out: a trailing newline pasted into a dashboard is
        # invisible and makes a different credential wherever it is concatenated.
        to_write.append((name, raw.strip()))
        print("%-32s %-18s will set" % (name, describe(raw)))

    if not to_write:
        print("\nnothing to do")
        return 0

    if not args.apply:
        print("\n%d variable(s) ready. Re-run with --apply to write them." % len(to_write))
        return 0

    # Resolve the executable rather than relying on PATH lookup inside
    # CreateProcess: on Windows `railway` is a .cmd shim, which a bare name does
    # not find. Resolved explicitly so the argument list stays a LIST — using
    # shell=True instead would flatten the secret into a command string.
    railway = shutil.which("railway") or shutil.which("railway.cmd")
    if not railway:
        print("cannot find the `railway` executable on PATH")
        return 2

    print()
    failures = 0
    for name, value in to_write:
        cmd = [railway, "variables", "--service", args.to, "--set", f"{name}={value}"]
        # `cmd` carries the secret; it is never printed. Only the name is.
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            print("  set %-30s ok" % name)
        else:
            failures += 1
            # stderr can echo the command back, so report the code only.
            print("  set %-30s FAILED (exit %d)" % (name, proc.returncode))

    print(
        "\n%d set, %d failed.\n"
        "Railway reads variables at process start, so REDEPLOY %r for these to take effect."
        % (len(to_write) - failures, failures, args.to)
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

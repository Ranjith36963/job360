#!/usr/bin/env python3
"""rollback_gear.py — the undo the product lane's speed is borrowed against.

WHY THIS FILE EXISTS
---------------------
`.github/merge-policy.yml` lets the `product` lane auto-merge on ci+review+
security+verify+ratchets, with NO human in that loop. The only thing that
makes that safe is the promise in the policy's own words: "speed comes from
being able to UNDO, not from being sure." This is that promise made real.

Two facts, verified 2026-08-17 against docs.railway.com and pinned in
merge-policy.yml so this file never re-derives them:

  1. `deploymentRollback(id: String!)` is a mutation on Railway's PUBLIC
     GraphQL API — "Rolls back to a deployment". It restores the previous
     deployment's image AND variables, no rebuild.
  2. It only works when the TARGET deployment reports `canRollback: true`.
     An undo you have not confirmed you possess is not an undo — so this
     script always asks Railway that question before it touches the mutation,
     never assumes yes.

TWO THINGS THE DOCS GOT WRONG, CAUGHT BY ASKING THE LIVE SCHEMA
---------------------------------------------------------------
Both would have failed 100% of real calls while the drill stayed green, because
a drill that fakes the subprocess tests the shape of OUR code, never the shape
of RAILWAY's. The instrument is `railway api search <name>` / `describe <name>`,
which prints the live introspection schema:

  * The published example shows `deploymentRollback(id) { id status }`. The live
    schema says `"namedType": "Boolean", "type": "Boolean!"` — a bare scalar. A
    selection set on a scalar is a GraphQL syntax error, so that form never
    works. The mutation is written without one here, and a `true` return IS the
    whole answer.
  * `railway api` does NOT read a `{query, variables}` JSON body on stdin. Its
    own `--help`: the GraphQL document is the positional QUERY argument (or
    `--file -` for stdin), and variables come via `--variables <JSON>`. A JSON
    envelope piped to stdin is read as the DOCUMENT and fails to parse.

Authenticated in CI by the `RAILWAY_TOKEN` env var that the `railway` CLI reads
itself — this script never touches the token's VALUE, only its NAME in messages.

WHAT "PREVIOUS" MEANS
----------------------
`railway deployment list --service <name> --json` returns deployments newest
first (Railway's own ordering — this script trusts it and does not re-sort).
The target is deployments[0] excluded (that is whatever is live right now) and
then the newest entry after it that ANSWERS `canRollback: true`. Skipping [0]
matters: rolling back onto the currently-live deployment is a no-op that
reports green while fixing nothing.

Capability is ASKED, never inferred from the status string. Measured on the
live backend service 2026-08-17: one SUCCESS (the running one) and 19 REMOVED,
yet `8776b576` — REMOVED — answers `canRollback: true`. A status-based rule
finds no target and the gear refuses every time, silently, until an outage.

NO SENTINELS
-------------
Every path that cannot produce a real answer — the `railway` binary missing,
malformed JSON, fewer than two deployments, nothing capable before the current
one — refuses LOUDLY and exits non-zero. None of them print a capability
JSON that could be misread as "yes, this is safe." A watcher that silently
treats "I couldn't check" as "all clear" is the exact failure this repo has
already paid for in other loops (see docs/IMPLEMENTATION_LOG.md, watchdog_check.py).

USAGE
-----
    python scripts/rollback_gear.py --can-rollback --service backend
    python scripts/rollback_gear.py --rollback --service backend
    python scripts/rollback_gear.py --rollback --service backend --dry-run
    python scripts/rollback_gear.py --drill
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
from unittest import mock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

EXIT_OK = 0
EXIT_REFUSED = 1  # a VERDICT — measured, and the answer is "no" or "cannot"


class RailwayCliMissingError(Exception):
    """The `railway` binary is not on PATH (CI's install/auth step didn't run first)."""


class RailwayApiMalformedError(Exception):
    """`railway` ran but its output was not the JSON shape this script asked for."""


class RailwayApiError(Exception):
    """`railway` ran, returned JSON, but reported a real failure (non-zero exit, or
    the GraphQL response carried a non-empty `errors` array)."""


class NoPreviousSuccessfulDeploymentError(Exception):
    """No deployment before the current one reports canRollback=true. Nothing to land on."""


CAN_ROLLBACK_QUERY = """
query CanRollback($id: String!) {
  deployment(id: $id) {
    id
    status
    canRollback
  }
}
""".strip()

# No selection set: the live schema types this `Boolean!`. See the module
# docstring — the published `{ id status }` form is a syntax error.
ROLLBACK_MUTATION = "mutation Rollback($id: String!) { deploymentRollback(id: $id) }"


def run_railway(args: list[str], timeout: int = 60) -> str:
    """Shell out to `railway <args>` and return raw stdout.

    The one place a missing binary or a non-zero exit becomes a named
    exception instead of a traceback or, worse, an empty string that a caller
    mistakes for "no deployments".
    """
    try:
        proc = subprocess.run(
            ["railway", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RailwayCliMissingError(
            "`railway` is not on PATH. In CI this means the step that installs the "
            "Railway CLI (and authenticates it via the RAILWAY_TOKEN env var) did not "
            "run, or ran after this script."
        ) from exc
    if proc.returncode != 0:
        raise RailwayApiError(
            f"`railway {' '.join(args)}` exited {proc.returncode}: {proc.stderr.strip()[:300]}"
        )
    return proc.stdout


def list_deployments(service: str) -> list[dict[str, object]]:
    """All known deployments for `service`, newest first (Railway's own ordering).

    Raises RailwayApiMalformedError rather than returning `[]` on bad input —
    an empty list here would be indistinguishable from "this service really
    has zero deployments", which is never true for a live Railway service.
    """
    raw = run_railway(["deployment", "list", "--service", service, "--json"])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RailwayApiMalformedError(
            f"`railway deployment list --service {service} --json` did not return valid "
            f"JSON: {exc}"
        ) from exc
    if not isinstance(data, list):
        raise RailwayApiMalformedError(
            f"expected a JSON array of deployments from `railway deployment list`, got "
            f"{type(data).__name__}"
        )
    return data


def find_rollback_target(service: str) -> dict[str, object]:
    """The newest earlier deployment Railway says it can roll back to.

    Deliberately skips deployments[0] (whatever is live right now): "previous"
    means "before the one running now". Rolling back onto yourself is a no-op
    dressed up as a fix.

    ASK THE CAPABILITY, DO NOT INFER IT FROM A STATUS STRING
    -------------------------------------------------------
    The first version of this function looked for the first earlier deployment
    with `status == "SUCCESS"`. Measured against the live backend service on
    2026-08-17, that rule finds NOTHING and the gear refuses every time:

        686b0df6  SUCCESS   <- only the one running now
        8776b576  REMOVED
        649f4c77  REMOVED
        ... 17 more, all REMOVED

    Railway marks a superseded deployment REMOVED, not SUCCESS. But REMOVED is
    about whether it is RUNNING, not whether it can be restored — asked
    directly, that same 8776b576 answers:

        { "id": "8776b576-...", "status": "REMOVED", "canRollback": true }

    So the status string was never the question. `canRollback` is the question,
    and it is a field, so this asks it. A status-shaped guess would have made
    the whole product lane's safety promise silently inert — refusing during an
    outage, which is the worst possible moment to discover it.
    """
    deployments = list_deployments(service)
    if len(deployments) < 2:
        raise NoPreviousSuccessfulDeploymentError(
            f"'{service}' has {len(deployments)} known deployment(s) — too few to have a "
            f"'previous' one. There is nothing to roll back to."
        )
    unknown: list[str] = []
    for dep in deployments[1:]:
        dep_id = str(dep.get("id", ""))
        if not dep_id:
            continue
        try:
            if query_can_rollback(dep_id):
                return dep
        except (RailwayApiError, RailwayApiMalformedError) as exc:
            # Could not measure this one. Record it and keep looking — but the
            # count is reported below, so an all-unmeasurable run never reads
            # as "no target exists".
            unknown.append(f"{dep_id[:12]}: {exc}")
    detail = ""
    if unknown:
        detail = (
            f" {len(unknown)} of them could not be checked at all "
            f"(first: {unknown[0][:160]}) — treat this as a BROKEN instrument, not "
            f"as proof no target exists."
        )
    exc = NoPreviousSuccessfulDeploymentError(
        f"'{service}': no deployment before the current one reports canRollback=true "
        f"(checked {len(deployments) - 1} older deployment(s)).{detail} There is nothing "
        f"safe to roll back to."
    )
    # TWO DIFFERENT ANSWERS THAT LOOK THE SAME FROM OUTSIDE:
    #   "I checked all 19 and none can be rolled back to"  -> a MEASUREMENT
    #   "I could not check them"                           -> a BROKEN INSTRUMENT
    # Collapsing those is how a dead detector reads as good news. The caller
    # gets both numbers and decides.
    exc.checked = len(deployments) - 1  # type: ignore[attr-defined]
    exc.unmeasurable = len(unknown)  # type: ignore[attr-defined]
    raise exc


def railway_api(payload: dict[str, object], timeout: int = 45) -> dict[str, object]:
    """Run one GraphQL document through `railway api` and return its `data` object.

    `railway api` is the only door to `deploymentRollback` — the CLI has no
    `rollback` verb of its own.

    CALLING SHAPE (from `railway api --help`, not from a doc page):
        railway api --file - --variables '<json>'   <<< "<graphql document>"

    The document goes over STDIN via `--file -`, so braces and newlines never
    have to survive shell quoting. Variables go as one JSON string in
    `--variables`. The earlier version piped a `{query, variables}` envelope to
    bare `railway api`; the CLI reads piped stdin as the DOCUMENT itself, so
    that envelope was parsed as GraphQL and every call would have failed.

    `payload` keeps the familiar `{"query": ..., "variables": {...}}` shape
    because every caller and the drill already speak it — it is unpacked here.
    """
    query = str(payload.get("query", ""))
    variables = payload.get("variables") or {}
    args = ["railway", "api", "--file", "-"]
    if variables:
        args += ["--variables", json.dumps(variables)]
    try:
        proc = subprocess.run(
            args,
            input=query,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RailwayCliMissingError(
            "`railway` is not on PATH — cannot reach the GraphQL API without it."
        ) from exc
    if proc.returncode != 0:
        raise RailwayApiError(f"`railway api` exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RailwayApiMalformedError(f"`railway api` did not return valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RailwayApiMalformedError(
            f"expected a JSON object from `railway api`, got {type(data).__name__}"
        )
    if data.get("errors"):
        raise RailwayApiError(
            f"Railway's GraphQL API returned errors: {json.dumps(data['errors'])[:300]}"
        )
    result = data.get("data")
    if not isinstance(result, dict):
        raise RailwayApiMalformedError(f"`railway api` response had no usable `data` object: {data!r}")
    return result


def query_can_rollback(deployment_id: str) -> bool:
    """Ask Railway directly whether `deployment_id` reports `canRollback: true`.

    Never inferred from status or age — those are correlated with
    rollback-ability, not equal to it, and this is the one flag Railway
    itself says is authoritative.
    """
    data = railway_api({"query": CAN_ROLLBACK_QUERY, "variables": {"id": deployment_id}})
    deployment = data.get("deployment")
    if not isinstance(deployment, dict) or "canRollback" not in deployment:
        raise RailwayApiMalformedError(
            f"deployment {deployment_id} query did not return canRollback: {data!r}"
        )
    return bool(deployment["canRollback"])


def cmd_can_rollback(service: str) -> int:
    """`--can-rollback`: print capability JSON, exit 0 if capable, 1 otherwise.

    Every failure branch here — no target, CLI missing, malformed JSON —
    returns 1 WITHOUT printing the capability JSON. Only a real, completed
    measurement (true or false) gets printed, so nothing downstream can
    mistake "I could not check" for "I checked, and it's fine."
    """
    try:
        target = find_rollback_target(service)
    except NoPreviousSuccessfulDeploymentError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        # A COMPLETED search that found nothing is a real reading, so it prints.
        # A search that could not measure everything prints nothing, so no
        # downstream step can read "I don't know" as "no, and that's fine".
        if getattr(exc, "unmeasurable", 1) == 0:
            print(json.dumps({
                "service": service,
                "target_deployment_id": None,
                "can_rollback": False,
                "checked": getattr(exc, "checked", 0),
            }))
        return EXIT_REFUSED
    except (RailwayCliMissingError, RailwayApiMalformedError, RailwayApiError) as exc:
        print(f"::error::could not find a rollback target for '{service}': {exc}", file=sys.stderr)
        return EXIT_REFUSED

    target_id = str(target["id"])
    try:
        can_rollback = query_can_rollback(target_id)
    except (RailwayCliMissingError, RailwayApiMalformedError, RailwayApiError) as exc:
        print(
            f"::error::could not confirm rollback capability for '{service}' "
            f"(target {target_id}): {exc}",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    result = {"service": service, "target_deployment_id": target_id, "can_rollback": can_rollback}
    print(json.dumps(result))
    return EXIT_OK if can_rollback else EXIT_REFUSED


def cmd_rollback(service: str, dry_run: bool) -> int:
    """`--rollback`: roll `service` back to its previous SUCCESSFUL deployment.

    Re-derives and re-confirms `canRollback` itself rather than trusting a
    caller's earlier `--can-rollback` check — those are two separate process
    invocations in the workflow, and Railway's answer could have changed
    between them.
    """
    try:
        target = find_rollback_target(service)
    except NoPreviousSuccessfulDeploymentError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (RailwayCliMissingError, RailwayApiMalformedError, RailwayApiError) as exc:
        print(f"::error::could not find a rollback target for '{service}': {exc}", file=sys.stderr)
        return EXIT_REFUSED

    target_id = str(target["id"])
    try:
        can_rollback = query_can_rollback(target_id)
    except (RailwayCliMissingError, RailwayApiMalformedError, RailwayApiError) as exc:
        print(f"::error::could not confirm canRollback for {target_id}: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    if not can_rollback:
        print(
            f"::error::deployment {target_id} reports canRollback=false. Railway will refuse "
            f"the mutation, so refusing before we call it — an undo you have not confirmed you "
            f"possess is not an undo.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    payload: dict[str, object] = {"query": ROLLBACK_MUTATION, "variables": {"id": target_id}}

    if dry_run:
        print("DRY RUN — this call would be sent to `railway api` and was NOT sent:")
        print(json.dumps(payload, indent=2))
        return EXIT_OK

    try:
        data = railway_api(payload)
    except (RailwayCliMissingError, RailwayApiMalformedError, RailwayApiError) as exc:
        print(f"::error::deploymentRollback mutation failed for {target_id}: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    # `deploymentRollback` is typed `Boolean!` by the live schema. A missing key
    # or a non-bool means Railway answered something we do not understand, and
    # an undo we cannot confirm happened must never report success.
    result = data.get("deploymentRollback")
    if not isinstance(result, bool):
        print(
            f"::error::Railway accepted the call but did not return a boolean "
            f"(schema says Boolean!): {data!r}",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    if not result:
        print(
            f"::error::deploymentRollback returned false for {target_id} — "
            f"Railway refused the rollback. Production is UNCHANGED.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    print(json.dumps({"service": service, "rolled_back_to": target_id, "ok": True}))
    return EXIT_OK


# ─────────────────────────────────────────────────────────────────────────────
# The drill. `subprocess.run` is monkeypatched for every case — this file never
# makes a live Railway call on its own account. Same shape as revert_gear.py's
# drill: the negative controls matter as much as the one happy path, because a
# gear that only ever refuses is one nobody will trust at 2 a.m. either.
# ─────────────────────────────────────────────────────────────────────────────

# SHAPED LIKE THE REAL SERVICE, NOT LIKE A TIDY EXAMPLE. Copied from an actual
# `railway deployment list --service backend --json` on 2026-08-17: exactly one
# SUCCESS (the live one) and every earlier deployment REMOVED. The original
# fixture had two SUCCESSes in a row, a state this service never reaches, and
# that pretty fixture is precisely what hid the status-vs-canRollback bug.
_DEPLOYMENTS_TWO_SUCCESS: list[dict[str, object]] = [
    {"id": "dep-current", "status": "SUCCESS", "meta": {"createdAt": "2026-08-17T10:00:00Z"}},
    {"id": "dep-prev", "status": "REMOVED", "meta": {"createdAt": "2026-08-16T10:00:00Z"}},
    {"id": "dep-older", "status": "REMOVED", "meta": {"createdAt": "2026-08-15T10:00:00Z"}},
]
# A service with NOTHING behind it. Deliberately one entry, not two: with two,
# this case became indistinguishable from (b) once capability replaced status —
# both would be "asked everything, nothing capable", which is a MEASUREMENT and
# rightly prints one. The case worth keeping separate is the one where there is
# nothing to ask about at all, so no capability JSON may be printed.
_DEPLOYMENTS_NO_PREVIOUS: list[dict[str, object]] = [
    {"id": "dep-current", "status": "SUCCESS", "meta": {}},
]


def _fake_run_factory(scenario: str):  # type: ignore[no-untyped-def]
    """Build a `subprocess.run` replacement for one drill scenario.

    Dispatches on argv shape the same way the real functions build it
    (`["railway", "deployment", "list", ...]` vs `["railway", "api"]` with the
    GraphQL body on stdin), so this exercises the real call sites, not a
    reimplementation of them.
    """

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if scenario == "cli_missing":
            raise FileNotFoundError("[Errno 2] No such file or directory: 'railway'")

        is_deployment_list = len(cmd) >= 2 and cmd[1] == "deployment"
        is_api = len(cmd) >= 2 and cmd[1] == "api"

        if is_deployment_list:
            if scenario == "malformed_json":
                return subprocess.CompletedProcess(cmd, 0, stdout="not valid json {{{", stderr="")
            if scenario == "no_previous":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps(_DEPLOYMENTS_NO_PREVIOUS), stderr=""
                )
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(_DEPLOYMENTS_TWO_SUCCESS), stderr=""
            )

        if is_api:
            # THE FAKE MUST MIRROR RAILWAY, NOT US. The first version of this
            # block parsed stdin as a `{query, variables}` JSON envelope —
            # exactly the assumption the calling code made — so the drill was
            # green while every real call would have failed. A fake built from
            # the same guess as the code under test proves only that the guess
            # is self-consistent.
            #
            # Real contract, from `railway api --help`:
            #   document -> stdin (with `--file -`), variables -> `--variables <json>`
            # Anything else is a wiring bug and is refused here rather than
            # quietly tolerated.
            if "--file" not in cmd or cmd[cmd.index("--file") + 1] != "-":
                raise AssertionError(
                    "railway api was called without `--file -`; the CLI would read "
                    "piped stdin as the GraphQL document"
                )
            raw_input = kwargs.get("input")
            query = raw_input if isinstance(raw_input, str) else ""
            variables: dict[str, object] = {}
            if "--variables" in cmd:
                variables = json.loads(cmd[cmd.index("--variables") + 1])

            if "deploymentRollback" in query:
                # The live schema types this `Boolean!`. A selection set on it is
                # a syntax error, so the fake refuses one — that is what makes
                # this drill able to catch the bug it was blind to before.
                if "deploymentRollback(id: $id) {" in query.replace("\n", " "):
                    raise AssertionError(
                        "deploymentRollback was called with a selection set; the live "
                        "schema types it Boolean! and Railway would reject the document"
                    )
                if not variables.get("id"):
                    raise AssertionError("deploymentRollback called with no `id` variable")
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps({"data": {"deploymentRollback": True}}), stderr=""
                )
            can_rollback = scenario == "capable"
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "data": {
                            "deployment": {
                                "id": variables.get("id"),
                                "status": "SUCCESS",
                                "canRollback": can_rollback,
                            }
                        }
                    }
                ),
                stderr="",
            )

        raise AssertionError(f"drill fake got an unexpected command: {cmd}")

    return _fake_run


def self_drill() -> int:
    """Exercise the 5 mandatory cases against monkeypatched fakes. Never a live call."""
    print("DRILL — the rollback gear must refuse everything except a real, capable target.")
    print("=" * 72)
    results: list[tuple[str, bool, str]] = []

    def ok(name: str, passed: bool, detail: str = "") -> None:
        results.append((name, passed, "" if passed else detail))

    service = "backend"

    for scenario, want_rc, want_can, label in (
        ("capable", EXIT_OK, True, "(a) capable path: previous SUCCESS deployment + canRollback:true"),
        ("not_capable", EXIT_REFUSED, False, "(b) canRollback:false is reported honestly, not upgraded to a pass"),
    ):
        buf = io.StringIO()
        with mock.patch("subprocess.run", side_effect=_fake_run_factory(scenario)):
            with contextlib.redirect_stdout(buf):
                rc = cmd_can_rollback(service)
        lines = [line for line in buf.getvalue().splitlines() if line.strip()]
        payload = json.loads(lines[-1]) if lines else {}
        ok(
            label,
            rc == want_rc
            # When nothing is capable there IS no target, so the id is null.
            # The old expectation ("dep-prev" either way) encoded the discarded
            # status-based design, where a target was picked before capability
            # was ever asked about.
            and payload.get("can_rollback") == want_can
            and payload.get("target_deployment_id") == ("dep-prev" if want_can else None),
            f"rc={rc} payload={payload}",
        )

    for scenario, label in (
        ("cli_missing", "(c) `railway` binary missing is refused, never swallowed into a default"),
        ("malformed_json", "(d) malformed JSON from railway is refused, not parsed as empty/healthy"),
        ("no_previous", "(e) nothing behind the live deployment -> refused, no fabricated target"),
    ):
        buf = io.StringIO()
        with mock.patch("subprocess.run", side_effect=_fake_run_factory(scenario)):
            with contextlib.redirect_stdout(buf):
                rc = cmd_can_rollback(service)
        ok(
            label,
            rc == EXIT_REFUSED and buf.getvalue().strip() == "",
            f"rc={rc} stdout={buf.getvalue()!r} (a capability JSON must NEVER print here)",
        )

    print()
    for name, passed, detail in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        if not passed and detail:
            print(f"         {detail[:220]}")
    n = sum(1 for _, p, _ in results if p)
    print()
    print("=" * 72)
    print(f"DRILL RESULT: {n}/{len(results)} passed")
    if n != len(results):
        print("The rollback gear did something plausible with a bad input. Do not trust it.")
        return EXIT_REFUSED
    print("Every unmeasurable/incapable case was refused loudly; the capable case really answered yes.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--service", help="Railway service name, e.g. backend or frontend")
    ap.add_argument(
        "--can-rollback",
        action="store_true",
        help="print capability JSON for the previous SUCCESSFUL deployment; exit 0 if capable, 1 if not",
    )
    ap.add_argument(
        "--rollback",
        action="store_true",
        help="roll the service back to its previous SUCCESSFUL deployment",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="with --rollback, print the GraphQL call that WOULD be made and exit 0 without calling",
    )
    ap.add_argument("--drill", action="store_true", help="self-test with fakes; never touches a real service")
    args = ap.parse_args(argv)

    try:
        if args.drill:
            return self_drill()

        if args.can_rollback and args.rollback:
            ap.error("--can-rollback and --rollback are mutually exclusive")
        if not args.can_rollback and not args.rollback:
            ap.error("one of --can-rollback, --rollback or --drill is required")
        if not args.service:
            ap.error("--service is required")
        if args.dry_run and not args.rollback:
            ap.error("--dry-run only applies to --rollback")

        if args.can_rollback:
            return cmd_can_rollback(args.service)
        return cmd_rollback(args.service, dry_run=args.dry_run)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - the gear must fail loud, never silently pass
        print(f"::error::rollback_gear crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())

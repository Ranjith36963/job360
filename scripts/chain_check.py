#!/usr/bin/env python3
"""chain_check.py — prove the harness chain is CONNECTED, not merely present.

    detect -> triage -> repair -> verify -> PR -> owner

Every node in that chain existed the whole time. The WIRES kept breaking, one
at a time, silently, for weeks:

  * detect->triage died 17 days on two spellings of one bot name
    ("app/github-actions" from the REST API vs "github-actions[bot]" from the
    webhook payload). Two ends of one wire, spelled differently. Nothing in the
    repo ever compared them.
  * triage->repair: repair.yml had not run in 17 days and no test noticed,
    because repair.yml was perfectly healthy — nobody was calling it.
  * verify->scorecard measured 0, then stopped, and stopping looked like zero.

Every NODE was green. That is the whole disease: node health is not wire
health, and a checker that inspects nodes will report GREEN forever.

WHAT THIS DOES
--------------
It reads .github/workflows/*.yml and .github/actions/*/action.yml as a GRAPH of
producers and consumers, then asserts every hop actually connects:

  W1  a `steps.X.outputs.Y` consumer names an output step X really writes
  W2  ...and step X exists at all
  W3  a `needs.J.outputs.Y` consumer names an output job J really declares,
      and J is actually in that job's `needs:`
  W4  a gate never reads a step DEFINED LATER (guard #9's exact shape: the
      marker printed after the function already returned — passes review,
      unreachable in production)
  W5  a gate comparing an output to a literal names a literal the producer can
      really emit. This is the bot-name bug, generalised: one end of the wire
      writes `yes`, the other end tests `== 'true'`, forever false, exit 0.
  W6  `gh workflow run F` names a workflow file that exists
  W7  ...and that file actually accepts `workflow_dispatch`
  W8  ...and every `-f key=` it passes is an input that file declares
  W9  `on.workflow_run.workflows: [N]` names a workflow whose `name:` is N
  W10 `uses: ./.github/actions/A` passes only inputs A declares, and every
      required input of A
  W11 (warn) a gate that runs under `always()`/`failure()` reads an output from
      a producer that can die before writing it -- the gate then silently reads
      "" and the handoff is skipped GREEN. This is the shape of the alert path
      whose missing key exited 0 for weeks.
  W13 (error) a comparison whose LITERAL its SOURCE can never produce --
              gh --jq .author.login returns `app/NAME`, the webhook returns
              `NAME[bot]`; triage.yml auto-authorisation had NEVER fired.
  W12 (warn) the same actor is spelled two ways in two places -- the literal
      detect->triage bug.

Then it prints a MAP of the dispatch graph, and with --live asks GitHub when
each target workflow last ran and last SUCCEEDED, so a wire that has been dead
for 17 days says "17 days" instead of nothing.

It is a text map and an exit code. Deliberately not a graph database: a code
graph that rebuilds ten thousand nodes per commit with zero consumers is stale
by design and nobody reads it.

DRILLING IT (the part that matters)
-----------------------------------
    python scripts/chain_check.py --drill

Copies .github to a temp dir, breaks ONE wire on purpose, and demands this
checker go RED and NAME THE EXACT HOP. Three breaks, three different
mechanisms (a renamed output, a misspelt dispatch target, a literal that no
longer matches), plus a NEGATIVE CONTROL -- a comment-only edit that must
produce NO new finding, which is what stops "any edit turns it red" from
passing as a checker.

And the drill itself is drillable:

    python scripts/chain_check.py --drill --break-checker W1

blinds check W1 and the drill must FAIL. If a self-test cannot be made to fail
on purpose, it is not a self-test.

Usage:
    python scripts/chain_check.py                 # text map + findings
    python scripts/chain_check.py --live          # + last-run ages from `gh`
    python scripts/chain_check.py --json
    python scripts/chain_check.py --strict        # warnings fail too
    python scripts/chain_check.py --drill
    python scripts/chain_check.py --drill --break-checker W6
Exit: 0 clean, 1 broken wire(s), 2 the checker could not run.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - environment problem, not a wire problem
    print("chain_check: PyYAML is required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)

# Windows consoles default to cp1252 and would crash on the box-drawing glyphs.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LINE_KEY = "__line__"

# Checks that BREAK the chain if they fire, vs. checks that only smell bad.
ERROR_CHECKS = {"W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9", "W10", "W13"}
WARN_CHECKS = {"W11", "W12"}
ALL_CHECKS = ERROR_CHECKS | WARN_CHECKS


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────


class _LineLoader(yaml.SafeLoader):
    """SafeLoader that records the source line of every mapping."""


def _construct_mapping(loader: _LineLoader, node: Any) -> dict:
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        try:
            hash(key)
        except TypeError:
            key = str(key)
        mapping[key] = loader.construct_object(value_node, deep=False)
    mapping[LINE_KEY] = node.start_mark.line + 1
    return mapping


_LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _line_of(obj: Any, default: int = 0) -> int:
    if isinstance(obj, dict):
        value = obj.get(LINE_KEY, default)
        return value if isinstance(value, int) else default
    return default


@dataclass
class Step:
    job: str
    index: int
    id: str | None
    name: str
    uses: str | None
    run: str
    if_expr: str
    line: int
    continue_on_error: bool
    with_: dict = field(default_factory=dict)
    # output key -> set of literal values it can write, or None when the value
    # is a shell variable / unknowable statically.
    writes: dict[str, set[str] | None] = field(default_factory=dict)


@dataclass
class Job:
    name: str
    needs: list[str]
    outputs: dict[str, str]
    if_expr: str
    line: int
    steps: list[Step] = field(default_factory=list)


@dataclass
class Workflow:
    path: Path
    file: str
    name: str
    on: dict
    jobs: dict[str, Job]
    text: str

    @property
    def dispatch_inputs(self) -> set[str] | None:
        """Declared workflow_dispatch inputs, or None if not dispatchable."""
        block = self.on.get("workflow_dispatch", "__missing__")
        if block == "__missing__":
            return None
        if not isinstance(block, dict):
            return set()
        inputs = block.get("inputs") or {}
        if not isinstance(inputs, dict):
            return set()
        return {k for k in inputs if k != LINE_KEY}


@dataclass
class LocalAction:
    ref: str  # e.g. ./.github/actions/alert
    inputs: dict[str, dict]
    outputs: set[str]


@dataclass
class Finding:
    check: str
    severity: str  # error | warn
    hop: str  # the WIRE, written so a human can find it: file:line producer->consumer
    message: str
    file: str
    line: int

    def key(self) -> str:
        return f"{self.check}|{self.file}:{self.line}|{self.hop}"

    def render(self) -> str:
        tag = "BROKEN" if self.severity == "error" else "SUSPECT"
        return f"  [{self.check} {tag}] {self.file}:{self.line}  {self.hop}\n      {self.message}"


# `echo "key=value" >> "$GITHUB_OUTPUT"` — the only form whose VALUE we trust.
_ECHO_TO_OUTPUT = re.compile(
    r"""^\s*echo\s+(?P<q>["']?)(?P<key>[A-Za-z_][A-Za-z0-9_-]*)=(?P<val>.*?)(?P=q)?\s*>>\s*["']?\$(?:\{)?GITHUB_OUTPUT""",
    re.MULTILINE,
)
# Any `key=` / `key<<` written inside a step that redirects to $GITHUB_OUTPUT.
# Catches brace-block writers:  { echo "body<<$d"; ...; } >> "$GITHUB_OUTPUT"
_ANY_OUTPUT_KEY = re.compile(
    r"""^\s*(?:echo|printf)\s+(?:-e\s+)?["']?(?P<key>[A-Za-z_][A-Za-z0-9_-]*)(?P<op>=|<<)""",
    re.MULTILINE,
)
_MENTIONS_OUTPUT = re.compile(r"\$\{?GITHUB_OUTPUT")
# A shell heredoc whose opening line ALSO redirects to $GITHUB_OUTPUT:
#     python - <<'PY' >> "$GITHUB_OUTPUT"
#     cat <<EOF >> "$GITHUB_OUTPUT"
# The `\s` before `<<` is load-bearing. It is what separates a real heredoc from
# the GHA multiline-output form `echo "body<<$d" >> "$GITHUB_OUTPUT"`, where the
# `<<` is glued to the key and the following lines are a VALUE, not a script.
_HEREDOC_OPEN = re.compile(
    r"""^(?P<pre>.*?)\s<<-?\s*(?P<q>['"]?)(?P<delim>[A-Za-z_][A-Za-z0-9_]*)(?P=q)"""
)
_OUTPUT_REDIRECT = re.compile(r""">>\s*["']?\$\{?GITHUB_OUTPUT""")
# Inside a python heredoc, a write is a print — never an `echo`. A checker that
# knows only echo/printf reads such a step as writing NOTHING, and then reports
# every live wire out of it as broken (see _iter_output_writes).
_PY_OUTPUT_KEY = re.compile(
    r"""^\s*(?:print|sys\.stdout\.write)\s*\(\s*[rbfu]*(?P<q>["'])(?P<key>[A-Za-z_][A-Za-z0-9_-]*)="""
)
# Inside a plain `cat <<EOF >> "$GITHUB_OUTPUT"` body the lines ARE the pairs.
_PLAIN_OUTPUT_KEY = re.compile(r"""^\s*(?P<key>[A-Za-z_][A-Za-z0-9_-]*)=""")
# A whole value that is one GHA expression comparison -> renders exactly true/false.
_BOOL_EXPR = re.compile(r"^\$\{\{\s*[^}]*?(?:==|!=|>|<|>=|<=)[^}]*?\}\}$")

_STEP_OUT_REF = re.compile(r"steps\.(?P<id>[A-Za-z0-9_-]+)\.outputs\.(?P<key>[A-Za-z0-9_-]+)")
_NEEDS_OUT_REF = re.compile(r"needs\.(?P<job>[A-Za-z0-9_-]+)\.outputs\.(?P<key>[A-Za-z0-9_-]+)")
_GH_WORKFLOW_RUN = re.compile(r"gh\s+workflow\s+run\s+(?P<target>[A-Za-z0-9_.\-/]+)(?P<rest>[^\n]*)")
_DISPATCH_INPUT = re.compile(r"-f\s+(?P<key>[A-Za-z0-9_-]+)=")


def _iter_output_writes(run: str) -> Iterator[tuple[int, str, str]]:
    """Yield (1-indexed line, key, op) for every $GITHUB_OUTPUT write in `run`.

    `op` is "=" when the value sits on that same line, and "<<" when it does not
    (a heredoc body), which is exactly the distinction _parse_writes needs to
    decide whether it may enumerate the literal values.

    There are TWO writer shapes in this repo and the checker must read both:

        echo "key=value" >> "$GITHUB_OUTPUT"        <- flat
        python - <<'PY' >> "$GITHUB_OUTPUT"         <- heredoc body
        print(f"key={value}")
        PY

    A shape the checker cannot read is indistinguishable from a step that writes
    nothing, so every live wire out of that step gets reported as severed. That
    is a false negative in the INSTRUMENT, and it is the same failure this whole
    script exists to catch: an instrument must count the way its consumer does.

    Heredoc bodies are only read when the OPENING line also redirects to
    $GITHUB_OUTPUT, so a heredoc writing a temp file is never mistaken for one
    writing outputs.
    """
    if not run:
        return
    delim: str | None = None
    body_is_python = False
    for n, line in enumerate(run.splitlines(), start=1):
        if delim is not None:
            if line.strip() == delim:
                delim = None
                continue
            body = _PY_OUTPUT_KEY if body_is_python else _PLAIN_OUTPUT_KEY
            hit = body.match(line)
            if hit is not None:
                # The value is an f-string / shell interpolation. We will not
                # pretend to evaluate it, so it is unknowable — the same
                # contract the `key<<EOF` multiline form already has.
                yield n, hit.group("key"), "<<"
            continue
        opener = _HEREDOC_OPEN.match(line)
        if opener is not None and _OUTPUT_REDIRECT.search(line):
            delim = opener.group("delim")
            body_is_python = "python" in opener.group("pre")
            continue
        for m in _ANY_OUTPUT_KEY.finditer(line):
            yield n, m.group("key"), m.group("op")


def _parse_writes(run: str) -> dict[str, set[str] | None]:
    """Which output keys can this run: block set, and to which literal values."""
    if not run or not _MENTIONS_OUTPUT.search(run):
        return {}
    writes: dict[str, set[str] | None] = {}
    for _, key, op in _iter_output_writes(run):
        writes.setdefault(key, set())
        if op == "<<":  # heredoc body — value is unknowable here
            writes[key] = None
    for m in _ECHO_TO_OUTPUT.finditer(run):
        key, raw = m.group("key"), m.group("val").strip().strip('"').strip("'")
        current = writes.get(key, set())
        if current is None:
            continue
        if _BOOL_EXPR.match(raw):
            current |= {"true", "false"}  # GHA renders comparisons as true/false
        elif "${{" in raw or "$" in raw or "`" in raw:
            writes[key] = None  # dynamic — we cannot enumerate it, so we do not
            continue
        else:
            current.add(raw)
        writes[key] = current
    # Any key we saw ONLY through the loose pattern (never in a same-line
    # redirect) has an unknown value set.
    for key, vals in list(writes.items()):
        if vals is not None and not vals:
            writes[key] = None
    return writes


def _write_depth(run: str, key: str) -> int:
    """1-indexed line inside `run` where `key` is first written to $GITHUB_OUTPUT.

    Depth is the whole point: a key written on line 1 cannot be skipped, but a key
    written on line 12 is preceded by 11 commands that can abort first and leave the
    output empty. Returns 0 when the key is never written here.
    """
    if not run or not key:
        return 0
    for n, found, _ in _iter_output_writes(run):
        if found == key:
            return n
    return 0


def _tests_nonempty_literal_only(if_expr: str, ref: str) -> bool:
    """True when `if_expr` gates on `ref` matching a literal but never guards emptiness.

    `if: steps.x.outputs.k == 'yes'` is false when the producer died and left `k`
    empty — indistinguishable from a genuine 'no', so the handoff is skipped with a
    green tick. Comparing against '' (or testing `ref` bare for truthiness) IS the
    emptiness guard, so those forms are not flagged.
    """
    if not if_expr or ref not in if_expr:
        return False
    esc = re.escape(ref)
    # An explicit emptiness guard in any of its usual spellings.
    if re.search(rf"{esc}\s*(==|!=)\s*(''|\"\")", if_expr):
        return False
    if re.search(rf"{esc}\s*(&&|\|\|)|(&&|\|\|)\s*{esc}\b", if_expr):
        return False  # used bare in a boolean chain — that IS a truthiness test
    return bool(re.search(rf"{esc}\s*(==|!=)\s*('[^']*'|\"[^\"]*\")", if_expr))


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def load_workflows(root: Path) -> tuple[list[Workflow], list[str]]:
    wf_dir = root / ".github" / "workflows"
    workflows: list[Workflow] = []
    errors: list[str] = []
    for path in sorted(list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml"))):
        text = path.read_text(encoding="utf-8")
        try:
            doc = yaml.load(text, Loader=_LineLoader)
        except yaml.YAMLError as exc:
            errors.append(f"{path.name}: unparseable YAML: {exc}")
            continue
        if not isinstance(doc, dict):
            errors.append(f"{path.name}: top level is not a mapping")
            continue
        # YAML 1.1: a bare `on:` key parses as the boolean True.
        on_block = doc.get("on", doc.get(True, {}))
        if not isinstance(on_block, dict):
            on_block = {k: {} for k in ([on_block] if isinstance(on_block, str) else on_block or [])}
        jobs: dict[str, Job] = {}
        for job_name, job_doc in (doc.get("jobs") or {}).items():
            if job_name == LINE_KEY or not isinstance(job_doc, dict):
                continue
            needs = job_doc.get("needs") or []
            if isinstance(needs, str):
                needs = [needs]
            outputs = {k: _as_str(v) for k, v in (job_doc.get("outputs") or {}).items() if k != LINE_KEY}
            job = Job(
                name=job_name,
                needs=[n for n in needs if isinstance(n, str)],
                outputs=outputs,
                if_expr=_as_str(job_doc.get("if")),
                line=_line_of(job_doc),
            )
            for idx, step_doc in enumerate(job_doc.get("steps") or []):
                if not isinstance(step_doc, dict):
                    continue
                run = _as_str(step_doc.get("run"))
                with_block = step_doc.get("with") or {}
                job.steps.append(
                    Step(
                        job=job_name,
                        index=idx,
                        id=step_doc.get("id"),
                        name=_as_str(step_doc.get("name")) or _as_str(step_doc.get("uses")) or f"step {idx}",
                        uses=step_doc.get("uses"),
                        run=run,
                        if_expr=_as_str(step_doc.get("if")),
                        line=_line_of(step_doc),
                        continue_on_error=bool(step_doc.get("continue-on-error")),
                        with_={k: v for k, v in with_block.items() if k != LINE_KEY} if isinstance(with_block, dict) else {},
                        writes=_parse_writes(run),
                    )
                )
            jobs[job_name] = job
        workflows.append(
            Workflow(
                path=path,
                file=path.name,
                name=_as_str(doc.get("name")) or path.stem,
                on=on_block,
                jobs=jobs,
                text=text,
            )
        )
    return workflows, errors


def load_local_actions(root: Path) -> dict[str, LocalAction]:
    actions: dict[str, LocalAction] = {}
    base = root / ".github" / "actions"
    if not base.is_dir():
        return actions
    for action_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for candidate in ("action.yml", "action.yaml"):
            path = action_dir / candidate
            if not path.exists():
                continue
            try:
                doc = yaml.load(path.read_text(encoding="utf-8"), Loader=_LineLoader) or {}
            except yaml.YAMLError:
                doc = {}
            inputs = {k: v for k, v in (doc.get("inputs") or {}).items() if k != LINE_KEY}
            outputs = {k for k in (doc.get("outputs") or {}) if k != LINE_KEY}
            ref = f"./.github/actions/{action_dir.name}"
            actions[ref] = LocalAction(ref=ref, inputs=inputs, outputs=outputs)
            break
    return actions


# ─────────────────────────────────────────────────────────────────────────────
# The checks
# ─────────────────────────────────────────────────────────────────────────────


def _find_line(wf: Workflow, needle: str, near: int = 0) -> int:
    """Best line for a reference: the occurrence closest to `near`."""
    lines = wf.text.splitlines()
    hits = [i + 1 for i, line in enumerate(lines) if needle in line]
    if not hits:
        return near
    if not near:
        return hits[0]
    return min(hits, key=lambda ln: abs(ln - near))


def _expr_sites(job: Job) -> list[tuple[str, str, int, Step | None]]:
    """Every place an expression is evaluated: (kind, text, line, step)."""
    sites: list[tuple[str, str, int, Step | None]] = []
    if job.if_expr:
        sites.append(("job-if", job.if_expr, job.line, None))
    for key, expr in job.outputs.items():
        sites.append((f"job-output:{key}", expr, job.line, None))
    for step in job.steps:
        if step.if_expr:
            sites.append(("step-if", step.if_expr, step.line, step))
        blob = step.run + "\n" + json.dumps(step.with_, default=str)
        sites.append(("step-body", blob, step.line, step))
    return sites


def check_wires(
    workflows: list[Workflow],
    actions: dict[str, LocalAction],
    disabled: set[str],
) -> list[Finding]:
    findings: list[Finding] = []
    by_file = {wf.file: wf for wf in workflows}
    by_name = {wf.name: wf for wf in workflows}

    def add(check: str, severity: str, wf: Workflow, line: int, hop: str, message: str) -> None:
        if check in disabled:
            return
        findings.append(Finding(check, severity, hop, message, wf.file, line))

    for wf in workflows:
        for job in wf.jobs.values():
            steps_by_id = {s.id: s for s in job.steps if s.id}
            for kind, text, line, step in _expr_sites(job):
                # ── W1/W2: steps.X.outputs.Y must be something X really sets.
                for m in _STEP_OUT_REF.finditer(text):
                    sid, key = m.group("id"), m.group("key")
                    ref = f"steps.{sid}.outputs.{key}"
                    ref_line = _find_line(wf, ref, line)
                    producer = steps_by_id.get(sid)
                    if producer is None:
                        add(
                            "W2",
                            "error",
                            wf,
                            ref_line,
                            f"{job.name}: ?? -> {kind}",
                            f"reads `{ref}` but no step in job `{job.name}` has `id: {sid}`. "
                            f"This gate can never be true; the handoff is dead and silent.",
                        )
                        continue
                    if producer.uses:
                        local = actions.get(str(producer.uses).rstrip("/"))
                        if local is not None and key not in local.outputs:
                            add(
                                "W1",
                                "error",
                                wf,
                                ref_line,
                                f"{job.name}: {sid} -> {kind}",
                                f"reads `{ref}` but local action `{local.ref}` declares no output "
                                f"`{key}` (declares: {sorted(local.outputs) or 'none'}).",
                            )
                        continue  # third-party action: outputs are not ours to know
                    if key not in producer.writes:
                        wrote = sorted(producer.writes) or "nothing"
                        add(
                            "W1",
                            "error",
                            wf,
                            ref_line,
                            f"{job.name}: {sid} -> {kind}",
                            f"reads `{ref}` but step `{sid}` ({producer.name!r}) never writes "
                            f"`{key}` to $GITHUB_OUTPUT; it writes: {wrote}. "
                            f"Producer and consumer spell this wire differently.",
                        )
                        continue
                    # ── W4: the gate reads a step defined LATER. Guard #9.
                    if step is not None and kind == "step-if" and producer.index > step.index:
                        add(
                            "W4",
                            "error",
                            wf,
                            ref_line,
                            f"{job.name}: {sid} -> {step.name!r}",
                            f"gate reads `{ref}` but step `{sid}` runs AFTER this one "
                            f"(step {producer.index} vs {step.index}). The value is always empty: "
                            f"unreachable in production, invisible in review.",
                        )
                    # ── W5: compared against a literal the producer cannot emit.
                    if kind in ("step-if", "job-if"):
                        vals = producer.writes.get(key)
                        if vals:
                            for cmp_m in re.finditer(
                                re.escape(ref) + r"\s*(==|!=)\s*'([^']*)'", text
                            ):
                                op, lit = cmp_m.group(1), cmp_m.group(2)
                                if op == "==" and lit not in vals:
                                    add(
                                        "W5",
                                        "error",
                                        wf,
                                        ref_line,
                                        f"{job.name}: {sid} -> {kind}",
                                        f"gate tests `{ref} == '{lit}'` but step `{sid}` only ever "
                                        f"writes {sorted(vals)}. Two ends of one wire, spelled "
                                        f"differently — the gate is false forever and exits GREEN.",
                                    )
                    # ── W11: gate runs even when the producer died.
                    #
                    # Only a REAL hole, not every always() gate. Three conditions
                    # must all hold, and each one removes a whole class of noise:
                    #   1. the gate runs under always()/failure() — so it will be
                    #      evaluated even on the run where the producer died;
                    #   2. it tests the output against a NON-EMPTY literal and
                    #      never against '' — i.e. it does not handle the
                    #      producer-died case, it just quietly reads "" and is
                    #      false. (`!= ''` and `== ''` gates ARE the correct
                    #      defensive form and are deliberately not flagged.)
                    #   3. the producer writes the key deep in a multi-command
                    #      script, so there is real work that can abort first.
                    #      A one-line `echo have=... >> $GITHUB_OUTPUT` cannot
                    #      die halfway and is not worth a warning.
                    if (
                        step is not None
                        and kind == "step-if"
                        and re.search(r"\b(always|failure)\s*\(\s*\)", step.if_expr)
                        and not producer.continue_on_error
                        and _tests_nonempty_literal_only(step.if_expr, ref)
                        and _write_depth(producer.run, key) >= 3
                    ):
                        add(
                            "W11",
                            "warn",
                            wf,
                            ref_line,
                            f"{job.name}: {sid} ~> {step.name!r}",
                            f"this step runs under always()/failure() and gates on "
                            f"`{ref}` matching a literal, but producer `{sid}` only writes `{key}` "
                            f"on line {_write_depth(producer.run, key)} of its script — anything "
                            f"above that aborting leaves the output EMPTY, the gate false, and "
                            f"this handoff skipped with a green tick. Gate on `!= ''` too, or the "
                            f"failure of the producer silently cancels the alarm.",
                        )

                # ── W3: needs.J.outputs.Y must be declared by J, and J needed.
                for m in _NEEDS_OUT_REF.finditer(text):
                    dep, key = m.group("job"), m.group("key")
                    ref = f"needs.{dep}.outputs.{key}"
                    ref_line = _find_line(wf, ref, line)
                    if dep not in wf.jobs:
                        add("W3", "error", wf, ref_line, f"{job.name}: {dep} -> {kind}",
                            f"reads `{ref}` but this workflow has no job `{dep}`.")
                        continue
                    if dep not in job.needs:
                        add("W3", "error", wf, ref_line, f"{job.name}: {dep} -> {kind}",
                            f"reads `{ref}` but job `{job.name}` does not list `{dep}` in `needs:` "
                            f"(needs: {job.needs or 'none'}). The value is always empty.")
                    if key not in wf.jobs[dep].outputs:
                        declared = sorted(wf.jobs[dep].outputs) or "none"
                        add("W3", "error", wf, ref_line, f"{job.name}: {dep} -> {kind}",
                            f"reads `{ref}` but job `{dep}` declares no output `{key}` "
                            f"(declares: {declared}).")

            for step in job.steps:
                # ── W6/W7/W8: `gh workflow run F -f k=v`
                for m in _GH_WORKFLOW_RUN.finditer(step.run):
                    target, rest = m.group("target"), m.group("rest")
                    if "$" in target or "{" in target:
                        continue  # computed target — nothing static to check
                    line = _find_line(wf, f"gh workflow run {target}", step.line)
                    hop = f"{wf.file} -> {target}"
                    tgt = by_file.get(target)
                    if tgt is None and not target.endswith((".yml", ".yaml")):
                        tgt = by_name.get(target)
                    if tgt is None:
                        add("W6", "error", wf, line, hop,
                            f"dispatches `{target}` but no such workflow exists in "
                            f".github/workflows. This hop goes nowhere.")
                        continue
                    if tgt.dispatch_inputs is None:
                        add("W7", "error", wf, line, hop,
                            f"dispatches `{target}` but that workflow has no `workflow_dispatch:` "
                            f"trigger, so the call is refused. (A label or issue event raised with "
                            f"GITHUB_TOKEN starts nothing — dispatch is the only way back in.)")
                        continue
                    for im in _DISPATCH_INPUT.finditer(rest):
                        key = im.group("key")
                        if key not in tgt.dispatch_inputs:
                            add("W8", "error", wf, line, hop,
                                f"passes `-f {key}=` to `{target}`, which declares inputs "
                                f"{sorted(tgt.dispatch_inputs) or 'none'}. The receiver never sees "
                                f"it: same wire, two spellings.")

                # ── W10: local composite action inputs.
                ref = str(step.uses).rstrip("/") if step.uses else ""
                if ref.startswith("./"):
                    local = actions.get(ref)
                    if local is None:
                        add("W10", "error", wf, step.line, f"{wf.file} -> {ref}",
                            f"uses local action `{ref}` but no action.yml exists there.")
                        continue
                    for key in step.with_:
                        if key not in local.inputs:
                            add("W10", "error", wf, step.line, f"{wf.file} -> {ref}",
                                f"passes `with: {key}` but `{ref}` declares inputs "
                                f"{sorted(local.inputs)}.")
                    for key, spec in local.inputs.items():
                        required = isinstance(spec, dict) and spec.get("required") and "default" not in spec
                        if required and key not in step.with_:
                            add("W10", "error", wf, step.line, f"{wf.file} -> {ref}",
                                f"does not pass required input `{key}` of `{ref}`.")

        # ── W9: workflow_run must name a workflow that exists.
        wr = wf.on.get("workflow_run")
        if isinstance(wr, dict):
            for target in wr.get("workflows") or []:
                if not isinstance(target, str):
                    continue
                if target not in by_name and target not in by_file:
                    add("W9", "error", wf, _find_line(wf, target, _line_of(wr)),
                        f"{target} -> {wf.file}",
                        f"triggers on `workflow_run` of `{target}`, but no workflow declares "
                        f"`name: {target}`. Nothing will ever start this workflow.")

    findings.extend(_check_actor_spellings(workflows, disabled))
    return findings


_ACTOR_CMP = re.compile(
    r"""(?P<field>[A-Za-z0-9_.\[\]"']*(?:login|actor|author)[A-Za-z0-9_.\[\]"']*)\s*(?:==|=)\s*['"](?P<lit>[^'"]+)['"]"""
)
_ACTOR_CMP_SH = re.compile(r"""grep\s+-qFx\s+["'](?P<lit>[^'"]+)["']""")


def _normalise_actor(lit: str) -> str:
    out = lit.lower()
    out = re.sub(r"^app/", "", out)
    out = re.sub(r"\[bot\]$", "", out)
    return out


def _check_actor_spellings(workflows: list[Workflow], disabled: set[str]) -> list[Finding]:
    """The literal detect->triage bug: one identity, two spellings, 17 days dead."""
    if "W12" in disabled:
        return []
    seen: dict[str, dict[str, list[tuple[str, int]]]] = {}
    for wf in workflows:
        for lineno, line in enumerate(wf.text.splitlines(), start=1):
            for m in list(_ACTOR_CMP.finditer(line)) + list(_ACTOR_CMP_SH.finditer(line)):
                lit = m.group("lit")
                if "bot" not in lit.lower() and "actions" not in lit.lower():
                    continue
                seen.setdefault(_normalise_actor(lit), {}).setdefault(lit, []).append((wf.file, lineno))
    out: list[Finding] = []
    for norm, spellings in seen.items():
        if len(spellings) < 2:
            continue
        where = "; ".join(
            f"{s!r} at " + ", ".join(f"{f}:{ln}" for f, ln in sites) for s, sites in sorted(spellings.items())
        )
        first_file, first_line = sorted(sites for sites in spellings.values())[0][0]
        out.append(
            Finding(
                "W12",
                "warn",
                f"actor `{norm}`",
                f"the same identity is spelled {len(spellings)} ways: {where}. "
                f"The REST API and the webhook payload disagree about bot names; when two ends of "
                f"one wire disagree, the wire is dead and everything still looks green.",
                first_file,
                first_line,
            )
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# The map
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Hop:
    src: str
    dst: str
    mechanism: str
    line: int


def dispatch_edges(workflows: list[Workflow]) -> list[Hop]:
    edges: list[Hop] = []
    by_name = {wf.name: wf.file for wf in workflows}
    for wf in workflows:
        for job in wf.jobs.values():
            for step in job.steps:
                for m in _GH_WORKFLOW_RUN.finditer(step.run):
                    target = m.group("target")
                    if "$" in target or "{" in target:
                        continue
                    edges.append(Hop(wf.file, target, "gh workflow run", step.line))
        wr = wf.on.get("workflow_run")
        if isinstance(wr, dict):
            for target in wr.get("workflows") or []:
                if isinstance(target, str):
                    edges.append(Hop(by_name.get(target, target), wf.file, "workflow_run", _line_of(wr)))
    # dedupe on (src, dst, mechanism)
    unique: dict[tuple[str, str, str], Hop] = {}
    for hop in edges:
        unique.setdefault((hop.src, hop.dst, hop.mechanism), hop)
    return sorted(unique.values(), key=lambda h: (h.dst, h.src))


def gh_last_runs(files: list[str]) -> dict[str, dict]:
    """Ask GitHub when each workflow last ran and last SUCCEEDED. Best-effort."""
    out: dict[str, dict] = {}
    for wf_file in files:
        info: dict[str, Any] = {"last_run": None, "last_success": None, "error": None}
        try:
            proc = subprocess.run(
                ["gh", "run", "list", "--workflow", wf_file, "--limit", "30",
                 "--json", "conclusion,createdAt,status"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            if proc.returncode != 0:
                info["error"] = (proc.stderr or "gh failed").strip().splitlines()[:1]
                out[wf_file] = info
                continue
            runs = json.loads(proc.stdout or "[]")
            now = datetime.now(timezone.utc)
            for run in runs:
                when = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
                age = (now - when).days
                if info["last_run"] is None:
                    info["last_run"] = age
                if run.get("conclusion") == "success" and info["last_success"] is None:
                    info["last_success"] = age
        except Exception as exc:  # noqa: BLE001 - liveness is best-effort by design
            info["error"] = str(exc)
        out[wf_file] = info
    return out


def render_map(workflows: list[Workflow], hops: list[Hop], live: dict[str, dict] | None) -> str:
    lines = ["", "CHAIN MAP — who hands off to whom", "=" * 60]
    known = {wf.file for wf in workflows}
    inbound: dict[str, list[Hop]] = {}
    for hop in hops:
        inbound.setdefault(hop.dst, []).append(hop)
    for dst in sorted(inbound):
        exists = dst in known
        head = f"\n  -> {dst}" + ("" if exists else "   *** NO SUCH WORKFLOW ***")
        if live is not None and exists:
            info = live.get(dst) or {}
            if info.get("error"):
                head += "   [liveness unknown: gh error]"
            elif info.get("last_run") is None:
                head += "   [NEVER RUN]"
            else:
                succ = info.get("last_success")
                succ_txt = "never succeeded" if succ is None else f"last success {succ}d ago"
                head += f"   [last run {info['last_run']}d ago, {succ_txt}]"
        lines.append(head)
        for hop in sorted(inbound[dst], key=lambda h: h.src):
            lines.append(f"       {hop.src}:{hop.line}  --{hop.mechanism}-->")
    orphans = sorted(f for f in known if f not in inbound)
    lines.append("\n  (no inbound dispatch — started by schedule/push/PR only):")
    lines.append("       " + ", ".join(orphans))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────────────────────


# Which spelling of a bot login each SOURCE actually produces. Measured
# 2026-08-16 against issues #335, #334 and #330 in this repo, not assumed.
_GH_CLI_SRC = re.compile(r"\$\(\s*gh\s+[^)]*--jq\s+\.?(?:author|user)\.login[^)]*\)")
_BOT_SUFFIX = re.compile(r"^[A-Za-z0-9_-]+\[bot\]$")
_APP_PREFIX = re.compile(r"^app/[A-Za-z0-9_-]+$")


def _check_source_vs_literal(workflows: list[Workflow], disabled: set[str]) -> list[Finding]:
    """W13 — a comparison whose LITERAL cannot be produced by its SOURCE.

    W12 catches one identity spelled two ways in the repo. It could not catch
    this, because triage.yml spelled it ONE way everywhere: `github-actions[bot]`.
    The mismatch was not inside the file at all -- it was between the file and
    the API it reads.

        issue_author=$(gh issue view "$n" --json author --jq .author.login)
        ...
        echo "$issue_author" | grep -qFx "github-actions[bot]"

    `gh` returns `app/github-actions` for a bot. The webhook payload returns
    `github-actions[bot]`. So that condition could never be true, and the
    auto-authorisation path had NEVER FIRED since the day it was written --
    with nothing red, because falling through to "needs a human" looks exactly
    like a careful gate doing its job.

    A dead branch that fails SAFE is still dead, and it is the hardest kind to
    notice precisely because its failure mode is indistinguishable from working.
    """
    if "W13" in disabled:
        return []
    out: list[Finding] = []
    for wf in workflows:
        lines = wf.text.splitlines()
        # Variables fed by the gh CLI, which renders bot logins as `app/NAME`.
        gh_vars: dict[str, int] = {}
        for lineno, line in enumerate(lines, start=1):
            m = re.match(r"\s*([A-Za-z_][A-Za-z_0-9]*)=(.*)", line)
            if m and _GH_CLI_SRC.search(m.group(2)):
                gh_vars[m.group(1)] = lineno
        if not gh_vars:
            continue
        for lineno, line in enumerate(lines, start=1):
            for var, src_line in gh_vars.items():
                if f"${var}" not in line and f"${{{var}}}" not in line:
                    continue
                lits = re.findall(r"""["']([A-Za-z0-9_./\[\]-]+)["']""", line)
                bot_lits = [x for x in lits if _BOT_SUFFIX.match(x)]
                if not bot_lits:
                    continue
                # Accepting the `app/` spelling too is the fix; if it is already
                # on this line, the wire is whole.
                if any(_APP_PREFIX.match(x) for x in lits):
                    continue
                out.append(Finding(
                    "W13", "error", f"{var} <- gh --jq .author.login",
                    f"line {src_line} fills `{var}` from the gh CLI, which renders a bot "
                    f"login as `app/NAME`. This line compares it against "
                    f"{', '.join(repr(b) for b in bot_lits)} — the WEBHOOK spelling. The two "
                    f"never match, so this branch can never be taken, and nothing goes red "
                    f"because falling through looks exactly like the gate working. Accept "
                    f"both spellings (grep -qFx -e 'NAME[bot]' -e 'app/NAME').",
                    wf.file, lineno))
    return out


def run_checks(root: Path, disabled: set[str]) -> tuple[list[Finding], list[Workflow], list[str]]:
    workflows, parse_errors = load_workflows(root)
    actions = load_local_actions(root)
    findings = check_wires(workflows, actions, disabled)
    findings += _check_source_vs_literal(workflows, disabled)
    return findings, workflows, parse_errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=None, help="repo root (default: git toplevel)")
    parser.add_argument("--live", action="store_true", help="ask `gh` how long each hop has been dead")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--strict", action="store_true", help="warnings fail the run too")
    parser.add_argument("--no-map", action="store_true")
    parser.add_argument("--drill", action="store_true", help="break wires on purpose; the checker must go RED")
    parser.add_argument("--break-checker", default=None, metavar="Wn",
                        help="blind one check (W1..W13) — used to prove the DRILL itself can fail")
    args = parser.parse_args(argv)

    if args.root:
        root = Path(args.root)
    else:
        try:
            top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", check=True).stdout.strip()
            root = Path(top)
        except Exception:
            root = Path.cwd()

    disabled = {args.break_checker.upper()} if args.break_checker else set()
    if disabled and not disabled <= ALL_CHECKS:
        print(f"chain_check: --break-checker must be one of {sorted(ALL_CHECKS)}", file=sys.stderr)
        return 2

    if args.drill:
        return run_drill(root, disabled)

    if not (root / ".github" / "workflows").is_dir():
        print(f"chain_check: no .github/workflows under {root}", file=sys.stderr)
        return 2

    findings, workflows, parse_errors = run_checks(root, disabled)
    errors = [f for f in findings if f.severity == "error"]
    warns = [f for f in findings if f.severity == "warn"]
    hops = dispatch_edges(workflows)
    live = None
    if args.live:
        targets = sorted({h.dst for h in hops} | {h.src for h in hops})
        live = gh_last_runs([t for t in targets if t.endswith((".yml", ".yaml"))])

    if args.as_json:
        print(json.dumps({
            "workflows": len(workflows),
            "hops": [vars(h) for h in hops],
            "parse_errors": parse_errors,
            "findings": [vars(f) for f in findings],
            "live": live,
        }, indent=2, default=str))
    else:
        print(f"chain_check: {len(workflows)} workflows, {len(hops)} handoff wires, "
              f"{len(errors)} broken, {len(warns)} suspect")
        for err in parse_errors:
            print(f"  [PARSE] {err}")
        if errors:
            print("\nBROKEN WIRES (the chain is severed here)")
            print("-" * 60)
            for f in errors:
                print(f.render())
        if warns:
            print("\nSUSPECT WIRES (still connected, but this is how they die)")
            print("-" * 60)
            for f in warns:
                print(f.render())
        if not args.no_map:
            print(render_map(workflows, hops, live))
        if not errors and not warns:
            print("\nEvery wire connects: producers and consumers agree on names, "
                  "no gate is unreachable, every dispatch target exists.")

    if parse_errors:
        return 2
    if errors:
        return 1
    if warns and args.strict:
        return 1
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# The drill — break a wire on purpose and demand this checker names it
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Drill:
    name: str
    mechanism: str
    target: str  # workflow file to mutate, or to CREATE when `create` is set
    before: str
    after: str
    expect_check: str
    expect_in_message: str
    # A synthetic workflow written into the copy instead of mutating a real one.
    # Needed for shapes no workflow in THIS repo happens to use yet: without it
    # the checker's blind spots can only be found by a branch that trips them.
    create: str | None = None
    # This drill must add ZERO findings. Used for a shape the checker is
    # supposed to read correctly — the false-POSITIVE direction.
    expect_silent: bool = False


# A producer that writes its outputs from a python heredoc. Nothing in .github/
# uses this shape today, so only a synthetic file can hold the checker to it.
# The wire here is REAL: `parse` prints `readyz_url=`, the job output reads it.
_HEREDOC_OK = """\
name: drill — heredoc writer (synthetic)
on:
  workflow_dispatch:
jobs:
  policy:
    runs-on: ubuntu-latest
    outputs:
      readyz_url: ${{ steps.parse.outputs.readyz_url }}
    steps:
      - name: Parse the dial
        id: parse
        run: |
          python - <<'PY' >> "$GITHUB_OUTPUT"
          print(f"readyz_url={watch['readyz_url']}")
          PY
"""

# The same shape with the wire genuinely cut: the producer prints `readyz_url`,
# the consumer reads `readyz_urlx`. Teaching the checker to READ heredocs must
# not teach it to EXCUSE them — a fix that silences the shape wholesale would
# make this drill pass by going blind, which is the failure it is here to catch.
_HEREDOC_BROKEN = _HEREDOC_OK.replace(
    "readyz_url: ${{ steps.parse.outputs.readyz_url }}",
    "readyz_url: ${{ steps.parse.outputs.readyz_urlx }}",
)

# W13 and W6 used to mutate real lines in triage.yml — the auto-authorisation
# branch (`grep -qFx -e "github-actions[bot]" -e "app/github-actions"`) and its
# `gh workflow run repair.yml` dispatch. Both were deleted 2026-09-05 when
# triage.yml became diagnose-and-comment only (repair.yml itself was removed
# as unwired scaffolding). The checks (`_check_source_vs_literal` for W13, the
# `gh workflow run` scan for W6) are still real and still general, so each
# drill now supplies its own synthetic fixture instead of depending on
# triage.yml's current wording.
_W13_BROKEN = """\
name: drill — W13 gh-literal mismatch (synthetic)
on:
  workflow_dispatch:
jobs:
  x:
    runs-on: ubuntu-latest
    steps:
      - name: check bot author
        run: |
          issue_author=$(gh issue view "$n" --json author --jq .author.login)
          echo "$issue_author" | grep -qFx "github-actions[bot]"
"""

_W6_BROKEN = """\
name: drill — W6 misspelt dispatch target (synthetic)
on:
  workflow_dispatch:
jobs:
  x:
    runs-on: ubuntu-latest
    steps:
      - name: dispatch
        run: gh workflow run totally-fake-workflow.yml -f pr="$n"
"""


DRILLS = [
    Drill(
        name="renamed output",
        mechanism="W1 — producer/consumer name mismatch",
        target="triage.yml",
        before='echo "have=${{ secrets.CLAUDE_CODE_OAUTH_TOKEN != \'\' }}" >> "$GITHUB_OUTPUT"',
        after='echo "haz=${{ secrets.CLAUDE_CODE_OAUTH_TOKEN != \'\' }}" >> "$GITHUB_OUTPUT"',
        expect_check="W1",
        expect_in_message="steps.token.outputs.have",
    ),
    Drill(
        name="a literal its source can never produce",
        mechanism="W13 -- the mismatch is between the file and the API it reads",
        target="drill-w13-broken.yml",
        before="",
        after="",
        expect_check="W13",
        expect_in_message="app/NAME",
        create=_W13_BROKEN,
    ),
    Drill(
        name="misspelt dispatch target",
        mechanism="W6 — hop points at a workflow that does not exist",
        target="drill-w6-broken.yml",
        before="",
        after="",
        expect_check="W6",
        expect_in_message="totally-fake-workflow.yml",
        create=_W6_BROKEN,
    ),
    Drill(
        name="literal the producer cannot emit",
        mechanism="W5 — two ends of one wire, spelled differently",
        target="triage.yml",
        before="steps.token.outputs.have == 'true'",
        after="steps.token.outputs.have == 'yes'",
        expect_check="W5",
        expect_in_message="'yes'",
    ),
    Drill(
        name="heredoc writer is READ, not mistaken for silence",
        mechanism="W1 must stay QUIET — the producer really does write these keys",
        target="drill-heredoc-ok.yml",
        before="",
        after="",
        expect_check="",
        expect_in_message="",
        create=_HEREDOC_OK,
        expect_silent=True,
    ),
    Drill(
        name="heredoc writer with the wire actually cut",
        mechanism="W1 — reading heredocs must not mean excusing them",
        target="drill-heredoc-broken.yml",
        before="",
        after="",
        expect_check="W1",
        expect_in_message="steps.parse.outputs.readyz_urlx",
        create=_HEREDOC_BROKEN,
    ),
]

# The negative control. If a comment-only edit turns the checker red, the
# checker is not detecting breakage, it is detecting CHANGE — and a change
# detector dressed as a wire checker is exactly guard eleven.
CONTROL = Drill(
    name="NEGATIVE CONTROL (comment only)",
    mechanism="must produce NO new finding",
    target="triage.yml",
    before="permissions:",
    after="# drill: a comment that changes nothing\npermissions:",
    expect_check="",
    expect_in_message="",
)


def _copy_github(root: Path, dest: Path) -> None:
    shutil.copytree(root / ".github", dest / ".github")


def run_drill(root: Path, disabled: set[str]) -> int:
    print("DRILL — breaking wires on purpose. The checker must go RED and name the hop.")
    if disabled:
        print(f"  !! checker blinded: {sorted(disabled)} (proving the DRILL itself can fail)")
    print("=" * 72)

    with tempfile.TemporaryDirectory(prefix="chainchk-") as tmp:
        base = Path(tmp) / "base"
        base.mkdir(parents=True)
        _copy_github(root, base)
        baseline, baseline_wfs, parse_errors = run_checks(base, disabled)
        if parse_errors:
            print(f"  drill cannot run: baseline copy has parse errors: {parse_errors}")
            return 2
        baseline_keys = {f.key() for f in baseline}
        print(f"  baseline: {len(baseline)} finding(s) on the untouched tree — "
              f"a drill only counts NEW findings, so real ones do not mask the test.\n")

        passed = 0
        failed: list[str] = []
        for drill in DRILLS + [CONTROL]:
            work = Path(tmp) / re.sub(r"\W+", "_", drill.name)
            work.mkdir(parents=True)
            _copy_github(root, work)
            target = work / ".github" / "workflows" / drill.target

            if drill.create is not None:
                # A synthetic workflow. Adding a FILE is the mutation here, so
                # the anchor check cannot apply — but the vacuity risk is the
                # same, so the file must really have been loaded as a workflow.
                if target.exists():
                    failed.append(f"{drill.name}: {drill.target} already exists in .github/workflows — "
                                  f"the synthetic drill would be overwriting a real workflow.")
                    print(f"  [FAIL] {drill.name}: target name collides with a real workflow")
                    continue
                target.write_text(drill.create, encoding="utf-8")
            else:
                text = target.read_text(encoding="utf-8")

                # A mutation that did not apply would make the drill pass vacuously.
                # That is precisely how a self-test becomes a lie, so it is fatal.
                if drill.before not in text:
                    failed.append(f"{drill.name}: MUTATION DID NOT APPLY — "
                                  f"anchor {drill.before!r} not found in {drill.target}. "
                                  f"The drill is stale, not passing.")
                    print(f"  [FAIL] {drill.name}: anchor missing, drill is stale")
                    continue
                mutated = text.replace(drill.before, drill.after, 1)
                if mutated == text:
                    failed.append(f"{drill.name}: replacement was a no-op")
                    continue
                target.write_text(mutated, encoding="utf-8")

            findings, work_wfs, errs = run_checks(work, disabled)
            new = [f for f in findings if f.key() not in baseline_keys]

            # The created file must have PARSED. An unparseable synthetic
            # workflow is silently ignored by load_workflows, which would make
            # `expect_silent` pass while proving nothing at all.
            if drill.create is not None and len(work_wfs) != len(baseline_wfs) + 1:
                failed.append(f"{drill.name}: the synthetic workflow was not loaded "
                              f"({len(work_wfs)} workflows vs baseline {len(baseline_wfs)}). "
                              f"The drill proved nothing.")
                print(f"  [FAIL] {drill.name}: synthetic workflow never parsed")
                continue

            if drill is CONTROL or drill.expect_silent:
                if new or errs:
                    failed.append(f"{drill.name}: an edit that breaks NOTHING produced "
                                  f"{len(new)} new finding(s) — the checker is reporting a wire "
                                  f"as dead that is demonstrably live: {[f.render().strip() for f in new]}")
                    print(f"  [FAIL] {drill.name}: {[f.render().strip() for f in new]}")
                else:
                    passed += 1
                    verb = "created" if drill.create is not None else "edited"
                    print(f"  [PASS] {drill.name}\n         {verb} {drill.target}, "
                          f"0 new findings — the checker stayed quiet, as it must.")
                continue

            hit = [
                f for f in new
                if f.check == drill.expect_check and drill.expect_in_message in (f.message + f.hop)
            ]
            if not hit:
                failed.append(
                    f"{drill.name}: expected a {drill.expect_check} finding naming "
                    f"{drill.expect_in_message!r}; got {len(new)} new finding(s): "
                    f"{[f.check for f in new]}"
                )
                print(f"  [FAIL] {drill.name} ({drill.mechanism})")
                for f in new:
                    print(f.render())
                continue
            passed += 1
            print(f"  [PASS] {drill.name} ({drill.mechanism})")
            broke = f"added {drill.target} with the wire cut" if drill.create is not None else drill.before[:60]
            print(f"         broke: {drill.target}: {broke}")
            print(f"         RED ->{hit[0].render().lstrip()}")

        print("\n" + "=" * 72)
        total = len(DRILLS) + 1
        print(f"DRILL RESULT: {passed}/{total} passed")
        for msg in failed:
            print(f"  FAILED: {msg}")
        if failed:
            print("\nA wire was broken and this checker did not name it. "
                  "Until that is fixed, this checker is guard eleven.")
            return 1
        print("\nEvery deliberate break was caught and named; the harmless edit was ignored.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

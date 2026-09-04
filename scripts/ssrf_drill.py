#!/usr/bin/env python3
"""ssrf_drill.py — prove the URL-fetch SSRF guard can still go RED.

WHY THIS EXISTS (drill_registry.py's law)
------------------------------------------
"A guard is trusted because someone has WATCHED IT GO RED." A passing unit
test suite proves the guard denies what it denies TODAY. It says nothing
about whether the guard would still deny it if one control were quietly
weakened — which is exactly the failure class that shipped ten dead guards
here before this registry existed (see `scripts/drill_registry.py`'s
docstring: a size check that swallowed its own exit code, a sed range whose
anchor had been deleted, a self-test that grepped for a string...).

So this drill BREAKS the real guard/fetcher source ON PURPOSE, one control at
a time, and demands the attack that control exists to stop actually lands
once it is gone — plus a NEGATIVE CONTROL (case 10) that must stay allowed,
because a guard that denies everything passes every "did the attack land"
check and is useless.

HOW IT MUTATES
--------------
Each case copies the real source file(s) (never the checked-out originals —
mutation happens on a TEMP COPY) into a fresh temp package, finds an ANCHOR
comment the source is REQUIRED to carry verbatim (see ANCHORS below), and
comments out the first non-blank statement line after it. If an anchor is not
found, the case FAILS LOUDLY rather than silently applying zero mutations and
reporting a pass — that is precisely the "anchor had been deleted" failure
`drill_registry.py` names as the reason this repo stopped trusting checkers
that "exist" instead of checkers someone has watched fail.

Every case also runs its attack against the UNMUTATED source first and
asserts it is blocked — a case can never pass because the attack never
worked in the first place.

Offline. No DNS, no socket, no real network: every resolver used here is an
injected fake, and the two hop-level cases (3, 5, 7) use ``aioresponses`` to
mock the one real HTTP surface (aiohttp), exactly like the frozen test suite.
Target: well under `DRILL_TIMEOUT_S` (240s, `scripts/drill_registry.py:71`).

ANCHORS (the guard/fetcher source MUST carry these exact comments)
--------------------------------------------------------------------
``backend/src/services/fetch/guard.py``:
  # SSRF-ANCHOR:DENY_NETS_V6          — directly above ``DENY_NETS_V6 = (``
  # SSRF-ANCHOR:V4_MAPPED_UNWRAP      — directly above the v4-mapped unwrap
  # SSRF-ANCHOR:V6_PREDICATE_BELT     — directly above the v6 stdlib-predicate deny
  # SSRF-ANCHOR:DENY_WHOLE_HOST       — directly above "any denied -> deny all"
  # SSRF-ANCHOR:SCHEME_CHECK          — directly above the http(s)-only check
  # SSRF-ANCHOR:DECEPTIVE_IP_REFUSAL  — directly above the digit/hex/octal refusal
  # SSRF-ANCHOR:PEERNAME_RECHECK      — directly above ``verify_peername``'s real check

``backend/src/services/fetch/fetcher.py``:
  # SSRF-ANCHOR:REDIRECT_SCREEN_EVERY_HOP — directly above the per-hop screen call
  # SSRF-ANCHOR:STREAMING_SIZE_CAP        — directly above the cap-check in the read loop
  # SSRF-ANCHOR:CONTENT_TYPE_CHECK        — directly above the content-type allowlist check

Each anchored line's guarded behaviour must be expressible as ONE statement
(the mutation neutralises exactly one line) — this is a build requirement on
the implementation, not a suggestion; see the frozen unit tests in
``backend/tests/test_url_fetch_guard.py`` for the exact function contracts
these anchors sit inside.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import ipaddress
import socket
import sys
import tempfile
import time
import types
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
GUARD_SRC = BACKEND / "src" / "services" / "fetch" / "guard.py"
FETCHER_SRC = BACKEND / "src" / "services" / "fetch" / "fetcher.py"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# Cases 3/5/7 mock HTTP with aioresponses, which cannot build an aiohttp>=3.14
# ClientResponse on its own (missing ``stream_writer``). pytest gets the shim
# from tests/conftest.py; this script runs outside pytest, so it installs the
# same one itself. Must happen before any ClientResponse is constructed.
from tests.aiohttp314_shim import install as _install_aiohttp314_shim  # noqa: E402

_install_aiohttp314_shim()

_CASE_COUNTER = {"n": 0}


class AnchorNotFoundError(RuntimeError):
    """Raised loudly — never swallowed — when a required anchor is missing."""


def _comment_out_after(text: str, anchor: str, case: str) -> str:
    """Return `text` with the first non-blank statement AFTER `anchor`
    commented out. Raises AnchorNotFoundError if the anchor is missing — an
    anchor nobody can find is exactly the "deleted anchor" failure this
    drill exists to catch, so it is never treated as "nothing to mutate".
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if anchor in line:
            for j in range(i + 1, len(lines)):
                if lines[j].strip():
                    indent = lines[j][: len(lines[j]) - len(lines[j].lstrip())]
                    lines[j] = f"{indent}pass  # SSRF-DRILL[{case}] disabled -> {lines[j].strip()}"
                    return "\n".join(lines) + "\n"
            raise AnchorNotFoundError(f"{case}: anchor {anchor!r} found but no statement follows it")
    raise AnchorNotFoundError(
        f"{case}: anchor {anchor!r} not found in source. Either the guard lost the "
        f"marker comment, or this drill's anchor name is stale — both are the same "
        f"failure this drill exists to catch: a mutation nobody can locate is a "
        f"mutation that silently never applies."
    )


def _fresh_package(tag: str) -> Path:
    """A throwaway ``<tmp>/drillpkg_<tag>/services/fetch/`` package so every
    case gets its own clean copy — a mutation in case N must never leak into
    case N+1, and a failed import in one case must never poison another's
    module cache entry.
    """
    tmp = Path(tempfile.mkdtemp(prefix=f"ssrf_drill_{tag}_"))
    pkg_root = tmp / f"drillpkg_{tag}"
    fetch_dir = pkg_root / "services" / "fetch"
    fetch_dir.mkdir(parents=True)
    (pkg_root / "__init__.py").write_text("", encoding="utf-8")
    (pkg_root / "services" / "__init__.py").write_text("", encoding="utf-8")
    (fetch_dir / "__init__.py").write_text("", encoding="utf-8")
    return fetch_dir


def _rewrite_internal_imports(text: str, tag: str) -> str:
    """Point this module's own internal absolute imports at the drill copy,
    e.g. ``from src.services.fetch.guard import ...`` ->
    ``from drillpkg_<tag>.services.fetch.guard import ...``. Everything else
    (``from src.core import settings``, stdlib, aiohttp) still resolves
    against the real checked-out `backend/` on `sys.path`.
    """
    return text.replace("src.services.fetch", f"drillpkg_{tag}.services.fetch")


def _load(path: Path, dotted_name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(dotted_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - importlib contract
        raise RuntimeError(f"could not build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[dotted_name] = module
    sys.path.insert(0, str(path.parent.parent.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _prepare_guard(case: str, mutate_anchor: str | None) -> types.ModuleType:
    """A fresh, optionally-mutated copy of guard.py, importable as
    ``drillpkg_<case>.services.fetch.guard``.
    """
    _CASE_COUNTER["n"] += 1
    tag = f"{case}_{_CASE_COUNTER['n']}"
    fetch_dir = _fresh_package(tag)
    text = GUARD_SRC.read_text(encoding="utf-8")
    if mutate_anchor is not None:
        text = _comment_out_after(text, mutate_anchor, case)
    text = _rewrite_internal_imports(text, tag)
    dst = fetch_dir / "guard.py"
    dst.write_text(text, encoding="utf-8")
    return _load(dst, f"drillpkg_{tag}.services.fetch.guard")


def _prepare_fetcher(case: str, mutate_anchor: str | None) -> tuple[types.ModuleType, types.ModuleType]:
    """A fresh, optionally-mutated (guard, fetcher) pair. `fetcher.py` imports
    `guard.py`, so both must live in the same throwaway package.
    """
    _CASE_COUNTER["n"] += 1
    tag = f"{case}_{_CASE_COUNTER['n']}"
    fetch_dir = _fresh_package(tag)

    guard_text = _rewrite_internal_imports(GUARD_SRC.read_text(encoding="utf-8"), tag)
    (fetch_dir / "guard.py").write_text(guard_text, encoding="utf-8")

    fetcher_text = FETCHER_SRC.read_text(encoding="utf-8")
    if mutate_anchor is not None:
        fetcher_text = _comment_out_after(fetcher_text, mutate_anchor, case)
    fetcher_text = _rewrite_internal_imports(fetcher_text, tag)
    fetcher_dst = fetch_dir / "fetcher.py"
    fetcher_dst.write_text(fetcher_text, encoding="utf-8")

    guard_mod = _load(fetch_dir / "guard.py", f"drillpkg_{tag}.services.fetch.guard")
    fetcher_mod = _load(fetcher_dst, f"drillpkg_{tag}.services.fetch.fetcher")
    return guard_mod, fetcher_mod


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Cases 1, 2, 6, 9, 10: pure guard.screen_ip / screen_url mutations ──────


def case_1_deny_nets_v6_dropped() -> tuple[bool, str]:
    """Was "drop DENY_NETS_V6 -> ::1 / fd00::1 land". Re-pointed 2026-09-04:
    the adversarial review added a stdlib-predicate belt to the v6 arm of
    ``_base_reason`` (closing fec0::1, 64:ff9b:1::7f00:1, 2001:10::1,
    ::a9fe:a9fe, ::7f00:1, 5f00::1 — all previously undenied). That belt makes
    ``::1``/``fd00::1`` (loopback/private) — and in fact EVERY address
    currently in ``DENY_NETS_V6``, measured across Python 3.10/3.12/3.13 —
    denied independently of ``DENY_NETS_V6``, so dropping that tuple alone can
    no longer show an attack landing for anything the list carries.

    ``2001:10::1`` is the one address the review named that the belt catches
    (``is_private``) while ``DENY_NETS_V6`` does NOT cover it (no CIDR here
    spans ``2001:10::/28`` or ``2001::/23``) — so THIS case now points at the
    anchor that is actually load-bearing for it: the new predicate belt
    itself, not the hand-typed list.
    """
    target = ipaddress.ip_address("2001:10::1")
    before = _prepare_guard("c1_before", None)
    if before.screen_ip(target) is None:
        return False, "baseline already allows 2001:10::1 — the attack does not need this mutation"
    after = _prepare_guard("c1_after", "SSRF-ANCHOR:V6_PREDICATE_BELT")
    landed = after.screen_ip(target) is None
    return landed, "dropping the v6 predicate belt must let 2001:10::1 through"


def case_2_v4_mapped_unwrap_dropped() -> tuple[bool, str]:
    target = ipaddress.ip_address("::ffff:169.254.169.254")
    before = _prepare_guard("c2_before", None)
    if before.screen_ip(target) is None:
        return False, "baseline already allows the v4-mapped metadata address"
    after = _prepare_guard("c2_after", "SSRF-ANCHOR:V4_MAPPED_UNWRAP")
    landed = after.screen_ip(target) is None
    return landed, "dropping the v4-mapped unwrap must let ::ffff:169.254.169.254 through"


def case_4_deny_whole_host_weakened() -> tuple[bool, str]:
    async def resolve(host: str, port: int = 0, family: int = 0) -> list[dict[str, Any]]:
        return [
            {"hostname": host, "host": "93.184.216.34", "port": port, "family": socket.AF_INET},
            {"hostname": host, "host": "10.0.0.1", "port": port, "family": socket.AF_INET},
        ]

    before = _prepare_guard("c4_before", None)
    baseline = _run(before.screen_host("mixed.example.test", 443, resolve=resolve))
    if baseline.ok:
        return False, "baseline already allows a host with one private address"
    after = _prepare_guard("c4_after", "SSRF-ANCHOR:DENY_WHOLE_HOST")
    mutated = _run(after.screen_host("mixed.example.test", 443, resolve=resolve))
    return mutated.ok, "filtering to the good address instead of denying the whole host must let it through"


def case_6_scheme_check_removed() -> tuple[bool, str]:
    before = _prepare_guard("c6_before", None)
    if before.screen_url("file:///etc/passwd").ok:
        return False, "baseline already accepts file:// URLs"
    after = _prepare_guard("c6_after", "SSRF-ANCHOR:SCHEME_CHECK")
    landed = after.screen_url("file:///etc/passwd").ok
    return landed, "removing the scheme check must accept file:///etc/passwd"


def case_8_peername_recheck_removed() -> tuple[bool, str]:
    approved = frozenset({"93.184.216.34"})
    before = _prepare_guard("c8_before", None)
    if before.verify_peername(approved, "127.0.0.1") is None:
        return False, "baseline already accepts an unapproved peername"
    after = _prepare_guard("c8_after", "SSRF-ANCHOR:PEERNAME_RECHECK")
    landed = after.verify_peername(approved, "127.0.0.1") is None
    return landed, "removing the peername re-check must accept 127.0.0.1 despite only 93.184.216.34 being approved"


def case_9_deceptive_ip_refusal_removed() -> tuple[bool, str]:
    before = _prepare_guard("c9_before", None)
    if before.screen_url("http://2130706433/").ok:
        return False, "baseline already accepts the decimal IP literal"
    after = _prepare_guard("c9_after", "SSRF-ANCHOR:DECEPTIVE_IP_REFUSAL")
    landed = after.screen_url("http://2130706433/").ok
    return landed, "removing the deceptive-literal refusal must accept http://2130706433/"


def case_10_negative_control() -> tuple[bool, str]:
    """A guard that denies EVERYTHING passes cases 1-9 for free and is
    useless. This is what makes the other nine mean something: the
    UNMUTATED guard must still allow a plain public address.
    """
    guard = _prepare_guard("c10", None)
    allowed = guard.screen_ip(ipaddress.ip_address("93.184.216.34")) is None
    return allowed, "the unmutated guard must still allow a genuinely public address"


# ── Cases 3, 5, 7: fetcher-level, mocked HTTP (aioresponses) ────────────────


def _resolve_map(overrides: dict[str, str], default: str = "93.184.216.34") -> Callable:
    async def _resolve(host: str, port: int = 0, family: int = 0) -> list[dict[str, Any]]:
        return [{"hostname": host, "host": overrides.get(host, default), "port": port, "family": socket.AF_INET}]

    return _resolve


def _fetch_with(fetcher_mod, url: str, resolve) -> Any:
    fetcher_mod.DEFAULT_RESOLVE = resolve
    fetcher_mod.DEFAULT_CLOCK = time.monotonic
    fetcher_mod.VERIFY_PEERNAME_AFTER_CONNECT = False
    return _run(fetcher_mod.fetch_url(url))


def case_3_redirect_only_screens_first_hop() -> tuple[bool, str]:
    from aioresponses import aioresponses

    start = "https://public.example.test/start"
    private_target = "https://internal.example.test/secret"
    resolve = _resolve_map({"internal.example.test": "10.0.0.1"})

    _, before = _prepare_fetcher("c3_before", None)
    with aioresponses() as m:
        m.get(start, status=302, headers={"Location": private_target})
        m.get(private_target, status=200, body="<html></html>", headers={"Content-Type": "text/html"})
        baseline = _fetch_with(before, start, resolve)
    baseline_outcome = getattr(baseline, "outcome", None)
    if baseline_outcome != "ssrf_denied":
        return False, f"baseline must deny the redirect to a private address, got {baseline_outcome!r}"

    _, after = _prepare_fetcher("c3_after", "SSRF-ANCHOR:REDIRECT_SCREEN_EVERY_HOP")
    with aioresponses() as m:
        m.get(start, status=302, headers={"Location": private_target})
        m.get(private_target, status=200, body="<html></html>", headers={"Content-Type": "text/html"})
        mutated = _fetch_with(after, start, resolve)
    landed = getattr(mutated, "outcome", None) == "ok"
    return landed, "screening only the first hop must let a redirect to a private address through"


def case_5_streaming_cap_removed() -> tuple[bool, str]:
    from aioresponses import aioresponses

    from src.core import settings

    cap = 4096
    body = "x" * (cap * 10)
    url = "https://big.example.test/huge.html"
    resolve = _resolve_map({})
    old_cap = getattr(settings, "URL_FETCH_MAX_BYTES", None)
    settings.URL_FETCH_MAX_BYTES = cap
    try:
        _, before = _prepare_fetcher("c5_before", None)
        with aioresponses() as m:
            m.get(url, status=200, body=body, headers={"Content-Type": "text/html"})
            baseline = _fetch_with(before, url, resolve)
        if getattr(baseline, "outcome", None) != "too_large":
            return False, f"baseline must refuse an oversize body, got {getattr(baseline, 'outcome', baseline)!r}"

        _, after = _prepare_fetcher("c5_after", "SSRF-ANCHOR:STREAMING_SIZE_CAP")
        with aioresponses() as m:
            m.get(url, status=200, body=body, headers={"Content-Type": "text/html"})
            mutated = _fetch_with(after, url, resolve)
        landed = getattr(mutated, "outcome", None) == "ok"
        return landed, "removing the streaming size cap must let an oversize body through whole"
    finally:
        if old_cap is not None:
            settings.URL_FETCH_MAX_BYTES = old_cap


def case_7_content_type_check_removed() -> tuple[bool, str]:
    from aioresponses import aioresponses

    url = "https://files.example.test/job.pdf"
    resolve = _resolve_map({})

    _, before = _prepare_fetcher("c7_before", None)
    with aioresponses() as m:
        m.get(url, status=200, body=b"%PDF-1.4 not html", headers={"Content-Type": "application/pdf"})
        baseline = _fetch_with(before, url, resolve)
    if getattr(baseline, "outcome", None) != "unsupported_content":
        return False, f"baseline must refuse a PDF, got {getattr(baseline, 'outcome', baseline)!r}"

    _, after = _prepare_fetcher("c7_after", "SSRF-ANCHOR:CONTENT_TYPE_CHECK")
    with aioresponses() as m:
        m.get(url, status=200, body=b"%PDF-1.4 not html", headers={"Content-Type": "application/pdf"})
        mutated = _fetch_with(after, url, resolve)
    landed = getattr(mutated, "outcome", None) == "ok"
    return landed, "removing the content-type check must let a PDF be parsed as HTML"


CASES: list[tuple[str, Callable[[], tuple[bool, str]]]] = [
    ("1  drop the v6 predicate belt -> http://[2001:10::1]/ lands", case_1_deny_nets_v6_dropped),
    ("2  drop the v4-mapped-v6 unwrap -> ::ffff:169.254.169.254 lands", case_2_v4_mapped_unwrap_dropped),
    ("3  screen only the first redirect hop -> public->302->10.0.0.1 lands", case_3_redirect_only_screens_first_hop),
    (
        "4  filter addresses instead of denying the host -> 1 public+1 private host lands",
        case_4_deny_whole_host_weakened,
    ),
    ("5  remove the streaming size cap -> a 40KB body is read whole", case_5_streaming_cap_removed),
    ("6  remove the scheme check -> file:///etc/passwd is accepted", case_6_scheme_check_removed),
    ("7  remove the content-type check -> a PDF is parsed as HTML", case_7_content_type_check_removed),
    ("8  remove the peername re-check -> an unapproved peer is accepted", case_8_peername_recheck_removed),
    (
        "9  remove the deceptive-IP-literal refusal -> http://2130706433/ reaches a resolver",
        case_9_deceptive_ip_refusal_removed,
    ),
    ("10 NEGATIVE CONTROL: the unmutated guard still allows a public host", case_10_negative_control),
]


def run_drill() -> int:
    if not GUARD_SRC.exists():
        print(f"SKIP-IMPOSSIBLE: {GUARD_SRC} does not exist yet — nothing to drill.", file=sys.stderr)
        print("This is expected RED until the SSRF guard is implemented.", file=sys.stderr)
        return 1

    start = time.monotonic()
    results: list[tuple[str, bool, str]] = []
    for label, fn in CASES:
        try:
            ok, detail = fn()
        except AnchorNotFoundError as exc:
            ok, detail = False, str(exc)
        except Exception as exc:  # noqa: BLE001 - a case blowing up is a FAILED case, not a crashed drill
            ok, detail = False, f"case raised {exc.__class__.__name__}: {exc}"
        results.append((label, ok, detail))

    elapsed = time.monotonic() - start
    print("SSRF GUARD DRILL — breaking the guard on purpose, ten ways.")
    print("=" * 72)
    for label, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            print(f"         {detail}")
    passed = sum(1 for _, ok, _ in results if ok)
    print()
    print(f"elapsed: {elapsed:.2f}s (target < 5s locally; budget is DRILL_TIMEOUT_S=240s in CI)")
    print(f"DRILL RESULT: {passed}/{len(results)} passed")
    if passed != len(results):
        print("A deliberate break did not land, or the negative control did not stay allowed.")
        print("The guard cannot be trusted until every case here is watched passing.")
        return 1
    print("Every deliberate break landed; the negative control stayed allowed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--drill", action="store_true", help="break the SSRF guard on purpose; it must go RED")
    args = ap.parse_args(argv)
    if not args.drill:
        ap.print_help()
        return 0
    return run_drill()


if __name__ == "__main__":
    raise SystemExit(main())

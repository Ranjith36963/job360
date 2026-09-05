"""Committed, reviewable replacement for FOUR throwaway re-extraction scripts.

WHY THIS EXISTS. ``run_two_pass_extraction`` has exactly one caller in this
codebase: a user-triggered save route, reached only when a user personally
re-saves their profile. Nothing scheduled ever re-reads an EXISTING profile —
so when ``EXTRACTOR_VERSION`` bumps because a prompt improved, every profile
whose owner does not happen to re-save stays frozen at the OLD extraction,
silently, forever.

Production has already patched this FOUR separate times with uncommitted,
throwaway scripts — their ``source_action`` values (``shelf_backfill_reextract``,
``github_enrich_repair`` x2, ``backfill_linkedin_contact``,
``backfill_linkedin_headline``) are visible in the live version history but
none of those scripts, or those strings, exist anywhere in this repo. Worse:
those scripts called ``save_profile()`` directly with a hand-patched field,
which stamps a fresh ``updated_at`` while leaving ``llm_input_hashes``
untouched — so the profile then LOOKS freshly updated while its extraction is
still stale. That is what made a later investigation misdiagnose the cause.

THIS SCRIPT is the honest replacement:
  * Selects ONLY profiles ``two_pass.stale_extraction_inputs`` says are
    actually behind — never a blanket re-extract. Each re-extraction is a
    paid LLM call, so re-running everyone "just in case" is real money for
    users whose extraction is already current.
  * Re-extracts through ``run_two_pass_extraction`` — the SAME path a real
    save takes — never a bespoke partial field patch. That bespoke-patch
    pattern is the whole lesson of the four scripts this replaces.
  * DRY-RUN BY DEFAULT. Prints what it would do; changes nothing without
    ``--apply``.
  * ``--limit N`` bounds how many STALE profiles get processed in one run —
    the operator chooses how much to spend per invocation.
  * Records a source_action distinct from every past ad-hoc value
    ("stale_extractor_version_reextract"), so the version history says
    plainly what touched the row.
  * Safe to re-run: a profile already current (nothing in
    ``stale_extraction_inputs``) is skipped, so running this twice in a row
    is a no-op the second time.

Follows the shape of ``scripts/cleanup_preference_pollution.py`` (dry-run
default, ``--apply`` to write, plain per-profile print lines, a final
summary) — read that file first if this one is unclear.

Do NOT run this against production without the owner's go-ahead: each
``--apply`` re-extraction is a real, billed LLM call. Never makes an LLM call
under ``--limit 0`` / dry-run.

Usage:
    railway run -s Postgres bash -c \
      'export DATABASE_URL="$DATABASE_PUBLIC_URL"; \
       python scripts/reextract_stale_profiles.py [--user-id X] [--limit N] [--apply]'
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# The source_action every re-extraction from this script is stamped with.
# Deliberately distinct from every value in the live version history so a
# later investigation can tell at a glance this ran (unlike the four
# throwaway scripts it replaces, whose action strings don't exist anywhere
# in the codebase to look up).
SOURCE_ACTION = "stale_extractor_version_reextract"


async def main(user_id: str | None, limit: int, apply: bool) -> int:
    from src.services.profile.storage import (
        list_profile_user_ids,
        load_profile,
        save_profile,
    )
    from src.services.profile.two_pass import (
        run_two_pass_extraction,
        stale_extraction_inputs,
    )

    user_ids = [user_id] if user_id else list_profile_user_ids()

    scanned = 0
    stale_count = 0
    reextracted = 0
    failed = 0

    for uid in user_ids:
        # NO truthiness test on `limit`. It was `if apply and limit and ...`,
        # which makes `--limit 0` FALSY and therefore skips the cap entirely —
        # so `--apply --limit 0`, the one invocation the docstring above
        # promises "never makes an LLM call", would instead re-extract every
        # stale profile in the database with no bound at all. Each of those is
        # a real billed LLM call. 0 must mean "stop immediately", which a plain
        # `>=` comparison gives for free.
        if apply and reextracted + failed >= limit:
            break
        if not apply and stale_count >= limit:
            break

        # The BASE profile (`with_overlay=False`): this sweep re-extracts and
        # SAVES, and a load→mutate→save that starts from the merged profile
        # copies the agent's overlay values into extraction's own JSON — after
        # which clearing the edit reveals the edit again instead of the CV.
        profile = load_profile(uid, with_overlay=False)
        if profile is None:
            continue
        scanned += 1

        stale = stale_extraction_inputs(profile)
        if not stale:
            continue  # already current — the whole point of checking first
        stale_count += 1
        print(f"{uid}: stale = {', '.join(stale)}")

        if not apply:
            continue

        try:
            # THE SAME PATH a real save takes — never a bespoke field patch.
            # That is the one lesson every throwaway predecessor script broke.
            await run_two_pass_extraction(profile)
            save_profile(profile, uid, SOURCE_ACTION)
            reextracted += 1
        except Exception as exc:  # noqa: BLE001 — one user's failure must not abort the sweep
            failed += 1
            print(f"  FAILED: {type(exc).__name__}: {str(exc)[:160]}")

    print(
        f"\nscanned {scanned} profile(s), {stale_count} stale"
        + (f", {reextracted} re-extracted, {failed} failed" if apply else "")
    )
    if not apply:
        print(f"\nDRY RUN — {stale_count} profile(s) would be re-extracted. "
              "Re-run with --apply.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--user-id", default=None,
        help="re-extract one user's profile; omit to scan every profile",
    )
    ap.add_argument(
        "--limit", type=int, default=20,
        help="max STALE profiles to process this run (bounds paid LLM calls)",
    )
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.user_id, a.limit, a.apply)))

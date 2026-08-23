"""A MISSING KEY MUST NEVER LOOK LIKE A BAD RESULT.

THE INCIDENT. The weekly ranking eval reported "audit produced no judgments —
treating as failure" for a WEEK (runs 31107748440, 31368793516). Nothing was
wrong with the ranking. No judge key was set: GitHub renders an unset Actions
secret as an EMPTY string, `llm_provider` raised only after the provider loop,
and every caller swallowed that raise per-item into "the judge returned
nothing". A config error wearing a quality-regression costume is the most
expensive wrong alarm there is — it sends a solo founder debugging the product
instead of the config.

PR #313 fixed exactly one workflow. These tests fix the SHAPE, at the place the
failure actually happens (``llm_provider.require_llm_key``), so a caller written
tomorrow inherits the loud version for free.

Every test here is offline (rule #4): no key is ever set to a real value, and
every provider call is mocked or unreachable.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import src.services.profile.llm_provider as lp

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _blank_all_keys(monkeypatch) -> None:
    """The production shape: every provider key renders as an empty string."""
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.setattr(lp, name, "")


# ---------------------------------------------------------------------------
# 1. The shared preflight — one place, four names, an instruction
# ---------------------------------------------------------------------------


def test_the_four_key_names_live_in_one_shared_constant():
    """One list, so a fifth provider cannot be added to the chain and forgotten
    by the alarm. The chain itself is built from these same names."""
    assert lp.LLM_KEY_VARS == (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "CEREBRAS_API_KEY",
    )


@pytest.mark.asyncio
async def test_llm_extract_with_no_keys_raises_a_missing_key_error(monkeypatch):
    """The headline test. All four empty -> a dedicated exception that NAMES
    every variable it looked for, says they were ALL empty, and says what to do.

    It must NOT be the generic "all providers failed" error: that reads as "we
    tried and the providers are down", which is a different fix.
    """
    _blank_all_keys(monkeypatch)
    lp.reset_llm_key_alarm()

    with pytest.raises(lp.LLMKeyMissing) as exc:
        await lp.llm_extract("judge this job")

    msg = str(exc.value)
    for name in lp.LLM_KEY_VARS:
        assert name in msg, f"the error must name {name} — it is one of the fixes"
    assert "ALL" in msg, "must say all four were empty, not just 'no key'"
    assert "CONFIG" in msg.upper(), "must label itself a config failure"
    assert "free tier" in msg.lower(), "must say the cheap way out"
    # Not a quality signal, and not the 'providers are down' signal.
    assert not isinstance(exc.value, lp.LLMAllProvidersFailed)
    assert "All LLM providers failed" not in msg


@pytest.mark.asyncio
async def test_llm_extract_fast_shares_the_same_preflight(monkeypatch):
    """The fast (bulk-scoring) path is a second door to the same chain. It must
    not have its own, weaker version of the check."""
    _blank_all_keys(monkeypatch)
    lp.reset_llm_key_alarm()

    with pytest.raises(lp.LLMKeyMissing) as exc:
        await lp.llm_extract_fast("score this job")

    for name in lp.LLM_KEY_VARS:
        assert name in str(exc.value)


@pytest.mark.asyncio
async def test_preflight_fires_before_any_provider_is_touched(monkeypatch):
    """Cheap and early: with no key there is nothing to try, so the chain must
    not walk providers first. (The old code checked only AFTER the loop.)"""
    _blank_all_keys(monkeypatch)
    lp.reset_llm_key_alarm()

    with patch.object(lp, "_call_openai", new_callable=AsyncMock) as o, \
         patch.object(lp, "_call_gemini", new_callable=AsyncMock) as g, \
         patch.object(lp, "_call_groq", new_callable=AsyncMock) as q, \
         patch.object(lp, "_call_cerebras", new_callable=AsyncMock) as c:
        with pytest.raises(lp.LLMKeyMissing):
            await lp.llm_extract("x")
    assert not any(m.called for m in (o, g, q, c))


@pytest.mark.asyncio
async def test_missing_key_is_logged_at_error_so_prod_cannot_swallow_it(
    monkeypatch, caplog
):
    """Sentry turns an ERROR log into an issue. Even if a caller swallows the
    exception (main.py:490 and rescore.py:449 both catch Exception), the ERROR
    line still escapes — that is the belt to the exception's braces.

    Once per process, not once per job: 160 identical Sentry events is how an
    alarm gets muted.
    """
    _blank_all_keys(monkeypatch)
    lp.reset_llm_key_alarm()

    with caplog.at_level(logging.ERROR, logger="job360.profile.llm_provider"):
        for _ in range(3):
            with pytest.raises(lp.LLMKeyMissing):
                await lp.llm_extract("x")

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1, f"expected exactly one ERROR, got {len(errors)}"
    for name in lp.LLM_KEY_VARS:
        assert name in errors[0].getMessage()


# ---------------------------------------------------------------------------
# 2. No crying wolf — a present key must not trip any of this
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_free_key_is_enough_and_the_preflight_stays_quiet(monkeypatch, caplog):
    """ANY ONE of the four unblocks the judge. Proven with a free-tier key
    (GROQ), not the paid one — the owner's cheapest fix must be a real fix."""
    _blank_all_keys(monkeypatch)
    monkeypatch.setattr(lp, "GROQ_API_KEY", "fake-groq-key")
    lp.reset_llm_key_alarm()
    lp.reset_dead_providers()

    with caplog.at_level(logging.ERROR, logger="job360.profile.llm_provider"), \
         patch.object(lp, "_call_groq", new_callable=AsyncMock,
                      return_value={"fit_score": 80}) as groq:
        out = await lp.llm_extract("judge this job")

    assert out == {"fit_score": 80}
    assert groq.await_count == 1
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], \
        "a working key must produce no config alarm"


@pytest.mark.parametrize(
    ("key_name", "call_name"),
    [
        ("GEMINI_API_KEY", "_call_gemini"),
        ("GROQ_API_KEY", "_call_groq"),
        ("CEREBRAS_API_KEY", "_call_cerebras"),
    ],
)
@pytest.mark.asyncio
async def test_each_free_tier_key_alone_really_reaches_a_provider(
    monkeypatch, key_name, call_name
):
    """"Use a free key" must be advice that WORKS, not advice that sounds nice.

    The owner is told any ONE of the four unblocks the judge and that three are
    free. This pins that claim to the code: each free key ALONE routes to its
    own provider. It is not hypothetical — OpenAI was the documented PRIMARY
    provider for months while its SDK was undeclared, so the import raised,
    the chain swallowed it, and OpenAI silently never ran (pyproject.toml:30-44).
    """
    _blank_all_keys(monkeypatch)
    monkeypatch.setattr(lp, key_name, "fake-key")
    lp.reset_llm_key_alarm()
    lp.reset_dead_providers()

    with patch.object(lp, call_name, new_callable=AsyncMock,
                      return_value={"ok": True}) as provider:
        assert await lp.llm_extract("x") == {"ok": True}
        assert provider.await_count == 1


@pytest.mark.asyncio
async def test_a_real_provider_outage_is_still_not_a_missing_key(monkeypatch):
    """The other side of the mistake: keys present but every provider down must
    stay 'providers failed', never 'no key'. Otherwise we would send the owner
    to the secrets page for an outage."""
    _blank_all_keys(monkeypatch)
    monkeypatch.setattr(lp, "GROQ_API_KEY", "fake-groq-key")
    lp.reset_llm_key_alarm()
    lp.reset_dead_providers()

    with patch.object(lp, "_call_groq", new_callable=AsyncMock,
                      side_effect=Exception("groq 500")):
        with pytest.raises(lp.LLMAllProvidersFailed):
            await lp.llm_extract("x")


# ---------------------------------------------------------------------------
# 3. The instruments that report on the judge must not inherit the masquerade
# ---------------------------------------------------------------------------


def test_eval_ranking_reuses_the_shared_key_list_instead_of_its_own_copy():
    """The weekly accuracy instrument kept its own private copy of the four
    names. Two lists drift; then a fifth provider is alarmed on by one and not
    the other. Static check on purpose: importing the script sets a Windows
    event-loop policy at module level."""
    text = (_REPO_ROOT / "backend" / "scripts" / "eval_ranking.py").read_text(
        encoding="utf-8"
    )
    assert "from src.services.profile.llm_provider import" in text
    assert "NO_LLM_KEY_MESSAGE" in text and "configured_llm_keys" in text
    assert '"OPENAI_API_KEY", "GEMINI_API_KEY"' not in text, \
        "the private duplicate of the key list is still there"


def _load_provider_probe():
    """Import scripts/provider_probe.py by path (it is stdlib-only, no package)."""
    path = _REPO_ROOT / "scripts" / "provider_probe.py"
    spec = importlib.util.spec_from_file_location("_probe_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_probe_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_provider_probe_watches_exactly_the_same_four_names():
    """The probe is stdlib-only on purpose (external-health.yml never installs
    the backend), so it cannot import the shared constant and re-types the list.
    Pin the two together here — otherwise a fifth provider gets added to the
    chain and the daily probe keeps reporting on four."""
    mod = _load_provider_probe()
    assert tuple(mod.LLM_KEY_VARS) == lp.LLM_KEY_VARS


def test_provider_probe_goes_red_when_every_llm_key_is_absent(monkeypatch, capsys):
    """THE DAILY BLIND SPOT (external-health.yml, cron 07:10). Absence was a
    footnote: with all four LLM keys unset but ONE other credential present, the
    probe printed "Every configured credential authenticated" and exited 0 —
    green, daily, forever, while the judge could not make a single call.

    Network is stubbed: this test makes no HTTP call.
    """
    mod = _load_provider_probe()
    for name in ("OPENAI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "GEMINI_API_KEY",
                 "REED_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "PROBE_FORCE_RED"):
        monkeypatch.delenv(name, raising=False)
    # One NON-LLM credential present — this is what made the probe look healthy.
    monkeypatch.setenv("RESEND_API_KEY", "stub-not-a-real-key")
    monkeypatch.setenv("SMTP_FROM", "login@job360.uk")
    monkeypatch.setattr(mod, "_probe", lambda *a, **k: ("ok", "stubbed"))

    rc = mod.main()
    out = capsys.readouterr().out

    assert rc != 0, "no LLM key at all must not be a green run"
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY"):
        assert name in out
    assert "free tier" in out.lower()


def test_provider_probe_stays_green_when_one_llm_key_is_present(monkeypatch, capsys):
    """No crying wolf: one free key configured and healthy -> exit 0."""
    mod = _load_provider_probe()
    for name in ("OPENAI_API_KEY", "CEREBRAS_API_KEY", "GEMINI_API_KEY",
                 "REED_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "PROBE_FORCE_RED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "stub-not-a-real-key")
    monkeypatch.setenv("RESEND_API_KEY", "stub-not-a-real-key")
    monkeypatch.setenv("SMTP_FROM", "login@job360.uk")
    monkeypatch.setattr(mod, "_probe", lambda *a, **k: ("ok", "stubbed"))

    assert mod.main() == 0
    assert "authenticated" in capsys.readouterr().out

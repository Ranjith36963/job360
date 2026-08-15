"""No provider may hardcode its model name.

WHY (found in PRODUCTION Sentry, 2026-08-15, issue PYTHON-FASTAPI-J).

The Gemini call had ``_model = "gemini-2.0-flash"`` written into the source.
Google retired that model, and the live error was:

    LLM provider 'gemini' looks PERMANENTLY misconfigured (NotFound: 404 This
    model models/gemini-2.0-flash is no longer available...). SKIPPING it for
    the rest of this process

So that entire fallback had been dead. The only symptom was one line inside an
error nobody was reading, and the extraction chain silently ran one provider
short. In the same window the primary (OpenAI gpt-4o-mini) was hitting 429 rate
limits and Groq was returning 401 expired_api_key — a three-deep fallback chain
with every layer degraded, and no test could have caught any of it.

A hardcoded model name is a dependency on somebody else's deprecation schedule
with no response available except a code change and a deploy. Model names rot
on a timescale of months; this repo's own CLAUDE.md makes the same point about
never asserting live-world facts from memory.

This does NOT pin WHICH model each provider uses — that is a cost and quality
decision, and pinning it here would just move the rot into a test. It pins that
each one can be changed WITHOUT a deploy.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_PROVIDER_SRC = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src" / "services" / "profile" / "llm_provider.py"
)

_PROVIDER_FUNCS = ("_call_openai", "_call_gemini", "_call_groq", "_call_cerebras")


def _model_assignment(func_name: str) -> ast.AST | None:
    """The right-hand side of ``_model = ...`` inside a provider function."""
    tree = ast.parse(_PROVIDER_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id == "_model":
                        return stmt.value
    return None


@pytest.mark.parametrize("func_name", _PROVIDER_FUNCS)
def test_the_model_is_not_a_string_literal(func_name: str) -> None:
    """A bare string means the only way to react to a retirement is a deploy."""
    value = _model_assignment(func_name)
    assert value is not None, f"{func_name} has no `_model = ...` — has it moved?"
    assert not (isinstance(value, ast.Constant) and isinstance(value.value, str)), (
        f"{func_name} hardcodes its model name ({value.value!r}). When the "
        "provider retires it, this fallback dies silently and the only fix is a "
        "code change. Read it from an env-overridable setting instead — that is "
        "exactly how gemini-2.0-flash killed the Gemini fallback in production."
    )


@pytest.mark.parametrize("func_name", _PROVIDER_FUNCS)
def test_the_model_comes_from_something_configurable(func_name: str) -> None:
    """The positive half. Without it, deleting the line entirely would also pass
    the test above."""
    value = _model_assignment(func_name)
    src = ast.unparse(value)
    configurable = (
        "os.getenv" in src            # read straight from the environment
        or src.endswith("_MODEL")     # a settings constant that reads getenv
        or "_MODEL" in src
    )
    assert configurable, (
        f"{func_name}'s model comes from {src!r}, which is neither an env var "
        "nor a *_MODEL setting — it cannot be changed without a deploy."
    )


def test_every_model_setting_actually_reads_the_environment() -> None:
    """The settings constants must be env-backed, not just named like it.

    ``GEMINI_MODEL = "gemini-3.7-flash"`` would satisfy the two tests above
    while being exactly as unchangeable as the literal it replaced.
    """
    settings_src = (
        pathlib.Path(__file__).resolve().parents[1] / "src" / "core" / "settings.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(settings_src)

    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.endswith("_MODEL"):
                found[target.id] = ast.unparse(node.value)

    assert found, "no *_MODEL settings found at all — did they move?"
    not_env_backed = {
        name: src for name, src in found.items() if "os.getenv" not in src
    }
    assert not not_env_backed, (
        f"these *_MODEL settings are constants, not env reads: {not_env_backed}. "
        "Naming it like a setting does not make it changeable."
    )

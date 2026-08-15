"""A capability switched ON but structurally impossible must say so LOUDLY.

THE DEFECT, measured against production 2026-08-15.

The API image installs ``.[semantic]`` plus torch. The WORKER image installs a
plain ``.`` — confirmed in the live Railway build log:

    load build definition from backend/Dockerfile.worker
    RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

The worker runs ``refresh_catalog``, the daily job-ingestion cron. So the
process that pulls in the most new jobs is the one that cannot embed them.
Semantic coverage in prod: 687 vectors for 9,977 jobs (6.9%).

The shape of the failure is what made it survive. ``sentence_transformers`` is
imported lazily (rule #16), so the MODULE import succeeds and nothing complains
at startup. The ImportError only surfaces per job, deep inside the backfill
loop, where a per-job handler catches it and logs
``embed backfill: job <id> failed``. One missing package therefore produced a
storm of identical warnings that named neither the cause nor the remedy, and
every instrument stayed green.

These tests pin the preflight: when semantic is ON and the stack is absent, the
backfill must stop immediately and say what is wrong and how to fix it.
"""
from __future__ import annotations

import logging

import pytest


class TestSemanticStackProbe:
    def test_it_reports_true_when_the_package_imports(self) -> None:
        """The probe must reflect reality, not a flag. If the package is
        installed here, saying "missing" would disable embedding on a machine
        that can perfectly well do it."""
        from src.services.embeddings import semantic_stack_installed

        installed = semantic_stack_installed()
        try:
            import sentence_transformers  # noqa: F401
            truth = True
        except ImportError:
            truth = False
        assert installed is truth

    def test_it_reports_false_when_the_import_fails(self, monkeypatch) -> None:
        """Simulate the worker image. builtins.__import__ is patched rather than
        sys.modules, because the probe's whole job is to survive the ImportError
        that a genuinely absent package raises."""
        import builtins

        from src.services import embeddings

        real_import = builtins.__import__

        def _no_sentence_transformers(name, *args, **kwargs):
            if name == "sentence_transformers" or name.startswith("sentence_transformers."):
                raise ImportError("No module named 'sentence_transformers'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_sentence_transformers)
        assert embeddings.semantic_stack_installed() is False


class TestBackfillStopsLoudlyWithoutTheStack:
    """The OUTCOME, not the mechanism: no work attempted, and one actionable
    ERROR rather than N unactionable warnings."""

    @pytest.mark.asyncio
    async def test_it_returns_zero_and_logs_an_actionable_error(
        self, monkeypatch, caplog
    ) -> None:
        import builtins

        from src import main as main_mod

        real_import = builtins.__import__

        def _no_sentence_transformers(name, *args, **kwargs):
            if name == "sentence_transformers" or name.startswith("sentence_transformers."):
                raise ImportError("No module named 'sentence_transformers'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_sentence_transformers)

        # A DB/conn that would explode if touched. That is the point: the
        # preflight must return BEFORE any query runs, so passing objects with
        # no usable methods proves no work was attempted. A test that passed
        # working fakes could not tell "stopped early" from "ran and found
        # nothing".
        class _Explodes:
            def __getattr__(self, name):  # noqa: ANN001
                raise AssertionError(
                    f"backfill touched the database ({name}) despite the "
                    "semantic stack being absent"
                )

        with caplog.at_level(logging.ERROR, logger="job360"):
            embedded = await main_mod._embed_backfill_budget(
                _Explodes(), _Explodes(), 50
            )

        assert embedded == 0

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "the failure was not raised to ERROR — it stays invisible"
        text = " ".join(r.getMessage() for r in errors).lower()
        # The message has to carry the REMEDY. An error that says "failed"
        # without saying what to do is the same dead end as the warning storm
        # it replaces.
        assert "semantic_enabled" in text
        assert "sentence-transformers" in text
        assert "[semantic]" in text or "pip install" in text

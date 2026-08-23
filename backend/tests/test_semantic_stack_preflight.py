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


class TestTheMessageCarriesTheRemedy:
    """The ERROR text is the whole point, so its content is pinned.

    The warning it replaces said "embed backfill: job 4172 failed" — true, and
    useless. An operator reading a hundred of those learns nothing about the
    cause or the fix. If this message ever degrades back to a bare "failed",
    the loud path is loud and still worthless.

    The escalation itself is exercised where it lives, in
    tests/test_embed_backfill.py, which owns the DB fixture for that function.
    Duplicating that setup here would mean a second, drifting copy of it.
    """

    def test_the_wording_names_the_cause_and_both_ways_out(self) -> None:
        import inspect

        from src import main as main_mod

        source = inspect.getsource(main_mod._embed_backfill_budget).lower()
        assert "semantic_enabled" in source, "does not name the flag that is on"
        assert "sentence-transformers" in source, "does not name the missing package"
        assert "[semantic]" in source, "does not give the install command"
        # The second way out matters as much as the first: if embedding is not
        # wanted on this service, the honest fix is to turn the flag OFF so the
        # gap is a decision rather than a silent hole.
        assert "turn semantic_enabled off" in source, (
            "only offers 'install it' — an operator who does not want embedding "
            "here is left with a permanent ERROR and no sanctioned way to clear it"
        )

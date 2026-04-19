"""Tests for workers.settings — the ARQ boot surface.

Design goal: importing `src.workers.settings` must NOT import `arq` at
module top. Per CLAUDE.md rule #11 (and the blueprint library-mode tax
rationale), arq is a ~4MB dep chain that pytest should never touch.
The import is lazy inside the method that actually spins up ARQ.
"""
import importlib
import os
import sys
from unittest.mock import patch


def test_worker_settings_functions_includes_send_notification():
    """Batch 3.5 Deliverable D: send_notification must be in the functions list."""
    # Reload to pick up current module state
    mod = importlib.import_module("src.workers.settings")
    assert hasattr(mod, "WorkerSettings"), "settings module must export WorkerSettings"
    funcs = mod.WorkerSettings.functions
    func_names = {getattr(f, "__name__", str(f)) for f in funcs}
    assert "send_notification" in func_names, (
        f"send_notification missing from WorkerSettings.functions: {func_names}"
    )
    assert "score_and_ingest" in func_names, (
        f"score_and_ingest missing from WorkerSettings.functions: {func_names}"
    )


def test_worker_settings_redis_settings_from_env(monkeypatch):
    """REDIS_URL env var must drive the redis_settings host/port."""
    monkeypatch.setenv("REDIS_URL", "redis://custom-host:6380/2")
    # Force re-evaluation of the module-level redis_settings binding
    if "src.workers.settings" in sys.modules:
        del sys.modules["src.workers.settings"]
    mod = importlib.import_module("src.workers.settings")
    rs = mod.WorkerSettings.redis_settings
    # Contract: the redis_settings object surfaces .host and .port (either ARQ's
    # RedisSettings dataclass or an equivalent namespace/namedtuple shim).
    assert getattr(rs, "host", None) == "custom-host"
    assert getattr(rs, "port", None) == 6380


def test_arq_not_imported_at_module_top():
    """Import workers.settings without arq in sys.modules — must not raise.

    Simulates a pytest environment where arq is not installed (even though
    it might be in this dev venv). If settings.py imported arq at top,
    patching it out of sys.modules and reloading would ImportError.
    """
    # Drop arq from sys.modules + block re-import
    arq_mods = [k for k in list(sys.modules.keys()) if k == "arq" or k.startswith("arq.")]
    saved = {k: sys.modules.pop(k) for k in arq_mods}
    if "src.workers.settings" in sys.modules:
        del sys.modules["src.workers.settings"]
    try:
        # Block import of arq completely
        class _Blocker:
            def find_module(self, name, path=None):
                if name == "arq" or name.startswith("arq."):
                    return self
                return None

            def load_module(self, name):
                raise ImportError(f"arq import blocked by test: {name}")

            def find_spec(self, name, path, target=None):
                if name == "arq" or name.startswith("arq."):
                    raise ImportError(f"arq import blocked by test: {name}")
                return None

        blocker = _Blocker()
        sys.meta_path.insert(0, blocker)
        try:
            # This must succeed — no top-level arq import
            import src.workers.settings  # noqa: F401
        finally:
            sys.meta_path.remove(blocker)
    finally:
        # Restore
        for k, m in saved.items():
            sys.modules[k] = m
        if "src.workers.settings" in sys.modules:
            del sys.modules["src.workers.settings"]

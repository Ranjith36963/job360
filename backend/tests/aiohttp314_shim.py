"""aiohttp 3.14 <-> aioresponses compatibility shim (TEST-ONLY, importable).

aiohttp 3.14 made ``stream_writer`` a REQUIRED keyword-only argument of
ClientResponse.__init__. aioresponses builds its fake responses by calling
that constructor directly (core.py ``_build_response``) and never passes it,
so every mocked request died with::

    TypeError: ClientResponse.__init__() missing 1 required
    keyword-only argument: 'stream_writer'

That single incompatibility is why aiohttp was pinned <3.14 — which left 11
CVEs (PYSEC-2026-237, -2104..-2113) unfixable in production and waived by ID
in .github/workflows/security.yml. aioresponses 0.7.9 is the latest release
and still does not pass it, so waiting upstream was not an option.

What the shim does: fill in ``stream_writer`` ONLY when the caller omitted it.
aioresponses always passes ``writer=None``, and in that branch aiohttp reads
exactly one attribute -- ``stream_writer.output_size`` -- and does NOT retain
the object (``self._stream_writer`` stays None, so every later guard on it is
already False). A stub exposing ``output_size`` is therefore sufficient AND
complete; this is not a guess, it was read off aiohttp 3.14.1's source.

Safety: ``setdefault`` means any caller that DOES pass a real stream_writer --
i.e. all genuine aiohttp traffic -- is completely untouched.

Why a module and not a block in conftest.py: ``scripts/ssrf_drill.py`` mocks
HTTP with aioresponses too, but runs OUTSIDE pytest (CI's ``chain`` job), so a
conftest-only shim never reached it — PR #496's second run failed 3/10 drill
cases on exactly the TypeError above. Both callers now import ``install()``.
Nothing under ``tests/`` ships to production. Delete the whole module when
aioresponses gains aiohttp-3.14 support.
"""

from __future__ import annotations

from typing import Any


def install() -> None:
    """Patch ``aiohttp.ClientResponse.__init__`` to default ``stream_writer``.

    Idempotent; a no-op on aiohttp < 3.14 (no such parameter) and when aiohttp
    is not importable.
    """
    try:
        import inspect as _inspect

        from aiohttp import client_reqrep as _client_reqrep
    except Exception:  # pragma: no cover - aiohttp always present in tests
        return

    _response_cls = _client_reqrep.ClientResponse
    _orig_init = _response_cls.__init__
    try:
        _params = _inspect.signature(_orig_init).parameters
    except (TypeError, ValueError):  # pragma: no cover - C-accelerated init
        return

    # aiohttp < 3.14 has no such parameter -> nothing to do, leave it alone.
    if "stream_writer" not in _params:
        return
    if getattr(_orig_init, "_job360_shimmed", False):  # pragma: no cover
        return

    class _NoopStreamWriter:
        """Minimal AbstractStreamWriter stand-in for mocked responses.

        Only ``output_size`` is ever read on the ``writer is None`` path that
        aioresponses uses; nothing is written through it.
        """

        output_size = 0

    def _patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stream_writer", _NoopStreamWriter())
        return _orig_init(self, *args, **kwargs)

    _patched_init._job360_shimmed = True  # type: ignore[attr-defined]
    _response_cls.__init__ = _patched_init  # type: ignore[method-assign]

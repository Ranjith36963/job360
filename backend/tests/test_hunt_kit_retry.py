"""http_request retries 429 and transient 502/503/504 only; other 4xx fail fast.

Mechanism: jobspipe agent.txt:319 "Back off and retry on HTTP 429 and documented transient 5xx;
other 4xx errors are never retried."
"""

from __future__ import annotations

import io
import urllib.error
from typing import Any

import pytest

from src.hunt_kit import hunt_kit as hk


class _Resp:
    status = 200

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def geturl(self) -> str:
        return "https://example.com/x"

    def read(self, n: int = -1) -> bytes:
        return b"ok"


def _run(monkeypatch: pytest.MonkeyPatch, codes: list[int], **kw: Any) -> tuple[int, int]:
    calls: list[int] = []
    seq = list(codes)

    def fake_open(req: Any, timeout: int) -> Any:
        calls.append(1)
        code = seq.pop(0)
        if code == 200:
            return _Resp()
        raise urllib.error.HTTPError("https://example.com/x", code, "x", {}, io.BytesIO(b""))  # type: ignore[arg-type]

    monkeypatch.setattr(hk, "_open", fake_open)
    monkeypatch.setattr(hk, "check_url", lambda u: True)
    monkeypatch.setattr(hk.time, "sleep", lambda s: None)
    status, _, _ = hk.http_request("https://example.com/x", **kw)
    return status, len(calls)


@pytest.mark.parametrize("code", [502, 503, 504])
def test_transient_5xx_retried_once_then_ok(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    assert _run(monkeypatch, [code, 200]) == (200, 2)


def test_transient_5xx_retried_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, [503, 503, 200]) == (503, 2)


@pytest.mark.parametrize("code", [400, 401, 403, 404, 500])
def test_other_errors_fail_fast(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    assert _run(monkeypatch, [code, 200]) == (code, 1)


def test_429_still_retries_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, [429, 429, 200]) == (200, 3)


def test_5xx_does_not_eat_a_429_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, [503, 429, 429, 200]) == (200, 4)


def test_post_is_never_retried_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, [503, 200], data=b"{}") == (503, 1)


def test_5xx_retry_works_with_zero_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, [502, 200], retries=0) == (200, 2)

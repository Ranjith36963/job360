"""Slice 8 (#515) — the artifact diff, read-only.

docs/plans/2026-09-11-cv-diff/spec.md. The web shows original vs tailored
and marks the version the receipt names as applied; nothing here writes.
There is deliberately NO Keep button and NO MCP tool (VISION decision 26 —
the agent holds both texts and keeps versions through ``save_artifact``).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings
from src.services.applications.diff import diff_lines

_AD = {
    "title": "Platform Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/9",
    "description": "Build the platform. Kubernetes, Go, Postgres.",
}

_ORIGINAL_CV = "Jane Doe\nSKILLS\nPython, Postgres\nEXPERIENCE\nBuilt fraud models at Acme."
_TAILORED_CV = "Jane Doe\nSKILLS\nPython, Postgres, Kubernetes\nEXPERIENCE\nBuilt fraud models at Acme."


def _seed_profile_cv(user_id: str, raw_text: str) -> None:
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text=raw_text)), user_id, source_action="cv_upload")


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _save(client: AsyncClient, app_id: int, kind: str, text: str) -> int:
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": kind, "text": text})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["artifact_id"])


async def _diff(client: AsyncClient, app_id: int, artifact_id: int, **params: Any):
    return await client.get(f"/api/applications/{app_id}/artifacts/{artifact_id}/diff", params=params)


# ── the pure function ─────────────────────────────────────────────────────────


def test_diff_lines_marks_one_del_and_one_add_for_a_changed_line():
    out = diff_lines(_ORIGINAL_CV, _TAILORED_CV)
    ops = [(line["op"], line["text"]) for line in out["lines"]]
    assert ("del", "Python, Postgres") in ops
    assert ("add", "Python, Postgres, Kubernetes") in ops
    assert out["added"] == 1 and out["removed"] == 1
    assert out["truncated"] is False
    # every unchanged line is present exactly once as `equal`
    assert sum(1 for op, _ in ops if op == "equal") == 4


def test_diff_lines_identical_texts_have_no_changes():
    out = diff_lines(_ORIGINAL_CV, _ORIGINAL_CV)
    assert out["added"] == 0 and out["removed"] == 0
    assert all(line["op"] == "equal" for line in out["lines"])


def test_diff_lines_caps_both_sides_and_says_so(monkeypatch):
    monkeypatch.setattr(settings, "APPLICATION_DIFF_MAX_LINES", 3)
    out = diff_lines("a\nb\nc\nd\ne", "a\nb\nc\nd\ne\nf")
    assert out["truncated"] is True
    assert len(out["lines"]) == 3


# ── the route ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cv_diffs_against_the_profile_cv_by_default(authenticated_async_context, fixture_user_id):
    _seed_profile_cv(fixture_user_id, _ORIGINAL_CV)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        v1 = await _save(client, app_id, "cv", _TAILORED_CV)
        resp = await _diff(client, app_id, v1)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "cv"
    assert body["base"]["source"] == "profile"
    assert body["target"]["artifact_id"] == v1
    assert body["target"]["applied"] is False
    ops = [(line["op"], line["text"]) for line in body["lines"]]
    assert ("del", "Python, Postgres") in ops
    assert ("add", "Python, Postgres, Kubernetes") in ops


@pytest.mark.asyncio
async def test_applied_is_true_only_for_the_version_the_receipt_names(authenticated_async_context, fixture_user_id):
    _seed_profile_cv(fixture_user_id, _ORIGINAL_CV)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        v1 = await _save(client, app_id, "cv", "cv v1")
        v2 = await _save(client, app_id, "cv", _TAILORED_CV)
        receipt = await client.post(
            f"/api/applications/{app_id}/receipt", json={"cv_artifact_id": v2, "channel": "company site"}
        )
        assert receipt.status_code == 201, receipt.text
        d1 = await _diff(client, app_id, v1)
        d2 = await _diff(client, app_id, v2)
    assert d1.json()["target"]["applied"] is False
    assert d2.json()["target"]["applied"] is True


@pytest.mark.asyncio
async def test_against_another_version_and_the_previous_version_default(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        v1 = await _save(client, app_id, "cover_letter", "Dear team,\nI build platforms.")
        v2 = await _save(client, app_id, "cover_letter", "Dear team,\nI build reliable platforms.")
        default = await _diff(client, app_id, v2)
        explicit = await _diff(client, app_id, v2, against=str(v1))
        first = await _diff(client, app_id, v1)
    # a non-cv kind has no "original" on the profile: the previous version is the base
    assert default.json()["base"] == {
        "source": "artifact", "artifact_id": v1, "version_no": 1, "label": "v1",
    }
    assert explicit.json()["base"]["artifact_id"] == v1
    assert default.json()["removed"] == 1 and default.json()["added"] == 1
    # the first version of a non-cv kind has nothing before it
    assert first.json()["base"]["source"] == "none"
    assert first.json()["added"] == 2 and first.json()["removed"] == 0


@pytest.mark.asyncio
async def test_foreign_or_mismatched_ids_are_404_and_bad_against_is_422(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cv = await _save(client, app_id, "cv", "cv text")
        letter = await _save(client, app_id, "cover_letter", "letter text")
        unknown_app = await _diff(client, app_id + 9999, cv)
        unknown_artifact = await _diff(client, app_id, cv + 9999)
        other_kind = await _diff(client, app_id, cv, against=str(letter))
        junk = await _diff(client, app_id, cv, against="latest")
    assert unknown_app.status_code == 404
    assert unknown_artifact.status_code == 404
    assert other_kind.status_code == 404
    assert junk.status_code == 422


@pytest.mark.asyncio
async def test_no_profile_cv_gives_an_empty_base_not_an_error(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        v1 = await _save(client, app_id, "cv", "only line")
        resp = await _diff(client, app_id, v1)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["base"]["source"] == "profile"
    assert body["added"] == 1 and body["removed"] == 0


def test_no_mcp_tool_diffs_an_artifact():
    """Rule M2 — the agent already holds both texts; the diff is web-only."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "api" / "mcp_server.py"
    assert "/diff" not in src.read_text(encoding="utf-8")
    assert "diff_artifact" not in src.read_text(encoding="utf-8")

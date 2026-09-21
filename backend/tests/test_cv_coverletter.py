"""The tailored CV / cover letter AFTER decision 28 (2026-09-21, slice A).

Job360 has no brain of its own: the agent writes the CV and the cover letter and
saves the text with `save_artifact`. These routes read that saved text back, show
which lines are grounded in the user's own CV, take a human edit as a NEW
version, and render an ATS-friendly PDF / DOCX.

Covered here:
  - the bundle reads the NEWEST saved version per kind (rule #21: real text in,
    the same text out — never a default)
  - a human edit is a new version, never an overwrite (M3)
  - keep/download learn PATTERNS ONLY — no user content leaks (§7 privacy)
  - ATS PDF + DOCX download off a saved artifact
  - auth (rule #12/#25) and the IDOR line: another user's job is a 404
  - the LLM path is GONE: no generate route, no generator module
"""

from __future__ import annotations

import json

import pytest

from src.api import dependencies as api_deps

# ── Fakes: never touch the sync profile store ─────────────────────────────────

class _CV:
    def __init__(self, raw_text: str, linkedin_raw_text: str = ""):
        self.raw_text = raw_text
        self.linkedin_raw_text = linkedin_raw_text


class _Profile:
    def __init__(self, cv: _CV):
        self.cv_data = cv


_DEFAULT_CV = "Jane Doe\nSKILLS\nPython, ML pipelines, PyTorch\nEXPERIENCE\nBuilt fraud models at Acme."

_AD = {
    "title": "ML Engineer",
    "company": "Acme AI",
    "location": "London",
    "apply_url": "https://acme.example/careers/1",
    "description": "Build ML fraud pipelines in Python and PyTorch.",
}


def _fake_load_profile(_uid, cv_text: str = _DEFAULT_CV):
    return _Profile(_CV(cv_text))


async def _bring(client, ad: dict = _AD) -> tuple[int, int]:
    """Bring the ad → (job_id, application_id). The application is what the
    tailor routes resolve the caller's documents through."""
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["job"]["id"], body["application_id"]


async def _save(client, application_id: int, kind: str, text: str):
    """The agent's door: POST /applications/{id}/artifacts (MCP `save_artifact`)."""
    resp = await client.post(
        f"/api/applications/{application_id}/artifacts", json={"kind": kind, "text": text}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Unit tests (no DB) ────────────────────────────────────────────────────────

def test_the_tailor_has_no_llm_left():
    """Decision 28 slice A — the generator and its prompts are gone, and no
    route generates anything. If either comes back, this is the tripwire."""
    import importlib

    for gone in ("src.services.tailoring.generator", "src.services.tailoring.prompts"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(gone)

    import src.api.routes.tailor as tailor

    assert not hasattr(tailor, "generate")
    paths = {r.path for r in tailor.router.routes}  # type: ignore[attr-defined]
    assert not any(p.endswith("/generate") for p in paths), paths


def test_derive_patterns_leaks_no_content():
    """Layer-1 universal store is PATTERNS ONLY (spec §7): a distinctive CV phrase
    must NOT survive into the derived features."""
    from src.services.tailoring.patterns import derive_patterns
    text = "SUMMARY\nSecretCompanyXYZ confidential achievement 4242.\n- did a thing"
    feats = derive_patterns(text, "cv")
    blob = json.dumps(feats)
    assert "SecretCompanyXYZ" not in blob
    assert "4242" not in blob
    assert set(feats) >= {"doc_kind", "word_band", "style", "bullet_ratio"}


def test_pdf_render_is_a_pdf():
    from src.services.tailoring.pdf import render_pdf
    out = render_pdf("Jane Doe\nExperience\n- built things", title="Curriculum Vitae")
    assert isinstance(out, bytes) and out[:4] == b"%PDF"


# ── Route / integration tests ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bundle_returns_what_the_agent_saved(authenticated_async_context):
    """Value-presence (rule #21): the exact text the agent saved comes back —
    not a key with a default in it."""
    async with authenticated_async_context() as client:
        job_id, app_id = await _bring(client)
        await _save(client, app_id, "cv", "A CV the agent wrote from the profile.")
        await _save(client, app_id, "cover_letter", "Dear Acme AI, …")

        resp = await client.get(f"/api/tailor/{job_id}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    docs = {d["doc_kind"]: d for d in body["documents"]}
    assert set(docs) == {"cv", "cover_letter"}
    assert docs["cv"]["text"] == "A CV the agent wrote from the profile."
    assert docs["cv"]["version_no"] == 1
    assert docs["cv"]["made_by"], "the saver must be named — provenance, not a blank"


@pytest.mark.asyncio
async def test_bundle_is_empty_before_the_agent_saves_anything(authenticated_async_context):
    async with authenticated_async_context() as client:
        job_id, _ = await _bring(client)
        resp = await client.get(f"/api/tailor/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["documents"] == []


@pytest.mark.asyncio
async def test_bundle_404_when_the_job_was_never_brought(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.get("/api/tailor/999999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_save_edit_is_a_new_version_not_an_overwrite(authenticated_async_context):
    async with authenticated_async_context() as client:
        job_id, app_id = await _bring(client)
        await _save(client, app_id, "cv", "the agent's cv")

        resp = await client.patch(f"/api/tailor/{job_id}/cv", json={"text": "My polished CV vX"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["text"] == "My polished CV vX"
        assert resp.json()["version_no"] == 2
        assert resp.json()["made_by"] == "human"

        # both versions still readable — nothing was rewritten (M3)
        bundle = await client.get(f"/api/tailor/{job_id}")
        assert bundle.json()["documents"][0]["text"] == "My polished CV vX"
        detail = await client.get(f"/api/applications/{app_id}", params={"with_artifact_text": True})
    texts = [a["text"] for a in detail.json()["artifacts"]]
    assert "the agent's cv" in texts and "My polished CV vX" in texts


@pytest.mark.asyncio
async def test_save_edit_rejects_oversized_text(authenticated_async_context):
    """N6 — a client can't push an unbounded blob into the DB via the edit body."""
    async with authenticated_async_context() as client:
        job_id, app_id = await _bring(client)
        await _save(client, app_id, "cv", "the agent's cv")
        resp = await client.patch(f"/api/tailor/{job_id}/cv", json={"text": "x" * 50_001})
        assert resp.status_code == 422

        # No poisoning: the saved version is untouched.
        bundle = await client.get(f"/api/tailor/{job_id}")
    assert bundle.json()["documents"][0]["text"] == "the agent's cv"


@pytest.mark.asyncio
async def test_unknown_doc_kind_is_404(authenticated_async_context):
    async with authenticated_async_context() as client:
        job_id, app_id = await _bring(client)
        await _save(client, app_id, "cv", "the agent's cv")
        resp = await client.patch(f"/api/tailor/{job_id}/resume", json={"text": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_keep_learns_patterns_only(authenticated_async_context):
    """§5 learn-from-kept-only, §7 privacy: keeping records STRUCTURE, never
    the user's words. Decision 26 — no Keep flag is written on the version."""
    db = await api_deps.get_db()
    async with authenticated_async_context() as client:
        job_id, app_id = await _bring(client)
        await _save(client, app_id, "cv", "SUMMARY\nSecretPhraseQQQ\n- bullet one\n- bullet two")
        resp = await client.post(f"/api/tailor/{job_id}/cv/keep")

    assert resp.status_code == 200, resp.text
    assert resp.json()["text"].startswith("SUMMARY")
    patterns = await db.get_tailoring_patterns("cv")
    assert patterns, "keeping a doc should record a universal pattern"
    assert "SecretPhraseQQQ" not in json.dumps(patterns)


@pytest.mark.asyncio
async def test_learn_only_from_kept(authenticated_async_context):
    """A saved-but-never-used version is NOT a learning signal (§5)."""
    db = await api_deps.get_db()
    async with authenticated_async_context() as client:
        job_id, app_id = await _bring(client)
        await _save(client, app_id, "cv", "saved, never kept, never downloaded")
    assert await db.get_tailoring_patterns("cv") == []


@pytest.mark.asyncio
async def test_keep_404s_with_nothing_saved(authenticated_async_context):
    async with authenticated_async_context() as client:
        job_id, _ = await _bring(client)
        resp = await client.post(f"/api/tailor/{job_id}/cv/keep")
    assert resp.status_code == 404
    assert "save_artifact" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_download_renders_the_saved_version_as_pdf(authenticated_async_context):
    async with authenticated_async_context() as client:
        job_id, app_id = await _bring(client)
        await _save(client, app_id, "cv", "Jane Doe\nEXPERIENCE\n- built fraud models")
        resp = await client.post(f"/api/tailor/{job_id}/cv/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_download_renders_docx_too(authenticated_async_context):
    async with authenticated_async_context() as client:
        job_id, app_id = await _bring(client)
        await _save(client, app_id, "cover_letter", "Dear Acme AI,\n\nI build fraud pipelines.")
        resp = await client.post(f"/api/tailor/{job_id}/cover_letter/download", params={"fmt": "docx"})
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"  # a .docx is a zip
    assert "cover_letter_" in resp.headers["content-disposition"]


@pytest.mark.asyncio
async def test_download_rejects_an_unknown_format(authenticated_async_context):
    async with authenticated_async_context() as client:
        job_id, app_id = await _bring(client)
        await _save(client, app_id, "cv", "some cv")
        resp = await client.post(f"/api/tailor/{job_id}/cv/download", params={"fmt": "rtf"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_provenance_marks_the_users_own_facts(authenticated_async_context, monkeypatch):
    """The provenance view is deterministic and LLM-free: a line lifted from the
    user's own CV reads as grounded; an invented one does not."""
    import src.api.routes.tailor as tailor
    monkeypatch.setattr(tailor, "load_profile", _fake_load_profile)

    async with authenticated_async_context() as client:
        job_id, app_id = await _bring(client)
        await _save(client, app_id, "cv", "Built fraud models at Acme.\nWon the Nobel Prize in Chemistry.")
        prov = await client.get(f"/api/tailor/{job_id}/cv/provenance")

    assert prov.status_code == 200, prov.text
    segments = {s["text"]: s["grounded"] for s in prov.json()}
    assert segments["Built fraud models at Acme."] is True
    assert segments["Won the Nobel Prize in Chemistry."] is False


@pytest.mark.asyncio
async def test_documents_are_user_scoped(authenticated_async_context, fixture_user_id):
    """Privacy/IDOR (rule #25): the tailor resolves the application from the
    CALLER, so another user's job reads as 404, never as their documents."""
    from src.services.applications import spine as applications_spine

    db = await api_deps.get_db()
    async with authenticated_async_context() as client:
        job_id, app_id = await _bring(client)
        await _save(client, app_id, "cv", "the owner's cv")

    assert await applications_spine.latest_artifact(db, fixture_user_id, app_id, "cv")
    assert await applications_spine.latest_artifact(db, "someone-else-id", app_id, "cv") is None
    assert await applications_spine.get_application_by_job(db, "someone-else-id", job_id) is None


@pytest.mark.asyncio
async def test_tailor_requires_auth(authenticated_async_context):
    async with authenticated_async_context() as client:
        job_id, _ = await _bring(client)
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app
    async with authenticated_async_context():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
            resp = await anon.get(f"/api/tailor/{job_id}")
    assert resp.status_code == 401

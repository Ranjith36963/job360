"""Tests for the Per-User AI CV & Cover Letter feature (docs/peruser_cv_coverletter.md).

Covers, per the spec's Definition of Done:
  - the locked "reshape, don't fabricate" prompts (guardrail #2)
  - generation happy path + value-presence (rule #21: real input → non-default output)
  - quota gate (guardrail #1), auth gate (rule #12/#25), 404/400 edges
  - the edit-feedback loop: save → keep → learn only from KEPT (§5)
  - Layer-2 per-user learning (kept docs become few-shot examples, §6)
  - Layer-1 universal patterns are PATTERNS ONLY — no user content leaks (§7 privacy)
  - ATS PDF download
"""

from __future__ import annotations

import datetime
import json

import pytest

from src.api import dependencies as api_deps

# ── Fakes: never touch a real LLM or the sync profile store ───────────────────

class _CV:
    def __init__(self, raw_text: str, linkedin_raw_text: str = ""):
        self.raw_text = raw_text
        self.linkedin_raw_text = linkedin_raw_text


class _Profile:
    def __init__(self, cv: _CV):
        self.cv_data = cv


_DEFAULT_CV = "Jane Doe\nSKILLS\nPython, ML pipelines, PyTorch\nEXPERIENCE\nBuilt fraud models at Acme."


def _fake_load_profile(_uid, cv_text: str = _DEFAULT_CV):
    return _Profile(_CV(cv_text))


def _make_fake_llm(captured: list, document: str = "Tailored — reshaped from the real CV."):
    async def _fake(prompt: str, system: str = "") -> dict:
        captured.append({"prompt": prompt, "system": system})
        return {"document": document}
    return _fake


def _patch_route(monkeypatch, captured, *, profile=_fake_load_profile, document="Tailored — reshaped."):
    import src.api.routes.tailor as tailor
    monkeypatch.setattr(tailor, "llm_extract", _make_fake_llm(captured, document))
    monkeypatch.setattr(tailor, "load_profile", profile)
    return tailor


async def _insert_job(db, *, title="ML Engineer", company="Acme AI",
                      description="Build ML fraud pipelines in Python and PyTorch.") -> int:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    await db._conn.execute(
        """INSERT INTO jobs (title, company, location, description, apply_url, source,
             date_found, normalized_company, normalized_title, first_seen)
           VALUES (?, ?, 'London', ?, ?, 'reed', ?, ?, ?, ?)""",
        (title, company, description, "https://example.com/x", now,
         company.lower(), title.lower(), now),
    )
    await db._conn.commit()
    cur = await db._conn.execute(
        "SELECT id FROM jobs WHERE normalized_company = ? AND normalized_title = ?",
        (company.lower(), title.lower()),
    )
    row = await cur.fetchone()
    return int(row[0])


# ── Unit tests (no DB) ────────────────────────────────────────────────────────

def test_prompts_forbid_fabrication():
    """Guardrail #2 is the whole trust of the feature — the system prompts must
    forbid inventing anything."""
    from src.services.tailoring.prompts import COVER_LETTER_SYSTEM, CV_SYSTEM
    for sys_prompt in (CV_SYSTEM, COVER_LETTER_SYSTEM):
        assert "NEVER FABRICATE" in sys_prompt
        assert "never" in sys_prompt.lower() and "invent" in sys_prompt.lower()
        assert "ATS" in sys_prompt  # guardrail #5


@pytest.mark.asyncio
async def test_generator_reshapes_returns_document():
    from src.services.tailoring import generate_document
    captured: list = []
    doc = await generate_document(
        doc_kind="cv", cv_text="Real CV: Python, ML at Acme.", job_title="ML Engineer",
        company="X", job_description="Python role", llm_extract_fn=_make_fake_llm(captured),
    )
    assert doc.document  # non-empty
    assert captured and "Python, ML at Acme" in captured[0]["prompt"]  # real CV flowed in


@pytest.mark.asyncio
async def test_generator_empty_cv_raises():
    from src.services.tailoring import generate_document
    from src.services.tailoring.generator import EmptyCVError
    with pytest.raises(EmptyCVError):
        await generate_document(doc_kind="cv", cv_text="   ", job_title="x",
                                company="y", job_description="z",
                                llm_extract_fn=_make_fake_llm([]))


@pytest.mark.asyncio
async def test_generator_bad_kind_raises():
    from src.services.tailoring import generate_document
    with pytest.raises(ValueError):
        await generate_document(doc_kind="resume", cv_text="x", job_title="a",
                                company="b", job_description="c",
                                llm_extract_fn=_make_fake_llm([]))


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
async def test_generate_happy_path_stores_both_docs(authenticated_async_context, fixture_user_id, monkeypatch):
    captured: list = []
    _patch_route(monkeypatch, captured, document="Tailored CV body reshaped from real facts.")
    db = await api_deps.get_db()
    job_id = await _insert_job(db)

    async with authenticated_async_context() as client:
        resp = await client.post(f"/api/tailor/{job_id}/generate")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    kinds = {d["doc_kind"] for d in body["documents"]}
    assert kinds == {"cv", "cover_letter"}
    assert body["quota_used"] == 1

    # value-presence (rule #21): drafts are real non-empty text stored in the DB
    cv = await db.get_tailored_doc(fixture_user_id, job_id, "cv")
    cover = await db.get_tailored_doc(fixture_user_id, job_id, "cover_letter")
    assert cv["ai_draft"] == "Tailored CV body reshaped from real facts."
    assert cover["ai_draft"]
    # and the real inputs (job title + CV skill) actually reached the LLM
    all_prompts = "\n".join(c["prompt"] for c in captured)
    assert "ML Engineer" in all_prompts and "PyTorch" in all_prompts
    # usage recorded for the quota gate
    assert await db.count_tailored_usage_month(fixture_user_id) == 1


@pytest.mark.asyncio
async def test_generate_404_when_no_job(authenticated_async_context, monkeypatch):
    _patch_route(monkeypatch, [])
    async with authenticated_async_context() as client:
        resp = await client.post("/api/tailor/999999/generate")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_generate_400_when_no_cv(authenticated_async_context, monkeypatch):
    captured: list = []
    _patch_route(monkeypatch, captured, profile=lambda _uid: None)  # no profile → no CV
    db = await api_deps.get_db()
    job_id = await _insert_job(db)
    async with authenticated_async_context() as client:
        resp = await client.post(f"/api/tailor/{job_id}/generate")
    assert resp.status_code == 400
    assert not captured  # never called the LLM without a real CV (guardrail #2)


@pytest.mark.asyncio
async def test_generate_402_when_quota_exhausted(authenticated_async_context, monkeypatch):
    import src.api.routes.tailor as tailor
    _patch_route(monkeypatch, [])
    monkeypatch.setattr(tailor, "TAILOR_FREE_PER_MONTH", 0)  # everyone over cap
    db = await api_deps.get_db()
    job_id = await _insert_job(db)
    async with authenticated_async_context() as client:
        resp = await client.post(f"/api/tailor/{job_id}/generate")
    assert resp.status_code == 402


@pytest.mark.asyncio
async def test_get_returns_generated_docs(authenticated_async_context, monkeypatch):
    _patch_route(monkeypatch, [])
    db = await api_deps.get_db()
    job_id = await _insert_job(db)
    async with authenticated_async_context() as client:
        await client.post(f"/api/tailor/{job_id}/generate")
        resp = await client.get(f"/api/tailor/{job_id}")
    assert resp.status_code == 200
    assert len(resp.json()["documents"]) == 2


@pytest.mark.asyncio
async def test_save_edit_persists_polished(authenticated_async_context, fixture_user_id, monkeypatch):
    _patch_route(monkeypatch, [])
    db = await api_deps.get_db()
    job_id = await _insert_job(db)
    async with authenticated_async_context() as client:
        await client.post(f"/api/tailor/{job_id}/generate")
        resp = await client.patch(f"/api/tailor/{job_id}/cv", json={"text": "My polished CV vX"})
    assert resp.status_code == 200
    assert resp.json()["polished"] == "My polished CV vX"
    row = await db.get_tailored_doc(fixture_user_id, job_id, "cv")
    assert row["polished"] == "My polished CV vX"
    assert row["ai_draft"]  # original draft preserved (the diff = learning signal)


@pytest.mark.asyncio
async def test_save_edit_rejects_oversized_text(authenticated_async_context, fixture_user_id, monkeypatch):
    """N6 — a client can't push an unbounded blob into the DB via the edit body."""
    _patch_route(monkeypatch, [])
    db = await api_deps.get_db()
    job_id = await _insert_job(db)
    oversized_text = "x" * 50_001
    async with authenticated_async_context() as client:
        await client.post(f"/api/tailor/{job_id}/generate")
        resp = await client.patch(f"/api/tailor/{job_id}/cv", json={"text": oversized_text})
    assert resp.status_code == 422
    # No poisoning: the draft is untouched (still the original AI draft, not the oversized text)
    row = await db.get_tailored_doc(fixture_user_id, job_id, "cv")
    assert row["polished"] is None


@pytest.mark.asyncio
async def test_keep_marks_kept_and_learns_patterns_only(authenticated_async_context, fixture_user_id, monkeypatch):
    _patch_route(monkeypatch, [])
    db = await api_deps.get_db()
    job_id = await _insert_job(db)
    async with authenticated_async_context() as client:
        await client.post(f"/api/tailor/{job_id}/generate")
        await client.patch(f"/api/tailor/{job_id}/cv",
                           json={"text": "SUMMARY\nSecretPhraseQQQ\n- bullet one\n- bullet two"})
        resp = await client.post(f"/api/tailor/{job_id}/cv/keep")
    assert resp.status_code == 200
    assert resp.json()["status"] == "kept"

    row = await db.get_tailored_doc(fixture_user_id, job_id, "cv")
    assert row["status"] == "kept" and row["kept_at"]

    # universal layer learned STRUCTURE only — the user's content must not be there (§7)
    patterns = await db.get_tailoring_patterns("cv")
    assert patterns, "keeping a doc should record a universal pattern"
    assert "SecretPhraseQQQ" not in json.dumps(patterns)


@pytest.mark.asyncio
async def test_learn_only_from_kept(authenticated_async_context, monkeypatch):
    """A generated-but-abandoned draft is NOT a learning signal (§5)."""
    _patch_route(monkeypatch, [])
    db = await api_deps.get_db()
    job_id = await _insert_job(db)
    async with authenticated_async_context() as client:
        await client.post(f"/api/tailor/{job_id}/generate")  # draft, never kept
    # nothing kept → universal layer stays empty
    assert await db.get_tailoring_patterns("cv") == []


@pytest.mark.asyncio
async def test_layer2_uses_kept_docs_as_examples(authenticated_async_context, monkeypatch):
    """Per-user learning (§6): a kept doc becomes a few-shot example in the next prompt."""
    captured: list = []
    _patch_route(monkeypatch, captured)
    db = await api_deps.get_db()
    job1 = await _insert_job(db, title="ML Engineer")
    job2 = await _insert_job(db, title="Data Scientist", company="Beta Corp")
    async with authenticated_async_context() as client:
        await client.post(f"/api/tailor/{job1}/generate")
        await client.patch(f"/api/tailor/{job1}/cv",
                           json={"text": "MY_UNIQUE_VOICE_ZZ polished CV"})
        await client.post(f"/api/tailor/{job1}/cv/keep")
        captured.clear()
        await client.post(f"/api/tailor/{job2}/generate")  # new job → should reuse my style
    assert any("MY_UNIQUE_VOICE_ZZ" in c["prompt"] for c in captured)


@pytest.mark.asyncio
async def test_download_returns_pdf_and_keeps(authenticated_async_context, fixture_user_id, monkeypatch):
    _patch_route(monkeypatch, [])
    db = await api_deps.get_db()
    job_id = await _insert_job(db)
    async with authenticated_async_context() as client:
        await client.post(f"/api/tailor/{job_id}/generate")
        resp = await client.get(f"/api/tailor/{job_id}/cv/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"
    row = await db.get_tailored_doc(fixture_user_id, job_id, "cv")
    assert row["status"] == "kept"  # downloading = using it


@pytest.mark.asyncio
async def test_docs_are_user_scoped(authenticated_async_context, fixture_user_id, monkeypatch):
    """Privacy/IDOR (rule #25): docs are keyed by user.id; another user sees none."""
    _patch_route(monkeypatch, [])
    db = await api_deps.get_db()
    job_id = await _insert_job(db)
    async with authenticated_async_context() as client:
        await client.post(f"/api/tailor/{job_id}/generate")
    assert await db.get_tailored_docs(fixture_user_id, job_id)  # owner has docs
    assert await db.get_tailored_docs("someone-else-id", job_id) == []  # nobody else


@pytest.mark.asyncio
async def test_generate_requires_auth(authenticated_async_context, monkeypatch):
    _patch_route(monkeypatch, [])
    db = await api_deps.get_db()
    job_id = await _insert_job(db)
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app
    async with authenticated_async_context():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
            resp = await anon.post(f"/api/tailor/{job_id}/generate")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_fabrication_flag_surfaces_not_section_headers(authenticated_async_context, fixture_user_id, monkeypatch):
    """Guardrail #2 integrity pass: an invented proper noun is flagged + surfaced to the
    user through the API; a normal CV section header (SUMMARY) is not a false positive."""
    fake_doc = "SUMMARY\nData engineer who worked at FabricatedCorpXQ using Python."
    _patch_route(monkeypatch, [], document=fake_doc)
    db = await api_deps.get_db()
    job_id = await _insert_job(db)
    async with authenticated_async_context() as client:
        resp = await client.post(f"/api/tailor/{job_id}/generate")
    assert resp.status_code == 200, resp.text
    cv = [d for d in resp.json()["documents"] if d["doc_kind"] == "cv"][0]
    assert "FabricatedCorpXQ" in cv["flagged_terms"]   # real fabrication surfaced
    assert "SUMMARY" not in cv["flagged_terms"]         # section header not flagged
    # persists to the DB so the warning is still there on reopen
    row = await db.get_tailored_doc(fixture_user_id, job_id, "cv")
    assert "FabricatedCorpXQ" in row["flagged_terms"]

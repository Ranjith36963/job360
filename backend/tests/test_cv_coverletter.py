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
async def test_generate_503_hides_internal_error_detail(authenticated_async_context, monkeypatch, caplog):
    """N5 — an LLM/provider failure must not leak raw exception text (which can
    contain API keys / internal details) to the client. The real error still
    goes to the server-side logger."""
    import logging

    async def _raising_llm(prompt: str, system: str = "") -> dict:
        raise RuntimeError("upstream 500: api_key=sk-supersecret-leak-me")

    import src.api.routes.tailor as tailor
    monkeypatch.setattr(tailor, "llm_extract", _raising_llm)
    monkeypatch.setattr(tailor, "load_profile", _fake_load_profile)

    db = await api_deps.get_db()
    job_id = await _insert_job(db)

    with caplog.at_level(logging.ERROR, logger="job360.api.tailor"):
        async with authenticated_async_context() as client:
            resp = await client.post(f"/api/tailor/{job_id}/generate")

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail == "Generation failed, please try again."
    assert "sk-supersecret-leak-me" not in detail
    # the real error reached the server-side logger, just not the client
    assert any("sk-supersecret-leak-me" in rec.getMessage() or
               (rec.exc_info and "sk-supersecret-leak-me" in str(rec.exc_info[1]))
               for rec in caplog.records)


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
        resp = await client.post(f"/api/tailor/{job_id}/cv/download")
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


# ── M7: upsert_tailored_doc is atomic (DELETE + INSERT in one transaction) ─────

@pytest.mark.asyncio
async def test_upsert_replaces_atomically(fixture_user_id):
    """A normal regenerate replaces the existing draft — exactly one row survives."""
    db = await api_deps.get_db()
    job_id = await _insert_job(db)
    await db.upsert_tailored_doc(fixture_user_id, job_id, "cv", "v1 original draft")
    await db.upsert_tailored_doc(fixture_user_id, job_id, "cv", "v2 fresh draft")
    row = await db.get_tailored_doc(fixture_user_id, job_id, "cv")
    assert row["ai_draft"] == "v2 fresh draft"  # replaced
    assert len(await db.get_tailored_docs(fixture_user_id, job_id)) == 1  # no dupes


@pytest.mark.asyncio
async def test_upsert_insert_failure_does_not_lose_existing_doc(fixture_user_id, monkeypatch):
    """M7 data-loss fix: if the INSERT dies AFTER the DELETE, the transaction rolls
    back and the user's original tailored document survives — it is NOT lost."""
    db = await api_deps.get_db()
    job_id = await _insert_job(db)
    await db.upsert_tailored_doc(fixture_user_id, job_id, "cv", "precious original draft")

    # Break the INSERT step only; the DELETE still runs first inside the txn.
    real_execute = db._conn.execute

    async def _failing_execute(sql, params=()):
        if sql.strip().upper().startswith("INSERT INTO TAILORED_DOCUMENTS"):
            raise RuntimeError("simulated crash between DELETE and INSERT")
        return await real_execute(sql, params)

    monkeypatch.setattr(db._conn, "execute", _failing_execute)
    with pytest.raises(RuntimeError):
        await db.upsert_tailored_doc(fixture_user_id, job_id, "cv", "doomed new draft")
    monkeypatch.undo()  # restore real execute for the assertion read

    # The DELETE was rolled back with the failed INSERT — original doc still there.
    row = await db.get_tailored_doc(fixture_user_id, job_id, "cv")
    assert row is not None, "original doc was lost — DELETE was not rolled back"
    assert row["ai_draft"] == "precious original draft"


# ── Provenance: a tailored doc must name the profile snapshot that wrote it ────


@pytest.mark.asyncio
async def test_generated_docs_are_stamped_with_the_profile_version(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    """A tailored CV must record WHICH profile version produced it.

    `tailored_documents.profile_version` has existed since migration 0023
    ("which user_profile_versions snapshot fed the draft") and
    `upsert_tailored_doc` has always accepted it — but the one caller never
    passed it, so production held 4 tailored documents with 0 versions between
    them.

    That is the most expensive, most personal artifact this product makes: a
    paid LLM call, sent to a real employer. Without the stamp there is no way to
    answer "which version of my profile wrote this?" — and no way to find the
    documents generated from a profile that was later found to be wrong (the
    CV-blend bug produced exactly such profiles).
    """
    import src.services.profile.storage as storage

    captured: list = []
    _patch_route(monkeypatch, captured)
    # Pin the version the route should stamp, so the assertion is exact rather
    # than "some integer".
    monkeypatch.setattr(
        "src.api.routes.tailor.current_profile_version_id", lambda _uid: 4242
    )
    assert hasattr(storage, "current_profile_version_id")

    db = await api_deps.get_db()
    job_id = await _insert_job(db)

    async with authenticated_async_context() as client:
        resp = await client.post(f"/api/tailor/{job_id}/generate")
    assert resp.status_code == 200, resp.text

    # BOTH documents — the cover letter is generated from the same profile.
    for kind in ("cv", "cover_letter"):
        row = await db.get_tailored_doc(fixture_user_id, job_id, kind)
        assert row is not None, f"{kind} was not stored"
        assert row["profile_version"] == 4242, (
            f"{kind} must record the profile snapshot that produced it, "
            f"got {row.get('profile_version')!r}"
        )


@pytest.mark.asyncio
async def test_missing_profile_version_does_not_block_generation(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    """No version available (fresh account, or the versions table is empty) must
    still produce documents — provenance is a record, never a gate."""
    captured: list = []
    _patch_route(monkeypatch, captured)
    monkeypatch.setattr(
        "src.api.routes.tailor.current_profile_version_id", lambda _uid: None
    )

    db = await api_deps.get_db()
    job_id = await _insert_job(db)

    async with authenticated_async_context() as client:
        resp = await client.post(f"/api/tailor/{job_id}/generate")

    assert resp.status_code == 200, resp.text
    row = await db.get_tailored_doc(fixture_user_id, job_id, "cv")
    assert row["ai_draft"], "the document must still be generated and stored"
    assert row["profile_version"] is None

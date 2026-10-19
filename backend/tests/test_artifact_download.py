"""Download ONE stored CV / cover-letter version as PDF or DOCX (owner
decision 2026-10-04: Copy / Word / PDF on every version)."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tests.test_application_spine import _second_user_session_cookie, _session_client

_AD = {
    "title": "Platform Engineer",
    "company": "Northwind Ltd",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/9",
    "description": "Build the platform. Kubernetes, Go, Postgres.",
}


async def _setup(client: AsyncClient) -> tuple[int, int, int, int]:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    ids = []
    for kind, text in (("cv", "Jane Doe\nSKILLS\nPython"), ("cover_letter", "Dear team,\nHello."), ("answers", "Q: why?")):
        r = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": kind, "text": text})
        assert r.status_code == 201, r.text
        ids.append(int(r.json()["artifact_id"]))
    return app_id, ids[0], ids[1], ids[2]


def _url(app_id: int, artifact_id: int) -> str:
    return f"/api/applications/{app_id}/artifacts/{artifact_id}/download"


@pytest.mark.asyncio
async def test_owner_gets_pdf_and_docx_with_a_safe_filename(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cv, letter, _ = await _setup(client)
        pdf = await client.post(_url(app_id, cv), params={"fmt": "pdf"})
        docx = await client.post(_url(app_id, letter), params={"fmt": "docx"})
    assert pdf.status_code == 200, pdf.text
    assert pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-type"] == "application/pdf"
    assert 'filename="northwind-ltd-cv-v1.pdf"' in pdf.headers["content-disposition"]
    assert docx.status_code == 200, docx.text
    assert docx.content.startswith(b"PK")
    assert 'filename="northwind-ltd-cover-letter-v1.docx"' in docx.headers["content-disposition"]


@pytest.mark.asyncio
async def test_other_user_gets_404(authenticated_async_context):
    async with authenticated_async_context() as owner:
        app_id, cv, _, _ = await _setup(owner)
    cookie = await _second_user_session_cookie("dl-intruder@example.com")
    async with _session_client(cookie) as intruder:
        assert (await intruder.post(_url(app_id, cv), params={"fmt": "pdf"})).status_code == 404


@pytest.mark.asyncio
async def test_wrong_kind_and_bad_fmt_are_rejected(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cv, _, answers = await _setup(client)
        wrong_kind = await client.post(_url(app_id, answers), params={"fmt": "pdf"})
        bad_fmt = await client.post(_url(app_id, cv), params={"fmt": "exe"})
        unknown = await client.post(_url(app_id, cv + 9999), params={"fmt": "pdf"})
    assert wrong_kind.status_code == 400
    assert bad_fmt.status_code == 400
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_is_401(authenticated_async_context):
    from src.api.main import app

    async with authenticated_async_context():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
            resp = await anon.post(_url(1, 1), params={"fmt": "pdf"})
    assert resp.status_code == 401

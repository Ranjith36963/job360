"""Security notices (gap roadmap slice 36, G400): the account owner gets one
email when an assistant is connected, a personal token is minted, or the
password is changed or reset.

The low-level sender (``security_notices.send_system_email``) is monkeypatched,
exactly as ``test_magic_link.py`` does for its own caller.
"""
from __future__ import annotations

import pytest

from src.core import settings
from src.repositories import pgsync
from src.services.auth import security_notices, tokens
from tests.test_oauth_server import CLAUDE_REDIRECT, _authorize, _pkce_pair, _register_client, _rid_from_location

EMAIL = "test@example.com"  # the authenticated_async_context fixture user
FORGOT = f"{settings.SITE_BASE_URL}/forgot-password"


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr("src.services.auth.security_notices.send_system_email", _fake)
    return calls


@pytest.fixture
def broken_sender(monkeypatch):
    async def _boom(**_kwargs):
        raise RuntimeError("smtp exploded for test@example.com")

    monkeypatch.setattr("src.services.auth.security_notices.send_system_email", _boom)


def _assert_warns_how_to_recover(mail: dict) -> None:
    warning = f"If this wasn't you, reset your password at {FORGOT} — that also disconnects every assistant."
    assert warning in mail["body_text"]
    assert FORGOT in mail["body_html"]
    assert " UTC" in mail["body_text"]


async def _decide(session_client, client_id: str, *, approve: bool):
    _, challenge = _pkce_pair()
    resp = await _authorize(
        session_client, client_id=client_id, redirect_uri=CLAUDE_REDIRECT, code_challenge=challenge
    )
    rid = _rid_from_location(resp.headers["location"])
    decided = await session_client.post(f"/api/oauth/authorize/{rid}/decision", json={"approve": approve})
    assert decided.status_code == 200, decided.text


async def _connect_assistant(session_client, *, client_name: str = "Claude") -> None:
    registered = await _register_client([CLAUDE_REDIRECT], client_name=client_name)
    await _decide(session_client, registered["client_id"], approve=True)


def _issue_reset_token(user_id: str) -> str:
    raw, hashed = tokens.generate_token()
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute(
        "INSERT INTO password_resets(user_id, token_hash, expires_at) VALUES (?, ?, '2999-01-01T00:00:00Z')",
        (user_id, hashed),
    )
    conn.commit()
    conn.close()
    return raw


# ── The four triggers ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_oauth_grant_sends_one_notice_naming_the_client(authenticated_async_context, sent):
    async with authenticated_async_context() as client:
        await _connect_assistant(client, client_name="Claude")
    assert len(sent) == 1
    mail = sent[0]
    assert mail["to_email"] == EMAIL
    assert mail["subject"] == "Claude was connected to your Job360 account"
    assert "Claude was connected to your Job360 account" in mail["body_text"]
    _assert_warns_how_to_recover(mail)


@pytest.mark.asyncio
async def test_approving_an_existing_grant_again_sends_nothing(authenticated_async_context, sent):
    async with authenticated_async_context() as client:
        registered = await _register_client([CLAUDE_REDIRECT])
        await _decide(client, registered["client_id"], approve=True)
        await _decide(client, registered["client_id"], approve=True)  # reuses the live grant
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_denying_an_oauth_request_sends_nothing(authenticated_async_context, sent):
    async with authenticated_async_context() as client:
        registered = await _register_client([CLAUDE_REDIRECT])
        await _decide(client, registered["client_id"], approve=False)
    assert sent == []


@pytest.mark.asyncio
async def test_minting_a_token_sends_one_notice_without_the_token(authenticated_async_context, sent):
    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": "my-agent"})
    assert resp.status_code == 201, resp.text
    secret = resp.json()["token"]
    assert len(sent) == 1
    mail = sent[0]
    assert mail["to_email"] == EMAIL
    assert mail["subject"] == "A new assistant token 'my-agent' was created"
    _assert_warns_how_to_recover(mail)
    blob = " ".join(str(v) for v in mail.values())
    assert secret not in blob and "j360_" not in blob  # the value (and its prefix) never leaves the 201 body


@pytest.mark.asyncio
async def test_a_refused_mint_sends_nothing(authenticated_async_context, sent, monkeypatch):
    monkeypatch.setattr(settings, "API_TOKENS_PER_USER", 1)
    async with authenticated_async_context() as client:
        assert (await client.post("/api/tokens", json={"name": "first"})).status_code == 201
        assert (await client.post("/api/tokens", json={"name": "second"})).status_code == 409
    assert [m["subject"] for m in sent] == ["A new assistant token 'first' was created"]


@pytest.mark.asyncio
async def test_changing_the_password_sends_one_notice(authenticated_async_context, sent):
    async with authenticated_async_context() as client:
        resp = await client.patch(
            "/api/auth/users/me/password",
            json={"current_password": "s3cretpassword", "new_password": "newpassword1"},
        )
    assert resp.status_code == 204, resp.text
    assert len(sent) == 1
    mail = sent[0]
    assert mail["to_email"] == EMAIL
    assert mail["subject"] == "Your Job360 password was changed"
    _assert_warns_how_to_recover(mail)


@pytest.mark.asyncio
async def test_a_wrong_current_password_sends_nothing(authenticated_async_context, sent):
    async with authenticated_async_context() as client:
        resp = await client.patch(
            "/api/auth/users/me/password",
            json={"current_password": "WRONG-password", "new_password": "newpassword1"},
        )
    assert resp.status_code == 401
    assert sent == []


@pytest.mark.asyncio
async def test_resetting_the_password_sends_one_notice(authenticated_async_context, sent):
    raw = _issue_reset_token(authenticated_async_context.fixture_user_id)
    async with authenticated_async_context() as client:
        resp = await client.post(
            "/api/auth/password-reset/confirm", json={"token": raw, "new_password": "newpassword2"}
        )
    assert resp.status_code == 204, resp.text
    assert len(sent) == 1
    assert sent[0]["to_email"] == EMAIL
    assert sent[0]["subject"] == "Your Job360 password was changed"
    assert raw not in " ".join(str(v) for v in sent[0].values())


@pytest.mark.asyncio
async def test_an_invalid_reset_token_sends_nothing(authenticated_async_context, sent):
    async with authenticated_async_context() as client:
        resp = await client.post(
            "/api/auth/password-reset/confirm", json={"token": "x" * 30, "new_password": "newpassword2"}
        )
    assert resp.status_code == 400
    assert sent == []


# ── A failing sender never changes the route's status ─────────────────────────


@pytest.mark.asyncio
async def test_a_failing_sender_does_not_change_any_route_status(authenticated_async_context, broken_sender):
    raw = _issue_reset_token(authenticated_async_context.fixture_user_id)
    async with authenticated_async_context() as client:
        minted = await client.post("/api/tokens", json={"name": "my-agent"})
        await _connect_assistant(client)  # asserts the decision route answered 200
        changed = await client.patch(
            "/api/auth/users/me/password",
            json={"current_password": "s3cretpassword", "new_password": "newpassword1"},
        )
        reset = await client.post(
            "/api/auth/password-reset/confirm", json={"token": raw, "new_password": "newpassword2"}
        )
    assert minted.status_code == 201 and minted.json()["token"].startswith("j360_")
    assert changed.status_code == 204
    assert reset.status_code == 204


@pytest.mark.asyncio
async def test_failure_is_logged_with_ids_only(authenticated_async_context, broken_sender, caplog):
    caplog.set_level("WARNING", logger="job360.auth.security_notices")
    uid = authenticated_async_context.fixture_user_id
    assert await security_notices.send_security_notice(str(settings.DB_PATH), uid, "password_changed") is False
    lines = [r.getMessage() for r in caplog.records if "security_notice_failed" in r.getMessage()]
    assert len(lines) == 1
    assert uid in lines[0] and "password_changed" in lines[0]
    assert EMAIL not in lines[0]  # not even the error text, which echoed the address


@pytest.mark.asyncio
async def test_unsent_is_false_and_success_is_logged(authenticated_async_context, monkeypatch, caplog):
    uid = authenticated_async_context.fixture_user_id

    async def _unset(**_kwargs):
        return False  # what send_system_email returns when no mail backend is configured

    monkeypatch.setattr("src.services.auth.security_notices.send_system_email", _unset)
    assert await security_notices.send_security_notice(str(settings.DB_PATH), uid, "password_changed") is False

    async def _ok(**_kwargs):
        return True

    monkeypatch.setattr("src.services.auth.security_notices.send_system_email", _ok)
    caplog.set_level("INFO", logger="job360.auth.security_notices")
    assert await security_notices.send_security_notice(str(settings.DB_PATH), uid, "password_changed") is True
    assert f"security_notice_sent: user={uid} kind=password_changed" in caplog.text


@pytest.mark.asyncio
async def test_unknown_kind_and_unknown_user_send_nothing(authenticated_async_context, sent):
    uid = authenticated_async_context.fixture_user_id
    assert await security_notices.send_security_notice(str(settings.DB_PATH), uid, "nope") is False
    assert await security_notices.send_security_notice(str(settings.DB_PATH), "no-such-user", "password_changed") is False
    assert sent == []


# ── Untrusted names ───────────────────────────────────────────────────────────


def test_client_name_with_header_injection_is_sanitised():
    subject, text, html_body = security_notices._build(
        "oauth_grant", client_name="Evil\r\nBcc: attacker@example.com\x00<b>x</b>" + "y" * 200
    )
    assert "\r" not in subject and "\n" not in subject and "\x00" not in subject
    assert subject.startswith("Evil Bcc: attacker@example.com <b>x</b>y")
    name = subject.removesuffix(" was connected to your Job360 account")
    assert len(name) == security_notices.MAX_NAME_CHARS
    assert "\r" not in text and "Bcc" in text.split("\n\n")[1]  # the one body line it lands on has no newline in it
    assert "<b>" not in html_body and "&lt;b&gt;" in html_body


def test_token_name_is_sanitised_and_capped_too():
    subject, text, _html = security_notices._build("token_created", token_name="a\r\nBcc: z@example.com" + "y" * 200)
    cleaned = ("a Bcc: z@example.com" + "y" * 200)[: security_notices.MAX_NAME_CHARS]
    assert subject == f"A new assistant token '{cleaned}' was created"
    assert "\r" not in subject and "\n" not in subject


def test_empty_names_fall_back():
    assert security_notices._build("oauth_grant", client_name="\r\n")[0] == (
        "An assistant was connected to your Job360 account"
    )
    assert security_notices._build("token_created", token_name=None)[0] == (
        "A new assistant token 'unnamed' was created"
    )

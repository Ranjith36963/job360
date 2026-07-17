"""SSRF guard unit tests + send-time DNS-rebinding re-check (M1).

No real network: hostnames are resolved via a monkeypatched
``socket.getaddrinfo``; IP-literal hosts skip resolution entirely.
"""
import os
import socket
import tempfile
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from migrations import runner
from src.repositories import pg
from src.services.channels import crypto, dispatcher
from src.services.channels.ssrf_guard import assert_public_http_url


def _resolve_to(ip: str):
    def _fake(host, *args, **kwargs):
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, 0))]

    return _fake


# ---------------------------------------------------------------------------
# assert_public_http_url — scheme / port / IP-literal / DNS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",            # loopback v4
        "http://[::1]/x",                # loopback v6
        "http://169.254.169.254/x",      # cloud metadata (link-local)
        "http://10.0.0.5/x",             # RFC1918
        "http://192.168.1.1/x",          # RFC1918
        "http://172.16.5.5/x",           # RFC1918
        "http://0.0.0.0/x",              # unspecified
    ],
)
def test_ip_literal_private_rejected(url):
    with pytest.raises(ValueError):
        assert_public_http_url(url)


def test_public_ip_literal_ok():
    assert_public_http_url("http://93.184.216.34/x")  # no raise


def test_bad_scheme_rejected():
    with pytest.raises(ValueError, match="http"):
        assert_public_http_url("ftp://example.com/x")


@pytest.mark.parametrize("port", [22, 25, 3306, 6379, 9200])
def test_bad_port_rejected(port):
    with patch.object(socket, "getaddrinfo", _resolve_to("93.184.216.34")):
        with pytest.raises(ValueError, match="port"):
            assert_public_http_url(f"http://example.com:{port}/x")


@pytest.mark.parametrize("port", [80, 443, 8080, 8443])
def test_allowed_port_ok(port):
    with patch.object(socket, "getaddrinfo", _resolve_to("93.184.216.34")):
        assert_public_http_url(f"http://example.com:{port}/x")  # no raise


def test_public_host_resolves_ok():
    with patch.object(socket, "getaddrinfo", _resolve_to("93.184.216.34")):
        assert_public_http_url("https://hooks.example.com/x")  # no raise


def test_host_resolving_private_rejected():
    with patch.object(socket, "getaddrinfo", _resolve_to("10.1.2.3")):
        with pytest.raises(ValueError, match="private"):
            assert_public_http_url("https://sneaky.example.com/x")


def test_unresolvable_host_rejected():
    def _boom(host, *args, **kwargs):
        raise socket.gaierror("nope")

    with patch.object(socket, "getaddrinfo", _boom):
        with pytest.raises(ValueError, match="resolved"):
            assert_public_http_url("https://nope.invalid/x")


# ---------------------------------------------------------------------------
# Send-time re-resolution (DNS-rebinding defence) through the dispatcher
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fernet_key():
    crypto.set_test_key(Fernet.generate_key().decode("ascii"))


@pytest.fixture
async def channel_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    async with pg.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(job_id)
            );
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                stage TEXT NOT NULL DEFAULT 'applied',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(job_id)
            );
            """
        )
        await db.commit()
    await runner.up(path)
    async with pg.connect(path) as db:
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            ("alice", "a@x", "!"),
        )
        await db.commit()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


async def _insert_webhook(db_path, apprise_url):
    ct = crypto.encrypt(apprise_url)
    async with pg.connect(db_path) as db:
        cur = await db.execute(
            """
            INSERT INTO user_channels(user_id, channel_type, display_name,
                                      credential_encrypted, enabled)
            VALUES(?, 'webhook', 'hook', ?, 1)
            """,
            ("alice", ct),
        )
        await db.commit()
        return cur.lastrowid


@pytest.mark.asyncio
async def test_send_blocks_webhook_rebound_to_private(channel_db):
    """A stored webhook whose host now resolves to a private IP is blocked at send."""
    cid = await _insert_webhook(channel_db, "jsons://internal.example/hook")
    with patch("apprise.Apprise") as MockApp, \
            patch.object(socket, "getaddrinfo", _resolve_to("10.0.0.5")):
        instance = MockApp.return_value
        instance.notify.return_value = True
        if hasattr(instance, "async_notify"):
            del instance.async_notify
        async with pg.connect(channel_db) as db:
            result = await dispatcher.test_send(db, cid, user_id="alice")
    assert result.ok is False
    assert "private" in result.error
    instance.notify.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_blocks_webhook_rebound_to_private(channel_db):
    """dispatch() drops a webhook re-pointed to a private host, still returns a result."""
    await _insert_webhook(channel_db, "jsons://internal.example/hook")
    with patch("apprise.Apprise") as MockApp, \
            patch.object(socket, "getaddrinfo", _resolve_to("127.0.0.1")):
        instance = MockApp.return_value
        instance.notify.return_value = True
        if hasattr(instance, "async_notify"):
            del instance.async_notify
        async with pg.connect(channel_db) as db:
            results = await dispatcher.dispatch(
                db, user_id="alice", title="Hi", body="there"
            )
    assert len(results) == 1
    assert results[0].ok is False
    assert "private" in results[0].error
    instance.notify.assert_not_called()


@pytest.mark.asyncio
async def test_send_allows_webhook_still_public(channel_db):
    """A webhook whose host still resolves public is delivered normally."""
    cid = await _insert_webhook(channel_db, "jsons://good.example/hook")
    with patch("apprise.Apprise") as MockApp, \
            patch.object(socket, "getaddrinfo", _resolve_to("93.184.216.34")):
        instance = MockApp.return_value
        instance.notify.return_value = True
        if hasattr(instance, "async_notify"):
            del instance.async_notify
        async with pg.connect(channel_db) as db:
            result = await dispatcher.test_send(db, cid, user_id="alice")
    assert result.ok is True

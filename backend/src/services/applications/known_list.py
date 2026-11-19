"""The hunt's known list, served by Job360 (2026-11-17 hunt run: helpers hand-copied 966 rows).

The 11-17 run's helpers paged ``list_applications`` and copied each ``job_url`` into files by hand; nothing
proved the copy whole. Now ``whats_due`` hands an assistant a short-lived signed link (``mint``), and
``GET /api/known-list/<token>`` (routes/known_list.py) answers the user's known jobs as plain text, one
line per job: ``urls`` = every job link (the application's and the catalog row's, distinct), ``ct`` =
``company ||| title ||| id ||| location`` per application (the place added 2026-11-18, #1665; a reader of the
first columns is unchanged). The token is stateless (itsdangerous, HMAC-SHA256 over the
user id, salt ``job360.known-list``, ``KNOWN_LIST_TTL_MINUTES``), read-only, and names one user: a token
can only ever read the user it was minted for. Test applications are left out, as in list_applications.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Literal, Optional

from src.core import settings
from src.repositories.database import JobDatabase

_SALT = "job360.known-list"
Form = Literal["urls", "ct"]


class KnownListError(Exception):
    """A token that does not resolve: ``reason`` is a closed word (bad_signature | expired)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _signer() -> Any:
    from itsdangerous import TimestampSigner  # noqa: PLC0415 - only this door signs known-list links

    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        raise KnownListError("no_secret")
    return TimestampSigner(secret, salt=_SALT)


async def _rows(db: JobDatabase, user_id: str) -> list[dict[str, Any]]:
    from src.services.applications.test_marker import TEST_APP_SQL  # noqa: PLC0415

    test_sql, test_params = TEST_APP_SQL("a")
    cur = await db._db.execute(
        "SELECT a.id, a.job_company, a.job_title, a.job_location, a.job_url, j.apply_url AS catalog_url "  # noqa: S608
        "FROM applications a "
        f"LEFT JOIN jobs j ON j.id = a.job_id WHERE a.user_id = ? AND NOT {test_sql} ORDER BY a.id",
        [user_id, *test_params],
    )
    return [dict(r) for r in await cur.fetchall()]


def _one_line(value: Any) -> str:
    """A stored field as one line that cannot fake a column (never caller text in a regex)."""
    return " ".join(str(value or "").replace("|||", "|").split())


def render(rows: list[dict[str, Any]], form: Form) -> list[str]:
    if form == "ct":
        return [
            " ||| ".join((_one_line(r["job_company"]), _one_line(r["job_title"]), str(r["id"]),
                          _one_line(r["job_location"])))
            for r in rows
        ]
    seen: dict[str, None] = {}
    for r in rows:
        for url in (r.get("job_url"), r.get("catalog_url")):
            line = _one_line(url)
            if line:
                seen.setdefault(line, None)
    return list(seen)


async def mint(db: JobDatabase, user_id: str, now: datetime) -> dict[str, Any]:
    """The signed links plus the line counts the run checks its downloads against."""
    rows = await _rows(db, user_id)
    token = _signer().sign(user_id.encode("utf-8")).decode("ascii")
    base = f"{settings.SITE_BASE_URL}/api/known-list/{token}"
    expires = now + timedelta(minutes=settings.KNOWN_LIST_TTL_MINUTES)
    return {
        "urls_url": base, "ct_url": f"{base}?form=ct", "total": len(rows), "urls": len(render(rows, "urls")),
        "expires_at": expires.isoformat(timespec="seconds"),
    }


def resolve(token: str) -> str:
    """The user id a live token names; KnownListError otherwise (never says whose)."""
    from itsdangerous import BadSignature, SignatureExpired  # noqa: PLC0415

    try:
        raw = _signer().unsign(token.encode("ascii", "replace"), max_age=settings.KNOWN_LIST_TTL_MINUTES * 60)
    except SignatureExpired:
        raise KnownListError("expired") from None
    except BadSignature:
        raise KnownListError("bad_signature") from None
    return str(raw.decode("utf-8"))


async def lines_for(db: JobDatabase, user_id: str, form: Form) -> list[str]:
    return render(await _rows(db, user_id), form)


def parse_form(value: Optional[str]) -> Form:
    return "ct" if value == "ct" else "urls"

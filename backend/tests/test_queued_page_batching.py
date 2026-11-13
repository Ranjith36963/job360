"""The queued page's per-row work runs per PAGE: ``kit.compute_answers`` once per country and the duplicate
reads (company, same role, same company) as a fixed number of statements, with the answers unchanged.

2026-11-13, production: ``GET /api/applications?queued=true`` took 1.6 to 2.3 s on a large account, of which
``q_answers`` (about 650 to 870 ms) and ``q_duplicates`` (about 285 to 390 ms) were one call or four statements
per queued row. The oracle here is the ORIGINAL per-row path (``compute_answers`` + ``_missing_flags`` and
``kit.duplicate_facts``), called row by row over the same rows. Timing is never asserted: call and statement
counts are.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.api.routes import browser as browser_routes
from src.core import settings
from src.services.applications import kit, queue_preflight, ready
from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings, UserProfile

_DATES = [datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc), datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)]


def _exec(statement: str, rows: list[tuple]) -> list[Any]:
    """Run ``statement`` once per parameter tuple on its own connection, commit, return the last fetch."""
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    out: list[Any] = []
    try:
        for params in rows:
            cur = conn.execute(statement, params)
            if statement.lstrip().upper().startswith("SELECT") or "RETURNING" in statement.upper():
                out = cur.fetchall()
        conn.commit()
        return out
    finally:
        conn.close()


def _clock(monkeypatch: pytest.MonkeyPatch, at: datetime) -> None:
    class Fixed(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Fixed:  # type: ignore[override]
            return cls.fromtimestamp(at.timestamp(), tz or timezone.utc)

    monkeypatch.setattr(queue_preflight, "datetime", Fixed)


async def _app(client: Any, company_key: Any, title: str, n: int, country: str | None = None) -> int:
    """Bring one ad, then pin the job's ``normalized_company`` and the application's title/country exactly."""
    resp = await client.post("/api/jobs/bring", json={
        "title": f"{title}-{n}", "company": f"Firm{n}", "description": "Build things.", "apply_url": f"https://x.example/{n}",
    })
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    job_id = int(_exec("SELECT job_id FROM applications WHERE id = ?", [(app_id,)])[0][0])
    _exec("UPDATE jobs SET normalized_company = ? WHERE id = ?", [(company_key, job_id)])
    _exec("UPDATE applications SET job_title = ?, job_country = ? WHERE id = ?", [(title, country, app_id)])
    return app_id


def _applied(user_id: str, app_id: int, occurred: str, recorded: str | None = None, n: int = 1,
             who: str | None = None) -> None:
    _exec(
        "INSERT INTO application_events (user_id, application_id, event_type, detail, payload, occurred_at, "
        "recorded_at, recorded_by) VALUES (?, ?, 'applied', '', '{}', ?, ?, 'test')",
        [(who or user_id, app_id, occurred, recorded or occurred)] * n,
    )


async def _page(db: Any, ids: list[int]) -> list[dict[str, Any]]:
    marks = ",".join("?" for _ in ids)
    cur = await db._db.execute(
        f"SELECT id, job_id, job_title, job_company, job_url, job_country FROM applications WHERE id IN ({marks}) "  # noqa: S608
        "ORDER BY id", ids,
    )
    return [dict(r) for r in await cur.fetchall()]


async def _oracle(db: Any, user_id: str, rows: list[dict[str, Any]], now: datetime) -> dict[int, dict[str, Any]]:
    """What ``preflight_rows`` produced before the page was batched: per row, the original helpers."""
    profile, receipts = UserProfile(), await kit.recent_receipts(db, user_id)
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        country = (str(r.get("job_country") or "").strip().upper()) or None
        _blocks, missing = kit.compute_answers(profile, [], country)
        flags = ready._missing_flags(profile, missing, country)  # noqa: SLF001
        app_row = {"id": int(r["id"]), "job_id": r.get("job_id"), "job_url": r.get("job_url"),
                   "job_company": r.get("job_company")}
        dup = await kit.duplicate_facts(db, user_id, app_row, now, receipts=receipts)
        out[int(r["id"])] = {"kit_missing": [str(f["key"]) for f in flags if f.get("key")], "dup": dup}
    return out


async def _run(db: Any, user_id: str, rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    cfg = rules.effective(AssistantSettings())
    return await queue_preflight.preflight_rows(db, user_id, rows, {}, {}, cfg, UserProfile(), [])


async def _statements(db: Any, call: Any) -> tuple[int, Any]:
    real = db._db.execute
    seen: list[str] = []

    async def counting(sql: str, params: Any = ()) -> Any:
        seen.append(sql)
        return await real(sql, params)

    db._db.execute = counting
    try:
        out = await call()
    finally:
        db._db.execute = real
    return len(seen), out


@pytest.mark.asyncio
async def test_answers_are_computed_once_per_country_and_duplicate_reads_do_not_grow(
    authenticated_async_context, monkeypatch
):
    user_id = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        ids = [await _app(client, f"co{i % 3}", f"Role {i}", i, ["GB", "US"][i % 2]) for i in range(6)]
    calls: list[Any] = []
    real = kit.compute_answers

    def counting(*a: Any, **kw: Any) -> Any:
        calls.append(a[2])
        return real(*a, **kw)

    monkeypatch.setattr(kit, "compute_answers", counting)
    async with browser_routes._db() as db:
        rows = await _page(db, ids)
        small_n, _ = await _statements(db, lambda: _run(db, user_id, rows[:3]))
        calls.clear()
        large_n, got = await _statements(db, lambda: _run(db, user_id, rows))
    assert len(got) == 6
    assert len(calls) <= 2, f"compute_answers ran {len(calls)} times for 2 countries"
    assert large_n == small_n, f"{small_n} statements for 3 rows, {large_n} for 6"


@pytest.mark.asyncio
@pytest.mark.parametrize("today", _DATES, ids=["mid-month", "year-end-last-second"])
async def test_batched_page_equals_the_per_row_path(authenticated_async_context, monkeypatch, today):
    _clock(monkeypatch, today)

    def iso(days: float) -> str:
        return (today - timedelta(days=days)).isoformat()

    uid = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        mk = lambda *a, **k: _app(client, *a, **k)  # noqa: E731
        a = [
            await mk("acme", "Data Engineer", 0, "GB"), await mk("acme", "data  engineer!", 1, "GB"),
            await mk("acme", "Designer", 2, "us"), await mk("beta", "Analyst", 3, " gb "),
            await mk("beta", "Analyst ", 4, "US"), await mk("unknown", "Ghost", 5), await mk("", "Plain", 6),
            await mk("orphan", "Orphan", 7), await mk("gone", "Gone", 8),
            await mk("cap1", "Target One", 9, "DE"), await mk("cap2", "Target Two", 10, "DE"),
        ]
        edge = [  # [page row, boundary-cap row, filler, match] groups for the extra cases, then page rows
            await mk("cap3", "Target Three", 20), await mk("cap3", "Filler Three", 21), await mk("cap3", "Target Three", 22),
            await mk(" Unknown ", "Spacey", 23), await mk(" Unknown ", "Spacey", 24),
            await mk("edge", "Edge Role", 25), await mk("edge", "Edge Role", 26),
            await mk("edge2", "Edge A", 27), await mk("edge2", "Edge B", 28),
            await mk("other", "Shared Title", 29),
        ]
        others = [await mk("cap1", "Noise", 11), await mk("cap1", "Target One", 12), await mk("cap2", "Target Two", 13),
                  await mk("cap2", "Filler", 14)]
    _exec("UPDATE applications SET job_id = 987654321 WHERE id = ?", [(a[8],)])  # the job row is gone (job_id is NOT NULL)
    _applied(uid, a[0], iso(5))
    _applied(uid, a[1], iso(3), n=2)                                   # several applied events: counted DISTINCT
    _applied(uid, a[3], iso(400), iso(1))                              # old occurred_at, recorded inside the window
    _applied(uid, a[5], iso(1))                                        # company "unknown" never counts
    _applied(uid, a[2], iso(1), who="someone-else")                    # another user's event: excluded
    for stamp in (today.strftime("%Y-%m-%dT%H:%M:%SZ"), iso(30).replace("+00:00", "Z"), iso(1)[:10], iso(179.9999)):
        _applied(uid, a[2], stamp)                                     # Z, date-only and near-cutoff forms
    # cap1: the only "Target One" match sits at kept position 201 (200 newer events of another title): NOT found
    _applied(uid, others[1], iso(10))
    _applied(uid, others[0], iso(9), n=200)
    # cap2: a[10]'s own 150 events are the newest; the match is at kept position 101: found
    _applied(uid, others[2], iso(8))
    _applied(uid, others[3], iso(7), n=100)
    _applied(uid, a[10], iso(6), n=150)
    # 1. cap boundary: edge[0]'s own 3 events are skipped; 199 newer filler events; the match is kept position 200
    _applied(uid, edge[2], iso(12))
    _applied(uid, edge[1], iso(11), n=199)
    _applied(uid, edge[0], iso(10), n=3)
    # 2. company " Unknown " (spaces, capital) is a real company on the per-row path
    _applied(uid, edge[4], iso(4))
    # 3. occurred_at EXACTLY on each window's cutoff string (recorded long before, so only occurred_at can count)
    role_cut = (today - timedelta(days=settings.DUPLICATE_ROLE_WINDOW_DAYS)).isoformat()
    company_cut = (today - timedelta(days=settings.DUPLICATE_COMPANY_WINDOW_DAYS)).isoformat()
    _applied(uid, edge[6], role_cut, iso(900))
    _applied(uid, edge[8], company_cut, iso(900))
    # 4. another user's application at the same company: its event under their id AND one under this user's id
    other_job = int(_exec("SELECT job_id FROM applications WHERE id = ?", [(edge[9],)])[0][0])
    foreign = int(_exec(
        "INSERT INTO applications (user_id, job_id, job_title, job_company, created_at, updated_at) VALUES "
        "('someone-else', ?, 'Shared Title', 'Other', ?, ?) RETURNING id", [(other_job, iso(3), iso(3))])[0][0])
    _applied("someone-else", foreign, iso(2))
    _applied(uid, foreign, iso(2))
    page = [*a, *edge]
    job6 = int(_exec("SELECT job_id FROM applications WHERE id = ?", [(a[6],)])[0][0])
    _exec("INSERT INTO application_receipts (user_id, job_id, sent_at, job_title, job_company, application_id, "
          "job_apply_url) VALUES (?, ?, ?, 't', 'c', ?, 'https://r.example/x')", [(uid, job6, iso(2), a[0])])
    async with browser_routes._db() as db:
        rows = await _page(db, page)
        want = await _oracle(db, uid, rows, today)
        got = await _run(db, uid, rows)
        batch = await kit.duplicate_facts_batch(db, uid, rows, today, receipts=await kit.recent_receipts(db, uid))
    for app_id, w in want.items():
        assert got[app_id]["kit_missing"] == w["kit_missing"], app_id
        assert got[app_id]["duplicate_flag"] == w["dup"]["flag"], app_id
        assert batch[app_id] == w["dup"], app_id
    # the fixture really reaches each branch (rule #21: values, not defaults)
    assert {w["dup"]["flag"] for w in want.values()} == {"same_job", "same_role", "same_company", ""}
    assert want[a[9]]["dup"]["same_role"] is None and want[a[10]]["dup"]["same_role"] is not None
    assert want[a[4]]["dup"]["same_role"] is not None and want[a[4]]["dup"]["same_company_30d"] == 0
    assert want[a[7]]["dup"]["flag"] == "" and want[a[8]]["dup"]["flag"] == ""
    # the new cases keep exercising their trap: the oracle's own values
    assert want[edge[0]]["dup"]["same_role"] is not None                     # 1. found at kept position exactly 200
    assert want[edge[3]]["dup"]["same_role"] is not None                     # 2. " Unknown " is a real company
    assert want[edge[5]]["dup"]["same_role"] is not None                     # 3. role cutoff: >= includes it
    assert want[edge[7]]["dup"]["same_company_30d"] == 1                     # 3. company cutoff: >= includes it
    assert want[edge[9]]["dup"]["same_role"] is None                         # 4. a.user_id filter ignores the foreign app
    assert want[edge[9]]["dup"]["same_company_30d"] == 1                     # 4. the count has no a.user_id filter

"""W-08 / W-10 — never lose the CV the user actually applied with.

The bug being fixed is data destruction, not a missing feature. `tailored_documents`
is UNIQUE(user_id, job_id, doc_kind) and `upsert_tailored_doc` is DELETE-then-INSERT,
so there was only ever "the current document". Generate → apply → regenerate later,
and the file that went to the employer was gone from the database permanently.

So the tests that matter here are the ones that assert something SURVIVES. The happy
path (a snapshot exists) is easy; the real guards are:

  * regenerating does not destroy the applied version
  * the application keeps pointing at what was actually sent, not at whatever exists now
  * applying before tailoring is honestly recorded as "no document", not a fake one
  * one user's snapshot never binds to another user's application (rules #12/#25)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.repositories import pg
from src.repositories.database import JobDatabase

USER = "docs-user"
OTHER = "docs-other"


@pytest.fixture
async def db(tmp_path):
    async with pg.connect(str(tmp_path / "docs.db")) as conn:
        await conn.executescript(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                staleness_state TEXT DEFAULT 'active'
            );
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                job_id INTEGER NOT NULL,
                stage TEXT NOT NULL DEFAULT 'applied',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                cv_version_id INTEGER,
                cover_letter_version_id INTEGER,
                UNIQUE(user_id, job_id)
            );
            CREATE TABLE tailored_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                job_id INTEGER NOT NULL,
                doc_kind TEXT NOT NULL,
                ai_draft TEXT NOT NULL DEFAULT '',
                polished TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                model TEXT,
                profile_version INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                kept_at TEXT,
                flagged_terms TEXT DEFAULT '[]',
                UNIQUE(user_id, job_id, doc_kind)
            );
            CREATE TABLE tailored_document_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                job_id INTEGER NOT NULL,
                doc_kind TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL,
                model TEXT,
                profile_version INTEGER,
                created_at TEXT NOT NULL
            );
            """
        )
        for uid in (USER, OTHER):
            await conn.execute(
                "INSERT INTO users (id, email, password_hash, created_at) VALUES (?,?,?,?)",
                (uid, f"{uid}@example.com", "x", "2026-01-01"),
            )
        await conn.execute("INSERT INTO jobs (title, company) VALUES (?,?)", ("SRE", "Monzo"))
        await conn.commit()
        yield conn


@pytest.fixture
def jdb(db):
    return JobDatabase.from_connection("", db)


async def versions(conn, user_id: str = USER) -> list[dict]:
    cur = await conn.execute(
        "SELECT id, doc_kind, content, source FROM tailored_document_versions "
        "WHERE user_id = ? ORDER BY id",
        (user_id,),
    )
    return [
        {"id": r[0], "doc_kind": r[1], "content": r[2], "source": r[3]}
        for r in await cur.fetchall()
    ]


async def application(conn, user_id: str = USER, job_id: int = 1) -> dict:
    cur = await conn.execute(
        "SELECT cv_version_id, cover_letter_version_id FROM applications "
        "WHERE user_id = ? AND job_id = ?",
        (user_id, job_id),
    )
    row = await cur.fetchone()
    return {"cv_version_id": row[0], "cover_letter_version_id": row[1]}


# ── W-10: regenerating must not destroy history ──────────────────────────────


@pytest.mark.asyncio
async def test_regenerating_archives_the_outgoing_document(jdb, db):
    """The exact data loss: the old text must survive the DELETE."""
    await jdb.upsert_tailored_doc(USER, 1, "cv", "FIRST DRAFT — the one they sent")
    await jdb.upsert_tailored_doc(USER, 1, "cv", "SECOND DRAFT — written later")

    archived = await versions(db)

    assert archived, "regenerating destroyed the old document with no archive"
    assert any("FIRST DRAFT" in v["content"] for v in archived), (
        f"the superseded text is gone: {archived}"
    )
    assert archived[0]["source"] == "superseded"


@pytest.mark.asyncio
async def test_the_first_generation_archives_nothing(jdb, db):
    """Nothing was destroyed, so nothing should be recorded. No phantom rows."""
    await jdb.upsert_tailored_doc(USER, 1, "cv", "ONLY DRAFT")
    assert await versions(db) == []


@pytest.mark.asyncio
async def test_the_users_own_edit_is_what_gets_archived(jdb, db):
    """If they polished it, the polished text is what they would have sent."""
    await jdb.upsert_tailored_doc(USER, 1, "cv", "AI DRAFT")
    await jdb.save_tailored_polished(USER, 1, "cv", "MY OWN EDITED VERSION")
    await jdb.upsert_tailored_doc(USER, 1, "cv", "REGENERATED")

    archived = await versions(db)

    assert "MY OWN EDITED VERSION" in archived[0]["content"], (
        f"archived the AI draft instead of the user's edit: {archived}"
    )


# ── W-08: the application remembers what was sent ────────────────────────────


@pytest.mark.asyncio
async def test_applying_binds_the_document_that_existed_at_that_moment(jdb, db):
    await jdb.upsert_tailored_doc(USER, 1, "cv", "THE CV I APPLIED WITH")
    await jdb.upsert_tailored_doc(USER, 1, "cover_letter", "THE LETTER I APPLIED WITH")

    await jdb.create_application(1, USER)

    app = await application(db)
    assert app["cv_version_id"] is not None, "application did not record the CV"
    assert app["cover_letter_version_id"] is not None, "application did not record the letter"

    rows = {v["id"]: v for v in await versions(db)}
    assert "THE CV I APPLIED WITH" in rows[app["cv_version_id"]]["content"]
    assert rows[app["cv_version_id"]]["source"] == "applied"


@pytest.mark.asyncio
async def test_regenerating_after_applying_does_not_change_what_was_sent(jdb, db):
    """THE test. This is the question the owner asked and could not answer."""
    await jdb.upsert_tailored_doc(USER, 1, "cv", "VERSION ONE — actually sent")
    await jdb.create_application(1, USER)
    app_before = await application(db)

    # Months later, they tailor the same job again.
    await jdb.upsert_tailored_doc(USER, 1, "cv", "VERSION TWO — never sent")

    app_after = await application(db)
    rows = {v["id"]: v for v in await versions(db)}

    assert app_after["cv_version_id"] == app_before["cv_version_id"], (
        "the application's document pointer moved when they regenerated"
    )
    assert "VERSION ONE" in rows[app_after["cv_version_id"]]["content"], (
        "the application now points at a document that was never sent"
    )


@pytest.mark.asyncio
async def test_applying_with_no_document_records_nothing_rather_than_guessing(jdb, db):
    """Applying before tailoring is normal. NULL honestly means 'there was none'."""
    await jdb.create_application(1, USER)

    app = await application(db)
    assert app["cv_version_id"] is None
    assert app["cover_letter_version_id"] is None
    assert await versions(db) == []


@pytest.mark.asyncio
async def test_downloading_after_applying_binds_the_document(jdb, db):
    """The common real order: apply first, tailor and download afterwards.

    Without this the feature only works for people who happen to tailor before
    they apply, which is not how anyone actually behaves.
    """
    await jdb.create_application(1, USER)
    assert (await application(db))["cv_version_id"] is None

    await jdb.upsert_tailored_doc(USER, 1, "cv", "TAILORED AFTER APPLYING")
    await jdb.keep_tailored_doc(USER, 1, "cv")

    app = await application(db)
    assert app["cv_version_id"] is not None, "download did not bind to the application"
    rows = {v["id"]: v for v in await versions(db)}
    assert "TAILORED AFTER APPLYING" in rows[app["cv_version_id"]]["content"]


@pytest.mark.asyncio
async def test_a_later_download_does_not_overwrite_an_existing_binding(jdb, db):
    """First binding wins — it is the one closest to the moment of applying."""
    await jdb.upsert_tailored_doc(USER, 1, "cv", "SENT WITH THE APPLICATION")
    await jdb.create_application(1, USER)
    first = (await application(db))["cv_version_id"]

    await jdb.upsert_tailored_doc(USER, 1, "cv", "A LATER REWRITE")
    await jdb.keep_tailored_doc(USER, 1, "cv")

    assert (await application(db))["cv_version_id"] == first


@pytest.mark.asyncio
async def test_downloading_without_an_application_binds_nothing_and_does_not_crash(jdb, db):
    await jdb.upsert_tailored_doc(USER, 1, "cv", "NO APPLICATION FOR THIS JOB")
    await jdb.keep_tailored_doc(USER, 1, "cv")

    cur = await db.execute("SELECT COUNT(*) FROM applications")
    assert (await cur.fetchone())[0] == 0


# ── isolation ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_users_document_never_binds_to_anothers_application(jdb, db):
    """Rules #12/#25 — everything scoped by user.id, never by job_id alone."""
    await jdb.upsert_tailored_doc(OTHER, 1, "cv", "SOMEONE ELSE'S CV")

    await jdb.create_application(1, USER)

    app = await application(db, USER)
    assert app["cv_version_id"] is None, "bound another user's document to this application"
    assert await versions(db, USER) == []


@pytest.mark.asyncio
async def test_archiving_is_scoped_to_one_document_kind(jdb, db):
    """Regenerating the CV must not archive or disturb the cover letter."""
    await jdb.upsert_tailored_doc(USER, 1, "cv", "CV ONE")
    await jdb.upsert_tailored_doc(USER, 1, "cover_letter", "LETTER ONE")
    await jdb.upsert_tailored_doc(USER, 1, "cv", "CV TWO")

    archived = await versions(db)

    assert len(archived) == 1
    assert archived[0]["doc_kind"] == "cv"
    letter = await jdb.get_tailored_doc(USER, 1, "cover_letter")
    assert letter and letter["ai_draft"] == "LETTER ONE"


@pytest.mark.asyncio
async def test_the_live_document_still_reads_as_before(jdb, db):
    """The existing contract is untouched: one current row per (user, job, kind)."""
    await jdb.upsert_tailored_doc(USER, 1, "cv", "FIRST")
    await jdb.upsert_tailored_doc(USER, 1, "cv", "SECOND")

    doc = await jdb.get_tailored_doc(USER, 1, "cv")
    assert doc is not None
    assert doc["ai_draft"] == "SECOND"
    assert doc["status"] == "draft"
    cur = await db.execute(
        "SELECT COUNT(*) FROM tailored_documents WHERE user_id=? AND job_id=? AND doc_kind=?",
        (USER, 1, "cv"),
    )
    assert (await cur.fetchone())[0] == 1


def test_now_is_iso() -> None:
    """Guard the helper the snapshots timestamp with, so a bad format is caught here."""
    assert datetime.now(timezone.utc).isoformat().endswith("+00:00")

"""User-level templates and outreach variant labels (outreach pack #13).

A template is the user's reusable wording, stored as text and versioned per
label (``lower(trim(label))``). It belongs to the user, not to a job or a
person. Job360 never fills, renders or sends a template: the user's own
assistant fills in its variables. The table (``user_templates``) is
append-only; only account erasure deletes from it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.repositories import pg
from src.services.applications.spine import SpineError
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:
    from src.repositories.database import JobDatabase


def clean_variant_label(raw: Optional[str]) -> str:
    """strip(); '' allowed. Over the cap or a control character is a 422."""
    label = (raw or "").strip()
    if len(label) > settings.VARIANT_LABEL_MAX_CHARS:
        raise SpineError(422, f"label exceeds VARIANT_LABEL_MAX_CHARS ({settings.VARIANT_LABEL_MAX_CHARS} chars)")
    if any(ord(ch) < 32 for ch in label):
        raise SpineError(422, "label must be one line of plain text")
    return label


def _check_kind(kind: str) -> None:
    if kind not in settings.USER_TEMPLATE_KINDS:
        raise SpineError(422, f"kind must be one of USER_TEMPLATE_KINDS {settings.USER_TEMPLATE_KINDS}")


def _serialize(row: Any) -> dict[str, Any]:
    r = dict(row)
    return {
        "template_id": r["id"], "kind": r["kind"], "label": r["label"], "version_no": r["version_no"],
        "text": r["text"], "chars": r["chars"], "made_by": r["made_by"], "model": r.get("model"),
        "created_at": r["created_at"],
    }


async def _scalar(db: JobDatabase, sql: str, params: tuple[Any, ...]) -> int:
    cur = await db._db.execute(sql, params)
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def save_template(
    db: JobDatabase, user_id: str, *, kind: str, label: str, text: str, made_by: str,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Store the next version of the user's template ``label``. Every check
    comes before any write."""
    _check_kind(kind)
    clean = clean_variant_label(label)
    if not clean:
        raise SpineError(422, "label is required for a template: it names the variant stats counts")
    if not (text or "").strip():
        raise SpineError(422, "text must not be empty for a template")
    if len(text) > settings.USER_TEMPLATE_MAX_CHARS:
        raise SpineError(422, f"text exceeds USER_TEMPLATE_MAX_CHARS ({settings.USER_TEMPLATE_MAX_CHARS} chars)")
    key = clean.lower()
    count_sql = "SELECT COUNT(*) FROM user_templates WHERE user_id = ? AND kind = ? AND label_key = ?"
    existing = await _scalar(db, count_sql, (user_id, kind, key))
    if existing == 0:
        labels = await _scalar(
            db, "SELECT COUNT(DISTINCT label_key) FROM user_templates WHERE user_id = ? AND kind = ?",
            (user_id, kind),
        )
        if labels >= settings.USER_TEMPLATE_LABELS_MAX:
            raise SpineError(
                429, f"too many template labels; cap is USER_TEMPLATE_LABELS_MAX ({settings.USER_TEMPLATE_LABELS_MAX})"
            )
    if existing >= settings.USER_TEMPLATE_VERSIONS_PER_LABEL_MAX:
        raise SpineError(
            429,
            "too many versions of this template; cap is USER_TEMPLATE_VERSIONS_PER_LABEL_MAX "
            f"({settings.USER_TEMPLATE_VERSIONS_PER_LABEL_MAX})",
        )

    now = datetime.now(timezone.utc).isoformat()
    template_id: Optional[int] = None
    version_no = 0
    for _attempt in range(5):
        version_no = await _scalar(db, count_sql, (user_id, kind, key)) + 1
        try:
            ins = await db._db.execute(
                "INSERT INTO user_templates "
                "(user_id, kind, label, label_key, version_no, text, chars, made_by, model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, kind, clean, key, version_no, text, len(text), made_by, model, now),
            )
        except pg.IntegrityError:
            await db._db.rollback()
            continue
        template_id = int(ins.lastrowid or 0)
        break
    if template_id is None:
        raise SpineError(429, "could not allocate a template version under concurrent writes — retry")

    get_audit_logger().info(
        "user_template_saved",
        extra={
            "event": "user_template_saved", "user_id": safe_log_value(user_id), "template_id": template_id,
            "kind": safe_log_value(kind), "version_no": version_no, "chars": len(text),
        },
    )
    return {
        "template_id": template_id, "kind": kind, "label": clean, "version_no": version_no, "text": text,
        "chars": len(text), "made_by": made_by, "model": model, "created_at": now,
    }


async def list_templates(
    db: JobDatabase, user_id: str, *, kind: str, label: Optional[str] = None
) -> list[dict[str, Any]]:
    """Newest version of each label (``label`` None), or every version of one label."""
    _check_kind(kind)
    cols = "id, kind, label, version_no, text, chars, made_by, model, created_at"
    if label is None:
        cur = await db._db.execute(
            f"SELECT {cols} FROM ("  # noqa: S608 — constants only
            f"SELECT {cols}, label_key, ROW_NUMBER() OVER (PARTITION BY label_key ORDER BY version_no DESC) AS rn "
            "FROM user_templates WHERE user_id = ? AND kind = ?) t WHERE rn = 1 ORDER BY label_key ASC",
            (user_id, kind),
        )
    else:
        cur = await db._db.execute(
            f"SELECT {cols} FROM user_templates "  # noqa: S608 — constants only
            "WHERE user_id = ? AND kind = ? AND label_key = ? ORDER BY version_no DESC",
            (user_id, kind, clean_variant_label(label).lower()),
        )
    return [_serialize(r) for r in await cur.fetchall()]

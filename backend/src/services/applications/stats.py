"""Stats — counts over the event log, nothing else (spec
docs/plans/2026-09-05-contacts-stats/spec.md R5-R7, S1, S7, S10).

Everything here is a hand count: ``brought``/``applied``/``replied``/
``interview``/``offer``/``rejected`` as ``COUNT(DISTINCT application_id)``
having >=1 event of that type, plus two groupings (by the CV variant label
named on the latest receipt, and by role). No inference, no keyword lists —
grouping keys are ``lower(trim(...))`` of data the caller already wrote.

**One query, and it is bounded.** This module issues ONE query, not three.
It returns one row per application in scope (whether each status event type
fired, plus the CV label/profile version off that application's LATEST
receipt via a correlated subquery), and computes the overall totals AND both
groupings from that single result set in Python — no per-application loop
that hits the database.

The fetch itself is capped at ``settings.STATS_MAX_APPLICATIONS`` (S6): the
query reads the NEWEST N applications for the user, and the response says
``applications_truncated: true`` when there were more. Group count is capped
separately at ``settings.STATS_MAX_GROUPS``.

**Owner decision 2026-10-04 — four more splits**, every one over facts the
caller stored (never inferred), with the same counts/rates/order/cap as the
two above (``_group_by``):

* ``by_country`` — a REMOTE job (``job_remote`` true) is its own group, key
  ``"remote"``, whatever its country; any other job groups by its ISO alpha-2
  country (``job_country`` only — never the visa slot's ``visa_country``,
  which is a different fact). Unknown → key ``None``, label "Not set".
* ``by_job_source`` — ``job_found_on`` (closed set ``JOB_FOUND_ON``).
* ``by_channel`` — the LATEST receipt's ``channel``. New receipts are a
  closed set; older free-text receipts are history and are MAPPED, never
  rewritten (``spine.receipt_channel_key``): an exact set spelling → that
  member, any other text → ``"other"``, empty/no receipt → ``None``.
* ``by_contact_found_via`` — a different unit: CONTACTS, not applications.
  Per current ``found_via`` (base row overlaid by its newest edit): how many
  contacts, how many had an ``outreach_sent`` mark, how many had a reply
  mark, and ``reply_rate`` = contacts with BOTH a sent and a reply mark /
  contacts with a sent mark (``None`` when nothing was sent — rule #29).
  One aggregate query, bounded by the closed set.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications.spine import SpineError, receipt_channel_key
from src.services.auth import rate_limit

if TYPE_CHECKING:  # pragma: no cover — type-only, same reasoning as spine.py
    from src.repositories.database import JobDatabase

def _parse_since(raw: Optional[str]) -> Optional[str]:
    """R7 — optional ISO date/datetime, NORMALISED to a UTC isoformat string.

    ``applications.created_at`` is stored as ``datetime.now(timezone.utc).
    isoformat()`` — always ``+00:00``-suffixed text — and the comparison is a
    TEXT comparison in SQL. So the caller's spelling has to be converted to
    that same spelling before it can be compared: ``...T10:00:00Z`` sorts
    AFTER ``...T10:00:00+00:00`` lexically (``Z`` > ``+``), which silently
    excluded rows the caller meant to include, and an offset spelling
    (``12:00:00+02:00``) compared as though it were noon UTC.

    Returns ``.astimezone(timezone.utc).isoformat()``; a naive datetime is
    read as UTC (the only interpretation consistent with what is stored).
    Anything unparseable is a 422.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise SpineError(422, "since must be an ISO-8601 date or datetime") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _norm(raw: Optional[str]) -> Optional[str]:
    """``lower(trim(...))`` — the ONLY normalisation (memory: no hand-typed
    vocabularies). Empty/whitespace-only or missing collapses to the null
    group."""
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped.lower() if stripped else None


def _rates(applied: int, replied: int, interview: int, offer: int) -> dict[str, Optional[float]]:
    """R5 — null when ``applied = 0`` (rule #29: an empty shelf stays silent,
    never a computed 0%)."""
    if applied == 0:
        return {"reply_rate": None, "interview_rate": None, "offer_rate": None}
    return {
        "reply_rate": round(replied / applied, 3),
        "interview_rate": round(interview / applied, 3),
        "offer_rate": round(offer / applied, 3),
    }


def _gate_on_applied(rows: list[dict[str, Any]]) -> int:
    """Owner decision 2026-10-14 — a rate is "of the applications you applied
    to", so it can never exceed 1: ``replied`` / ``interview`` / ``offer`` are
    counted only on an application that ALSO has an ``applied`` event. The
    ones that reached a later stage with no ``applied`` recorded are not
    dropped silently — they are counted (and returned) as
    ``replies_without_applied`` so the agent can ask the user and fill the gap.
    Mutates ``rows`` in place."""
    without = 0
    for r in rows:
        if r["applied"]:
            continue
        if r["replied"] or r["interview"] or r["offer"]:
            without += 1
        r["replied"] = r["interview"] = r["offer"] = 0
    return without


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "brought": len(rows),
        "applied": sum(r["applied"] for r in rows),
        "replied": sum(r["replied"] for r in rows),
        "interview": sum(r["interview"] for r in rows),
        "offer": sum(r["offer"] for r in rows),
        "rejected": sum(r["rejected"] for r in rows),
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _counts(rows)
    return {
        **counts,
        **_rates(counts["applied"], counts["replied"], counts["interview"], counts["offer"]),
        # career-ops stats.mjs:182 - a rate over fewer than N applications is a hint, not a finding.
        "small_sample": counts["applied"] < settings.STATS_SMALL_SAMPLE_N,
    }


def _group(
    rows: list[dict[str, Any]],
    *,
    key_field: str,
    label_key: str,
    with_profile_versions: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """Group ``rows`` by ``lower(trim(row[key_field]))``; the display value
    (``label_key`` in the output) is the RAW ``key_field`` value on the
    group's earliest application (lowest ``application_id`` — spec R6),
    never the normalised key. Each group also carries the normalised ``key``
    itself, which is what the tie-break below orders on.

    Ordered ``applied DESC, brought DESC, key ASC``, capped at
    ``STATS_MAX_GROUPS``. THE THIRD TERM IS NOT DECORATION: with only the two
    count terms, tied groups came back in Python dict-insertion order — which
    is application order, so which groups survived the cap depended on the
    order the user happened to bring their jobs in, and two identical calls
    could disagree after any new row. The null group sorts last among ties;
    it is the "we could not name this" bucket, never a labelled variant's
    equal.
    """
    buckets: dict[Optional[str], list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(_norm(row.get(key_field)), []).append(row)

    groups: list[dict[str, Any]] = []
    for key, members in buckets.items():
        earliest = min(members, key=lambda m: m["application_id"])
        entry: dict[str, Any] = {
            "key": key,
            label_key: None if key is None else earliest.get(key_field),
        }
        entry.update(_summarize(members))
        if with_profile_versions:
            versions = sorted({m["cv_profile_version"] for m in members if m.get("cv_profile_version") is not None})
            entry["profile_versions"] = versions
        groups.append(entry)

    groups.sort(key=lambda g: (-g["applied"], -g["brought"], g["key"] is None, g["key"] or ""))
    truncated = len(groups) > settings.STATS_MAX_GROUPS
    return groups[: settings.STATS_MAX_GROUPS], truncated


NOT_SET_LABEL = "Not set"
REMOTE_KEY = "remote"


def _country_key(row: dict[str, Any]) -> Optional[str]:
    """``"remote"`` for a remote job; else the job's alpha-2 country (upper —
    stored that way by every door); else ``None``."""
    if row.get("job_remote") is True:
        return REMOTE_KEY
    # The job's own country only — never the visa slot's country, which is a
    # different fact (a remote ad can name a sponsorship country). Every
    # other reader shows `country` the same way (rule #29: no inferred value).
    code = (row.get("job_country") or "").strip().upper()
    return code or None


def _group_by(
    rows: list[dict[str, Any]], key_of: Any, *, label_of: Any = None
) -> tuple[list[dict[str, Any]], bool]:
    """Same counts, rates, order (``applied DESC, brought DESC, key ASC``,
    null last) and ``STATS_MAX_GROUPS`` cap as ``_group`` — but the key is
    already a canonical value (a closed-set member or an ISO code), so it is
    NOT lower-cased. ``label`` is the key itself unless ``label_of`` names it
    otherwise; the null group's label is always "Not set"."""
    buckets: dict[Optional[str], list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(key_of(row), []).append(row)
    groups: list[dict[str, Any]] = []
    for key, members in buckets.items():
        if key is None:
            label = NOT_SET_LABEL
        else:
            label = label_of(key) if label_of is not None else key
        groups.append({"key": key, "label": label, **_summarize(members)})
    groups.sort(key=lambda g: (-g["applied"], -g["brought"], g["key"] is None, g["key"] or ""))
    truncated = len(groups) > settings.STATS_MAX_GROUPS
    return groups[: settings.STATS_MAX_GROUPS], truncated


# An application marked by a `simulation` event that no later event corrects is a
# test: it counts nowhere (owner decision 2026-10-15). `{app}` is the SQL for its id.
_SIMULATED_SQL = (
    "EXISTS (SELECT 1 FROM application_events s WHERE s.application_id = {app} AND s.user_id = ? "
    "AND s.event_type = ? AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = s.id))"
)


async def _contact_found_via_groups(
    db: JobDatabase, user_id: str, since_val: Optional[str]
) -> list[dict[str, Any]]:
    """``by_contact_found_via`` — ONE aggregate query over the caller's own
    contacts (S1: ``user_id`` on every table), grouped by the CURRENT
    ``found_via`` (the newest ``contact_edits`` row for that field, else the
    base row; a cleared edit '' reads as unset). Bounded by the closed set,
    so there is no row cap to apply. ``since`` scopes by the contact's own
    ``created_at``."""
    where = ["ac.user_id = ?", "NOT " + _SIMULATED_SQL.format(app="ac.application_id")]
    where_params: list[Any] = [user_id, user_id, settings.SIMULATION_EVENT_TYPE]
    if since_val:
        where.append("ac.created_at >= ?")
        where_params.append(since_val)
    where_sql = " AND ".join(where)
    sql = f"""
        SELECT fv, COUNT(*) AS contacts, SUM(sent) AS sent, SUM(replied) AS replied,
               SUM(sent * replied) AS sent_and_replied
        FROM (
            SELECT
                NULLIF(COALESCE(fe.value, ac.found_via), '') AS fv,
                CASE WHEN EXISTS (
                    SELECT 1 FROM contact_outreach o
                    WHERE o.contact_id = ac.id AND o.user_id = ac.user_id AND o.entry = 'sent'
                ) THEN 1 ELSE 0 END AS sent,
                CASE WHEN EXISTS (
                    SELECT 1 FROM contact_outreach o
                    WHERE o.contact_id = ac.id AND o.user_id = ac.user_id AND o.entry = 'reply'
                ) THEN 1 ELSE 0 END AS replied
            FROM application_contacts ac
            LEFT JOIN (
                SELECT contact_id, value FROM (
                    SELECT contact_id, value,
                           ROW_NUMBER() OVER (PARTITION BY contact_id ORDER BY id DESC) AS rn
                    FROM contact_edits WHERE field = 'found_via' AND user_id = ?
                ) t WHERE rn = 1
            ) fe ON fe.contact_id = ac.id
            WHERE {where_sql}
        ) per_contact
        GROUP BY fv
    """  # noqa: S608 — where_sql is built from constants, never user input
    cur = await db._db.execute(sql, [user_id, *where_params])
    groups: list[dict[str, Any]] = []
    for r in await cur.fetchall():
        row = dict(r)
        key = row["fv"] or None
        sent = int(row["sent"] or 0)
        groups.append(
            {
                "key": key,
                "label": NOT_SET_LABEL if key is None else key,
                "contacts": int(row["contacts"] or 0),
                "outreach_sent": sent,
                "outreach_replied": int(row["replied"] or 0),
                "reply_rate": round(int(row["sent_and_replied"] or 0) / sent, 3) if sent else None,
            }
        )
    groups.sort(key=lambda g: (-g["contacts"], g["key"] is None, g["key"] or ""))
    return groups


async def _score_line_counts(user_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Owner decision 2026-10-10 - how many of the user's applications sit above /
    below THEIR OWN score line, and how many have no fit score (or there is no
    line). Counts only (product rule 4): nothing is ranked or recommended."""
    from src.services.applications.spine import user_score_line  # noqa: PLC0415
    from src.services.profile.assistant_settings import line_side  # noqa: PLC0415

    line = await user_score_line(user_id)
    counts = {"above": 0, "below": 0, "unknown": 0}
    for r in rows:
        counts[line_side(r.get("fit_score"), line)] += 1
    return {"line": line, "above": counts["above"], "below": counts["below"], "unscored": counts["unknown"]}


def _validate_view(view: Optional[str], company: Optional[str]) -> Optional[str]:
    """422s for ``view`` / ``company`` - checked BEFORE the rate limit, for the
    same reason as ``since``: a typo must not cost an hourly slot. Returns the
    stripped ``company`` (``None`` when not given)."""
    if view is not None and view not in settings.STATS_VIEWS:
        raise SpineError(422, f"view must be one of {list(settings.STATS_VIEWS)}")
    if company is None:
        return None
    if view not in ("company", "all"):
        raise SpineError(422, "company needs view='company'")
    cleaned = company.strip()
    if not cleaned:
        raise SpineError(422, "company must not be empty")
    if len(cleaned) > settings.STATS_COMPANY_MAX_CHARS:
        raise SpineError(422, f"company must be at most {settings.STATS_COMPANY_MAX_CHARS} characters")
    return cleaned


def _selected_views(view: Optional[str]) -> list[str]:
    """The view keys one call computes. ``all`` is every member of STATS_VIEWS
    except ``all`` itself - never a hard-coded list, so a view a later slice
    appends joins it automatically."""
    every = [v for v in settings.STATS_VIEWS if v != "all"]
    if view is None:
        return []
    return every if view == "all" else [view]


async def compute_stats(
    db: JobDatabase,
    user_id: str,
    since: Optional[str] = None,
    view: Optional[str] = None,
    company: Optional[str] = None,
) -> dict[str, Any]:
    """R5-R7 — overall counts + rates, ``by_cv_version``, ``by_role``, all
    scoped to the caller's own applications (S1: ``user_id`` filtered on
    every table in the join) and rate-limited per user (S7).

    ``since`` is parsed BEFORE the budget is consulted: a malformed value is
    a 422 the caller must fix, and making them pay an hourly slot for each
    attempt would lock them out for an hour over a typo.

    Every event-type name is BOUND AS A PARAMETER from ``src.core.settings``
    (S4) — this module never re-types one as a SQL literal, so the vocabulary
    has exactly one home and a rename there cannot leave a stale string here.
    """
    since_val = _parse_since(since)
    company_filter = _validate_view(view, company)

    key = f"stats:{user_id}"
    if not rate_limit.check_and_record(key, max_in_window=settings.STATS_MAX_PER_HOUR, window_seconds=3600):
        raise SpineError(429, "stats rate limit exceeded; try again in an hour")

    interview_types = settings.STATS_INTERVIEW_EVENT_TYPES
    interview_placeholders = ", ".join("?" for _ in interview_types)

    where = ["a.user_id = ?"]
    where_params: list[Any] = [user_id]
    if since_val:
        where.append("a.created_at >= ?")
        where_params.append(since_val)
    # Count the test applications, then leave them out BEFORE the row cap so they
    # can never crowd a real application out of the counted set.
    cur = await db._db.execute(
        f"SELECT COUNT(*) FROM applications a WHERE {' AND '.join(where)} AND "  # noqa: S608 - constants only
        + _SIMULATED_SQL.format(app="a.id"),
        [*where_params, user_id, settings.SIMULATION_EVENT_TYPE],
    )
    simulations_excluded = int((await cur.fetchone())[0] or 0)
    where.append("NOT " + _SIMULATED_SQL.format(app="a.id"))
    where_params.extend([user_id, settings.SIMULATION_EVENT_TYPE])
    where_sql = " AND ".join(where)

    # S6 — read one more than the cap so "there were more" is a fact, not a
    # guess: N+1 rows back means the tail was cut.
    limit = settings.STATS_MAX_APPLICATIONS

    sql = f"""
        SELECT
            a.id AS application_id,
            a.job_title AS job_title,
            a.job_country AS job_country,
            a.visa_country AS visa_country,
            a.job_remote AS job_remote,
            a.job_found_on AS job_found_on,
            a.fit_score AS fit_score,
            MAX(CASE WHEN e.event_type = ? THEN 1 ELSE 0 END) AS applied,
            MAX(CASE WHEN e.event_type = ? THEN 1 ELSE 0 END) AS replied,
            MAX(CASE WHEN e.event_type IN ({interview_placeholders}) THEN 1 ELSE 0 END) AS interview,
            MAX(CASE WHEN e.event_type = ? THEN 1 ELSE 0 END) AS offer,
            MAX(CASE WHEN e.event_type = ? THEN 1 ELSE 0 END) AS rejected,
            (
                SELECT art.label FROM application_receipts r
                LEFT JOIN application_artifacts art
                    ON art.id = r.cv_artifact_id AND art.user_id = a.user_id
                WHERE r.application_id = a.id AND r.user_id = a.user_id
                ORDER BY r.id DESC LIMIT 1
            ) AS cv_label,
            (
                SELECT art.profile_version FROM application_receipts r
                LEFT JOIN application_artifacts art
                    ON art.id = r.cv_artifact_id AND art.user_id = a.user_id
                WHERE r.application_id = a.id AND r.user_id = a.user_id
                ORDER BY r.id DESC LIMIT 1
            ) AS cv_profile_version,
            (
                SELECT r.channel FROM application_receipts r
                WHERE r.application_id = a.id AND r.user_id = a.user_id
                ORDER BY r.id DESC LIMIT 1
            ) AS receipt_channel
        FROM applications a
        LEFT JOIN application_events e ON e.application_id = a.id AND e.user_id = a.user_id
            -- A corrects_event_id note retracts its target (status.py drops it
            -- too) — a retracted apply/interview must never be counted.
            AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id)
        WHERE {where_sql}
        GROUP BY a.id, a.job_title, a.user_id, a.job_country, a.visa_country, a.job_remote, a.job_found_on,
                 a.fit_score
        ORDER BY a.id DESC
        LIMIT ?
    """  # noqa: S608 — where_sql/interview_placeholders are built from constants, never user input

    params: list[Any] = [
        settings.STATS_APPLIED_EVENT_TYPE,
        settings.STATS_REPLIED_EVENT_TYPE,
        *interview_types,
        settings.STATS_OFFER_EVENT_TYPE,
        settings.STATS_REJECTED_EVENT_TYPE,
        *where_params,
        limit + 1,
    ]
    cur = await db._db.execute(sql, params)
    rows = [dict(r) for r in await cur.fetchall()]
    applications_truncated = len(rows) > limit
    rows = rows[:limit]
    replies_without_applied = _gate_on_applied(rows)

    overall = {**_summarize(rows), "replies_without_applied": replies_without_applied,
               "simulations_excluded": simulations_excluded}

    by_cv_version, cv_truncated = _group(
        rows, key_field="cv_label", label_key="label", with_profile_versions=True
    )
    by_role, role_truncated = _group(
        rows, key_field="job_title", label_key="role", with_profile_versions=False
    )
    by_country, country_truncated = _group_by(
        rows, _country_key, label_of=lambda k: "Remote" if k == REMOTE_KEY else k
    )
    by_job_source, source_truncated = _group_by(rows, lambda r: r.get("job_found_on") or None)
    by_channel, channel_truncated = _group_by(rows, lambda r: receipt_channel_key(r.get("receipt_channel")))
    by_contact_found_via = await _contact_found_via_groups(db, user_id, since_val)
    score_line = await _score_line_counts(user_id, rows)

    # Every view key is present on every response; a view ADDS one block, the
    # rest stay null (additive - a view never replaces the response).
    view_blocks: dict[str, Any] = {v: None for v in settings.STATS_VIEWS if v != "all"}
    selected = _selected_views(view)
    if selected:
        from src.services.applications import feedback_numbers  # noqa: PLC0415

        view_blocks.update(
            await feedback_numbers.compute_views(
                db, user_id, since_val, selected, company=company_filter, view=str(view)
            )
        )

    # --- feedback slice 4: interview views (interview_counts.py) ---
    from src.services.applications import interview_counts  # noqa: PLC0415

    wanted = (
        interview_counts.INTERVIEW_VIEWS if view == "all"
        else ([view] if view in interview_counts.INTERVIEW_VIEWS else [])
    )
    for interview_view in wanted:
        view_blocks[interview_view] = await interview_counts.build_view(
            db, user_id, interview_view, since=since_val
        )
    # --- end slice 4 ---

    return {
        "since": since_val,
        "overall": overall,
        "by_cv_version": by_cv_version,
        "by_role": by_role,
        "by_country": by_country,
        "by_job_source": by_job_source,
        "by_channel": by_channel,
        "by_contact_found_via": by_contact_found_via,
        "score_line": score_line,
        "groups_truncated": (
            cv_truncated or role_truncated or country_truncated or source_truncated or channel_truncated
        ),
        "applications_truncated": applications_truncated,
        **view_blocks,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }

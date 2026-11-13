"""Does the OWNER need to press Send on a filled form? Derived from the submit gate's own asks (F6, 2026-11-13).

Display only: nothing here writes ``submit_approved``, calls ``may_submit`` or becomes a reason to submit.
``check_submit`` stays the one gate. This module answers one question for the Needs-you list: "would the gate
ask the user for this filled form?" The list says the assistant's when the answer is no, and the owner's
(``check_send``) when it is yes. Safety direction: a doubt is the OWNER's, never the assistant's, so the list
may over-ask, never under-ask. What it reads, mirroring ``assistant_settings.may_submit`` / ``_first_ask``:

* the mode (the per-job ``submit_mode_set`` override, else the user's ``submit_mode``): not auto = ask;
* the fill's own flags (guessed / sensitive / unapproved / blank answers), open asks, an open block;
* ``cv_not_seen`` (the CV neither seen nor cleared by a strong review), no CV at all;
* the score line (unknown side, or ``above_line`` / ``below_line`` set to ask);
* ``ask_always_site`` (``ask_before_signed_in_submit`` on and a brand host, a browser that is not Job360's,
  or a saved sign-in);
* the batched checks, once for the whole page: ``practice_run``, ``new_site_first_time``, a duplicate job or
  role, a CV with unsupported claims.

Not read (the gate handles them as stops or transient state, never as the owner's Send): caps, pace, hours,
an account email not yet verified, a session hold, a grant. A grant that would make a confirm-mode job auto
reads here as the owner's (the safe side).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from src.core import settings
from src.services.applications import kit, which_browser
from src.services.applications.job_checks import classify_host
from src.services.profile import assistant_settings as rules
from src.services.profile.models import VALID_SUBMIT_MODES

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

# The flag codes of a fill's answers (ready._answer_rows) that the gate asks the user for.
OWNER_FLAGS = frozenset({"guessed", "unapproved", "sensitive", "blank", "changed", "not_in_memory"})
_FLAG_REASON = {
    "guessed": "answer_guessed", "unapproved": "answer_unapproved", "sensitive": "sensitive_unconfirmed",
    "blank": "answer_blank", "changed": "answer_guessed", "not_in_memory": "answer_guessed",
}


@dataclass(frozen=True)
class Facts:
    """What one filled, not-yet-approved application says, from rows ``blockers`` already read."""

    override: Optional[str] = None  # the job's own newest submit_mode_set: confirm | auto_when_sure
    flag_codes: frozenset[str] = frozenset()
    open_ask: bool = False
    blocked: bool = False
    cv_artifact_id: Optional[int] = None
    cv_cleared: bool = False  # seen by the user, or cleared by a strong review
    fit_score: Optional[int] = None
    host: Optional[str] = None
    browsers: frozenset[str] = frozenset()
    dup_cleared: bool = False
    job_id: Optional[int] = None
    job_url: str = ""
    job_title: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def cheap_reasons(cfg: rules.EffectiveSettings, f: Facts) -> list[str]:
    """The owner reasons that need no query. Empty = nothing so far (the batched checks may still say owner)."""
    out: list[str] = []
    mode = f.override if f.override in VALID_SUBMIT_MODES else cfg.submit_mode
    if mode != rules.AUTO_SUBMIT_MODE:
        out.append("submit_mode_confirm")
    out += sorted({_FLAG_REASON[c] for c in f.flag_codes if c in _FLAG_REASON})
    if f.open_ask:
        out.append("question_open")
    if f.blocked:
        out.append("blocked")
    if f.cv_artifact_id is None:
        out.append("no_cv")
    elif not f.cv_cleared:
        out.append("cv_not_seen")
    side = rules.line_side(f.fit_score, cfg.apply_min_score)
    if side == "unknown":
        out.append("score_line_unknown")
    elif (cfg.above_line if side == "above" else cfg.below_line) == "ask":
        out.append(f"line_ask_{side}")
    if f.host is None:
        out.append("unknown_site")
    elif cfg.ask_before_signed_in_submit and (
        rules.host_kind(f.host) == "brand" or f.browsers != frozenset({which_browser.JOB360})
    ):
        out.append("ask_always_site")
    return out


async def reasons_for(
    db: JobDatabase, user_id: str, cfg: rules.EffectiveSettings, cands: dict[int, Facts],
) -> dict[int, list[str]]:
    """``{application_id: owner reasons}`` (empty list = the gate would not ask the owner). A fixed number of
    queries whatever ``len(cands)``: the batched checks run once, and only when a candidate got past the cheap ones."""
    out = {app_id: cheap_reasons(cfg, f) for app_id, f in cands.items()}
    rest = [i for i, r in out.items() if not r]
    if not rest:
        return out
    _since, applied_since_auto = await asyncio.to_thread(rules.practice_info, user_id)
    if applied_since_auto == 0:
        for i in rest:
            out[i].append("practice_run")
        return out
    for i in await _new_sites(db, user_id, {i: cands[i] for i in rest}):
        out[i].append("new_site_first_time")
    for i, why in (await _duplicates(db, user_id, {i: cands[i] for i in rest})).items():
        out[i].append(why)
    for i in await _unsupported(db, user_id, {i: cands[i] for i in rest}):
        out[i].append("cv_unsupported_claims")
    if cfg.ask_before_signed_in_submit:
        cur = await db._db.execute("SELECT 1 FROM browser_sign_ins WHERE user_id = ? LIMIT 1", (user_id,))
        if await cur.fetchone() is not None:  # a saved sign-in could cover the host: the owner looks
            for i in rest:
                out[i].append("ask_always_site")
    return out


async def _new_sites(db: JobDatabase, user_id: str, cands: dict[int, Facts]) -> list[int]:
    """Candidates whose host no OTHER receipted, non-simulated application filled on (``kit._new_site``'s rule)."""
    hosts = {i: f.host for i, f in cands.items() if f.host and classify_host(f.host) != "official_ats"}
    if not hosts:
        return []
    cur = await db._db.execute(
        "SELECT f.application_id, f.payload FROM application_events f WHERE f.user_id = ? "
        "AND f.event_type = 'form_filled' AND EXISTS (SELECT 1 FROM application_receipts r WHERE "
        "r.application_id = f.application_id AND r.user_id = f.user_id) "
        "AND NOT EXISTS (SELECT 1 FROM application_events s WHERE s.application_id = f.application_id "
        "AND s.user_id = f.user_id AND s.event_type = ? AND s.id NOT IN "
        "(SELECT corrects_event_id FROM application_events WHERE user_id = ? AND corrects_event_id IS NOT NULL)) "
        "ORDER BY f.id DESC LIMIT 2000",
        (user_id, settings.SIMULATION_EVENT_TYPE, user_id),
    )
    seen: list[tuple[int, Any]] = [(int(r["application_id"]), kit._json(dict(r).get("payload")).get("host"))
                                   for r in await cur.fetchall()]
    return [i for i, host in hosts.items() if not any(a != i and h == host for a, h in seen)]


async def _duplicates(db: JobDatabase, user_id: str, cands: dict[int, Facts]) -> dict[int, str]:
    """``{id: duplicate_job | duplicate_role}`` (``kit.duplicate_facts``' rules, without its date window: a
    doubt is the owner's). A candidate whose duplicate the user cleared is left out."""
    live = {i: f for i, f in cands.items() if not f.dup_cleared}
    if not live:
        return {}
    out: dict[int, str] = {}
    for r in await kit.recent_receipts(db, user_id):
        for i, f in live.items():
            mine = kit.norm_url(f.job_url)
            same = r["job_id"] == f.job_id or (mine and kit.norm_url(str(r["job_apply_url"] or "")) == mine)
            if int(r["application_id"]) != i and same:
                out[i] = "duplicate_job"
    cur = await db._db.execute(
        "SELECT e.application_id, a.job_title, j.normalized_company FROM application_events e "
        "JOIN applications a ON a.id = e.application_id JOIN jobs j ON j.id = a.job_id "
        "WHERE e.user_id = ? AND a.user_id = ? AND e.event_type = 'applied' ORDER BY e.id DESC LIMIT 2000",
        (user_id, user_id),
    )
    applied = [(int(r["application_id"]), kit.normalized_title(str(r["job_title"] or "")),
                str(r["normalized_company"] or "")) for r in await cur.fetchall()]
    if applied and any(i not in out for i in live):
        cur = await db._db.execute(
            f"SELECT id, normalized_company FROM jobs WHERE id IN ({','.join('?' for _ in live)})",  # noqa: S608
            tuple(f.job_id for f in live.values()),
        )
        company = {int(r["id"]): str(r["normalized_company"] or "") for r in await cur.fetchall()}
        for i, f in live.items():
            comp, title = company.get(int(f.job_id or 0), ""), kit.normalized_title(f.job_title)
            if i not in out and comp and comp != "unknown" and title and any(
                a != i and c == comp and t == title for a, t, c in applied
            ):
                out[i] = "duplicate_role"
    return out


async def _unsupported(db: JobDatabase, user_id: str, cands: dict[int, Facts]) -> list[int]:
    ids = {int(f.cv_artifact_id): i for i, f in cands.items() if f.cv_artifact_id is not None}
    if not ids:
        return []
    cur = await db._db.execute(
        f"SELECT id, checks_json FROM application_artifacts WHERE user_id = ? AND id IN ({','.join('?' for _ in ids)})",  # noqa: S608
        (user_id, *ids),
    )
    return [ids[int(r[0])] for r in await cur.fetchall()
            if any(c.get("code") in kit._UNSUPPORTED_CODES for c in kit._stored_checks(r[1]))]

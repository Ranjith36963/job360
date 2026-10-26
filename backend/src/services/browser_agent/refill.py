"""Refill the form (owner decision 2026-10-26): Job360's browser replays what the user's assistant typed.

When a CAPTCHA (or a sign-in) stops a filled form and the page is then lost (a deploy restarts the
process, the browser idles out), the user presses "Refill the form" on the website. Job360's own browser
opens the form again and replays, in order, exactly the steps the assistant carried out last time - the
same answers, the same CV upload, the same option clicks - then holds the page for "Finish it here".
A deploy no longer needs the click while the old hold would still run: on boot, ``restore.py`` replays the
same script for each such form and holds it for the time left.

* Recording (:func:`note`): every successful type / choose / tick / upload, a click on a radio or a list
  option, and a fill row, keeps a STABLE locator (``page_js.LOCATOR_OF_JS``: tag, type, role, label,
  question, nth) and the value. Never a submit-like click, never a password or one-time-code box.
* Replay (:func:`replay`): each step goes through the agent's own ``_act`` with NO gate, so every safety
  rule still holds (no password, no trap field, a final submit is refused - never clicked), nothing new
  is typed, and a CAPTCHA stops the replay (it is never solved).

Page text read here is untrusted; only the locator key and the value are kept.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any, Optional

from src.services.browser_agent import agent
from src.services.browser_agent.page_js import LOCATOR_OF_JS, STAMP_LOCATOR_JS

STEP_ACTIONS = frozenset({"type", "choose", "tick", "click", "upload"})
# A click is a step only on a choice: a radio, a box, a list option (never a button, a link or a submit).
_CLICK_ROLES = frozenset({"radio", "option", "checkbox", "switch"})
_LOCATOR_KEYS = ("tag", "type", "role", "name", "group", "nth")
REFILL_TIMEOUT_S = 300.0
STEP_TIMEOUT_S = 30.0
FIELD_NAME_MAX = 80


def host_of(url: str) -> str:
    """The lower-case host of a page address, without a leading ``www.``; "" when unreadable."""
    from urllib.parse import urlsplit  # noqa: PLC0415

    try:
        host = (urlsplit(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


async def describe(s: agent.Session, frame: Any, loc: Any) -> Optional[dict[str, Any]]:
    """The control's locator (with the page's host), or None: not recording, unreadable, or a secret box."""
    if not s.record:
        return None
    try:
        got = await loc.evaluate(LOCATOR_OF_JS)
    except Exception:  # noqa: BLE001 - an unreadable control is simply not replayable
        return None
    if not isinstance(got, dict) or got.get("secret") is not False or not isinstance(got.get("nth"), int):
        return None
    where = {k: got.get(k) for k in _LOCATOR_KEYS}
    if not all(isinstance(where[k], str) for k in _LOCATOR_KEYS if k != "nth"):
        return None
    where["frame"] = "/".join(agent._page_key(str(getattr(frame, "url", "") or "")))
    try:
        where["host"] = host_of(str(agent._live_page(s).url))
    except agent.BrowserError:
        return None
    return where


def note(s: agent.Session, action: str, where: Optional[dict[str, Any]], value: str = "", *, kind: str = "") -> None:
    """Keep one successful step for the route to store. Never a password, never a submit."""
    if where is None or action not in STEP_ACTIONS or kind in ("password", "submit") or not where.get("host"):
        return
    s.steps.append({"action": action, "where": where, "value": str(value)[: agent.MAX_TYPE_CHARS]})


def click_step(facts: dict[str, Any]) -> str:
    """What a click on this control replays as: "tick" (a box: its state), "click" (a radio, an option), or ""."""
    tag, kind, role = facts.get("tag", ""), facts.get("type", ""), str(facts.get("role") or "").lower()
    if (tag == "input" and kind == "checkbox") or role in ("checkbox", "switch"):
        return "tick"
    if (tag == "input" and kind == "radio") or role in _CLICK_ROLES:
        return "click"
    return ""


async def _stamp(page: Any, where: dict[str, Any]) -> str:
    """Find the recorded control on the page now and give it a fresh ref; "" when it is not there."""
    ref = f"e{9000 + secrets.randbelow(999)}_{secrets.token_hex(2)}"
    want = {**{k: where.get(k) for k in _LOCATOR_KEYS}, "ref": ref}
    frames = list(page.frames[: agent.MAX_FRAMES_CHECKED])
    # The frame it was recorded in first (an embedded form), then the others.
    frames.sort(key=lambda f: "/".join(agent._page_key(str(f.url or ""))) != where.get("frame"))
    for frame in frames:
        try:
            if await asyncio.wait_for(frame.evaluate(STAMP_LOCATOR_JS, want), agent.FRAME_READ_TIMEOUT_S):
                return ref
        except Exception:  # noqa: BLE001, S112 - a frame mid-navigation
            continue
    return ""


async def _one(pool: agent.BrowserPool, s: agent.Session, step: dict[str, Any], files: agent.FileFn) -> str:
    """Replay one step: "" when it holds its value now, else the reason."""
    action, value = step["action"], str(step.get("value") or "")
    ref = await _stamp(agent._live_page(s), step["where"])
    if not ref:
        return "not_found"
    out = await agent._act(
        pool, s, action, url="", ref=ref, text=value if action != "upload" else "", on=value == "yes",
        doc_kind=value if action == "upload" else "", gate=None, files=files,
    )
    if out.get("status") == "needs_you":
        raise agent.BrowserError("needs_you", str(out.get("reason") or "captcha"), "The page asks for a human check.")
    if action == "type" and not out.get("typed_matches"):
        return "value_differs"
    if action == "upload" and out.get("upload_error"):
        return "upload_failed"
    return ""  # choose and tick raise unless the field shows the value (agent._choose, agent._tick)


async def replay(
    pool: agent.BrowserPool, user_id: str, application_id: int, form_url: str, steps: list[dict[str, Any]],
    *, files: agent.FileFn, load_state: Optional[agent.StateFn] = None,
) -> dict[str, Any]:
    """Open (or reuse) the job's browser on ``form_url`` and replay ``steps`` in order. Never clicks a
    submit, never types anything not in ``steps``, stops at a CAPTCHA. Raises ``BrowserError`` (busy, off)."""
    key = agent.browser_key(user_id, application_id)
    s = await pool.get(key, create=True, load_state=load_state)
    if s is None or s.lock.locked():
        raise agent.BrowserError("busy", "busy", "Job360's browser is busy with this job right now. Try again soon.")
    async with s.lock:
        s.last_used = time.monotonic()
        s.record = False  # a replay is not a new script
        try:
            return await asyncio.wait_for(_replay(pool, s, form_url, steps, files), REFILL_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise agent.BrowserError("error", "timeout", "Refilling took too long and was stopped.") from None
        finally:
            s.last_used = time.monotonic()


async def _replay(
    pool: agent.BrowserPool, s: agent.Session, form_url: str, steps: list[dict[str, Any]], files: agent.FileFn,
) -> dict[str, Any]:
    try:
        here = str(agent._live_page(s).url)
    except agent.BrowserError:
        here = ""
    if not agent._same_page(here, form_url):
        await agent._act(
            pool, s, "open", url=form_url, ref="", text="", on=True, doc_kind="", gate=None, files=None,
        )
    ok, failed, stopped = 0, [], ""
    for i, step in enumerate(steps, 1):
        if stopped:
            reason = "not_tried"
        else:
            try:
                reason = await asyncio.wait_for(_one(pool, s, step, files), STEP_TIMEOUT_S)
            except agent.BrowserError as exc:
                reason = exc.reason
                stopped = reason if exc.status == "needs_you" else ""
            except asyncio.TimeoutError:
                reason = "timeout"
            except Exception:  # noqa: BLE001 - one step's browser failure never stops the others
                reason = "browser_error"
        if reason:
            name = str(step["where"].get("name") or "")[:FIELD_NAME_MAX]
            failed.append({"step": i, "action": step["action"], "field": name, "reason": reason})
        else:
            ok += 1
    s.last_used = time.monotonic()
    return {"status": "ok", "steps_ok": ok, "steps_failed": failed, **({"stopped": stopped} if stopped else {})}

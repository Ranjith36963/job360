"""The Job360 browser agent — a cloud Chromium the user's own assistant drives.

docs/product/BROWSER_AGENT.md. Job360 does not think here: the assistant
(Claude, ChatGPT, Gemini, ...) reads the page with ``look`` and decides every
click. This module only carries the action out, inside the hard lines in
``safety.py``:

* a password box is never typed into;
* a CAPTCHA, bot check or sign-in wall stops the run (``needs_you``);
* a final-submit click happens only after the injected ``Gate`` (the same
  ``check_submit`` every assistant uses) says ``submit``, and records
  ``submit_attempted`` first;
* every request the page makes is screened by the SSRF guard;
* no downloads, no service workers, one browser per user (and a separate one
  for the user's own sign-in view), closed when idle, and at most
  ``max_sessions`` assistant browsers and ``max_live`` sign-in views at once.

Playwright is imported lazily (rule #16) and is an optional dependency
(``pip install .[browser]``); without it every action answers ``off``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from src.services.applications.job_checks import redact_url_for_display
from src.services.browser_agent.egress import BLOCK_HEADER, BLOCK_MARK, UNREACHABLE_MARK, EgressProxy, OpenFn
from src.services.browser_agent.page_js import (
    CHOSEN_NEARBY_JS,
    CONTROL_STATE_JS,
    ELEMENT_FACTS_JS,
    FOCUS_IN_LIST_JS,
    FORM_STEP_JS,
    IS_TEXT_FIELD_JS,
    IS_TYPEAHEAD_JS,
    IS_WORKDAY_FIELD_JS,
    LEVER_CHECK_JS,
    NATIVE_SET_JS,
    NEAR_TEXT_JS,
    OWN_LIST_IDS_JS,
    PAGE_TEXT_JS,
    SHOWN_VALUE_JS,
    SNAPSHOT_JS,
    SUBMIT_GUARD_READ_JS,
    SUBMIT_GUARD_START_JS,
    TICK_VIA_LABEL_JS,
    WATCH_READ_JS,
    WATCH_START_JS,
)
from src.services.browser_agent.safety import RequestScreen, _captcha_frame, blocker_for, control_kind
from src.services.browser_agent.sites import (
    account_step,
    is_honeypot,
    looks_like_application,
    outcome_for,
    site_for,
)
from src.services.fetch.guard import ResolveFn

# Page text is fenced by the shared helper (gap-audit #15: stored text read over MCP uses it too).
from src.services.untrusted import UNTRUSTED
from src.services.untrusted import wrap_untrusted as _wrap_untrusted

logger = logging.getLogger("job360.services.browser_agent")

ACTIONS = ("open", "look", "click", "type", "choose", "tick", "upload", "key", "scroll", "back", "close")
# Keys the assistant may press (action "key", the key in `text`). Enter only inside a suggestion list:
# in an ordinary field Enter can submit the form, which would skip check_submit.
AGENT_KEYS = frozenset({"ArrowDown", "ArrowUp", "Tab", "Escape", "Enter"})
MAX_ELEMENTS = 150
MAX_TEXT = 6000
# The page sets these: its title, its address and the element list are each bounded.
MAX_TITLE = 200
MAX_URL = 2000
MAX_ELEMENTS_CHARS = 60_000
MAX_TYPE_CHARS = 5000
ACTION_TIMEOUT_MS = 10_000
NAV_TIMEOUT_MS = 30_000
# One action, end to end: past this the user's browser is let go, never held by a page that hangs.
ACT_TIMEOUT_S = 60.0
# How often idle browsers are swept, started by the app's lifespan when the agent is on.
REAP_EVERY_S = 60.0
_REF = re.compile(r"^e\d{1,4}_[a-z0-9]{1,4}$")


class BrowserError(Exception):
    """A refusal the tool shows to the assistant: ``status`` + ``reason`` + one plain sentence."""

    def __init__(self, status: str, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.reason = reason
        self.detail = detail
        # The page it happened on, for a needs_you stop: the route records ``blocked`` with its host.
        self.url = ""

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason, "detail": self.detail}


@dataclass
class Gate:
    """The final-submit gate, injected by the MCP tool so this module never imports a route.

    ``check(form_url)`` returns the ``check_submit`` answer for the application;
    ``mark_attempt()`` records ``submit_attempted`` right before the click.
    """

    check: Callable[[str], Awaitable[dict[str, Any]]]
    mark_attempt: Callable[[], Awaitable[None]]
    # Stores a screenshot of the site's confirmation as the application's proof (S7), when given.
    save_proof: Optional[Callable[[bytes], Awaitable[None]]] = None
    # Which application this gate is for: a second job on the same form page is its own submit.
    application_id: Optional[int] = None


# doc_kind -> (file name, PDF bytes) for an upload, injected by the MCP tool.
FileFn = Callable[[str], Awaitable[tuple[str, bytes]]]
# Loads the user's saved sign-ins as one Playwright storage state (slice 2), injected by the route.
StateFn = Callable[[], Awaitable[dict[str, Any]]]
VIEWPORT = {"width": 1280, "height": 900}
# The pool key prefix of a live sign-in view (live_key): those count against max_live, not max_sessions.
LIVE_PREFIX = "live:"


@dataclass
class Session:
    context: Any
    page: Any
    last_used: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Clicks on a ref that changed nothing, in a row (reset by any click that changes the page).
    dead_clicks: dict[str, int] = field(default_factory=dict)
    # The (application, page) a final submit was clicked on, and whether the site then refused it.
    submitted_on: tuple[Optional[int], str] = (None, "")
    submit_refused: bool = False
    # The last few (action, ref, text, url), for the repeat warning: three opens of three pages are no repeat.
    recent: list[tuple[str, str, str, str]] = field(default_factory=list)
    # A live sign-in view closes at this time (monotonic) however busy it is; None = no limit.
    closes_at: Optional[float] = None
    # Tabs whose last main-frame load the egress proxy refused (a redirect hop to a private address,
    # or a host it could not reach), with the reason the agent answers.
    blocked_tabs: dict[Any, str] = field(default_factory=dict)
    # Addresses of refused loads Playwright could not tie to a tab (a new tab's first load).
    blocked_urls: dict[str, str] = field(default_factory=dict)
    # The assistant session (its `session` label) driving this browser: another one is refused.
    owner: str = ""


class BrowserPool:
    """One Chromium process, one isolated context (cookies, storage) per user."""

    def __init__(
        self,
        *,
        max_sessions: int,
        idle_s: float,
        max_live: int = 2,
        resolve: ResolveFn,
        launch_args: Optional[list[str]] = None,
        open_connection: Optional[OpenFn] = None,
    ) -> None:
        self.max_sessions = max_sessions
        self.max_live = max_live
        self.idle_s = idle_s
        self.screen = RequestScreen(resolve)
        # The one way out for Chromium's traffic (egress.py): screens and pins every connection.
        self.egress = EgressProxy(resolve, open_connection)
        self._launch_args = list(launch_args or [])
        self._sessions: dict[str, Session] = {}
        self._playwright: Any = None
        self._browser: Any = None
        self._start_lock = asyncio.Lock()
        # Session creation awaits several times; two `open`s at once must not both pass the
        # "no session yet" and "under the cap" checks (CodeRabbit on #902).
        self._create_lock = asyncio.Lock()

    async def _ensure_browser(self) -> Any:
        async with self._start_lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            try:
                from playwright.async_api import async_playwright  # noqa: PLC0415 - optional, heavy (rule #16)
            except ImportError:
                raise BrowserError(
                    "off", "not_installed", "Job360's browser is not installed on this server."
                ) from None
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            if not self.egress.port:
                await self.egress.start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    # Every connection, loopback included, leaves through the screening proxy.
                    f"--proxy-server=http://127.0.0.1:{self.egress.port}",
                    "--proxy-bypass-list=<-loopback>",
                    # WebRTC sends UDP straight out, around the proxy and its address screen.
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    *self._launch_args,
                ],
            )
            return self._browser

    async def reap(self) -> None:
        """Close every session idle longer than ``idle_s``, and every live view past its lifetime."""
        now = time.monotonic()
        for user_id, s in list(self._sessions.items()):
            expired = s.closes_at is not None and now > s.closes_at
            if (now - s.last_used > self.idle_s or expired) and not s.lock.locked():
                await self.close(user_id)

    async def get(
        self, user_id: str, *, create: bool, load_state: Optional[StateFn] = None
    ) -> Optional[Session]:
        await self.reap()
        if self._browser is not None and not self._browser.is_connected():
            # Chromium died: every session was bound to it. Free them, or each one errors and
            # still counts against max_sessions until it idles out.
            for uid in list(self._sessions):
                await self.close(uid)
        s = self._sessions.get(user_id)
        if s is not None or not create:
            return s
        async with self._create_lock:
            s = self._sessions.get(user_id)
            if s is not None:
                return s
            return await self._create(user_id, load_state)

    async def _create(self, user_id: str, load_state: Optional[StateFn]) -> Session:
        """Build one user's browser. Called with ``_create_lock`` held."""
        # Live sign-in views have their own small cap: they never take an assistant's browser.
        is_live = user_id.startswith(LIVE_PREFIX)
        same_kind = sum(1 for k in self._sessions if k.startswith(LIVE_PREFIX) == is_live)
        if same_kind >= (self.max_live if is_live else self.max_sessions):
            raise BrowserError(
                "busy", "all_browsers_busy",
                "Every Job360 browser is in use right now. Try again in a few minutes, or use your own browser.",
            )
        browser = await self._ensure_browser()
        # Slice 2: the user's saved sign-ins (cookies + site storage) ride into the new browser.
        state = await load_state() if load_state is not None else None
        context = await browser.new_context(
            accept_downloads=False, service_workers="block", locale="en-GB", viewport=dict(VIEWPORT),
            storage_state=state if state and (state.get("cookies") or state.get("origins")) else None,
        )
        context.set_default_timeout(ACTION_TIMEOUT_MS)
        context.set_default_navigation_timeout(NAV_TIMEOUT_MS)

        async def _route(route: Any) -> None:
            verdict = await self.screen.screen(route.request.url)
            if verdict.ok:
                await route.continue_()
            else:
                logger.info("browser_agent blocked a request: %s", verdict.reason)
                await route.abort("blockedbyclient")

        await context.route("**/*", _route)
        page = await context.new_page()
        s = Session(context=context, page=page)

        def _on_page(new_page: Any) -> None:
            # "Apply" often opens the form in a new tab: follow it.
            s.page = new_page

        context.on("page", _on_page)

        def _main_frame(message: Any) -> Any:
            frame = message.frame
            return frame if frame.parent_frame is None else None

        def _on_request(request: Any) -> None:
            try:
                frame = _main_frame(request) if request.is_navigation_request() else None
            except Exception:  # noqa: BLE001 - a request with no frame (a worker): not a page load
                return
            if frame is not None:
                # The tab moves on: its refusal (by tab or by address) is over.
                s.blocked_tabs.pop(frame.page, None)
                s.blocked_urls.pop(frame.page.url, None)

        def _on_response(response: Any) -> None:
            # Only the proxy's own 403 holds its per-process mark: a site cannot send it.
            reason = _REFUSAL.get(response.headers.get(BLOCK_HEADER, "")) if response.status == 403 else None
            if reason is None:
                return
            try:
                if not response.request.is_navigation_request():
                    return
            except Exception:  # noqa: BLE001 - a response with no request: not a page load
                return
            try:
                frame = _main_frame(response)
            except Exception:  # noqa: BLE001 - a new tab's first load has no frame yet: keep its address
                s.blocked_urls[response.url] = reason
                return
            if frame is not None:
                s.blocked_tabs[frame.page] = reason

        context.on("request", _on_request)
        context.on("response", _on_response)
        self._sessions[user_id] = s
        return s

    async def close(self, user_id: str) -> bool:
        s = self._sessions.pop(user_id, None)
        if s is None:
            return False
        try:
            async with s.lock:  # never under an action in flight: it finishes first
                await s.context.close()
        except Exception:  # noqa: BLE001 - a dead context is already closed
            logger.debug("browser_agent context close failed", exc_info=True)
        return True

    async def shutdown(self) -> None:
        for user_id in list(self._sessions):
            await self.close(user_id)
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        await self.egress.close()
        self.egress.port = 0


BLOCKED_HOP = (
    "That led to a private or local address, and Job360's browser only opens public web pages. "
    "Open another link."
)
UNREACHABLE_HOP = "That site could not be found or did not answer. Open another link."
# The proxy's own marks (fresh each process: a site cannot send them) and what each one answers.
_REFUSAL = {BLOCK_MARK: "blocked_address", UNREACHABLE_MARK: "load_failed"}


def _refusal(s: Session, page: Any) -> Optional[BrowserError]:
    """The proxy's refusal if it is the tab's last load (a new tab is known by its address)."""
    if page not in s.blocked_tabs and page.url in s.blocked_urls:
        s.blocked_tabs[page] = s.blocked_urls.pop(page.url)
    reason = s.blocked_tabs.get(page)
    if reason == "load_failed":
        return BrowserError("error", "load_failed", UNREACHABLE_HOP)
    return _hop_refused() if reason else None


def _hop_refused() -> BrowserError:
    """A load the screen or the egress proxy refused (a redirect hop, a history entry): the same
    answer as opening that address directly."""
    return BrowserError("refused", "blocked_address", BLOCKED_HOP)


async def _press(s: Session, ref: str, key: str) -> dict[str, Any]:
    """Press one key in the focused field (or ``ref``'s field, focused first)."""
    if key not in AGENT_KEYS:
        raise BrowserError("refused", "bad_key", "key (in text) must be one of: " + ", ".join(sorted(AGENT_KEYS)))
    page = _live_page(s)
    if ref:
        frame, loc = await _find(s, ref)
        _refuse_captcha_frame(frame)
        await loc.focus()
    # Every key goes where the focus is, a hidden challenge frame included: find that frame first.
    frame = page.main_frame
    for f in page.frames[:MAX_FRAMES_CHECKED]:  # the focus may live in a frame (an embedded form)
        try:
            if await asyncio.wait_for(f.evaluate("() => document.hasFocus()"), FRAME_READ_TIMEOUT_S):
                frame = f
        except Exception:  # noqa: BLE001, S112 - a frame mid-navigation, or one that never answers
            continue
    _refuse_captcha_frame(frame)
    guarded = None
    if key == "Enter":
        if not await frame.evaluate(FOCUS_IN_LIST_JS):
            raise BrowserError(
                "refused", "enter_may_submit",
                "Enter is only pressed on a highlighted option of a suggestion list (type, then ArrowDown). "
                "If the list still refuses it, use action choose with the option's text. Anywhere else Enter can "
                "submit the form: click the form's submit button instead, so check_submit runs.",
            )
        # Defence in depth: a site that lets Enter through to its form would submit it. Stop that submit.
        await frame.evaluate(SUBMIT_GUARD_START_JS)
        guarded = frame
    submitted = False
    try:
        await page.keyboard.press(key)
        await _settle(page)
    finally:
        # The guard always comes off: left on, it would silently cancel the real, gated submit later.
        if guarded is not None:
            try:
                submitted = await guarded.evaluate(SUBMIT_GUARD_READ_JS)
            except Exception:  # noqa: BLE001 - the frame navigated: no submit got past the guard to do it
                submitted = False
    if guarded is not None:
        if submitted:
            raise BrowserError(
                "refused", "enter_may_submit",
                "That Enter would have submitted the form; Job360 stopped it. Click an option instead, and the "
                "form's submit button only when it is time, so check_submit runs.",
            )
    return await look(s, after_action=True)


# Workday answers longer than this are filled, not keyed: keying 3,000 characters takes over a minute.
KEYED_MAX_CHARS = 200
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


async def _type_into(loc: Any, text: str) -> str:
    """Type and read back. Suggestion boxes key by key; Workday's short fields key by key, then blur
    so Workday commits them; other fields fill(), then the native setter if the site's framework did
    not take it (one bounded heal, never a loop)."""
    typeahead = bool(await loc.evaluate(IS_TYPEAHEAD_JS))
    workday = not typeahead and bool(await loc.evaluate(IS_WORKDAY_FIELD_JS))
    # Never key a control character: as keys "\n" is Enter (it can submit the form, skipping
    # check_submit) and "\t" is Tab (the rest would land in another field). fill() inserts them as text.
    keyed = typeahead or (workday and len(text) <= KEYED_MAX_CHARS)
    if keyed and not _CONTROL_CHARS.search(text):
        await loc.fill("")
        await loc.press_sequentially(text, delay=25)
    else:
        await loc.fill(text)
    shown = str(await loc.evaluate(SHOWN_VALUE_JS))
    # The native setter is an input's or a textarea's: a contenteditable box keeps what fill() left.
    if shown != text and not typeahead and await loc.evaluate(IS_TEXT_FIELD_JS):
        shown = str(await loc.evaluate(NATIVE_SET_JS, text))
    if workday:
        await loc.blur()
    return shown


# Where custom dropdowns draw their options: ARIA options anywhere (often a portal at the end of the
# page), react-select's classes, and Workday's prompt options (freehire combobox.ts:44-119;
# OfferOS dom-fill.ts:913-960; ApplYourself fill.py:12-17) and Lever's location list (fill.py:50-56).
OPTION_SELECTOR = '[role=option], .select__option, [data-automation-id="promptOption"], .dropdown-results > *'


def _pick(options: list[str], want: str) -> int:
    """Index of the option to click: an exact match (any case), else the ONLY option starting with it,
    else the ONLY option containing it; -1 when none or ambiguous (never a guess)."""
    w = " ".join(want.split()).casefold()
    if not w:
        return -1  # nothing asked for: never "the only option there is"
    norm = [" ".join(o.split()).casefold() for o in options]
    exact = [i for i, o in enumerate(norm) if o == w]
    if exact:
        return exact[0] if len(exact) == 1 else -1  # two options with the same name: no guess
    for hits in ([i for i, o in enumerate(norm) if o.startswith(w)], [i for i, o in enumerate(norm) if w in o]):
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return -1
    return -1


# Options the user can see: a closed list drawn earlier (hidden) never answers for the open one.
VISIBLE_OPTIONS = ", ".join(f"{part.strip()}:visible" for part in OPTION_SELECTOR.split(","))
# The ids of the list a dropdown says is its own (aria-controls / aria-owns), when they are plain ids.
_LIST_ID = re.compile(r"^[\w:.-]{1,200}$")


def _option_not_found(text: str, seen: list[str]) -> BrowserError:
    # Option labels are page text: clipped, and inside the untrusted marker like the rest of the page.
    listed = _wrap_untrusted("; ".join(o[:60] for o in seen[:30])) if seen else "none were shown"
    return BrowserError(
        "refused", "option_not_found",
        f"No single option matches '{text[:80]}'. Type part of it first, then choose again. Options seen: {listed}",
    )


async def _choose(s: Session, loc: Any, text: str) -> None:
    """Pick an option: a native <select> by the same matching rules; a custom dropdown by opening it,
    finding the option wherever it is drawn, clicking it and checking the field now shows it."""
    if await loc.evaluate("(el) => el.tagName === 'SELECT'"):
        labels = [" ".join(str(t).split()) for t in await loc.evaluate("(el) => Array.from(el.options, o => o.text)")]
        i = _pick(labels, text)
        if i >= 0:
            await loc.select_option(index=i)
            return
        try:
            await loc.select_option(value=text, timeout=1000)
        except Exception:  # noqa: BLE001 - no option has that value either
            raise _option_not_found(text, labels) from None
        return
    page = _live_page(s)
    if not await loc.evaluate("(el) => el.getAttribute('aria-expanded') === 'true'"):
        await loc.click()
    own_lists = [i for i in await loc.evaluate(OWN_LIST_IDS_JS) if _LIST_ID.match(i)]
    seen: list[str] = []
    for frame in await _option_frames(page, loc):
        options, scoped = frame.locator(VISIBLE_OPTIONS), False
        for list_id in own_lists:  # the list the field names as its own, when it is in this frame
            own = frame.locator(f'[id="{list_id}"]')
            if await own.count():
                options, scoped = own.locator(VISIBLE_OPTIONS), True
                break
        try:
            await options.first.wait_for(state="visible", timeout=2000 if frame == page.main_frame else 300)
        except Exception:  # noqa: BLE001, S112 - no options drawn in this frame
            continue
        if not scoped and await frame.locator("[role=listbox]:visible").count() > 1:
            await page.keyboard.press("Escape")
            raise BrowserError(
                "refused", "two_lists_open",
                "Two dropdown lists are open and this field does not say which is its own. Close the other "
                "one (key Escape), then choose again.",
            )
        texts = [" ".join(t.split()) for t in await options.all_inner_texts()][:200]
        seen.extend(texts)
        i = _pick(texts, text)
        if i < 0:
            continue  # an unrelated open list here; the one we want may be in another frame
        before = str(await loc.evaluate(SHOWN_VALUE_JS)).casefold()
        await options.nth(i).click()
        await _settle(page)
        shown = str(await loc.evaluate(SHOWN_VALUE_JS)).casefold()
        # The picked option's own text shows, or the asked-for text shows AND the field changed (what was
        # typed into a suggestion box beforehand is no proof). react-select keeps its input empty and shows
        # the pick next to it: then read the field's surroundings.
        confirmed = (
            texts[i].casefold() in shown or (text.strip().casefold() in shown and shown != before)
            if shown.strip() else bool(await loc.evaluate(CHOSEN_NEARBY_JS, texts[i].casefold()))
        )
        if confirmed:
            return
        raise BrowserError(
            "error", "choose_unconfirmed",
            f"Clicked option {i + 1} but the field does not show it. The field shows: "
            + (_wrap_untrusted(shown[:60]) if shown.strip() else "nothing"),
        )
    await page.keyboard.press("Escape")
    raise _option_not_found(text, seen)


async def _option_frames(page: Any, loc: Any) -> list[Any]:
    """Where to look for the options: the field's own frame first (look lists fields in shown frames
    past the MAX_FRAMES-th), then the page, then other shown frames; at most MAX_FRAMES of them."""
    own = None
    try:
        own = await (await loc.element_handle(timeout=1000)).owner_frame()
    except Exception:  # noqa: BLE001 - detached: search the page as before
        own = None
    order = [f for f in (own, page.main_frame) if f is not None]
    for f in page.frames[:MAX_FRAMES_CHECKED]:
        if len(order) >= MAX_FRAMES:
            break
        if f not in order and await _frame_shown(f):
            order.append(f)
    return list(dict.fromkeys(order))[:MAX_FRAMES]


def _live_page(s: Session) -> Any:
    """The tab to act on: the newest one still open."""
    if s.page is not None and not s.page.is_closed():
        return s.page
    pages = [p for p in s.context.pages if not p.is_closed()]
    if not pages:
        raise BrowserError("refused", "no_page", "No page is open. Call browser with action open and a url.")
    s.page = pages[-1]
    return s.page


MAX_FRAMES = 20
# How many frames are looked at to find the MAX_FRAMES shown ones.
MAX_FRAMES_CHECKED = 100
# Controls kept for embedded frames when the page has any shown one.
FRAME_ELEMENT_FLOOR = 50
FRAME_READ_TIMEOUT_S = 3.0


async def _read_frame(
    frame: Any, max_elements: int, *, main: bool, window: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """One frame's snapshot. A child frame that fails is skipped; the page itself gets one more try
    and then an error, never an empty "ok" (CodeRabbit on #914). ``window`` is a child frame's part of
    the screen (``_frame_window``)."""
    for attempt in range(2 if main else 1):
        try:
            return await asyncio.wait_for(
                frame.evaluate(SNAPSHOT_JS, {"maxElements": max_elements, "maxText": MAX_TEXT, **(window or {})}),
                FRAME_READ_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001, S112 - a frame that navigated, hung or is cross-process mid-read
            if attempt == 0 and main:
                await asyncio.sleep(0.5)
    if main:
        raise BrowserError(
            "error", "page_unreadable",
            "The page could not be read (it may still be loading or moving to a new page). Call look again.",
        )
    return None


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + "…"


def _shown_url(url: str) -> str:
    """The address the assistant is told (gap-audit #18): scheme + host + path + job-id query keys. A
    magic link, session id or signed link in the query string or fragment must not reach the
    assistant's transcript. Shown only: the browser itself still sits on, and opens, the full address."""
    return _clip(redact_url_for_display(url), MAX_URL)


_LINK_IN_TEXT = re.compile(r"https?://[^\s'\"<>]+")


def _scrub_links(text: str) -> str:
    """``text`` (a browser error line) with every http(s) link redacted: Playwright's messages quote the URL."""
    return _LINK_IN_TEXT.sub(lambda m: redact_url_for_display(m.group(0)) or "[link]", text)


def _within_size(
    elements: list[dict[str, Any]], places: list[tuple[float, bool]]
) -> tuple[list[dict[str, Any]], int, int]:
    """The elements that fit in ``MAX_ELEMENTS_CHARS`` (as JSON), and how many did not, above and
    below the screen. The ones farthest from the screen go first, the biggest first at the same
    distance (a long option list before a button): the on-screen submit stays."""
    sizes = [len(json.dumps(e)) + 2 for e in elements]
    size, drop, above, below = sum(sizes), set(), 0, 0
    for i in sorted(range(len(elements)), key=lambda i: (places[i][0], sizes[i], i), reverse=True):
        if size <= MAX_ELEMENTS_CHARS:
            break
        drop.add(i)
        size -= sizes[i]
        if places[i][1]:
            above += 1
        else:
            below += 1
    return [e for i, e in enumerate(elements) if i not in drop], above, below


async def _frame_box(frame: Any) -> Optional[dict[str, Any]]:
    """A child frame's box on the page's screen (Playwright: relative to the main frame's viewport)."""
    try:
        handle = await frame.frame_element()
        box: Optional[dict[str, Any]] = await handle.bounding_box()
        return box
    except Exception:  # noqa: BLE001 - detached or cross-process mid-read
        return None


# Can the user see this frame's element: a real size, not hidden or see-through, and not parked off the
# document? An invisible reCAPTCHA or hCaptcha keeps its challenge frame at full size inside a
# `visibility: hidden` box at `top: -10000px` until it asks something (seen on Ashby and Lever, 2026-10-20).
_FRAME_ELEMENT_SHOWN_JS = r"""
(el) => {
  const r = el.getBoundingClientRect();
  if (r.width <= 1 || r.height <= 1) return false;
  if (el.checkVisibility && !el.checkVisibility({ visibilityProperty: true, opacityProperty: true })) return false;
  const win = el.ownerDocument.defaultView;
  return r.right + win.scrollX > 0 && r.bottom + win.scrollY > 0;
}
"""


async def _frame_shown(frame: Any) -> bool:
    """A child frame the user cannot see (0x0, hidden, off the page) holds no form worth reading and
    asks nothing: a challenge frame counts as a CAPTCHA only once it is shown. Every frame up to the
    page must be shown."""
    while frame is not None and frame.parent_frame is not None:
        try:
            handle = await frame.frame_element()
            shown = await asyncio.wait_for(handle.evaluate(_FRAME_ELEMENT_SHOWN_JS), FRAME_READ_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - detached or cross-process mid-read
            return False
        if not shown:
            return False
        frame = frame.parent_frame
    return True


def _frame_window(page: Any, box: dict[str, Any]) -> dict[str, Any]:
    """The screen's top and bottom in the frame's own coordinates, so "nearest the screen" inside a tall
    frame follows the OUTER page's scroll; and the frame's submit button is always kept."""
    height = (page.viewport_size or VIEWPORT)["height"]
    return {"vTop": -box["y"], "vBottom": height - box["y"], "keepSubmit": True}


async def _site_warnings(page: Any, site: str) -> list[str]:
    """Traps a site sets that the element list cannot show."""
    if site != "lever":
        return []
    try:
        facts = await asyncio.wait_for(page.evaluate(LEVER_CHECK_JS), FRAME_READ_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - a page mid-navigation: no warning this read
        return []
    out = []
    if facts.get("location_unpicked"):
        out.append("Location is typed but not picked from the list: choose it, or Lever refuses the application.")
    if facts.get("cookie_banner"):
        out.append("A cookie banner is over the form: click its Allow or Accept button first.")
    return out


def _load_failed(page: Any, after_action: bool) -> dict[str, Any]:
    """Chromium's own error page: never an empty "ok". After an action it is the action's answer
    (a raise would lose what the action did, like a submit that went)."""
    if not after_action:
        raise BrowserError("error", "load_failed", "The last page did not load. Open it again or try another link.")
    return {
        "status": "error", "reason": "load_failed", "url": _shown_url(page.url),
        "detail": "The page did not load after that action.",
    }


async def look(s: Session, *, after_action: bool = False) -> dict[str, Any]:
    """Read the page: the visible controls nearest the screen (with a ref to act on), how many more
    there are above and below, and the page text inside an untrusted-content marker."""
    page = _live_page(s)
    if page.url.startswith("chrome-error://"):
        return _load_failed(page, after_action)
    refused = _refusal(s, page)
    if refused is not None:  # the tab holds the proxy's refusal, never a page to read as "ok"
        raise refused
    elements: list[dict[str, Any]] = []
    places: list[tuple[float, bool]] = []
    texts: list[str] = []
    frame_urls: list[str] = []
    has_password = False
    above = below = 0
    read = 0
    frames = page.frames[:MAX_FRAMES_CHECKED]
    # A page that embeds a shown frame (a Greenhouse or iCIMS form on a careers page) keeps part of
    # the budget for it: 150 links on the page itself must not crowd the form out.
    boxes = [None] + [await _frame_box(f) for f in frames[1:]]
    # Shown = the user can see it (size, visibility, on the page): a hidden challenge frame asks nothing.
    shown = [True] + [await _frame_shown(f) for f in frames[1:]]
    embeds = any(shown[1:])
    captcha_urls: list[str] = []
    for i, frame in enumerate(frames):
        frame_urls.append(frame.url)
        if shown[i]:
            captcha_urls.append(frame.url)
        # The cap counts frames READ: hidden ones (ads, trackers) before the form do not use it up.
        if read >= MAX_FRAMES or not shown[i]:
            continue
        budget = MAX_ELEMENTS - (FRAME_ELEMENT_FLOOR if embeds else 0) if i == 0 else MAX_ELEMENTS - len(elements)
        box = boxes[i]
        try:
            snap = await _read_frame(frame, budget, main=i == 0, window=_frame_window(page, box) if box else None)
        except BrowserError:
            if page.url.startswith("chrome-error://"):  # the read failed because the error page came
                return _load_failed(page, after_action)
            raise
        if snap is None:
            continue
        read += 1
        got = snap.get("elements") or []
        elements.extend(got)
        # Each element's distance from the screen and whether it is above it, for the size cut.
        where = snap.get("places") or []
        if len(where) != len(got):
            where = [(0, False)] * len(got)
        places.extend((float(d), bool(a)) for d, a in where)
        if snap.get("text"):
            texts.append(snap["text"])
        has_password = has_password or bool(snap.get("has_password_box"))
        above += int(snap.get("omitted_above") or 0)
        below += int(snap.get("omitted_below") or 0)
    # Every SHOWN frame's URL counts for the CAPTCHA check, the frames past the cap included. A hidden
    # challenge frame (the invisible kind, waiting) asks nothing; once it asks, it is shown.
    captcha_urls.extend([f.url for f in page.frames[MAX_FRAMES_CHECKED:] if await _frame_shown(f)])
    # Chromium switches to its error page a moment after a failed load: check again after the reads.
    if page.url.startswith("chrome-error://"):
        return _load_failed(page, after_action)
    elements, cut_above, cut_below = _within_size(elements, places)
    above += cut_above
    below += cut_below
    text = "\n\n".join(texts)[:MAX_TEXT]
    out: dict[str, Any] = {
        "status": "ok",
        "url": _shown_url(page.url),
        "title": _clip(await page.title(), MAX_TITLE),
        "note": UNTRUSTED,
        "elements": elements,
        "text": _wrap_untrusted(text),
    }
    try:
        step = await asyncio.wait_for(page.evaluate(FORM_STEP_JS), FRAME_READ_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - a page mid-navigation: no step this read
        step = None
    if step:
        out["form_step"] = step
    for e in elements:
        if is_honeypot(str(e.get("name") or "")):
            e["leave_blank"] = True
    out["application_form"] = looks_like_application(elements)
    site = site_for(page.url, frame_urls[1:])
    if site is not None:
        out["site"] = {"name": site.name, "tips": list(site.tips)}
        warnings = await _site_warnings(page, site.name)
        if warnings:
            out["site_warnings"] = warnings
    outcome = outcome_for(site, page.url, text)
    if outcome.confirmed:
        out["confirmation_seen"] = True
    if outcome.refused:
        out["site_refused"] = list(outcome.refused)
    if above or below:
        out["more_elements"] = {
            "above": above, "below": below,
            "hint": "Only the controls nearest the screen are listed. scroll up or down, then look, to see the rest.",
        }
    blocker = blocker_for(captcha_urls, text, has_password, account_step(site, out["title"], text))
    if blocker is not None:
        out.update(status="needs_you", reason=blocker.reason, detail=blocker.detail)
    return out


CAPTCHA_DETAIL = (
    "This page asks for a human check (CAPTCHA). Job360's browser never solves one and does nothing more on "
    "this page: stop this job; the user finishes it with their own browser."
)


def _refuse_captcha_frame(frame: Any) -> None:
    """Nothing inside a CAPTCHA's own frame (or a frame within it) is ever clicked, typed or keyed."""
    while frame is not None:
        if _captcha_frame(frame.url or ""):
            raise BrowserError("needs_you", "captcha", CAPTCHA_DETAIL)
        frame = frame.parent_frame


# The frame's text as look reads it (whitespace folded, so a phrase split over lines still matches).
_BODY_TEXT_JS = "(n) => (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').slice(0, n)"


async def _page_captcha(page: Any) -> bool:
    """Whether the page shows a CAPTCHA now: the same frames and text ``look`` reads."""
    urls: list[str] = []
    texts: list[str] = []
    read = 0
    for i, f in enumerate(page.frames):
        if i and not await _frame_shown(f):
            continue
        urls.append(f.url)
        if i >= MAX_FRAMES_CHECKED or read >= MAX_FRAMES:
            continue
        try:
            texts.append(await asyncio.wait_for(f.evaluate(_BODY_TEXT_JS, MAX_TEXT), FRAME_READ_TIMEOUT_S))
        except Exception:  # noqa: BLE001, S112 - a frame mid-navigation: its address still counts
            continue
        read += 1
    blocker = blocker_for(urls, "\n\n".join(texts)[:MAX_TEXT], False)
    return blocker is not None and blocker.reason == "captcha"


async def _find(s: Session, ref: str) -> tuple[Any, Any]:
    """The frame a ref is in, and its locator."""
    if not _REF.match(ref or ""):
        raise BrowserError("refused", "bad_ref", "Use a ref from the latest look, like e12_ab3f.")
    page = _live_page(s)
    found: list[tuple[Any, Any, int]] = []
    for frame in page.frames[:MAX_FRAMES_CHECKED]:  # the frames look reads, so the only ones with refs
        loc = frame.locator(f'[data-j360-ref="{ref}"]')
        try:
            count = await asyncio.wait_for(loc.count(), FRAME_READ_TIMEOUT_S)
        except Exception:  # noqa: BLE001, S112 - detached frame, or one that never answers
            continue
        if count:
            found.append((frame, loc, count))
    if len(found) == 1 and found[0][2] == 1:
        return found[0][0], found[0][1].first
    if found:
        # The site copied a control since the last look: acting on "the first" could type into the wrong one.
        raise BrowserError(
            "refused", "ref_not_found",
            "That ref now matches more than one control. Call look again and use a new one.",
        )
    raise BrowserError("refused", "ref_not_found", "That ref is not on the page any more. Call look and use a new one.")


async def _kind(loc: Any) -> str:
    facts = await loc.evaluate(ELEMENT_FACTS_JS)
    return control_kind(
        facts.get("tag", ""), facts.get("type", ""), facts.get("name", ""), facts.get("autocomplete", ""),
        bool(facts.get("in_form")), facts.get("role", ""), bool(facts.get("page_has_answers")),
        bool(facts.get("owns_form")), bool(facts.get("toggle")),
    )


SETTLE_IDLE_MS = 3000


async def _settle(page: Any) -> None:
    """Let the page finish: the HTML, then up to ``SETTLE_IDLE_MS`` for the network to go quiet,
    because single-page sites (React forms) draw their fields only after their own requests."""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=ACTION_TIMEOUT_MS)
    except Exception:  # noqa: BLE001 - a click that does not navigate is normal
        return
    try:
        await page.wait_for_load_state("networkidle", timeout=SETTLE_IDLE_MS)
    except Exception:  # noqa: BLE001, S110 - a page that keeps polling never goes idle: read it as it is
        pass


# A link that opens its page in a new tab.
OPENS_NEW_TAB_JS = "(e) => !!(e.closest && e.closest('a[target=\"_blank\"]'))"
# How long a click on such a link waits for the new tab to be reported.
NEW_TAB_WAIT_S = 2.0


async def _settle_new_tab(s: Session, page: Any, tabs: int, opens_tab: bool) -> None:
    """A click that opens a new tab: wait for the tab to be reported, then let it load. Until it
    commits its first page its address is still blank, so the proxy's refusal could not yet be matched
    to it (a loaded CI machine read a refused address as a page)."""
    if opens_tab:
        for _ in range(int(NEW_TAB_WAIT_S / 0.05)):
            if len(page.context.pages) > tabs:
                break
            await asyncio.sleep(0.05)
    new_page = s.page
    if new_page is not None and new_page is not page and not new_page.is_closed():
        await _settle(new_page)


# How long a scroll may take to land before the page is read anyway (the end of the page never moves).
SCROLL_WAIT_MS = 1000
# A promise (boxed: evaluate_handle would wait for a bare one) that resolves on the first scroll anywhere
# in the page (capture: an inner box's scroll does not bubble), or after the wait; then two frames, so
# the page's own scroll handlers (lazy sections) have run and drawn.
SCROLL_SEEN_JS = """(ms) => ({seen: new Promise((done) => {
  const end = () => {
    removeEventListener('scroll', on, true);
    requestAnimationFrame(() => requestAnimationFrame(() => done(true)));
  };
  const on = () => { clearTimeout(t); end(); };
  const t = setTimeout(end, ms);
  addEventListener('scroll', on, {capture: true, once: true});
})})"""


async def _wheel(page: Any, dy: float) -> None:
    """Turn the wheel and wait for the page to move. ``mouse.wheel`` returns before Chromium scrolls
    (the scroll lands a frame or more later), so a look straight after it read the page as it was:
    a form that draws its next section on scroll looked like it had nothing below."""
    seen = await page.evaluate_handle(SCROLL_SEEN_JS, SCROLL_WAIT_MS)
    try:
        await page.mouse.wheel(0, dy)
        await asyncio.wait_for(page.evaluate("(box) => box.seen", seen), SCROLL_WAIT_MS / 1000 + FRAME_READ_TIMEOUT_S)
    except Exception:  # noqa: BLE001, S110 - the page navigated or never answers: read it as it is
        pass
    finally:
        try:
            await seen.dispose()
        except Exception:  # noqa: BLE001 - its page is already gone
            logger.debug("browser_agent scroll handle dispose failed", exc_info=True)


# The same action this many times in a row gets a warning (browser-use browser_use/agent/views.py:157-178,
# :211-215 nudges at 5, 8, 12 repeats; a smaller window here because each of our steps is one tool call).
REPEAT_LIMIT = 3
# A ref whose clicks changed nothing this many times is not clicked again blind.
DEAD_CLICK_LIMIT = 2


async def _watch(page: Any, clicked_in: Any = None) -> list[Any]:
    frames = list(page.frames[:MAX_FRAMES])
    # The clicked control's own frame always, even past the cap (look reads shown frames beyond it).
    if clicked_in is not None and clicked_in not in frames:
        frames.append(clicked_in)
    for f in frames:
        try:
            await asyncio.wait_for(f.evaluate(WATCH_START_JS), FRAME_READ_TIMEOUT_S)
        except Exception:  # noqa: BLE001, S112 - a frame mid-navigation: read as changed below
            continue
    return frames


async def _changed(page: Any, frames: list[Any], url: str, tabs: int) -> bool:
    """Whether the page moved after an action: a new address or tab, a frame that navigated, or any
    change to the page itself. When unsure, it changed (the safe side: no false "nothing happened")."""
    if page.url != url or len(page.context.pages) != tabs:
        return True
    for f in frames:
        try:
            seen = await asyncio.wait_for(f.evaluate(WATCH_READ_JS), FRAME_READ_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - navigated or detached: that is a change
            return True
        if seen != 0:
            return True
    return False


async def _save_proof(page: Any, gate: Gate) -> bool:
    if gate.save_proof is None:
        return False
    try:
        await gate.save_proof(await _capture(page, quality=70))
    except Exception:  # noqa: BLE001 - proof is extra: the application still went
        logger.warning("browser proof screenshot not saved", exc_info=True)
        return False
    return True


async def run(
    pool: BrowserPool,
    user_id: str,
    action: str,
    *,
    url: str = "",
    ref: str = "",
    text: str = "",
    on: bool = True,
    doc_kind: str = "",
    gate: Optional[Gate] = None,
    files: Optional[FileFn] = None,
    load_state: Optional[StateFn] = None,
    owner: str = "",
) -> dict[str, Any]:
    """Carry out one action for one user and return what the page looks like after it.

    ``owner`` is the caller's session label: while one session drives the user's browser, a call
    with another label is refused (``busy``), so two assistant runs never fill one form."""
    if action not in ACTIONS:
        raise BrowserError("refused", "bad_action", "action must be one of: " + ", ".join(ACTIONS) + ".")
    if action == "close":
        closed = await pool.close(user_id)
        return {"status": "ok", "closed": closed}
    s = await pool.get(user_id, create=action == "open", load_state=load_state)
    if s is None:
        raise BrowserError("refused", "no_browser", "No browser is open. Start with action open and a url.")
    if owner:
        # Checked and claimed with no await between: two sessions cannot both pass.
        if s.owner and s.owner != owner:
            raise BrowserError(
                "busy", "browser_in_use",
                "Another assistant session is using this user's Job360 browser. Wait for it to finish (it is "
                f"closed after {max(1, int(pool.idle_s // 60))} idle minutes), or ask the user before you close "
                "it (action close).",
            )
        s.owner = owner
    async with s.lock:
        s.last_used = time.monotonic()
        try:
            result = await asyncio.wait_for(
                _act(pool, s, action, url=url, ref=ref, text=text, on=on, doc_kind=doc_kind, gate=gate, files=files),
                ACT_TIMEOUT_S,
            )
        except BrowserError as exc:
            if exc.status == "needs_you" and not exc.url:
                try:
                    exc.url = redact_url_for_display(_live_page(s).url)
                except BrowserError:
                    pass
            raise
        except asyncio.TimeoutError:
            raise BrowserError(
                "error", "timeout",
                f"That action took longer than {int(ACT_TIMEOUT_S)} seconds and was stopped. Call look to see "
                "the page.",
            ) from None
        except Exception as exc:  # noqa: BLE001 - a browser failure is shown to the assistant, never a 500
            if getattr(exc, "status_code", None) is not None:
                raise  # an HTTP refusal from the gate (e.g. not the caller's application) stays one
            first = _scrub_links((str(exc).splitlines() or [type(exc).__name__])[0])[:300]
            raise BrowserError("error", "browser_error", first) from None
        s.last_used = time.monotonic()
        if action not in ("click", "look"):
            # The form changed (an answer typed, a box ticked, a new page): a control that did nothing
            # before may work now, so it gets its tries back.
            s.dead_clicks.clear()
        s.recent = ([*s.recent, (action, ref, text, url)])[-REPEAT_LIMIT:]
        if len(s.recent) == REPEAT_LIMIT and len(set(s.recent)) == 1 and action not in ("look", "scroll"):
            result["repeat_warning"] = (
                f"The same {action} {REPEAT_LIMIT} times in a row. Stop repeating it: look again, try another "
                "control, or ask_user."
            )
        return result


# Roles a click ticks, like a checkbox or radio input.
_TICK_ROLES = ("checkbox", "radio", "switch")


async def _act(
    pool: BrowserPool, s: Session, action: str, *, url: str, ref: str, text: str, on: bool, doc_kind: str,
    gate: Optional[Gate], files: Optional[FileFn],
) -> dict[str, Any]:
    if action == "open":
        verdict = await pool.screen.screen(url)
        if not verdict.ok:
            raise BrowserError(
                "refused", "blocked_address", f"Job360's browser only opens public web pages ({verdict.reason})."
            )
        try:
            page = _live_page(s)
        except BrowserError:
            # Every tab was closed: open a fresh one, or "call open" would be a dead end.
            page = s.page = await s.context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001 - shown to the assistant in one line
            first = _scrub_links((str(exc).splitlines() or [type(exc).__name__])[0])[:200]
            raise BrowserError("error", "load_failed", f"The page did not load: {first}") from None
        await _settle(page)
        if (refusal := _refusal(s, page)) is not None:
            raise refusal
        return await look(s)
    if action == "look":
        return await look(s)
    if action == "back":
        page = _live_page(s)
        try:
            await page.go_back()
        except Exception as exc:
            # The history entry is a private address: the request screen (route) refused it.
            if "ERR_BLOCKED_BY_CLIENT" in str(exc):
                raise _hop_refused() from None
            raise
        await _settle(page)
        if (refusal := _refusal(s, page)) is not None:
            raise refusal
        return await look(s, after_action=True)
    # A CAPTCHA stops the run: past this point every action could work the check itself.
    if await _page_captcha(_live_page(s)):
        raise BrowserError("needs_you", "captcha", CAPTCHA_DETAIL)
    if action == "key":
        return await _press(s, ref, text)
    if action == "scroll":
        if text not in ("down", "up"):
            raise BrowserError("refused", "bad_scroll", 'scroll needs text "down" or "up".')
        page = _live_page(s)
        await _wheel(page, 800 if text == "down" else -800)
        await _settle(page)
        return await look(s, after_action=True)

    frame, loc = await _find(s, ref)
    _refuse_captcha_frame(frame)
    kind = await _kind(loc)
    page = _live_page(s)

    # A trap field for bots says to leave it blank: never fill, pick, tick or upload into it, and never
    # click it (a click ticks a trap checkbox or radio).
    fills = action in ("choose", "upload", "click") or (action == "type" and text) or (action == "tick" and on)
    facts = await loc.evaluate(ELEMENT_FACTS_JS) if fills else {}
    if action == "click" and not (
        facts.get("tag") in ("input", "textarea", "select") or str(facts.get("role")).lower() in _TICK_ROLES
    ):
        fills = False
    if fills and is_honeypot(str(facts.get("name") or "")):
        raise BrowserError(
            "refused", "leave_blank", "That field says to leave it blank (a trap for bots). Leave it empty.",
        )

    if action == "click":
        submitted = False
        if s.dead_clicks.get(ref, 0) >= DEAD_CLICK_LIMIT:
            raise BrowserError(
                "refused", "no_effect",
                f"Clicking that changed nothing {DEAD_CLICK_LIMIT} times. Do not click it again blind: look, "
                "scroll, try another control, or ask_user.",
            )
        app_id = gate.application_id if gate is not None else None
        if kind == "submit" and s.submitted_on == (app_id, page.url) and not s.submit_refused:
            raise BrowserError(
                "refused", "already_submitted",
                "A final submit was already clicked on this page. Never submit twice: read the page; if "
                "nothing confirms it went, record blocked and tell the user.",
            )
        if kind == "submit":
            if gate is None:
                raise BrowserError(
                    "refused", "submit_needs_application",
                    "This looks like the final submit. Call again with application_id (and your session) so "
                    "Job360 can run check_submit first.",
                )
            answer = await gate.check(page.url)
            if answer.get("decision") != "submit":
                return {
                    "status": "refused", "reason": "check_submit", "decision": answer.get("decision"),
                    "gate_reason": answer.get("reason"), "detail": answer.get("detail"),
                }
            await gate.mark_attempt()
            submitted = True
        url, tabs = page.url, len(page.context.pages)
        try:
            before = await loc.evaluate(CONTROL_STATE_JS)
        except Exception:  # noqa: BLE001 - unreadable: compared as changed
            before = None
        frames = await _watch(page, frame)
        try:
            opens_tab = bool(await loc.evaluate(OPENS_NEW_TAB_JS, timeout=1000))
        except Exception:  # noqa: BLE001 - unreadable: no wait for a tab
            opens_tab = False
        if submitted:
            # Set before the click: a click that fails after it reached the page still counts as one.
            s.submitted_on, s.submit_refused = (app_id, url), False
        await loc.click()
        await _settle(page)
        await _settle_new_tab(s, page, tabs, opens_tab)
        changed = await _changed(page, frames, url, tabs)
        if not changed:
            try:
                changed = before is None or await loc.evaluate(CONTROL_STATE_JS, timeout=1000) != before
            except Exception:  # noqa: BLE001 - the control is gone: the page changed
                changed = True
        if changed:
            s.dead_clicks.clear()
        else:
            s.dead_clicks[ref] = s.dead_clicks.get(ref, 0) + 1
        try:
            # The proxy's refusal is never read as the page (a submit that went still says so below).
            out = refusal.as_dict() if (refusal := _refusal(s, s.page)) else await look(s, after_action=True)
        except Exception as exc:  # noqa: BLE001 - after a submit, any failure to read must still say it went
            if not submitted:
                raise
            out = exc.as_dict() if isinstance(exc, BrowserError) else {
                "status": "error", "reason": "browser_error",
                "detail": _scrub_links((str(exc).splitlines() or [type(exc).__name__])[0])[:300],
            }
        out["page_changed"] = changed
        if not changed and not submitted:
            out["next"] = (
                "That click changed nothing on the page. Do not click it again: look, scroll or try another control."
            )
        if submitted:
            s.submit_refused = bool(out.get("site_refused"))
            out["submit_clicked"] = True
            if out.get("status") == "error" and out.get("reason") == "load_failed":
                out["next"] = (
                    "The page after the submit did not load. Do NOT submit again: record blocked and tell the user."
                )
            elif out.get("status") == "error":
                # Often the confirmation page still loading: it may have gone through.
                out["next"] = (
                    "The page after the submit could not be read yet. Do NOT submit again: call look; a "
                    "confirmation means record_application, anything else means record blocked and tell the user."
                )
            elif out.get("site_refused"):
                out["next"] = (
                    "The site says it did NOT take the application (site_refused). Read the page text for "
                    "which fields, and fix them. check_submit holds a second submit until the user marks this "
                    "one 'not sent' on Job360 (Needs you): ask them to, then submit again."
                )
            elif out.get("confirmation_seen"):
                out["next"] = "The site shows a confirmation: call record_application."
                # The tab look just read: a confirmation can open in a new tab.
                out["proof_saved"] = gate is not None and await _save_proof(_live_page(s), gate)
            else:
                out["next"] = (
                    "No confirmation on the page yet. Read it. Do NOT click submit again; if nothing "
                    "confirms it, record blocked and tell the user."
                )
        return out

    if action == "type":
        if kind == "password":
            raise BrowserError(
                "refused", "password_field",
                "That is a password box. Job360 never types a password: the user signs in themselves.",
            )
        if len(text) > MAX_TYPE_CHARS:
            raise BrowserError("refused", "text_too_long", f"Type at most {MAX_TYPE_CHARS} characters at once.")
        # Windows line breaks are line breaks: "\r\n" and a lone "\r" become "\n" before the check.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if _CONTROL_CHARS.search(text.replace("\n", "")) or (
            "\n" in text and not await loc.evaluate("(el) => el.tagName === 'TEXTAREA'")
        ):
            raise BrowserError(
                "refused", "control_characters",
                "Type plain text only: line breaks only into a multi-line box, never tabs or other control keys.",
            )
        typed = await _type_into(loc, text)
        out = await look(s, after_action=True)
        out["typed_matches"] = typed == text
        return out

    if kind == "submit" and action in ("choose", "tick", "upload"):
        # These click too; a final submit is only ever clicked through the gate (CodeRabbit on #915).
        raise BrowserError(
            "refused", "submit_control",
            "That is the form's final submit. Use click with application_id, so check_submit runs first.",
        )

    if action == "choose":
        await _choose(s, loc, text)
        return await look(s, after_action=True)

    if action == "tick":
        try:
            await loc.set_checked(on)
        except Exception:  # noqa: BLE001 - a hidden input behind a styled label: click the label instead
            state = await loc.evaluate(TICK_VIA_LABEL_JS, on)
            if state != on:
                raise BrowserError(
                    "error", "tick_failed", "That box did not change. Call look and click its label instead."
                ) from None
        return await look(s, after_action=True)

    # action == "upload"
    if doc_kind not in ("cv", "cover_letter"):
        raise BrowserError("refused", "bad_doc_kind", 'doc_kind must be "cv" or "cover_letter".')
    if files is None:
        raise BrowserError(
            "refused", "upload_needs_application", "Uploads need application_id: Job360 sends the CV saved for it."
        )
    name, data = await files(doc_kind)
    page_before, near_before = await _upload_texts(frame, loc)
    is_file_input = (await loc.evaluate("(el) => el.tagName === 'INPUT' && el.type === 'file'"))
    with tempfile.TemporaryDirectory(prefix="j360-upload-") as tmp:
        path = os.path.join(tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        if is_file_input:
            await loc.set_input_files(path)
        else:
            # An "Attach" button with no reachable file box: catch the file picker it opens
            # (Playwright filechooser; the Greenhouse fix in ApplYourself src/apply/fill.py:450-509).
            try:
                async with page.expect_file_chooser(timeout=ACTION_TIMEOUT_MS) as chooser_info:
                    await loc.click()
                chooser = await chooser_info.value
            except Exception:  # noqa: BLE001 - the button opened no file picker
                raise BrowserError(
                    "refused", "not_an_upload",
                    "That control did not open a file picker. Use the file box or the Attach button.",
                ) from None
            await chooser.set_files(path)
    await _settle(page)
    page_after, near_after = await _upload_texts(frame, loc)
    out = await look(s, after_action=True)
    # A site can take the file into the box and still show an error (Greenhouse): believe only the file name
    # the site newly drew on the page, and never over an error that appeared next to the field.
    new_lines = [ln.strip() for ln in near_after.splitlines() if ln.strip() and ln.strip() not in near_before]
    error = next((ln for ln in new_lines if _UPLOAD_ERROR.search(ln)), "")
    shown_before = name in page_before or name in near_before
    out["upload_confirmed"] = not error and not shown_before and (name in page_after or name in near_after)
    if error:
        out["upload_error"] = _wrap_untrusted(error[:200])
    return out


_UPLOAD_ERROR = re.compile(r"failed|error|try again|too large|not supported", re.IGNORECASE)


async def _upload_texts(frame: Any, loc: Any) -> tuple[str, str]:
    """The field's frame text and the text of its own small box; "" for one that cannot be read."""
    out = []
    for target, js in ((frame, PAGE_TEXT_JS), (loc, NEAR_TEXT_JS)):
        try:
            out.append(str(await asyncio.wait_for(target.evaluate(js), FRAME_READ_TIMEOUT_S) or ""))
        except Exception:  # noqa: BLE001 - re-drawn or navigated mid-read: nothing seen
            out.append("")
    return out[0], out[1]


# ── The live view: the USER signs in themselves (slice 2) ─────────────────────
#
# Reached only from the Job360 website with a browser session, never a token
# (routes/browser.py uses require_session_user): an assistant can never drive
# these. What the user types goes straight to the page and is never logged,
# stored or returned. Keep = save what the SITE chose to remember (sign_ins.py).
# The live view is its own browser (live_key), never the one the assistant's
# token drives: the assistant cannot see the sign-in, and the user cannot take
# over or close the assistant's half-filled form.

LIVE_ACTIONS = ("start", "shot", "click", "type", "key", "scroll", "keep", "cancel")
LIVE_KEYS = frozenset({"Enter", "Tab", "Backspace", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"})
# A live view closes this long after Start, however busy: a tab left open cannot hold a browser forever.
LIVE_MAX_SECONDS = 15 * 60


def live_key(user_id: str) -> str:
    """The pool slot of a user's live sign-in view (it counts against ``max_live``, not ``max_sessions``)."""
    return f"{LIVE_PREFIX}{user_id}"
# The site the user started signing in on, per user: Keep saves it (and where the sign-in ended up).
_live_hosts: dict[str, str] = {}


SHOT_TRIES = 3


async def _capture(page: Any, *, quality: int) -> bytes:
    """A JPEG of the page. Taken while a navigation commits (a sign-in form that submits and reloads on
    Enter), Chromium answers "Unable to capture screenshot": wait for the new page's HTML, then try
    again, a bounded number of times; the last try's failure is raised as it is."""
    for attempt in range(SHOT_TRIES - 1):
        try:
            image: bytes = await page.screenshot(type="jpeg", quality=quality)
            return image
        except Exception:  # noqa: BLE001 - mid-navigation: wait for the new page, then the next try
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=ACTION_TIMEOUT_MS)
            except Exception:  # noqa: BLE001, S110 - still loading: the next try says so
                pass
            await asyncio.sleep(0.1 * (attempt + 1))
    last: bytes = await page.screenshot(type="jpeg", quality=quality)
    return last


async def _shot(s: Session) -> dict[str, Any]:
    import base64  # noqa: PLC0415

    page = _live_page(s)
    png = await _capture(page, quality=60)
    return {
        "status": "ok", "url": _clip(page.url, MAX_URL), "title": _clip(await page.title(), MAX_TITLE),
        "width": VIEWPORT["width"], "height": VIEWPORT["height"],
        "image": "data:image/jpeg;base64," + base64.b64encode(png).decode(),
    }


async def live(
    pool: BrowserPool,
    user_id: str,
    action: str,
    *,
    url: str = "",
    x: float = 0,
    y: float = 0,
    text: str = "",
    key: str = "",
    dy: float = 0,
    load_state: Optional[StateFn] = None,
    keep: Optional[Callable[[str, dict[str, Any]], Awaitable[int]]] = None,
) -> dict[str, Any]:
    """One step of the user's own sign-in, returning a fresh picture of the page."""
    if action not in LIVE_ACTIONS:
        raise BrowserError("refused", "bad_action", "action must be one of: " + ", ".join(LIVE_ACTIONS) + ".")
    if action == "cancel":
        _live_hosts.pop(user_id, None)
        return {"status": "ok", "closed": await pool.close(live_key(user_id))}
    s = await pool.get(live_key(user_id), create=action == "start", load_state=load_state)
    if s is None:
        raise BrowserError("refused", "no_browser", "Start again: type the job site's address and press Start.")
    if s.closes_at is None or action == "start":  # each Start is a new sign-in: its full time
        s.closes_at = time.monotonic() + LIVE_MAX_SECONDS
    # The page refreshes its picture every 1.5 s: only the user's own steps keep the view open.
    by_user = action != "shot"
    async with s.lock:
        if by_user:
            s.last_used = time.monotonic()
        page = _live_page(s)
        try:
            if action == "start":
                verdict = await pool.screen.screen(url)
                if not verdict.ok:
                    raise BrowserError(
                        "refused", "blocked_address", f"Only public web pages can be opened ({verdict.reason})."
                    )
                from src.services.browser_agent.sign_ins import site_host  # noqa: PLC0415

                _live_hosts[user_id] = site_host(url)
                await page.goto(url, wait_until="domcontentloaded")
            elif action == "click":
                if not (0 <= x <= VIEWPORT["width"] and 0 <= y <= VIEWPORT["height"]):
                    raise BrowserError("refused", "off_page", "That click is outside the page.")
                await page.mouse.click(x, y)
                await _settle(page)
            elif action == "type":
                if len(text) > MAX_TYPE_CHARS:
                    raise BrowserError("refused", "text_too_long", f"Type at most {MAX_TYPE_CHARS} characters.")
                await page.keyboard.type(text)
            elif action == "key":
                if key not in LIVE_KEYS:
                    raise BrowserError("refused", "bad_key", "key must be one of: " + ", ".join(sorted(LIVE_KEYS)))
                await page.keyboard.press(key)
                await _settle(page)
            elif action == "scroll":
                await page.mouse.wheel(0, max(-3000.0, min(3000.0, dy)))
            elif action == "keep":
                if keep is None:
                    raise BrowserError("refused", "cannot_keep", "Sign-ins cannot be kept here.")
                from src.services.browser_agent.sign_ins import site_host, state_for_host  # noqa: PLC0415

                state = await s.context.storage_state()
                hosts = {h for h in (_live_hosts.get(user_id, ""), site_host(page.url)) if h}
                if not hosts:
                    raise BrowserError("refused", "no_site", "Open the job site first.")
                kept = {h: await keep(h, state) for h in sorted(hosts)}
                # The assistant's browser, if open, loaded the saved sign-ins before this one: hand it the cookies.
                agent_s = pool._sessions.get(user_id)
                cookies = [c for h in sorted(hosts) for c in state_for_host(state, h)["cookies"]]
                if agent_s is not None and cookies:
                    try:
                        async with agent_s.lock:
                            await agent_s.context.add_cookies(cookies)
                    except Exception:  # noqa: BLE001 - closed meanwhile: its next browser loads them anyway
                        logger.debug("browser_agent could not hand the sign-in over", exc_info=True)
                out = await _shot(s)
                out["kept"] = kept
                return out
            return await _shot(s)
        except BrowserError:
            raise
        except Exception as exc:  # noqa: BLE001 - shown to the user in one line
            first = (str(exc).splitlines() or [type(exc).__name__])[0][:200]
            raise BrowserError("error", "browser_error", first) from None
        finally:
            if by_user:
                s.last_used = time.monotonic()


# ── The process-wide pool ─────────────────────────────────────────────────────

_pool: Optional[BrowserPool] = None


def get_pool() -> BrowserPool:
    """The one pool for this process, built from the live settings on first use."""
    global _pool
    if _pool is None:
        from src.core import settings  # noqa: PLC0415 - live read, house style
        from src.services.fetch.fetcher import DEFAULT_RESOLVE  # noqa: PLC0415

        _pool = BrowserPool(
            max_sessions=settings.BROWSER_AGENT_MAX_SESSIONS,
            max_live=settings.BROWSER_AGENT_MAX_LIVE,
            idle_s=settings.BROWSER_AGENT_IDLE_SECONDS,
            resolve=DEFAULT_RESOLVE,
        )
    return _pool


async def reap_forever(every_s: float = REAP_EVERY_S) -> None:
    """Close idle browsers on a clock, not only on the next request (the app's lifespan runs this)."""
    while True:
        await asyncio.sleep(every_s)
        try:
            await get_pool().reap()
        except Exception:  # noqa: BLE001 - one failed sweep must not end the sweeping
            logger.warning("browser_agent idle sweep failed", exc_info=True)


async def shutdown_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.shutdown()
        _pool = None

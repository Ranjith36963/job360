"use client";

import { useCallback, useEffect, useRef, useState, type MouseEvent, type PointerEvent } from "react";
import { createPortal } from "react-dom";
import { Loader2 } from "lucide-react";

import { browserLive, type BrowserLiveAction, type BrowserLiveShot } from "@/lib/api";
import { toast } from "@/lib/toast";

const BTN =
  "rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50";
const REFRESH_MS = 1500;
// A finger that moves less than this (px) is a tap; a mostly-vertical move of at least SWIPE_PX is a swipe.
const TAP_SLOP_PX = 10;
const SWIPE_PX = 24;

/**
 * Finish it here: the hand-off view (docs/product/BROWSER_AGENT.md). Job360's browser stopped on a human
 * check (a CAPTCHA) on a form it filled; the person solves it THEMSELVES on a live picture of that same
 * browser, so nothing is typed again. Website only: an assistant's token can never drive it. Done hands
 * the browser back to its idle clock; then "I submitted it" records the receipt. Each job has its own
 * browser: `applicationId` names whose form this is.
 * Maximize opens the picture full-screen (a fixed overlay) for a phone: a tap clicks at that point (mapped
 * from the shown size to the page's real size), a vertical swipe scrolls the page, Back goes back one page
 * in the browser's history (never to a new address). `startMaximized` opens it full-screen at once.
 */
export function FinishInBrowser({
  applicationId,
  onDone,
  startMaximized = false,
}: {
  applicationId: number;
  onDone: () => void;
  startMaximized?: boolean;
}) {
  const [shot, setShot] = useState<BrowserLiveShot | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [gone, setGone] = useState("");
  const [maximized, setMaximized] = useState(startMaximized);
  const busyRef = useRef(false);
  const touch = useRef<{ x: number; y: number } | null>(null);
  const swiped = useRef(false);

  const step = useCallback(async (s: BrowserLiveAction, quiet = false) => {
    if (busyRef.current) return null;
    busyRef.current = true;
    if (!quiet) setBusy(true);
    try {
      const res = await browserLive(s, "finish", applicationId);
      if (res.status === "ok" && res.image) setShot(res);
      else if (res.reason === "no_form") {
        setShot(null);
        setGone(res.detail || "The form has closed.");
      } else if (res.status !== "ok" && !quiet) toast.error(res.detail || "The browser did not answer.");
      return res;
    } catch (err) {
      if (!quiet) toast.apiError(err, "The browser did not answer.");
      return null;
    } finally {
      busyRef.current = false;
      if (!quiet) setBusy(false);
    }
  }, [applicationId]);

  // The first picture, fetched from a timer callback (not in the effect body: react-hooks/set-state-in-effect).
  useEffect(() => {
    const id = window.setTimeout(() => void step({ action: "shot" }), 0);
    return () => window.clearTimeout(id);
  }, [step]);

  // Keep the picture fresh (the check changes on its own), not while this tab is hidden.
  useEffect(() => {
    if (!shot) return;
    const id = window.setInterval(() => {
      if (document.visibilityState !== "hidden") void step({ action: "shot" }, true);
    }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [shot, step]);

  // Full-screen: Esc closes it, and the page behind it stops scrolling.
  useEffect(() => {
    if (!maximized) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMaximized(false);
    };
    window.addEventListener("keydown", onKey);
    const before = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = before;
    };
  }, [maximized]);

  function clickPicture(e: MouseEvent<HTMLButtonElement>) {
    // A keyboard "click" has no position (detail 0): it would click the page's top-left corner.
    if (swiped.current) {
      swiped.current = false; // the click that ends a swipe is not a tap
      return;
    }
    if (!shot?.width || !shot?.height || e.detail === 0) return;
    const box = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - box.left) / box.width) * shot.width;
    const y = ((e.clientY - box.top) / box.height) * shot.height;
    void step({ action: "click", x: Math.round(x), y: Math.round(y) });
  }

  function pointerDown(e: PointerEvent<HTMLButtonElement>) {
    touch.current = { x: e.clientX, y: e.clientY };
    swiped.current = false;
  }

  function pointerUp(e: PointerEvent<HTMLButtonElement>) {
    const from = touch.current;
    touch.current = null;
    if (!from || !shot?.height) return;
    const dx = e.clientX - from.x;
    const dy = e.clientY - from.y;
    if (Math.abs(dy) < SWIPE_PX || Math.abs(dy) < Math.abs(dx) * 1.5 || Math.hypot(dx, dy) < TAP_SLOP_PX) return;
    // Dragging the picture up moves the page down, like a finger on the page. Distance is in page pixels.
    const shown = e.currentTarget.getBoundingClientRect().height;
    const scale = shown > 0 ? shot.height / shown : 1;
    swiped.current = true;
    void step({ action: "scroll", dy: Math.max(-3000, Math.min(3000, Math.round(-dy * scale))) });
  }

  async function done() {
    // A background refresh may be in flight (step drops a call while busy): wait it out, so Done is never
    // silently dropped and the browser's 30-minute hold always ends.
    for (let i = 0; busyRef.current && i < 50; i++) await new Promise((r) => window.setTimeout(r, 100));
    const res = await step({ action: "done" });
    if (res?.status === "ok" || res?.reason === "no_form") onDone();
  }

  if (gone) {
    return (
      <p data-testid="finish-gone" className="text-xs text-muted-foreground">
        {gone}
      </p>
    );
  }
  if (!shot) {
    return <Loader2 data-testid="finish-loading" className="h-4 w-4 animate-spin" />;
  }
  const big = maximized;
  const bar = "flex flex-wrap items-center gap-2";
  const view = (
    <div
      data-testid={big ? "live-fullscreen" : "finish-view"}
      {...(big ? { role: "dialog", "aria-modal": true, "aria-label": "Job360's browser, full screen" } : {})}
      className={big ? "fixed inset-0 z-50 flex flex-col gap-2 overflow-hidden bg-background p-2" : "flex flex-col gap-2"}
      style={
        big
          ? {
              paddingTop: "max(0.5rem, env(safe-area-inset-top))",
              paddingRight: "max(0.5rem, env(safe-area-inset-right))",
              paddingBottom: "max(0.5rem, env(safe-area-inset-bottom))",
              paddingLeft: "max(0.5rem, env(safe-area-inset-left))",
            }
          : undefined
      }
    >
      {!big && (
        <p className="text-xs text-muted-foreground">
          Your filled form, live: click on the picture where you would click (solve a check, or press Submit).
        </p>
      )}
      <div className={bar}>
        <button type="button" data-testid="live-back" className={BTN} disabled={busy} onClick={() => void step({ action: "back" })}>
          Back
        </button>
        {big ? (
          <button
            type="button"
            data-testid="live-minimize"
            className={BTN}
            aria-label="Minimize the live view"
            onClick={() => setMaximized(false)}
          >
            Minimize
          </button>
        ) : (
          <button
            type="button"
            data-testid="live-maximize"
            className={BTN}
            aria-label="Maximize the live view"
            onClick={() => setMaximized(true)}
          >
            Maximize
          </button>
        )}
      </div>
      <div className={big ? "min-h-0 flex-1 overflow-auto" : undefined}>
        <button
          type="button"
          className="block w-full cursor-pointer touch-none p-0"
          aria-label={`Job360's browser showing ${shot.title || "the form"}. Click where you would click.`}
          onClick={clickPicture}
          onPointerDown={pointerDown}
          onPointerUp={pointerUp}
          onPointerCancel={() => (touch.current = null)}
        >
          {/* eslint-disable-next-line @next/next/no-img-element -- a live data: URL, not a static asset */}
          <img src={shot.image} alt="" draggable={false} className="w-full select-none rounded-md border border-border" />
        </button>
      </div>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!text) return;
          const typed = text;
          setText("");
          void step({ action: "type", text: typed });
        }}
      >
        <input
          aria-label="Type into the box you clicked"
          autoComplete="off"
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="flex-1 rounded-md border border-border bg-background px-2 py-1 text-base sm:text-xs"
        />
        <button type="submit" className={BTN} disabled={busy || !text}>
          Send
        </button>
      </form>
      <div className={bar}>
        {(["Enter", "Tab"] as const).map((k) => (
          <button key={k} type="button" className={BTN} disabled={busy} onClick={() => void step({ action: "key", key: k })}>
            {k}
          </button>
        ))}
        <button type="button" className={BTN} disabled={busy} onClick={() => void step({ action: "scroll", dy: -500 })}>
          Scroll up
        </button>
        <button type="button" className={BTN} disabled={busy} onClick={() => void step({ action: "scroll", dy: 500 })}>
          Scroll down
        </button>
        <button type="button" data-testid="finish-done" className={BTN} disabled={busy} onClick={() => void done()}>
          Done
        </button>
      </div>
    </div>
  );
  return big ? createPortal(view, document.body) : view;
}

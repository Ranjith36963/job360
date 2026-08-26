"use client";

/**
 * ConsentBanner — analytics consent prompt (docs/fable/05 C3).
 *
 * Shown only while the user has made NO choice. Until they accept, PostHog is
 * never initialised (see PostHogProviderWrapper), so this is a real gate rather
 * than cosmetic theatre.
 *
 * PECR/ICO expectations this deliberately meets:
 *   - "Decline" is exactly as prominent and as easy as "Accept" (one click, same
 *     visual weight). No dark-pattern where reject is buried.
 *   - Nothing is set before the choice — no pre-ticked "accept" default.
 *   - The choice is remembered so we don't nag on every page.
 */

import { useEffect, useRef, useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { getConsent, setConsent, subscribeConsent } from "@/lib/consent";

// Server-snapshot sentinel: on SSR/first hydration paint we don't know the
// choice yet, so render NOTHING (never flash the banner at someone who already
// decided). After mount the real snapshot (getConsent) takes over.
const UNHYDRATED = "unhydrated" as const;

export function ConsentBanner() {
  // Consent lives in an external store (localStorage + a window event), so
  // useSyncExternalStore is the idiomatic subscription — no setState-in-effect.
  const consent = useSyncExternalStore(
    subscribeConsent,
    getConsent,
    () => UNHYDRATED,
  );

  // The banner is position:fixed, so it sits ON TOP of the page instead of
  // pushing it. Nothing reserved room for it, and at 390x844 its ~200px height
  // landed squarely on the landing page's only call to action — a first-time
  // mobile visitor could not see or tap "Get Started" until they dismissed it.
  //
  // Reserving the height on <body> keeps the banner pinned (which is correct)
  // while guaranteeing the last of the page can always be scrolled clear of it.
  // Measured rather than hardcoded because the copy wraps to a different number
  // of lines at every width.
  const bannerRef = useRef<HTMLDivElement>(null);
  const visible = consent === null;

  useEffect(() => {
    if (!visible) return;
    const el = bannerRef.current;
    if (!el) return;

    const apply = () => {
      document.body.style.paddingBottom = `${el.offsetHeight}px`;
    };
    apply();

    // The height changes on rotate/resize as the paragraph reflows. Feature-
    // detected: jsdom (the unit-test environment) has no ResizeObserver, and an
    // unguarded `new ResizeObserver` throws during mount and takes the whole
    // component down with it.
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", apply);
      return () => {
        window.removeEventListener("resize", apply);
        document.body.style.paddingBottom = "";
      };
    }

    const observer = new ResizeObserver(apply);
    observer.observe(el);
    return () => {
      observer.disconnect();
      document.body.style.paddingBottom = "";
    };
  }, [visible]);

  // Show only to a hydrated visitor with NO choice yet.
  if (!visible) return null;

  return (
    <div
      ref={bannerRef}
      role="dialog"
      aria-modal="false"
      aria-label="Analytics cookie consent"
      className="fixed inset-x-0 bottom-0 z-50 border-t bg-background/95 p-4 backdrop-blur supports-[backdrop-filter]:bg-background/80"
    >
      <div className="mx-auto flex max-w-4xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted-foreground">
          We&apos;d like to use analytics to understand how Job360 is used and improve
          it. This is optional — Job360 works exactly the same either way, and we
          never record your session or your CV content.
        </p>
        <div className="flex shrink-0 gap-2">
          {/* Same size + weight as Accept: rejecting must be no harder. */}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setConsent("declined")}
          >
            Decline
          </Button>
          <Button size="sm" onClick={() => setConsent("accepted")}>
            Accept
          </Button>
        </div>
      </div>
    </div>
  );
}

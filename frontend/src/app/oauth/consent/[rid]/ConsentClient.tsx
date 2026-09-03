"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  decideConsent,
  getConsentRequest,
  type ConsentRequest,
} from "@/lib/api";
import { ApiError, apiErrorMessage } from "@/lib/api-error";
import { Button } from "@/components/ui/button";

// ---------------------------------------------------------------------------
// The consent decision. Re-fetches on mount and on window focus so a request
// that expires while the tab sits idle (the client's own retry, a slow
// email round trip) shows the expired copy instead of a stale Allow button
// that would 404 when clicked (spec R9).
// ---------------------------------------------------------------------------

type State = "loading" | "ready" | "expired" | "error" | "deciding";

export function ConsentClient({ rid }: { rid: string }) {
  const [state, setState] = useState<State>("loading");
  const [consent, setConsent] = useState<ConsentRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Mirror of `state` the focus listener can read without re-registering.
  const stateRef = useRef<State>("loading");
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  // Fetch on mount and again whenever the tab regains focus, so a request
  // that expires while idle (a slow email round trip, a stale background
  // tab) shows the expired copy instead of a dead Allow button — same
  // mount-effect + `cancelled` guard shape as verify-email/page.tsx.
  useEffect(() => {
    let cancelled = false;

    async function load() {
      // A focus event while the decision POST is in flight must not overwrite
      // "deciding" with "ready" (or "expired", once the rid is consumed).
      if (stateRef.current === "deciding") return;
      try {
        const data = await getConsentRequest(rid);
        if (cancelled) return;
        setConsent(data);
        setState("ready");
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.isNotFound) {
          setState("expired");
          return;
        }
        setError(apiErrorMessage(err, "Could not load this request."));
        setState("error");
      }
    }

    load();
    window.addEventListener("focus", load);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", load);
    };
  }, [rid]);

  async function onDecision(approve: boolean) {
    setState("deciding");
    try {
      const { redirect_to } = await decideConsent(rid, approve);
      // A real navigation, not a router push — the browser is being handed
      // back to the client app (spec S8: the backend never 303s to a
      // client-supplied URL itself, but the browser navigating on the
      // frontend's own say-so is fine and is how `redirect_to` is meant to
      // be used).
      window.location.assign(redirect_to);
    } catch (err) {
      // The request may have expired or been consumed between load and
      // decide (double-click, stale tab) — the decision endpoint 404s the
      // same way the GET does (spec R4), so treat it the same way.
      if (err instanceof ApiError && err.isNotFound) {
        setState("expired");
        return;
      }
      toast.error(apiErrorMessage(err, "Could not complete this request."));
      setState("ready");
    }
  }

  if (state === "loading") {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  if (state === "expired") {
    return (
      <p className="text-sm" data-testid="consent-expired">
        This request has expired. Go back to the app and try again.
      </p>
    );
  }

  if (state === "error" || !consent) {
    return (
      <p className="text-sm text-red-400" role="alert">
        {error ?? "Something went wrong. Please try again."}
      </p>
    );
  }

  const deciding = state === "deciding";

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <p className="text-sm">
          <span
            data-testid="consent-client-name"
            className="font-medium text-foreground"
          >
            {consent.client_name}
          </span>{" "}
          wants to connect to your Job360 account.
        </p>
        <p className="text-xs text-muted-foreground">
          This app registered itself with Job360 — its name is not verified.
        </p>
      </div>

      <div className="space-y-1 text-sm">
        <p className="text-muted-foreground">You will be sent back to:</p>
        <p
          data-testid="consent-redirect-uri"
          className="break-all font-mono text-xs"
        >
          {consent.redirect_uri}
        </p>
      </div>

      <p className="text-sm text-muted-foreground" data-testid="consent-user-email">
        Signed in as {consent.user_email}
      </p>

      <p className="text-sm">{consent.scope_description}</p>

      <div className="flex gap-2">
        <Button
          type="button"
          onClick={() => onDecision(true)}
          disabled={deciding}
          data-testid="consent-allow"
        >
          Allow
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => onDecision(false)}
          disabled={deciding}
          data-testid="consent-deny"
        >
          Deny
        </Button>
      </div>
    </div>
  );
}

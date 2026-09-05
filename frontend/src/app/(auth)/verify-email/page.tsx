"use client";

// Phase −2 item B — email verification landing page.
// Reached from the link in the verification email. Reads ?token=… from
// the URL and posts to /api/auth/verify-email/confirm.
//
// Unlike reset-password this is not a form — the user only needs to land
// on the page; confirmation fires automatically. We show a small spinner
// while the request is in flight and then a success / failure card.

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";

import { confirmEmailVerification } from "@/lib/api";
import { friendlyAuthError } from "@/lib/api-error";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

type State = "pending" | "ok" | "error";

function VerifyBody() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";
  // Derive the missing-token error from initial state instead of calling
  // setState synchronously inside the effect (react-hooks/set-state-in-effect).
  const [state, setState] = useState<State>(token ? "pending" : "error");
  const [error, setError] = useState<string | null>(
    token ? null : "Verification token missing from URL. Use the link from your email."
  );

  useEffect(() => {
    if (!token) return; // missing-token case already reflected in initial state
    let cancelled = false;
    (async () => {
      try {
        await confirmEmailVerification(token);
        if (!cancelled) setState("ok");
      } catch (err) {
        if (!cancelled) {
          setState("error");
          // friendlyAuthError surfaces the backend's generic 400 detail (or a
          // nice 429/500 message) WITHOUT the leaking "API error 400:" prefix.
          // See docs/fable/03.
          setError(
            friendlyAuthError(
              err,
              "Verification link is invalid or expired. Request a new one from your account settings.",
            ),
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (state === "pending") {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">Verifying…</p>
        <div className="h-2 animate-pulse rounded-md bg-muted" />
      </div>
    );
  }

  if (state === "ok") {
    return (
      <div className="space-y-4">
        <p className="text-sm">Your email is verified. You&apos;re all set.</p>
        <Button render={<Link href="/applications" />} className="w-full">
          Go to my applications
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-red-400">{error}</p>
      <Button render={<Link href="/applications" />} className="w-full" variant="secondary">
        Back to my applications
      </Button>
      <p className="text-center text-sm text-muted-foreground">
        Need a fresh link? Open your account settings to resend.
      </p>
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <div className="mx-auto max-w-md py-16">
      <Card>
        <CardHeader>
          <CardTitle>Email verification</CardTitle>
          <CardDescription>Confirming the address you registered with.</CardDescription>
        </CardHeader>
        <CardContent>
          <Suspense fallback={<div className="h-48 animate-pulse rounded-md bg-muted" />}>
            <VerifyBody />
          </Suspense>
        </CardContent>
      </Card>
    </div>
  );
}

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
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

type State = "pending" | "ok" | "error";

function VerifyBody() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [state, setState] = useState<State>("pending");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setState("error");
      setError("Verification token missing from URL. Use the link from your email.");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        await confirmEmailVerification(token);
        if (!cancelled) setState("ok");
      } catch (err) {
        if (!cancelled) {
          setState("error");
          setError(
            err instanceof Error
              ? err.message
              : "Verification link is invalid or expired. Request a new one from your account settings.",
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
        <Button asChild className="w-full">
          <Link href="/dashboard">Go to dashboard</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-red-400">{error}</p>
      <Button asChild className="w-full" variant="secondary">
        <Link href="/dashboard">Back to dashboard</Link>
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

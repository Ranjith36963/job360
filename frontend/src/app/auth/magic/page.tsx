"use client";

// Passwordless magic-link landing page.
// Reached from the link in the sign-in email. Reads ?token=… from the URL
// and posts to /api/auth/magic-link/consume ONLY when the user clicks the
// button. Consuming on mount would let corporate email scanners (which
// prefetch/render links before the human clicks) burn the single-use token
// and lock the real user out — scanners load pages, they don't click buttons.
// On success the backend sets the session cookie, so we redirect to
// /dashboard. On failure we show an error with a link back to /login.
//
// Mirrors the verify-email page pattern: a client component using
// useSearchParams inside <Suspense> (the Next.js 16 way to read query params
// in the browser — confirmed via Context7 /vercel/next.js).

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";

import { consumeMagicLink } from "@/lib/api";
import { friendlyAuthError } from "@/lib/api-error";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

type State = "confirm" | "submitting" | "error";

function MagicBody() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [state, setState] = useState<State>(token ? "confirm" : "error");
  const [error, setError] = useState<string | null>(
    token ? null : "Sign-in token missing from the URL. Use the link from your email."
  );

  async function signIn() {
    setState("submitting");
    try {
      await consumeMagicLink(token);
      router.replace("/dashboard");
    } catch (err) {
      setState("error");
      setError(
        friendlyAuthError(
          err,
          "This sign-in link is invalid or expired. Request a new one from the login page."
        )
      );
    }
  }

  if (state === "confirm" || state === "submitting") {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Click below to finish signing in to Job360.
        </p>
        <Button
          className="w-full"
          onClick={signIn}
          disabled={state === "submitting"}
        >
          {state === "submitting" ? "Signing you in…" : "Sign in to Job360"}
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-red-400">{error}</p>
      <Button render={<Link href="/login" />} className="w-full" variant="secondary">
        Back to login
      </Button>
    </div>
  );
}

export default function MagicLinkPage() {
  return (
    <div className="mx-auto max-w-md py-16">
      <Card>
        <CardHeader>
          <CardTitle>Sign in</CardTitle>
          <CardDescription>Completing your passwordless sign-in.</CardDescription>
        </CardHeader>
        <CardContent>
          <Suspense fallback={<div className="h-48 animate-pulse rounded-md bg-muted" />}>
            <MagicBody />
          </Suspense>
        </CardContent>
      </Card>
    </div>
  );
}

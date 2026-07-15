"use client";

// Passwordless magic-link landing page.
// Reached from the link in the sign-in email. Reads ?token=… from the URL
// and posts to /api/auth/magic-link/consume. On success the backend sets the
// session cookie, so we just redirect to /dashboard. On failure we show an
// error with a link back to /login.
//
// Mirrors the verify-email page pattern: a client component using
// useSearchParams inside <Suspense> (the Next.js 16 way to read query params
// in the browser — confirmed via Context7 /vercel/next.js).

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";

import { consumeMagicLink } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

type State = "pending" | "error";

function MagicBody() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [state, setState] = useState<State>(token ? "pending" : "error");
  const [error, setError] = useState<string | null>(
    token ? null : "Sign-in token missing from the URL. Use the link from your email."
  );

  useEffect(() => {
    if (!token) return; // missing-token case already reflected in initial state
    let cancelled = false;
    (async () => {
      try {
        await consumeMagicLink(token);
        if (!cancelled) router.replace("/dashboard");
      } catch (err) {
        if (!cancelled) {
          setState("error");
          setError(
            err instanceof Error
              ? err.message
              : "This sign-in link is invalid or expired. Request a new one from the login page."
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, router]);

  if (state === "pending") {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">Signing you in…</p>
        <div className="h-2 animate-pulse rounded-md bg-muted" />
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
          <CardTitle>Signing you in</CardTitle>
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

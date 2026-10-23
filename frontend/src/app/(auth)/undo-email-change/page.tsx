"use client";

// Gap roadmap slice 35 — "undo my email change" landing page.
// Reached via the link in the notice mailed to the OLD address. Reads ?token=…
// and posts {token} to /api/auth/email-change/undo. Unlike /verify-email this
// waits for a click: a mail scanner that loads the link must not undo (and sign
// everyone out) on its own.

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";

import { undoEmailChange } from "@/lib/api";
import { ApiError, friendlyAuthError } from "@/lib/api-error";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

type State = "idle" | "working" | "ok" | "error";

function UndoBody() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [state, setState] = useState<State>(token ? "idle" : "error");
  const [error, setError] = useState<string | null>(
    token ? null : "Undo token missing from URL. Use the link from your email.",
  );

  async function onUndo() {
    setState("working");
    setError(null);
    try {
      await undoEmailChange(token);
      setState("ok");
    } catch (err) {
      setState("error");
      // A 409 means the old address is now another account's; friendlyAuthError's
      // 409 text ("already registered — sign in") would mislead here.
      if (err instanceof ApiError && err.status === 409) {
        setError(
          "Your previous email address is now used by another account, so nothing was changed. Contact support.",
        );
      } else {
        setError(
          friendlyAuthError(err, "This undo link is invalid or expired (links last 24 hours)."),
        );
      }
    }
  }

  if (state === "ok") {
    return (
      <div className="space-y-4">
        <p className="text-sm">
          Done. Your previous email address is back, and every session, assistant
          token and connected assistant was signed out. Sign in again and change
          your password if you did not make this change.
        </p>
        <Button render={<Link href="/login" />} className="w-full">
          Sign in
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      {token && (
        <Button onClick={onUndo} className="w-full" disabled={state === "working"}>
          {state === "working" ? "Undoing…" : "Undo the email change"}
        </Button>
      )}
      <p className="text-center text-sm text-muted-foreground">
        <Link href="/login" className="text-foreground underline">
          Back to sign in
        </Link>
      </p>
    </div>
  );
}

export default function UndoEmailChangePage() {
  return (
    <div className="mx-auto max-w-md px-4 py-16">
      <Card className="border border-border bg-card shadow-none">
        <CardHeader>
          <CardTitle className="font-heading text-2xl font-normal tracking-tight">
            <h1>Undo an email change</h1>
          </CardTitle>
          <CardDescription>
            If the email on your Job360 account was changed and it wasn&apos;t you,
            this puts your previous address back and signs you out everywhere.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Suspense fallback={<div className="h-48 animate-pulse rounded-md bg-muted" />}>
            <UndoBody />
          </Suspense>
        </CardContent>
      </Card>
    </div>
  );
}

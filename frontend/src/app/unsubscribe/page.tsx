"use client";

// One-click unsubscribe landing page (wiring.md W-23).
//
// WHY A PAGE WITH A BUTTON, and not a link that just does it:
// email clients and security scanners PREFETCH links. A state-changing GET would
// unsubscribe people who never clicked — silently, and with no way for them to know
// it happened. This is the same trap /auth/magic already solves, and it is solved the
// same way: the emailed URL only OPENS this page, and the human pressing the button
// is what POSTs.
//
// No session is required. Someone who wants the emails to stop is often exactly the
// person who will not log in to make it happen, and a recipient who cannot find the
// exit presses "spam" instead — which costs the sending domain far more.

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { unsubscribeFromNotifications } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type State = "confirm" | "working" | "done" | "error";

function UnsubscribeBody() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [state, setState] = useState<State>("confirm");
  const [error, setError] = useState<string | null>(
    token ? null : "This link is missing its code. Use the link from your email."
  );

  async function confirm() {
    setState("working");
    try {
      await unsubscribeFromNotifications(token);
      setState("done");
    } catch (err) {
      setState("error");
      setError(
        apiErrorMessage(err, "We could not stop the emails. Try the link again.")
      );
    }
  }

  if (state === "done") {
    return (
      <CardContent className="space-y-4">
        <p className="text-sm text-foreground/80">
          Done — we&apos;ve stopped all Job360 emails to you.
        </p>
        {/* Say how to undo it. An unsubscribe that feels permanent and
            unexplained is one people regret and resent. */}
        <p className="text-sm text-muted-foreground">
          Changed your mind? Sign in and turn notifications back on in Settings —
          your channels and quiet hours are still exactly as you left them.
        </p>
        <Button render={<Link href="/login" />} variant="outline">
          Back to Job360
        </Button>
      </CardContent>
    );
  }

  return (
    <CardContent className="space-y-4">
      <p className="text-sm text-foreground/80">
        This stops <strong>all</strong> Job360 emails — job matches and application
        reminders.
      </p>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <Button onClick={confirm} disabled={!token || state === "working"}>
        {state === "working" ? "Stopping…" : "Stop all emails"}
      </Button>
    </CardContent>
  );
}

export default function UnsubscribePage() {
  return (
    <main className="mx-auto flex min-h-[60vh] max-w-md items-center px-4">
      <Card className="w-full">
        <CardHeader>
          <CardTitle>Unsubscribe</CardTitle>
          <CardDescription>Stop Job360 emails to this address.</CardDescription>
        </CardHeader>
        {/* useSearchParams needs a Suspense boundary in the App Router. */}
        <Suspense fallback={<CardContent>Loading…</CardContent>}>
          <UnsubscribeBody />
        </Suspense>
      </Card>
    </main>
  );
}

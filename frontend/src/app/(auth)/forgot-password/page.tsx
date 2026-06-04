"use client";

// Phase −2 item A — forgot-password page.
// Submits email → backend issues + emails a reset token. UI shows a
// generic "if you have an account, you'll get an email" message regardless
// of outcome (matches the backend's no-enumeration contract).

import { useState } from "react";
import Link from "next/link";

import { requestPasswordReset } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      await requestPasswordReset(email);
      setSubmitted(true);
    } catch (err) {
      // The backend always returns 204; a failure here is genuinely a
      // network / parse problem and we surface it verbatim.
      setError(err instanceof Error ? err.message : "request failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="mx-auto max-w-md py-16">
      <Card>
        <CardHeader>
          <CardTitle>Reset your password</CardTitle>
          <CardDescription>
            Enter the email you registered with. If we find a matching
            account, we&apos;ll send a reset link that works for the next
            hour.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {submitted ? (
            <div className="space-y-4">
              <p className="text-sm">
                If <span className="font-mono">{email}</span> is a Job360
                account, a reset link is on its way.
              </p>
              <p className="text-sm text-muted-foreground">
                Check your inbox — and your spam folder. The link expires
                in an hour. You can request another one if it does.
              </p>
              <p className="text-center text-sm">
                <Link href="/login" className="underline">
                  Back to sign in
                </Link>
              </p>
            </div>
          ) : (
            <form onSubmit={onSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              {error && <p className="text-sm text-red-400">{error}</p>}
              <Button type="submit" className="w-full" disabled={pending}>
                {pending ? "Sending…" : "Send reset link"}
              </Button>
              <p className="text-center text-sm text-muted-foreground">
                <Link href="/login" className="underline">
                  Back to sign in
                </Link>
              </p>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

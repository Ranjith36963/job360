"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";

import { register } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

// ---------------------------------------------------------------------------
// safeNext — validates the ?next param to prevent open-redirect attacks.
// Only allows paths that start with "/" but not "//" (protocol-relative).
// ---------------------------------------------------------------------------

export function safeNext(p: string | null): string {
  if (!p || !p.startsWith("/") || p.startsWith("//")) return "/profile";
  return p;
}

// ---------------------------------------------------------------------------
// Inner component — uses useSearchParams so it must live inside <Suspense>
// ---------------------------------------------------------------------------

function RegisterForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      await register(email, password);
      router.push(safeNext(next));
    } catch (err) {
      setError(err instanceof Error ? err.message : "registration failed");
    } finally {
      setPending(false);
    }
  }

  return (
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
      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          Minimum 8 characters. We hash with argon2id — never store plaintext.
        </p>
      </div>
      {error && <p className="text-sm text-red-400">{error}</p>}
      <Button type="submit" className="w-full" disabled={pending}>
        {pending ? "Creating..." : "Create account"}
      </Button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Page — wraps RegisterForm in <Suspense> so useSearchParams is hydration-safe
// ---------------------------------------------------------------------------

export default function RegisterPage() {
  return (
    <div className="mx-auto max-w-md py-16">
      <Card>
        <CardHeader>
          <CardTitle>Create your Job360 account</CardTitle>
          <CardDescription>
            Upload your CV next — matches start flowing within minutes.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Suspense fallback={<div className="h-52 animate-pulse rounded-md bg-muted" />}>
            <RegisterForm />
          </Suspense>
          <p className="mt-6 text-center text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link href="/login" className="underline">
              Sign in
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

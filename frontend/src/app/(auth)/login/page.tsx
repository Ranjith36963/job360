"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import { login, requestMagicLink } from "@/lib/api";
import { friendlyAuthError } from "@/lib/api-error";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

// ---------------------------------------------------------------------------
// safeNext — validates the ?next param to prevent open-redirect attacks.
// Only allows paths that start with "/" but not "//" (protocol-relative).
// ---------------------------------------------------------------------------

export function safeNext(p: string | null): string {
  if (!p || !p.startsWith("/") || p.startsWith("//")) return "/dashboard";
  return p;
}

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------

const magicSchema = z.object({
  email: z.string().email("Enter a valid email"),
});
type MagicSchema = z.infer<typeof magicSchema>;

const loginSchema = z.object({
  email: z.string().email("Enter a valid email"),
  password: z.string().min(8, "At least 8 characters"),
});
type LoginSchema = z.infer<typeof loginSchema>;

// ---------------------------------------------------------------------------
// Magic-link form (primary path) — email only, emails a sign-in link
// ---------------------------------------------------------------------------

function MagicLinkForm({ onUsePassword }: { onUsePassword: () => void }) {
  const [serverError, setServerError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<MagicSchema>({ resolver: zodResolver(magicSchema) });

  const onSubmit = handleSubmit(async (data) => {
    setServerError(null);
    try {
      await requestMagicLink(data.email);
      setSent(true);
    } catch (err) {
      setServerError(friendlyAuthError(err, "Could not send the link. Please try again."));
    }
  });

  if (sent) {
    return (
      <div className="space-y-4">
        <p role="status" className="text-sm">
          Check your email (and Spam) for a sign-in link.
        </p>
        <Button variant="secondary" className="w-full" onClick={() => setSent(false)}>
          Use a different email
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="magic-email">Email</Label>
        <Input
          id="magic-email"
          type="email"
          autoComplete="email"
          aria-invalid={!!errors.email}
          aria-describedby={errors.email ? "magic-email-error" : undefined}
          {...register("email")}
        />
        {errors.email && (
          <p id="magic-email-error" className="text-sm text-red-400">{errors.email.message}</p>
        )}
      </div>
      {serverError && (
        <p role="alert" className="text-sm text-red-400">
          {serverError}
        </p>
      )}
      <Button type="submit" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? "Sending..." : "Email me a sign-in link"}
      </Button>
      <p className="text-center text-sm">
        <button
          type="button"
          onClick={onUsePassword}
          className="text-muted-foreground underline"
        >
          Use password instead
        </button>
      </p>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Password form (kept available behind a toggle)
// ---------------------------------------------------------------------------

function PasswordForm({ onUseMagic }: { onUseMagic: () => void }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next");

  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginSchema>({ resolver: zodResolver(loginSchema) });

  const onSubmit = handleSubmit(async (data) => {
    setServerError(null);
    try {
      await login(data.email, data.password);
      router.push(safeNext(next));
    } catch (err) {
      setServerError(friendlyAuthError(err, "Login failed. Please try again."));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          type="email"
          autoComplete="email"
          aria-invalid={!!errors.email}
          aria-describedby={errors.email ? "email-error" : undefined}
          {...register("email")}
        />
        {errors.email && (
          <p id="email-error" className="text-sm text-red-400">{errors.email.message}</p>
        )}
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          aria-invalid={!!errors.password}
          aria-describedby={errors.password ? "password-error" : undefined}
          {...register("password")}
        />
        {errors.password && (
          <p id="password-error" className="text-sm text-red-400">{errors.password.message}</p>
        )}
      </div>
      {serverError && (
        <p role="alert" className="text-sm text-red-400">
          {serverError}
        </p>
      )}
      <Button type="submit" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? "Signing in..." : "Sign in"}
      </Button>
      <p className="text-center text-sm">
        <button
          type="button"
          onClick={onUseMagic}
          className="text-muted-foreground underline"
        >
          Email me a magic link instead
        </button>
      </p>
      <p className="text-center text-sm">
        <Link href="/forgot-password" className="text-muted-foreground underline">
          Forgot your password?
        </Link>
      </p>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Inner component — picks the mode; both use useSearchParams so it lives
// inside <Suspense>.
// ---------------------------------------------------------------------------

function LoginForm() {
  const [mode, setMode] = useState<"magic" | "password">("magic");
  const searchParams = useSearchParams();
  // Set by the middleware when it fails CLOSED on a backend outage (fable/03
  // F4): the user was redirected here even though their session may be fine.
  // Explain that, so the bounce doesn't read as "you were logged out".
  const serviceDown = searchParams.get("error") === "service_unavailable";

  return (
    <>
      {serviceDown && (
        <p
          role="alert"
          className="mb-4 rounded-md border border-yellow-600/40 bg-yellow-500/10 p-3 text-sm text-yellow-500"
        >
          We couldn&apos;t reach the server to verify your session — you may still be
          signed in. Please try again in a moment.
        </p>
      )}
      {mode === "magic" ? (
        <MagicLinkForm onUsePassword={() => setMode("password")} />
      ) : (
        <PasswordForm onUseMagic={() => setMode("magic")} />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Page — wraps LoginForm in <Suspense> so useSearchParams is hydration-safe
// ---------------------------------------------------------------------------

export default function LoginPage() {
  return (
    <div className="mx-auto max-w-md py-16">
      <Card>
        <CardHeader>
          <CardTitle>Sign in to Job360</CardTitle>
          <CardDescription>
            Welcome back. Your dashboard, notifications, and channels are one step away.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Suspense fallback={<div className="h-48 animate-pulse rounded-md bg-muted" />}>
            <LoginForm />
          </Suspense>
          <p className="mt-6 text-center text-sm text-muted-foreground">
            No account yet?{" "}
            <Link href="/register" className="underline">
              Create one
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

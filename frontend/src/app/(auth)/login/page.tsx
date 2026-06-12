"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import { login } from "@/lib/api";
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
// Schema
// ---------------------------------------------------------------------------

const loginSchema = z.object({
  email: z.string().email("Enter a valid email"),
  password: z.string().min(8, "At least 8 characters"),
});

type LoginSchema = z.infer<typeof loginSchema>;

// ---------------------------------------------------------------------------
// Inner component — uses useSearchParams so it must live inside <Suspense>
// ---------------------------------------------------------------------------

function LoginForm() {
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
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          type="email"
          autoComplete="email"
          {...register("email")}
        />
        {errors.email && (
          <p className="text-sm text-red-400">{errors.email.message}</p>
        )}
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          {...register("password")}
        />
        {errors.password && (
          <p className="text-sm text-red-400">{errors.password.message}</p>
        )}
      </div>
      {serverError && <p className="text-sm text-red-400">{serverError}</p>}
      <Button type="submit" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? "Signing in..." : "Sign in"}
      </Button>
      <p className="text-center text-sm">
        <Link href="/forgot-password" className="text-muted-foreground underline">
          Forgot your password?
        </Link>
      </p>
    </form>
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

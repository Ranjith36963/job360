"use client";

// Phase −2 item A — reset-password landing page.
// Reached via the link in the reset email. Reads ?token=… from the URL
// and posts {token, new_password} to /api/auth/password-reset/confirm.
//
// Uses useSearchParams via the standard Next.js 16 pattern: wrapped in
// <Suspense> so the page tree is hydration-safe.

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import { confirmPasswordReset } from "@/lib/api";
import { friendlyAuthError } from "@/lib/api-error";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

// ---------------------------------------------------------------------------
// Schema
// ---------------------------------------------------------------------------

const resetSchema = z
  .object({
    password: z.string().min(8, "At least 8 characters"),
    confirm: z.string().min(1, "Required"),
  })
  .refine((d) => d.password === d.confirm, {
    message: "Passwords must match",
    path: ["confirm"],
  });

type ResetSchema = z.infer<typeof resetSchema>;

function ResetForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [serverError, setServerError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ResetSchema>({ resolver: zodResolver(resetSchema) });

  const onSubmit = handleSubmit(async (data) => {
    setServerError(null);
    if (!token) {
      setServerError("Reset token missing from URL. Use the link from your email.");
      return;
    }
    try {
      await confirmPasswordReset(token, data.password);
      setDone(true);
    } catch (err) {
      // Backend returns 400 with ONE generic detail for any failure (unknown /
      // expired / used / deleted-user). friendlyAuthError surfaces that generic
      // detail (or a nice 429/500 message) WITHOUT the leaking "API error 400:"
      // prefix — so we never help an attacker distinguish cases. See docs/fable/03.
      setServerError(
        friendlyAuthError(err, "Reset link is invalid or expired. Request a new one."),
      );
    }
  });

  if (done) {
    return (
      <div className="space-y-4">
        <p className="text-sm">
          Password updated. All previous sessions on every device have
          been signed out.
        </p>
        <Button onClick={() => router.push("/login")} className="w-full">
          Sign in with your new password
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="password">New password</Label>
        <Input
          id="password"
          type="password"
          autoComplete="new-password"
          {...register("password")}
        />
        {errors.password && (
          <p className="text-sm text-red-400">{errors.password.message}</p>
        )}
      </div>
      <div className="space-y-2">
        <Label htmlFor="confirm">Confirm new password</Label>
        <Input
          id="confirm"
          type="password"
          autoComplete="new-password"
          {...register("confirm")}
        />
        {errors.confirm && (
          <p className="text-sm text-red-400">{errors.confirm.message}</p>
        )}
      </div>
      {serverError && <p className="text-sm text-red-400">{serverError}</p>}
      <Button type="submit" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? "Updating…" : "Set new password"}
      </Button>
      <p className="text-center text-sm text-muted-foreground">
        <Link href="/login" className="underline">
          Back to sign in
        </Link>
      </p>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <div className="mx-auto max-w-md py-16">
      <Card>
        <CardHeader>
          <CardTitle>Set a new password</CardTitle>
          <CardDescription>
            Pick a fresh password. After you update it, every existing
            session on every device will be signed out.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Suspense fallback={<div className="h-48 animate-pulse rounded-md bg-muted" />}>
            <ResetForm />
          </Suspense>
        </CardContent>
      </Card>
    </div>
  );
}

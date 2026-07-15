"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import { register as registerUser } from "@/lib/api";
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
  if (!p || !p.startsWith("/") || p.startsWith("//")) return "/profile";
  return p;
}

// ---------------------------------------------------------------------------
// Schema
// ---------------------------------------------------------------------------

const registerSchema = z.object({
  email: z.string().email("Enter a valid email"),
  password: z.string().min(8, "At least 8 characters"),
});

type RegisterSchema = z.infer<typeof registerSchema>;

// ---------------------------------------------------------------------------
// Inner component — uses useSearchParams so it must live inside <Suspense>
// ---------------------------------------------------------------------------

function RegisterForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next");

  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RegisterSchema>({ resolver: zodResolver(registerSchema) });

  const onSubmit = handleSubmit(async (data) => {
    setServerError(null);
    try {
      await registerUser(data.email, data.password);
      router.push(safeNext(next));
    } catch (err) {
      setServerError(friendlyAuthError(err, "Sign-up failed. Please try again."));
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
          autoComplete="new-password"
          {...register("password")}
        />
        {errors.password && (
          <p className="text-sm text-red-400">{errors.password.message}</p>
        )}
        <p className="text-xs text-muted-foreground">
          Minimum 8 characters. We hash with argon2id — never store plaintext.
        </p>
      </div>
      {serverError && (
        <p role="alert" className="text-sm text-red-400">
          {serverError}
        </p>
      )}
      <Button type="submit" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? "Creating..." : "Create account"}
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

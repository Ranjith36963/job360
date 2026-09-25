"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  changePassword,
  changeEmail,
  deleteAccount,
  logout,
  me,
  resendVerificationEmail,
  setTimezone,
} from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";

// ---------------------------------------------------------------------------
// Zod schemas
// ---------------------------------------------------------------------------

const changePasswordSchema = z
  .object({
    currentPassword: z.string().min(1, "Current password is required"),
    newPassword: z.string().min(8, "Password must be at least 8 characters"),
    confirmPassword: z.string().min(1, "Please confirm your new password"),
  })
  .refine((d) => d.newPassword === d.confirmPassword, {
    message: "Passwords do not match",
    path: ["confirmPassword"],
  });

const changeEmailSchema = z.object({
  currentPassword: z.string().min(1, "Current password is required"),
  newEmail: z.string().email("Enter a valid email address"),
});

type ChangePasswordValues = z.infer<typeof changePasswordSchema>;
type ChangeEmailValues = z.infer<typeof changeEmailSchema>;
type DeleteConfirmValues = { confirmText: string; currentPassword: string };

// ---------------------------------------------------------------------------
// Shared field error helper
// ---------------------------------------------------------------------------

function FieldError({ message }: { message?: string }) {
  if (!message) return null;
  return (
    <p className="text-xs text-red-400 mt-1" role="alert">
      {message}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Change Password
// ---------------------------------------------------------------------------

function ChangePasswordCard() {
  const [serverError, setServerError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<ChangePasswordValues>({
    resolver: zodResolver(changePasswordSchema),
  });

  async function onSubmit(data: ChangePasswordValues) {
    setServerError(null);
    setSuccess(null);
    try {
      await changePassword(data.currentPassword, data.newPassword);
      setSuccess("Password updated successfully.");
      reset();
    } catch (err) {
      setServerError(apiErrorMessage(err, "Failed to change password."));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Change password</CardTitle>
        <CardDescription>
          Update your password. For your security, you&apos;ll be signed out on
          all devices and need to sign in again.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit(onSubmit)} noValidate className="space-y-4">
          <div className="space-y-1">
            <Label htmlFor="cp-current">Current password</Label>
            <Input
              id="cp-current"
              type="password"
              autoComplete="current-password"
              aria-invalid={!!errors.currentPassword}
              {...register("currentPassword")}
            />
            <FieldError message={errors.currentPassword?.message} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="cp-new">New password</Label>
            <Input
              id="cp-new"
              type="password"
              autoComplete="new-password"
              aria-invalid={!!errors.newPassword}
              {...register("newPassword")}
            />
            <FieldError message={errors.newPassword?.message} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="cp-confirm">Confirm new password</Label>
            <Input
              id="cp-confirm"
              type="password"
              autoComplete="new-password"
              aria-invalid={!!errors.confirmPassword}
              {...register("confirmPassword")}
            />
            <FieldError message={errors.confirmPassword?.message} />
          </div>
          {serverError && <p className="text-sm text-red-400" role="alert">{serverError}</p>}
          {success && <p className="text-sm text-emerald-400" role="status">{success}</p>}
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Updating..." : "Update password"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Change Email
// ---------------------------------------------------------------------------

function ChangeEmailCard() {
  const router = useRouter();
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ChangeEmailValues>({
    resolver: zodResolver(changeEmailSchema),
  });

  async function onSubmit(data: ChangeEmailValues) {
    setServerError(null);
    try {
      await changeEmail(data.currentPassword, data.newEmail);
      await logout();
      router.push("/login");
    } catch (err) {
      setServerError(apiErrorMessage(err, "Failed to change email."));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Change email</CardTitle>
        <CardDescription>
          Enter your current password to verify your identity. You will be
          logged out after the change.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit(onSubmit)} noValidate className="space-y-4">
          <div className="space-y-1">
            <Label htmlFor="ce-current">Current password</Label>
            <Input
              id="ce-current"
              type="password"
              autoComplete="current-password"
              aria-invalid={!!errors.currentPassword}
              {...register("currentPassword")}
            />
            <FieldError message={errors.currentPassword?.message} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="ce-email">New email address</Label>
            <Input
              id="ce-email"
              type="email"
              autoComplete="email"
              aria-invalid={!!errors.newEmail}
              {...register("newEmail")}
            />
            <FieldError message={errors.newEmail?.message} />
          </div>
          {serverError && <p className="text-sm text-red-400" role="alert">{serverError}</p>}
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Updating..." : "Update email"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Delete Account
// ---------------------------------------------------------------------------

function DeleteAccountCard() {
  const router = useRouter();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<DeleteConfirmValues>();

  function openDialog() {
    reset();
    setServerError(null);
    setDialogOpen(true);
  }

  async function onConfirmDelete(data: DeleteConfirmValues) {
    setServerError(null);
    try {
      await deleteAccount(data.currentPassword);
      router.push("/login");
    } catch (err) {
      setServerError(apiErrorMessage(err, "Failed to delete account."));
    }
  }

  return (
    <>
      <Card className="border-red-900/40">
        <CardHeader>
          <CardTitle className="text-red-400">Danger zone</CardTitle>
          <CardDescription>
            This action is permanent and cannot be undone. All your data will be
            deleted.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="destructive" onClick={openDialog}>
            Delete my account
          </Button>
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete account</DialogTitle>
            <DialogDescription id="delete-dialog-desc">
              Are you sure? Type{" "}
              <span className="font-mono font-semibold">DELETE</span> to
              confirm. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleSubmit(onConfirmDelete)} noValidate>
            <div className="space-y-4 py-2">
              <div className="space-y-1">
                <Label htmlFor="del-password">Current password</Label>
                <Input
                  id="del-password"
                  type="password"
                  autoComplete="current-password"
                  aria-invalid={!!errors.currentPassword}
                  {...register("currentPassword", {
                    required: "Enter your password",
                  })}
                />
                <FieldError message={errors.currentPassword?.message} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="del-confirm">Type DELETE to confirm</Label>
                <Input
                  id="del-confirm"
                  placeholder="DELETE"
                  autoComplete="off"
                  aria-label="Type DELETE to confirm account deletion"
                  aria-describedby="delete-dialog-desc"
                  aria-invalid={!!errors.confirmText}
                  {...register("confirmText", {
                    validate: (v) => v === "DELETE" || "Type DELETE to confirm",
                  })}
                />
                <FieldError message={errors.confirmText?.message} />
              </div>
              {serverError && <p className="text-sm text-red-400 mt-1" role="alert">{serverError}</p>}
            </div>
            <DialogFooter className="mt-4">
              <Button
                type="button"
                variant="outline"
                onClick={() => setDialogOpen(false)}
                disabled={isSubmitting}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                variant="destructive"
                disabled={isSubmitting}
              >
                {isSubmitting ? "Deleting..." : "Delete my account"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// Time zone (owner decision, 2026-09-25)
// ---------------------------------------------------------------------------
//
// `spine.user_today` computes "today" from this value for every "what's due"
// read. Rule #29: an unwritten preference is silence, never a guess Job360
// makes FOR the user — so the browser's own zone is only ever a PREFILL
// shown before a Save, never written on its own.

const DEFAULT_TIMEZONE = "UTC";

function detectBrowserTimezone(): string | null {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return zone || null;
  } catch {
    return null;
  }
}

function TimezoneCard() {
  const [saved, setSaved] = useState<string | null>(null);
  const [selected, setSelected] = useState(DEFAULT_TIMEZONE);
  const [detected, setDetected] = useState(false);
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    me().then((user) => {
      if (cancelled || !user) return;
      const current = user.timezone || DEFAULT_TIMEZONE;
      setSaved(current);
      if (current === DEFAULT_TIMEZONE) {
        const browserZone = detectBrowserTimezone();
        if (browserZone && browserZone !== DEFAULT_TIMEZONE) {
          setSelected(browserZone);
          setDetected(true);
          return;
        }
      }
      setSelected(current);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSave() {
    const trimmed = selected.trim();
    if (!trimmed) return;
    setSaving(true);
    setServerError(null);
    setSuccess(null);
    try {
      const result = await setTimezone(trimmed);
      setSaved(result.timezone);
      setSelected(result.timezone);
      setDetected(false);
      setSuccess("Time zone saved.");
    } catch (err) {
      setServerError(apiErrorMessage(err, "Failed to save time zone."));
    } finally {
      setSaving(false);
    }
  }

  const unchanged = saved !== null && selected.trim() === saved;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Time zone</CardTitle>
        <CardDescription>
          Used to work out &quot;today&quot; for follow-up dates and what&apos;s
          due. An IANA name, e.g. &quot;Europe/London&quot; or &quot;America/New_York&quot;.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-end gap-2">
          <div className="flex-1 space-y-1">
            <Label htmlFor="timezone-input">Time zone</Label>
            <Input
              id="timezone-input"
              data-testid="timezone-input"
              value={selected}
              autoComplete="off"
              onChange={(e) => {
                setSelected(e.target.value);
                setDetected(false);
              }}
            />
          </div>
          <Button
            type="button"
            data-testid="timezone-save"
            disabled={saving || unchanged || !selected.trim()}
            onClick={() => void onSave()}
          >
            {saving ? "Saving..." : "Save"}
          </Button>
        </div>
        {detected && (
          <p className="text-xs text-muted-foreground" data-testid="timezone-detected">
            Detected from your browser — press Save to use it.
          </p>
        )}
        {serverError && <FieldError message={serverError} />}
        {success && (
          <p className="text-xs text-emerald-400" role="status">
            {success}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Verify email — resend the verification link
// ---------------------------------------------------------------------------

function VerifyEmailCard() {
  const [serverError, setServerError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  async function onResend() {
    setServerError(null);
    setSuccess(null);
    setSending(true);
    try {
      await resendVerificationEmail();
      setSuccess("Verification email sent. Check your inbox (and Spam), then click the link.");
    } catch (err) {
      setServerError(apiErrorMessage(err, "Failed to send verification email."));
    } finally {
      setSending(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Verify your email</CardTitle>
        <CardDescription>
          Some features (like connecting an agent) need a verified email. Resend the
          verification link if you didn&apos;t get it.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Button onClick={onResend} disabled={sending}>
          {sending ? "Sending..." : "Resend verification email"}
        </Button>
        {success && (
          <p className="mt-3 text-xs text-green-400" role="status">
            {success}
          </p>
        )}
        <FieldError message={serverError ?? undefined} />
      </CardContent>
    </Card>
  );
}

export default function AccountSettingsPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-8 py-12">
      <div>
        <h1 className="text-3xl font-semibold">Account settings</h1>
        <p className="mt-2 text-muted-foreground">
          Manage your password, email address, and account lifecycle.
        </p>
      </div>
      <VerifyEmailCard />
      <TimezoneCard />
      <ChangePasswordCard />
      <ChangeEmailCard />
      <DeleteAccountCard />
    </div>
  );
}

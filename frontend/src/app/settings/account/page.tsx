"use client";

import { useState } from "react";
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
// Page
// ---------------------------------------------------------------------------

export default function AccountSettingsPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-8 py-12">
      <div>
        <h1 className="text-3xl font-semibold">Account settings</h1>
        <p className="mt-2 text-muted-foreground">
          Manage your password, email address, and account lifecycle.
        </p>
      </div>
      <ChangePasswordCard />
      <ChangeEmailCard />
      <DeleteAccountCard />
    </div>
  );
}

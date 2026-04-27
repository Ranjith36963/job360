"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
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

// ---------------------------------------------------------------------------
// Change Password
// ---------------------------------------------------------------------------

function ChangePasswordCard() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    if (newPassword.length < 8) {
      setError("New password must be at least 8 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("New passwords do not match.");
      return;
    }

    setLoading(true);
    try {
      await changePassword(currentPassword, newPassword);
      setSuccess("Password updated successfully.");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to change password.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Change password</CardTitle>
        <CardDescription>
          Update your password. You will remain logged in after changing it.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="cp-current">Current password</Label>
            <Input
              id="cp-current"
              type="password"
              required
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              autoComplete="current-password"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cp-new">New password</Label>
            <Input
              id="cp-new"
              type="password"
              required
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cp-confirm">Confirm new password</Label>
            <Input
              id="cp-confirm"
              type="password"
              required
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
            />
          </div>
          {error && <p className="text-sm text-red-400" role="alert">{error}</p>}
          {success && <p className="text-sm text-emerald-400" role="status">{success}</p>}
          <Button type="submit" disabled={loading}>
            {loading ? "Updating..." : "Update password"}
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
  const [currentPassword, setCurrentPassword] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    setLoading(true);
    try {
      await changeEmail(currentPassword, newEmail);
      setSuccess("Email updated. Logging you out…");
      // Session is cleared server-side after email change; redirect to login.
      await logout();
      router.push("/login");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to change email.");
      setLoading(false);
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
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="ce-current">Current password</Label>
            <Input
              id="ce-current"
              type="password"
              required
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              autoComplete="current-password"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ce-email">New email address</Label>
            <Input
              id="ce-email"
              type="email"
              required
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              autoComplete="email"
            />
          </div>
          {error && <p className="text-sm text-red-400" role="alert">{error}</p>}
          {success && <p className="text-sm text-emerald-400" role="status">{success}</p>}
          <Button type="submit" disabled={loading}>
            {loading ? "Updating..." : "Update email"}
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
  const [confirmText, setConfirmText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onConfirmDelete() {
    setError(null);
    setLoading(true);
    try {
      await deleteAccount();
      router.push("/login");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete account.");
      setLoading(false);
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
          <Button
            variant="destructive"
            onClick={() => {
              setConfirmText("");
              setError(null);
              setDialogOpen(true);
            }}
          >
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
          <div className="space-y-2 py-2">
            <Label htmlFor="del-confirm">Type DELETE to confirm</Label>
            <Input
              id="del-confirm"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder="DELETE"
              autoComplete="off"
              aria-label="Type DELETE to confirm account deletion"
              aria-describedby="delete-dialog-desc"
            />
            {error && <p className="text-sm text-red-400" role="alert">{error}</p>}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDialogOpen(false)}
              disabled={loading}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={onConfirmDelete}
              disabled={confirmText !== "DELETE" || loading}
            >
              {loading ? "Deleting..." : "Delete my account"}
            </Button>
          </DialogFooter>
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

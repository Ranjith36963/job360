"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Trash2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * A destructive action that asks once, inline.
 *
 * WHY TWO-STEP AND NOT A MODAL. Clearing is recoverable — the server snapshots
 * the profile before every clear, so History can restore it. A full modal for
 * an undoable action is friction the user pays on every test cycle, and this
 * button exists precisely so profile state can be reset OFTEN. One deliberate
 * second click is proportionate; a stray click still cannot wipe anything.
 *
 * The armed state self-disarms after 4s so a half-pressed button never sits
 * there waiting to fire on an unrelated click later.
 */
export function ClearButton({
  onConfirm,
  label = "Clear",
  confirmLabel = "Click again to clear",
  size = "sm",
  className = "",
  disabled = false,
}: {
  onConfirm: () => Promise<void>;
  label?: string;
  confirmLabel?: string;
  size?: "sm" | "default";
  className?: string;
  disabled?: boolean;
}) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const handle = useCallback(async () => {
    if (!armed) {
      setArmed(true);
      timer.current = setTimeout(() => setArmed(false), 4000);
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    setArmed(false);
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
    }
  }, [armed, onConfirm]);

  return (
    <Button
      type="button"
      variant="outline"
      size={size}
      onClick={handle}
      disabled={disabled || busy}
      aria-label={armed ? confirmLabel : label}
      data-testid="clear-button"
      data-armed={armed ? "true" : "false"}
      className={`gap-1.5 ${
        armed
          ? "border-destructive/60 text-destructive hover:text-destructive"
          : "text-muted-foreground hover:text-foreground"
      } ${className}`}
    >
      {busy ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <Trash2 className="h-3.5 w-3.5" />
      )}
      {busy ? "Clearing..." : armed ? confirmLabel : label}
    </Button>
  );
}

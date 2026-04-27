"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogFooter,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { updateApplicationNotes } from "@/lib/api";

// ---------------------------------------------------------------------------
// NotesEditor — Dialog-based note editor for pipeline applications
// ---------------------------------------------------------------------------

interface NotesEditorProps {
  jobId: number;
  initialNotes: string;
  onSaved?: () => void;
}

export function NotesEditor({ jobId, initialNotes, onSaved }: NotesEditorProps) {
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState(initialNotes);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset local state when dialog opens
  function handleOpenChange(next: boolean) {
    if (next) {
      setNotes(initialNotes);
      setError(null);
    }
    setOpen(next);
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await updateApplicationNotes(jobId, notes);
      setOpen(false);
      onSaved?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save notes");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger
        render={
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs text-muted-foreground hover:text-foreground px-2"
          />
        }
      >
        Edit notes
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Application Notes</DialogTitle>
        </DialogHeader>

        <Textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Add notes about this application…"
          className="min-h-[120px] resize-y"
          disabled={saving}
        />

        {error && (
          <p className="text-xs text-destructive">{error}</p>
        )}

        <DialogFooter showCloseButton>
          <Button
            size="sm"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

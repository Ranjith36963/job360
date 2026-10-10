"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { deleteProofScreenshot, fetchProofScreenshot, getProof, uploadProofScreenshot } from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { ProofStateOut } from "@/lib/api";
import { toast } from "@/lib/toast";

const BTN =
  "rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50";
const NOTE = "text-xs text-muted-foreground";
const MAX_BYTES = 3 * 1024 * 1024;
const TYPES = ["image/png", "image/jpeg", "image/webp"];
const MAX_LIVE = 3;

const LEVEL_TEXT: Record<string, string> = {
  email: "Confirmation email",
  text: "Thank-you page text",
  screenshot_only: "Screenshot only",
  none: "No proof yet",
};

function uploadMessage(err: unknown): string | null {
  if (err instanceof ApiError) {
    if (err.status === 413) return "That image is over 3 MB.";
    if (err.status === 415) return "Only PNG, JPEG or WebP images.";
    if (err.status === 409) return "This application already has 3 screenshots.";
  }
  return null;
}

/** One thumbnail: the image is fetched with the session cookie and shown from a blob URL
 * (revoked on unmount), never from a cross-origin <img src>. */
function Thumb({ applicationId, id }: { applicationId: number; id: number }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    fetchProofScreenshot(applicationId, id)
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [applicationId, id]);

  if (failed) return <p data-testid={`proof-thumb-${id}`} className={NOTE}>Image unavailable</p>;
  if (!src) return <div data-testid={`proof-thumb-${id}`} className="h-20 w-28 rounded-md border border-border bg-muted" />;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      data-testid={`proof-thumb-${id}`}
      src={src}
      alt="Proof screenshot"
      className="h-20 w-auto rounded-md border border-border object-cover"
    />
  );
}

/** Proof that an application was submitted: what Job360 holds, plus a drop zone
 * for screenshots (max 3 live, 3 MB each). Deleting erases the image but keeps
 * the record that it existed. File contents are never logged. */
export function ProofDropZone({ applicationId }: { applicationId: number }) {
  const [data, setData] = useState<ProofStateOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setData(await getProof(applicationId));
    } catch (err) {
      toast.apiError(err, "Couldn't load proof");
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function upload(file: File | undefined) {
    if (!file) return;
    if (!TYPES.includes(file.type)) return void toast.error("Only PNG, JPEG or WebP images.");
    if (file.size > MAX_BYTES) return void toast.error("That image is over 3 MB.");
    setBusy(true);
    try {
      await uploadProofScreenshot(applicationId, file);
      await load();
    } catch (err) {
      const msg = uploadMessage(err);
      if (msg) toast.error(msg);
      else toast.apiError(err, "Couldn't upload the screenshot");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  async function remove(id: number) {
    if (!window.confirm("Delete this screenshot? The image is erased; the record that it existed stays.")) return;
    setBusy(true);
    try {
      await deleteProofScreenshot(applicationId, id);
      await load();
    } catch (err) {
      toast.apiError(err, "Couldn't delete the screenshot");
    } finally {
      setBusy(false);
    }
  }

  const live = (data?.screenshots ?? []).filter((s) => !s.deleted_at);
  const deleted = (data?.screenshots ?? []).filter((s) => s.deleted_at);

  return (
    <div data-testid="proof-dropzone" className="flex flex-col gap-3">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Proof</h2>
      <p data-testid="proof-level" className="text-sm text-foreground">
        {data ? (LEVEL_TEXT[data.proof.level] ?? LEVEL_TEXT.none) : "Loading…"}
      </p>

      {live.length >= MAX_LIVE ? (
        <p className={NOTE}>3 of 3 screenshots - delete one to add another</p>
      ) : (
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            void upload(e.dataTransfer.files?.[0]);
          }}
          className={`flex flex-col items-start gap-2 rounded-md border border-dashed p-3 ${over ? "border-brand" : "border-border"}`}
        >
          <p className={NOTE}>{busy ? "Uploading…" : "Drop a screenshot here (PNG, JPEG or WebP, up to 3 MB)"}</p>
          <button type="button" disabled={busy} onClick={() => input.current?.click()} className={BTN}>
            Choose image
          </button>
          <input
            ref={input}
            type="file"
            data-testid="proof-file-input"
            accept="image/png,image/jpeg,image/webp"
            hidden
            onChange={(e) => void upload(e.target.files?.[0])}
          />
        </div>
      )}

      {live.length > 0 && (
        <ul className="flex flex-wrap gap-3">
          {live.map((s) => (
            <li key={s.id} className="flex flex-col items-start gap-1.5">
              <Thumb key={`${applicationId}-${s.id}`} applicationId={applicationId} id={s.id} />
              <button type="button" data-testid={`proof-delete-${s.id}`} disabled={busy} onClick={() => void remove(s.id)} className={BTN}>
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}

      {deleted.map((s) => (
        <p key={s.id} data-testid={`proof-deleted-${s.id}`} className={NOTE}>
          {s.delete_note}
        </p>
      ))}
    </div>
  );
}

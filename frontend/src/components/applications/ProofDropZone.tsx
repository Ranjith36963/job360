"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { deleteProofScreenshot, fetchProofScreenshot, getProof, uploadProofScreenshot } from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { ProofStateOut } from "@/lib/api";
import { toast } from "@/lib/toast";

const BTN =
  "rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50";
const NOTE = "text-xs text-muted-foreground";
const TYPES = ["image/png", "image/jpeg", "image/webp"];

const LEVEL_TEXT: Record<string, string> = {
  email: "Confirmation email",
  text: "Thank-you page text",
  screenshot_only: "Screenshot only",
  none: "No proof yet",
};

/** Whole number when exact ("3"), else one decimal ("2.5"). */
const mb = (bytes: number) => String(Math.round((bytes / (1024 * 1024)) * 10) / 10);

const TYPE_REFUSAL = "Only PNG, JPEG or WebP images.";
const tooBig = (maxBytes: number) => `That image is over ${mb(maxBytes)} MB.`;
const tooMany = (maxLive: number) => `This application already has ${maxLive} screenshots.`;

/** One thumbnail, fetched with the session cookie and shown from a blob URL (revoked on unmount). */
function Thumb({ applicationId, id }: { applicationId: number; id: number }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    fetchProofScreenshot(applicationId, id)
      .then((blob) => {
        if (!cancelled) setSrc((url = URL.createObjectURL(blob)));
      })
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [applicationId, id]);

  const testId = `proof-thumb-${id}`;
  if (failed) return <p data-testid={testId} className={NOTE}>Image unavailable</p>;
  if (!src) return <div data-testid={testId} className="h-20 w-28 rounded-md border border-border bg-muted" />;
  // eslint-disable-next-line @next/next/no-img-element
  return <img data-testid={testId} src={src} alt="Proof screenshot" className="h-20 w-auto rounded-md border border-border object-cover" />;
}

/** Proof of submission: the level, plus a drop zone (limits come from the server). Deleting erases the image, keeps the record. */
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

  useEffect(() => void load(), [load]);

  async function act(fn: () => Promise<unknown>, failMsg: string) {
    setBusy(true);
    try {
      await fn();
      await load();
    } catch (err) {
      const refusals: Record<number, string | undefined> = {
        413: data ? tooBig(data.max_bytes) : undefined,
        415: TYPE_REFUSAL,
        409: data ? tooMany(data.max_live) : undefined,
      };
      const msg = err instanceof ApiError ? refusals[err.status] : undefined;
      if (msg) toast.error(msg);
      else toast.apiError(err, failMsg);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  async function upload(file: File | undefined) {
    if (!file) return;
    if (!TYPES.includes(file.type)) return void toast.error(TYPE_REFUSAL);
    if (data && file.size > data.max_bytes) return void toast.error(tooBig(data.max_bytes));
    await act(() => uploadProofScreenshot(applicationId, file), "Couldn't upload the screenshot");
  }

  async function remove(id: number) {
    if (!window.confirm("Delete this screenshot? The image is erased; the record that it existed stays.")) return;
    await act(() => deleteProofScreenshot(applicationId, id), "Couldn't delete the screenshot");
  }

  const shots = data?.screenshots ?? [];
  const live = shots.filter((s) => !s.deleted_at);
  const deleted = shots.filter((s) => s.deleted_at);

  return (
    <div data-testid="proof-dropzone" className="flex flex-col gap-3">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Proof</h2>
      <p data-testid="proof-level" className="text-sm text-foreground">
        {data ? (LEVEL_TEXT[data.proof.level] ?? LEVEL_TEXT.none) : "Loading…"}
      </p>

      {data && live.length >= data.max_live ? (
        <p className={NOTE}>{live.length} of {data.max_live} screenshots - delete one to add another</p>
      ) : (
        <div
          onDragOver={(e) => (e.preventDefault(), setOver(true))}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => (e.preventDefault(), setOver(false), void upload(e.dataTransfer.files?.[0]))}
          className={`flex flex-col items-start gap-2 rounded-md border border-dashed p-3 ${over ? "border-brand" : "border-border"}`}
        >
          <p className={NOTE}>{busy ? "Uploading…" : `Drop a screenshot here (PNG, JPEG or WebP${data ? `, up to ${mb(data.max_bytes)} MB` : ""})`}</p>
          <button type="button" disabled={busy} onClick={() => input.current?.click()} className={BTN}>Choose image</button>
          <input ref={input} type="file" data-testid="proof-file-input" accept={TYPES.join(",")} hidden onChange={(e) => void upload(e.target.files?.[0])} />
        </div>
      )}

      {live.length > 0 && (
        <ul className="flex flex-wrap gap-3">
          {live.map((s) => (
            <li key={s.id} className="flex flex-col items-start gap-1.5">
              <Thumb key={`${applicationId}-${s.id}`} applicationId={applicationId} id={s.id} />
              <button type="button" data-testid={`proof-delete-${s.id}`} disabled={busy} onClick={() => void remove(s.id)} className={BTN}>Delete</button>
            </li>
          ))}
        </ul>
      )}

      {deleted.map((s) => (
        <p key={s.id} data-testid={`proof-deleted-${s.id}`} className={NOTE}>{s.delete_note}</p>
      ))}
    </div>
  );
}

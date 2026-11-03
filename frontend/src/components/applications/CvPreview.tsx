"use client";

import { useEffect, useRef, useState } from "react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { fetchApplicationArtifactFile } from "@/lib/api";
import { openPdf, type PdfPages } from "@/lib/pdf-pages";

/** The CV as the employer gets it: the real PDF, every page, inside the page (owner, 2026-11-03).
 * One page fits the window; more pages scroll. Nothing else to choose - just the document and ✕. */
export function CvPreview({
  applicationId,
  artifactId,
  title,
  open,
  onOpenChange,
}: {
  applicationId: number;
  artifactId: number;
  title: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="flex h-[92dvh] max-h-[92dvh] w-full max-w-[calc(100%-1rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl"
        data-testid="cv-preview"
      >
        <div className="border-b border-border px-12 py-3 text-center">
          <DialogTitle className="truncate text-base">{title}</DialogTitle>
        </div>
        {open && <Pages applicationId={applicationId} artifactId={artifactId} />}
      </DialogContent>
    </Dialog>
  );
}

function Pages({ applicationId, artifactId }: { applicationId: number; artifactId: number }) {
  const box = useRef<HTMLDivElement>(null);
  const [pdf, setPdf] = useState<PdfPages | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    let opened: PdfPages | null = null;
    fetchApplicationArtifactFile(applicationId, artifactId, "pdf")
      .then((blob) => blob.arrayBuffer())
      .then((bytes) => openPdf(bytes))
      .then((doc) => {
        opened = doc;
        if (live) setPdf(doc);
        else doc.close();
      })
      .catch(() => live && setError("Couldn't open this CV. Use PDF to download it instead."));
    return () => {
      live = false;
      opened?.close();
    };
  }, [applicationId, artifactId]);

  return (
    <div ref={box} className="flex-1 overflow-y-auto bg-muted/40 p-3 sm:p-5" data-testid="cv-preview-pages">
      {error ? (
        <p role="alert" className="p-4 text-center text-sm text-muted-foreground">{error}</p>
      ) : !pdf ? (
        <p className="p-4 text-center text-sm text-muted-foreground" data-testid="cv-preview-loading">Opening your CV…</p>
      ) : (
        <div className="mx-auto flex max-w-[820px] flex-col gap-4">
          {Array.from({ length: pdf.count }, (_, i) => (
            <Page key={i} pdf={pdf} n={i + 1} box={box} />
          ))}
        </div>
      )}
    </div>
  );
}

function Page({ pdf, n, box }: { pdf: PdfPages; n: number; box: React.RefObject<HTMLDivElement | null> }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const width = Math.min(820, (el.parentElement?.clientWidth || box.current?.clientWidth || 600));
    const drawing = pdf.draw(n, el, width);
    drawing.done.catch(() => setFailed(true));
    return drawing.cancel;
  }, [pdf, n, box]);
  return (
    <div className="overflow-hidden rounded-md bg-white shadow-md ring-1 ring-black/10">
      {failed ? (
        <p className="p-4 text-sm text-neutral-600">Page {n} couldn&apos;t be drawn.</p>
      ) : (
        <canvas ref={canvas} aria-label={`Page ${n} of ${pdf.count}`} data-testid={`cv-preview-page-${n}`} className="block" />
      )}
    </div>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";
import posthog from "posthog-js";
import { Download, Loader2, Sparkles, Eye } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  generateTailored,
  getTailored,
  saveTailored,
  downloadTailored,
  getTailoredProvenance,
  type TailorFormat,
  type ProvenanceSegment,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import { toast } from "@/lib/toast";
import type { TailorBundle, TailorDocKind, TailoredDocOut } from "@/lib/types";

// ---------------------------------------------------------------------------
// TailorPanel — per-user AI CV + cover letter editor (docs/product/peruser_cv_coverletter.md §3.5)
//
// Dialog with a CV / Cover Letter tab pair. Every doc is ALWAYS editable
// (guardrail #3) — the textarea is seeded from `polished ?? ai_draft` and
// Save (PATCH) is available from the first render. Download marks the doc
// kept server-side, which is also the edit-feedback learning trigger (§5).
// ---------------------------------------------------------------------------

const TABS: { key: TailorDocKind; label: string }[] = [
  { key: "cv", label: "CV" },
  { key: "cover_letter", label: "Cover Letter" },
];

interface TailorPanelProps {
  jobId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Which doc to focus when the panel opens (default "cv"). */
  initialKind?: TailorDocKind;
}

function docFor(
  bundle: TailorBundle | null,
  kind: TailorDocKind
): TailoredDocOut | undefined {
  return bundle?.documents.find((d) => d.doc_kind === kind);
}

function seedText(bundle: TailorBundle | null, kind: TailorDocKind): string {
  const doc = docFor(bundle, kind);
  return doc?.polished ?? doc?.ai_draft ?? "";
}

export function TailorPanel({ jobId, open, onOpenChange, initialKind = "cv" }: TailorPanelProps) {
  const [bundle, setBundle] = useState<TailorBundle | null>(null);
  const [texts, setTexts] = useState<Record<TailorDocKind, string>>({
    cv: "",
    cover_letter: "",
  });
  const [activeTab, setActiveTab] = useState<TailorDocKind>("cv");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [downloading, setDownloading] = useState(false);
  // Per-line provenance (your facts vs AI-added) — lazy-loaded per tab on toggle.
  const [showProv, setShowProv] = useState<Record<TailorDocKind, boolean>>({
    cv: false,
    cover_letter: false,
  });
  const [prov, setProv] = useState<Record<TailorDocKind, ProvenanceSegment[] | null>>({
    cv: null,
    cover_letter: null,
  });

  async function toggleProvenance(kind: TailorDocKind) {
    const next = !showProv[kind];
    setShowProv((p) => ({ ...p, [kind]: next }));
    if (next && !prov[kind]) {
      try {
        const segs = await getTailoredProvenance(jobId, kind);
        setProv((p) => ({ ...p, [kind]: segs }));
      } catch (err) {
        toast.apiError(err, "Couldn't load fact highlights");
        setShowProv((p) => ({ ...p, [kind]: false }));
      }
    }
  }

  const applyBundle = useCallback((b: TailorBundle) => {
    setBundle(b);
    setTexts({ cv: seedText(b, "cv"), cover_letter: seedText(b, "cover_letter") });
    // Fresh docs → stale fact-highlights no longer apply.
    setProv({ cv: null, cover_letter: null });
    setShowProv({ cv: false, cover_letter: false });
  }, []);

  // Fetch existing docs every time the panel opens for this job.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setActiveTab(initialKind);
    getTailored(jobId)
      .then((b) => {
        if (!cancelled) applyBundle(b);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load tailored documents"
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, jobId, applyBundle, initialKind]);

  async function handleGenerate() {
    setGenerating(true);
    try {
      const b = await generateTailored(jobId);
      // W-27 — the funnel went dark right after Apply. Tailoring is the most
      // expensive thing the product does per user and nothing counted it.
      posthog.capture("tailored_document_generated", { job_id: jobId });
      applyBundle(b);
      toast.success("Tailored CV + cover letter generated");
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        toast.error(
          err.detail ||
            "Monthly free limit reached — tailoring is a premium feature."
        );
      } else if (err instanceof ApiError && err.status === 400) {
        toast.error("Upload your CV first on the Profile page.");
      } else {
        toast.apiError(err, "Failed to generate tailored documents");
      }
    } finally {
      setGenerating(false);
    }
  }

  async function handleSave(kind: TailorDocKind) {
    setSaving(true);
    try {
      const doc = await saveTailored(jobId, kind, texts[kind]);
      setBundle((prev) => {
        if (!prev) return prev;
        const others = prev.documents.filter((d) => d.doc_kind !== kind);
        return { ...prev, documents: [...others, doc] };
      });
      toast.success(`${kind === "cv" ? "CV" : "Cover letter"} saved`);
      // Edited text → previous fact-highlights are stale; drop them.
      setProv((p) => ({ ...p, [kind]: null }));
      setShowProv((p) => ({ ...p, [kind]: false }));
    } catch (err) {
      toast.apiError(err, "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function handleDownload(kind: TailorDocKind, fmt: TailorFormat) {
    setDownloading(true);
    try {
      await downloadTailored(jobId, kind, fmt);
      // Which KIND and which FORMAT — a bare "downloaded" count cannot tell a
      // CV from a cover letter, or PDF from DOCX.
      posthog.capture("tailored_document_downloaded", {
        job_id: jobId,
        doc_kind: kind,
        format: fmt,
      });
      toast.success(`${fmt.toUpperCase()} download started`);
    } catch (err) {
      toast.apiError(err, "Download failed");
    } finally {
      setDownloading(false);
    }
  }

  const hasDocs = (bundle?.documents.length ?? 0) > 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[88vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Tailor my CV</DialogTitle>
        </DialogHeader>

        {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {error && (
          <p className="text-xs text-destructive" role="alert">
            {error}
          </p>
        )}

        {!loading && !error && (
          <>
            <div className="flex items-center justify-between gap-2">
              {bundle && (
                <Badge variant="secondary" className="text-xs">
                  {bundle.quota_used}/{bundle.quota_limit} used this month
                </Badge>
              )}
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5 ml-auto"
                onClick={handleGenerate}
                disabled={generating}
              >
                {generating ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                {generating
                  ? "Generating…"
                  : hasDocs
                  ? "Regenerate"
                  : "Generate"}
              </Button>
            </div>

            {!hasDocs && !generating && (
              <p className="text-sm text-muted-foreground">
                No tailored documents yet — click Generate to create a CV and
                cover letter for this job.
              </p>
            )}

            {hasDocs && (
              <Tabs
                value={activeTab}
                onValueChange={(v) => setActiveTab(v as TailorDocKind)}
              >
                <TabsList>
                  {TABS.map((t) => (
                    <TabsTrigger key={t.key} value={t.key}>
                      {t.label}
                    </TabsTrigger>
                  ))}
                </TabsList>
                {TABS.map((t) => (
                  <TabsContent key={t.key} value={t.key} className="space-y-3">
                    {(docFor(bundle, t.key)?.flagged_terms?.length ?? 0) > 0 && (
                      <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                        <span className="font-semibold">⚠ Review — these may be invented:</span>{" "}
                        {(docFor(bundle, t.key)?.flagged_terms ?? []).join(", ")}. Check
                        them against your real CV before you apply.
                      </div>
                    )}
                    {showProv[t.key] && prov[t.key] ? (
                      <div className="space-y-2">
                        <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                          <span className="inline-flex items-center gap-1.5">
                            <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" aria-hidden="true" />
                            Your facts (from your CV)
                          </span>
                          <span className="inline-flex items-center gap-1.5">
                            <span className="h-2.5 w-2.5 rounded-full bg-amber-500" aria-hidden="true" />
                            AI-added / reshaped — verify before you send
                          </span>
                        </div>
                        <div className="min-h-[300px] max-h-[45vh] overflow-y-auto rounded-md border p-3 font-mono text-xs leading-relaxed">
                          {(prov[t.key] ?? []).map((seg, i) => (
                            <div
                              key={i}
                              className={
                                seg.grounded
                                  ? "text-emerald-300"
                                  : "rounded bg-amber-500/10 px-1 text-amber-200"
                              }
                            >
                              {seg.text || " "}
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <Textarea
                        value={texts[t.key]}
                        onChange={(e) =>
                          setTexts((prev) => ({ ...prev, [t.key]: e.target.value }))
                        }
                        placeholder={`Your tailored ${t.label.toLowerCase()}…`}
                        className="min-h-[300px] resize-y font-mono text-xs"
                        disabled={saving}
                      />
                    )}
                    <div className="flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        onClick={() => handleSave(t.key)}
                        disabled={saving || showProv[t.key]}
                      >
                        {saving ? "Saving…" : "Save"}
                      </Button>
                      <Button
                        size="sm"
                        variant={showProv[t.key] ? "default" : "outline"}
                        className="gap-1.5"
                        onClick={() => toggleProvenance(t.key)}
                      >
                        <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                        {showProv[t.key] ? "Back to editing" : "Highlight my facts"}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="gap-1.5"
                        onClick={() => handleDownload(t.key, "pdf")}
                        disabled={downloading}
                      >
                        <Download className="h-3.5 w-3.5" aria-hidden="true" />
                        {downloading ? "Downloading…" : "Download PDF"}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="gap-1.5"
                        onClick={() => handleDownload(t.key, "docx")}
                        disabled={downloading}
                      >
                        <Download className="h-3.5 w-3.5" aria-hidden="true" />
                        {downloading ? "Downloading…" : "Download DOCX"}
                      </Button>
                    </div>
                  </TabsContent>
                ))}
              </Tabs>
            )}
          </>
        )}

        <DialogFooter showCloseButton>
          <p className="mr-auto text-xs text-muted-foreground">
            Apply opens the company site — you submit these there.
          </p>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

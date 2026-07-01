"use client";

// Phase −2 item D — per-source health admin page.
//
// Reads /api/runs/source-health (aggregated across the last N runs) and
// renders a simple traffic-light table. Critical/warning rows surface
// first so operator eyes see them without scrolling.
//
// Auth is just require_user — there's no role distinction in the system
// today. Any logged-in user sees this. When roles land (Phase 7-ish),
// gate this on is_admin and add a 403 page; until then the URL is
// effectively unlisted in the nav by default.

import { useEffect, useState } from "react";
import { Activity } from "lucide-react";

import { getSourceHealth, SourceHealthEntry, SourceHealthResponse } from "@/lib/api";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const HEALTH_COLOR: Record<SourceHealthEntry["health"], string> = {
  ok: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
  warning: "bg-amber-500/20 text-amber-300 border-amber-500/40",
  critical: "bg-red-500/20 text-red-300 border-red-500/40",
};

function HealthBadge({ value }: { value: SourceHealthEntry["health"] }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${HEALTH_COLOR[value]}`}
    >
      {value}
    </span>
  );
}

export default function AdminSourcesPage() {
  const [data, setData] = useState<SourceHealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState(20);
  const [loading, setLoading] = useState(true);

  async function refresh(n: number) {
    setLoading(true);
    setError(null);
    try {
      const body = await getSourceHealth(n);
      setData(body);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load source health");
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh(runs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const summary = data
    ? {
        critical: data.sources.filter((s) => s.health === "critical").length,
        warning: data.sources.filter((s) => s.health === "warning").length,
        ok: data.sources.filter((s) => s.health === "ok").length,
      }
    : null;

  return (
    <div className="mx-auto max-w-6xl space-y-6 py-12">
      <PageHeader
        className="mb-0"
        icon={Activity}
        title="Source Health"
        subtitle="How your job sources are performing across recent runs."
        actions={
          summary ? (
            <span className="flex items-center gap-2 text-sm font-normal">
              <Badge variant="secondary">{summary.critical} critical</Badge>
              <Badge variant="secondary">{summary.warning} warning</Badge>
              <Badge variant="secondary">{summary.ok} ok</Badge>
            </span>
          ) : null
        }
      />
      <Card>
        <CardHeader>
          <CardDescription>
            Aggregated across the last {runs} pipeline runs you triggered.
            Sources that return 0 jobs three runs in a row are flagged
            <em> critical</em> — most often a markup change at the upstream
            site or an expired API key. Source-error entries on the
            ledger show as <em>warning</em>.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-4 flex items-center gap-2">
            <label className="text-sm text-muted-foreground" htmlFor="runs">
              Aggregate window:
            </label>
            <Select
              value={String(runs)}
              onValueChange={(val) => {
                const n = Number(val);
                setRuns(n);
                void refresh(n);
              }}
            >
              <SelectTrigger id="runs" size="sm" className="w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[5, 10, 20, 50, 100].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    last {n} runs
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void refresh(runs)}
              disabled={loading}
            >
              {loading ? "Refreshing…" : "Refresh"}
            </Button>
          </div>

          {error ? (
            <p className="text-sm text-red-400">{error}</p>
          ) : loading && !data ? (
            <div className="h-48 animate-pulse rounded-md bg-muted" />
          ) : data && data.sources.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No runs recorded yet for your account. Trigger a pipeline run
              from the dashboard, then return here.
            </p>
          ) : data ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase text-muted-foreground">
                  <tr className="border-b">
                    <th className="py-2 pr-4">Source</th>
                    <th className="py-2 pr-4">Health</th>
                    <th className="py-2 pr-4">Last count</th>
                    <th className="py-2 pr-4">Zero runs</th>
                    <th className="py-2 pr-4">Errored runs</th>
                    <th className="py-2 pr-4">Avg duration (s)</th>
                    <th className="py-2 pr-4">Last error</th>
                  </tr>
                </thead>
                <tbody>
                  {data.sources.map((s) => (
                    <tr key={s.source} className="border-b border-border/40">
                      <td className="py-2 pr-4 font-mono text-xs">{s.source}</td>
                      <td className="py-2 pr-4">
                        <HealthBadge value={s.health} />
                      </td>
                      <td className="py-2 pr-4">
                        {s.last_job_count ?? <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="py-2 pr-4">
                        {s.runs_zero_jobs}/{s.runs_observed}
                      </td>
                      <td className="py-2 pr-4">
                        {s.runs_errored}/{s.runs_observed}
                      </td>
                      <td className="py-2 pr-4">
                        {s.avg_duration_seconds?.toFixed(2) ?? (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                      <td className="py-2 pr-4 max-w-md truncate text-xs text-muted-foreground">
                        {s.last_error ?? "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

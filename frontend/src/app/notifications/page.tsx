"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { Bell } from "lucide-react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { getNotificationLedger } from "@/lib/api";
import type { NotificationLedgerListResponse } from "@/lib/types";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_LIMIT = 20;

const STATUS_STYLES: Record<string, string> = {
  sent: "bg-emerald-500/15 text-emerald-400",
  failed: "bg-red-500/15 text-red-400",
  pending: "bg-amber-500/15 text-amber-400",
};

function statusStyle(status: string): string {
  return STATUS_STYLES[status] ?? "bg-muted text-muted-foreground";
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function truncate(text: string | null, maxLen = 60): string {
  if (!text) return "—";
  return text.length > maxLen ? `${text.slice(0, maxLen)}…` : text;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function NotificationsPage() {
  const [data, setData] = useState<NotificationLedgerListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);

  // ---- Filter state ----
  const [channelFilter, setChannelFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  // ---- Fetch ----
  const fetchData = useCallback(async (pageOffset: number) => {
    setLoading(true);
    setError(null);
    try {
      const result = await getNotificationLedger(PAGE_LIMIT, pageOffset);
      setData(result);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load notifications"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData(offset);
  }, [fetchData, offset]);

  // ---- Derived values ----
  const notifications = data?.notifications ?? [];

  // Unique channels from current page data (for filter dropdown)
  const uniqueChannels = Array.from(
    new Set(notifications.map((n) => n.channel))
  ).sort();

  const filtered = notifications.filter((n) => {
    if (channelFilter !== "all" && n.channel !== channelFilter) return false;
    if (statusFilter !== "all" && n.status !== statusFilter) return false;
    return true;
  });

  const total = data?.total ?? 0;
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_LIMIT < total;

  // ---- Loading skeleton ----
  if (loading) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <div className="mb-6">
          <div className="h-8 w-64 rounded-lg bg-muted animate-pulse mb-2" />
          <div className="h-4 w-80 rounded-lg bg-muted animate-pulse" />
        </div>
        <div className="rounded-xl bg-card ring-1 ring-foreground/10 overflow-hidden">
          <div className="p-4 border-b border-foreground/10">
            <div className="h-8 w-48 rounded-lg bg-muted animate-pulse" />
          </div>
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="flex gap-4 px-4 py-3 border-b border-foreground/5 last:border-0"
            >
              {Array.from({ length: 6 }).map((__, j) => (
                <div
                  key={j}
                  className="h-4 rounded bg-muted animate-pulse flex-1"
                />
              ))}
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="relative">
      {/* Aurora background */}
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="aurora-glow-top aurora-glow-animated" />
        <div className="aurora-glow-left" />
      </div>

      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        {/* ═══════════════════════════════════════════════════
            HEADER
            ═══════════════════════════════════════════════════ */}
        <div className="animate-fade-in-up stagger-1 mb-6">
          <div className="flex items-center gap-3 mb-1">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/20">
              <Bell className="h-5 w-5 text-primary" />
            </div>
            <h1 className="text-2xl font-bold">Notification History</h1>
          </div>
          <p className="text-muted-foreground text-sm ml-[52px]">
            Recent notifications sent across all channels
          </p>
        </div>

        {/* ═══════════════════════════════════════════════════
            ERROR STATE
            ═══════════════════════════════════════════════════ */}
        {error && (
          <div className="mb-6 rounded-xl border border-destructive/20 bg-destructive/[0.06] p-4">
            <p className="text-sm text-destructive">{error}</p>
          </div>
        )}

        {/* ═══════════════════════════════════════════════════
            FILTER BAR
            ═══════════════════════════════════════════════════ */}
        <div className="animate-fade-in-up stagger-2 flex flex-wrap items-center gap-3 mb-4">
          {/* Channel filter */}
          <div className="flex items-center gap-2">
            <label
              htmlFor="channel-filter"
              className="text-sm text-muted-foreground whitespace-nowrap"
            >
              Channel
            </label>
            <select
              id="channel-filter"
              value={channelFilter}
              onChange={(e) => setChannelFilter(e.target.value)}
              className="rounded-lg border border-foreground/10 bg-card px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
            >
              <option value="all">All channels</option>
              {uniqueChannels.map((ch) => (
                <option key={ch} value={ch}>
                  {ch}
                </option>
              ))}
            </select>
          </div>

          {/* Status filter */}
          <div className="flex items-center gap-2">
            <label
              htmlFor="status-filter"
              className="text-sm text-muted-foreground whitespace-nowrap"
            >
              Status
            </label>
            <select
              id="status-filter"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded-lg border border-foreground/10 bg-card px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
            >
              <option value="all">All</option>
              <option value="sent">Sent</option>
              <option value="failed">Failed</option>
              <option value="pending">Pending</option>
            </select>
          </div>

          {/* Summary */}
          {data && (
            <span className="ml-auto text-xs text-muted-foreground">
              {filtered.length} of {total} notification{total !== 1 ? "s" : ""}
            </span>
          )}
        </div>

        {/* ═══════════════════════════════════════════════════
            TABLE
            ═══════════════════════════════════════════════════ */}
        <Card className="animate-fade-in-up stagger-3 overflow-x-auto">
          <CardHeader className="border-b border-foreground/10 pb-3">
            <span className="text-sm font-medium text-foreground/80">
              Notification Ledger
            </span>
          </CardHeader>
          <CardContent className="p-0">
            {filtered.length === 0 ? (
              <EmptyState
                icon={<Bell />}
                title="No notifications found"
                description={
                  statusFilter !== "all" || channelFilter !== "all"
                    ? "No notifications match the current filters."
                    : "Notifications will appear here once the notification worker runs."
                }
              />
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-foreground/10 text-left text-xs text-muted-foreground">
                    <th className="px-4 py-3 font-medium">Job ID</th>
                    <th className="px-4 py-3 font-medium">Channel</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium whitespace-nowrap">Sent At</th>
                    <th className="px-4 py-3 font-medium">Error</th>
                    <th className="px-4 py-3 font-medium text-right">Retries</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((n) => (
                    <tr
                      key={n.id}
                      className="border-b border-foreground/5 last:border-0 hover:bg-foreground/[0.02] transition-colors"
                    >
                      {/* Job ID — linked */}
                      <td className="px-4 py-3">
                        <Link
                          href={`/jobs/${n.job_id}`}
                          className="font-mono text-primary hover:underline"
                        >
                          #{n.job_id}
                        </Link>
                      </td>

                      {/* Channel */}
                      <td className="px-4 py-3 text-foreground/80 capitalize">
                        {n.channel}
                      </td>

                      {/* Status badge */}
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium capitalize ${statusStyle(n.status)}`}
                        >
                          {n.status}
                        </span>
                      </td>

                      {/* Sent at */}
                      <td className="px-4 py-3 text-foreground/60 whitespace-nowrap font-mono text-xs">
                        {formatDate(n.sent_at)}
                      </td>

                      {/* Error message */}
                      <td
                        className="px-4 py-3 text-foreground/60 max-w-[240px]"
                        title={n.error_message ?? undefined}
                      >
                        {truncate(n.error_message)}
                      </td>

                      {/* Retry count */}
                      <td className="px-4 py-3 text-right font-mono text-foreground/60">
                        {n.retry_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        {/* ═══════════════════════════════════════════════════
            PAGINATION
            ═══════════════════════════════════════════════════ */}
        {total > PAGE_LIMIT && (
          <div className="animate-fade-in-up stagger-4 flex items-center justify-between mt-4">
            <button
              onClick={() => setOffset(Math.max(0, offset - PAGE_LIMIT))}
              disabled={!hasPrev}
              className="rounded-lg border border-foreground/10 bg-card px-4 py-2 text-sm font-medium text-foreground hover:bg-foreground/5 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Previous
            </button>

            <span className="text-xs text-muted-foreground">
              Page {Math.floor(offset / PAGE_LIMIT) + 1} of{" "}
              {Math.ceil(total / PAGE_LIMIT)}
            </span>

            <button
              onClick={() => setOffset(offset + PAGE_LIMIT)}
              disabled={!hasNext}
              className="rounded-lg border border-foreground/10 bg-card px-4 py-2 text-sm font-medium text-foreground hover:bg-foreground/5 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Next
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { Bell, BellOff } from "lucide-react";

import {
  listChannels,
  getNotificationRule,
  saveNotificationRule,
  type Channel,
} from "@/lib/api";
import type { NotificationRule } from "@/lib/types";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { EmptyState } from "@/components/ui/empty-state";

type Mode = "instant" | "daily" | "every_n_hours";

// Editable shape of the one-per-user rulebook.
type Draft = {
  notify_mode: Mode;
  interval_hours: number;
  daily_send_time: string;
  score_threshold: number;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
  enabled: boolean;
};

const DEFAULT_DRAFT: Draft = {
  notify_mode: "instant",
  interval_hours: 6,
  daily_send_time: "08:00",
  score_threshold: 60,
  quiet_hours_start: null,
  quiet_hours_end: null,
  enabled: true,
};

function draftFromRule(rule: NotificationRule): Draft {
  return {
    notify_mode: (rule.notify_mode as Mode) ?? "instant",
    interval_hours: rule.interval_hours ?? 6,
    daily_send_time: rule.daily_send_time ?? "08:00",
    score_threshold: rule.score_threshold ?? 60,
    quiet_hours_start: rule.quiet_hours_start ?? null,
    quiet_hours_end: rule.quiet_hours_end ?? null,
    enabled: rule.enabled ?? true,
  };
}

export default function NotificationRulePage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [draft, setDraft] = useState<Draft>(DEFAULT_DRAFT);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const [chList, rule] = await Promise.all([
        listChannels(),
        getNotificationRule(),
      ]);
      setChannels(chList);
      setDraft(rule ? draftFromRule(rule) : DEFAULT_DRAFT);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load settings");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  function update(patch: Partial<Draft>) {
    setDraft((prev) => ({ ...prev, ...patch }));
    setSaved(false);
  }

  async function onSave() {
    setSaving(true);
    setError(null);
    try {
      const saved_ = await saveNotificationRule({
        notify_mode: draft.notify_mode,
        interval_hours: draft.interval_hours,
        daily_send_time: draft.daily_send_time,
        score_threshold: draft.score_threshold,
        quiet_hours_start: draft.quiet_hours_start,
        quiet_hours_end: draft.quiet_hours_end,
        enabled: draft.enabled,
      });
      setDraft(draftFromRule(saved_));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div
        className="mx-auto max-w-2xl space-y-8 py-12"
        role="status"
        aria-label="Loading notification settings"
      >
        <div className="space-y-2">
          <div className="h-8 w-56 animate-pulse rounded bg-muted" />
          <div className="h-4 w-96 animate-pulse rounded bg-muted" />
        </div>
        <Card>
          <CardContent className="space-y-4 py-8">
            <div className="h-4 w-full animate-pulse rounded bg-muted" />
            <div className="h-4 w-3/4 animate-pulse rounded bg-muted" />
            <div className="h-8 w-28 animate-pulse rounded bg-muted" />
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl space-y-8 py-12">
      <div>
        <h1 className="text-3xl font-semibold">Notifications</h1>
        <p className="mt-2 text-muted-foreground">
          One rulebook for when and how often you get notified. These settings
          apply to all your connected channels.
        </p>
      </div>

      {error && (
        <p className="rounded-md border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-400">
          {error}
        </p>
      )}

      {channels.length === 0 ? (
        <EmptyState
          title="No channels connected"
          description="Add a notification channel first, then come back here to set when and how often you get notified."
        />
      ) : (
        <Card>
          <CardHeader className="border-b pb-4">
            <div className="flex items-center justify-between">
              <CardTitle>Notification rule</CardTitle>
              <button
                type="button"
                role="switch"
                aria-checked={draft.enabled}
                aria-label={`Notifications are ${draft.enabled ? "on" : "off"} — click to turn ${draft.enabled ? "off" : "on"}`}
                onClick={() => update({ enabled: !draft.enabled })}
                className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold transition-colors ${
                  draft.enabled
                    ? "bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30 hover:bg-emerald-500/25"
                    : "bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/30 hover:bg-amber-500/25"
                }`}
              >
                {draft.enabled ? (
                  <Bell className="h-3.5 w-3.5" aria-hidden="true" />
                ) : (
                  <BellOff className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                {draft.enabled ? "On" : "Off"}
              </button>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              Applies to {channels.length} connected channel
              {channels.length > 1 ? "s" : ""}.
            </p>
          </CardHeader>

          <CardContent className="space-y-6 pt-4">
            {/* Off-state notice — make a disabled rule unmistakable so a user
                doesn't configure everything and silently receive nothing. */}
            {!draft.enabled && (
              <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                <BellOff className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <span>
                  Notifications are <strong>off</strong>. You won&apos;t receive any job
                  alerts until you turn them on (top-right) and Save.
                </span>
              </div>
            )}

            {/* Delivery mode */}
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium leading-none">
                How often
              </legend>
              <div className="flex flex-col gap-2 pt-1">
                {(
                  [
                    ["instant", "Instant — the moment a job matches"],
                    ["every_n_hours", "Every few hours — a bundle on a timer"],
                    ["daily", "Once a day — a bundle at a set time"],
                  ] as [Mode, string][]
                ).map(([value, label]) => (
                  <label
                    key={value}
                    className="flex cursor-pointer items-center gap-2 text-sm"
                  >
                    <input
                      type="radio"
                      name="notify-mode"
                      value={value}
                      checked={draft.notify_mode === value}
                      onChange={() => update({ notify_mode: value })}
                      className="accent-primary"
                    />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>

            {/* every_n_hours: interval */}
            {draft.notify_mode === "every_n_hours" && (
              <div className="space-y-2">
                <Label htmlFor="interval-hours">Send a bundle every</Label>
                <div className="flex items-center gap-2">
                  <input
                    id="interval-hours"
                    type="number"
                    min={1}
                    max={24}
                    value={draft.interval_hours}
                    onChange={(e) =>
                      update({
                        interval_hours: Math.min(
                          24,
                          Math.max(1, Number(e.target.value) || 1)
                        ),
                      })
                    }
                    className="h-9 w-20 rounded-md border bg-background px-3 text-sm"
                  />
                  <span className="text-sm text-muted-foreground">
                    hours (1–24)
                  </span>
                </div>
              </div>
            )}

            {/* daily: send time */}
            {draft.notify_mode === "daily" && (
              <div className="space-y-2">
                <Label htmlFor="daily-send-time">Daily send time</Label>
                <input
                  id="daily-send-time"
                  type="time"
                  value={draft.daily_send_time}
                  onChange={(e) =>
                    update({ daily_send_time: e.target.value || "08:00" })
                  }
                  className="h-9 rounded-md border bg-background px-3 text-sm"
                />
                <p className="text-xs text-muted-foreground">
                  Jobs are batched and sent once a day at this time (your
                  timezone).
                </p>
              </div>
            )}

            {/* Score threshold */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label htmlFor="threshold">Score threshold</Label>
                <span
                  className="text-sm font-medium tabular-nums"
                  aria-hidden="true"
                >
                  {draft.score_threshold}
                </span>
              </div>
              <input
                id="threshold"
                type="range"
                min={0}
                max={100}
                step={5}
                value={draft.score_threshold}
                aria-label={`Score threshold: ${draft.score_threshold}`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={draft.score_threshold}
                onChange={(e) =>
                  update({ score_threshold: Number(e.target.value) })
                }
                className="w-full accent-primary"
              />
              <p className="text-xs text-muted-foreground">
                Only notify when a job scores ≥ {draft.score_threshold} / 100.
              </p>
            </div>

            {/* Quiet hours */}
            <div className="space-y-2">
              <p
                className="text-sm font-medium leading-none"
                id="quiet-hours-label"
              >
                Quiet hours (optional)
              </p>
              <div
                className="flex items-center gap-3"
                role="group"
                aria-labelledby="quiet-hours-label"
              >
                <div className="flex flex-col gap-1">
                  <label
                    htmlFor="quiet-start"
                    className="text-xs text-muted-foreground"
                  >
                    Start
                  </label>
                  <input
                    id="quiet-start"
                    type="time"
                    value={draft.quiet_hours_start ?? ""}
                    aria-label="Quiet hours start time"
                    onChange={(e) =>
                      update({ quiet_hours_start: e.target.value || null })
                    }
                    className="h-9 rounded-md border bg-background px-3 text-sm"
                  />
                </div>
                <span className="mt-4 text-muted-foreground" aria-hidden="true">
                  to
                </span>
                <div className="flex flex-col gap-1">
                  <label
                    htmlFor="quiet-end"
                    className="text-xs text-muted-foreground"
                  >
                    End
                  </label>
                  <input
                    id="quiet-end"
                    type="time"
                    value={draft.quiet_hours_end ?? ""}
                    aria-label="Quiet hours end time"
                    onChange={(e) =>
                      update({ quiet_hours_end: e.target.value || null })
                    }
                    className="h-9 rounded-md border bg-background px-3 text-sm"
                  />
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                Sends are held during these hours and delivered once the window
                ends.
              </p>
            </div>

            {/* Save */}
            <div className="flex items-center gap-3 pt-1">
              <Button onClick={onSave} disabled={saving} aria-label="Save notification settings">
                {saving ? "Saving…" : "Save"}
              </Button>
              {saved && (
                <span className="text-sm text-emerald-400" role="status">
                  Saved
                </span>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

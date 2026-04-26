"use client";

import { useEffect, useState } from "react";

import {
  listChannels,
  getNotificationRules,
  createNotificationRule,
  updateNotificationRule,
  type Channel,
} from "@/lib/api";
import type { NotificationRule, NotificationRuleCreate } from "@/lib/types";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { EmptyState } from "@/components/ui/empty-state";

// Local draft state per channel (keyed by channel_type string used as the
// rule's `channel` field — mirrors the backend schema).
type DraftMap = Record<string, Partial<NotificationRuleCreate>>;

function getChannelKey(ch: Channel): string {
  return ch.channel_type;
}

export default function NotificationRulesPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [rules, setRules] = useState<NotificationRule[]>([]);
  const [drafts, setDrafts] = useState<DraftMap>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const [toggling, setToggling] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<Record<string, boolean>>({});

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const [chList, ruleData] = await Promise.all([
        listChannels(),
        getNotificationRules(),
      ]);
      setChannels(chList);
      setRules(ruleData.rules);

      // Seed drafts from existing rules
      const initialDrafts: DraftMap = {};
      for (const ch of chList) {
        const key = getChannelKey(ch);
        const existing = ruleData.rules.find((r) => r.channel === key);
        initialDrafts[key] = existing
          ? {
              channel: existing.channel,
              score_threshold: existing.score_threshold,
              notify_mode: existing.notify_mode,
              quiet_hours_start: existing.quiet_hours_start,
              quiet_hours_end: existing.quiet_hours_end,
              digest_send_time: existing.digest_send_time,
              enabled: existing.enabled,
            }
          : {
              channel: key,
              score_threshold: 50,
              notify_mode: "instant",
              quiet_hours_start: null,
              quiet_hours_end: null,
              digest_send_time: null,
              enabled: true,
            };
      }
      setDrafts(initialDrafts);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load settings");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function updateDraft(key: string, patch: Partial<NotificationRuleCreate>) {
    setDrafts((prev) => ({
      ...prev,
      [key]: { ...prev[key], ...patch },
    }));
  }

  async function onSave(ch: Channel) {
    const key = getChannelKey(ch);
    const draft = drafts[key];
    if (!draft) return;

    setSaving((prev) => ({ ...prev, [key]: true }));
    setError(null);
    try {
      const existing = rules.find((r) => r.channel === key);
      if (existing) {
        const updated = await updateNotificationRule(existing.id, {
          score_threshold: draft.score_threshold,
          notify_mode: draft.notify_mode,
          quiet_hours_start: draft.quiet_hours_start ?? null,
          quiet_hours_end: draft.quiet_hours_end ?? null,
          digest_send_time: draft.digest_send_time ?? null,
          enabled: draft.enabled,
        });
        setRules((prev) =>
          prev.map((r) => (r.id === updated.id ? updated : r))
        );
      } else {
        const created = await createNotificationRule({
          channel: key,
          score_threshold: draft.score_threshold ?? 50,
          notify_mode: draft.notify_mode ?? "instant",
          quiet_hours_start: draft.quiet_hours_start ?? null,
          quiet_hours_end: draft.quiet_hours_end ?? null,
          digest_send_time: draft.digest_send_time ?? null,
          enabled: draft.enabled ?? true,
        });
        setRules((prev) => [...prev, created]);
      }
      setSaved((prev) => ({ ...prev, [key]: true }));
      setTimeout(
        () => setSaved((prev) => ({ ...prev, [key]: false })),
        2000
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save rule");
    } finally {
      setSaving((prev) => ({ ...prev, [key]: false }));
    }
  }

  async function onToggleEnabled(ch: Channel) {
    const key = getChannelKey(ch);
    const existing = rules.find((r) => r.channel === key);
    if (!existing) return;

    setToggling((prev) => ({ ...prev, [key]: true }));
    setError(null);
    try {
      const updated = await updateNotificationRule(existing.id, {
        enabled: !existing.enabled,
      });
      setRules((prev) =>
        prev.map((r) => (r.id === updated.id ? updated : r))
      );
      updateDraft(key, { enabled: updated.enabled });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to toggle rule");
    } finally {
      setToggling((prev) => ({ ...prev, [key]: false }));
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-3xl py-12">
        <p className="text-sm text-muted-foreground">Loading notification rules…</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8 py-12">
      <div>
        <h1 className="text-3xl font-semibold">Notification rules</h1>
        <p className="mt-2 text-muted-foreground">
          Configure per-channel thresholds, delivery mode, and quiet hours.
        </p>
      </div>

      {error && (
        <p className="rounded-md border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-400">
          {error}
        </p>
      )}

      {channels.length === 0 ? (
        <EmptyState
          title="No channels configured"
          description="Add a notification channel first, then come back here to set delivery rules."
        />
      ) : (
        <div className="space-y-6">
          {channels.map((ch) => {
            const key = getChannelKey(ch);
            const draft = drafts[key] ?? {};
            const existingRule = rules.find((r) => r.channel === key);
            const threshold = draft.score_threshold ?? 50;
            const mode = draft.notify_mode ?? "instant";
            const isSaving = saving[key] ?? false;
            const isToggling = toggling[key] ?? false;
            const justSaved = saved[key] ?? false;
            const isEnabled = draft.enabled ?? true;

            return (
              <Card key={ch.id}>
                <CardHeader className="border-b pb-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <CardTitle>{ch.display_name}</CardTitle>
                      <p className="mt-0.5 text-xs uppercase tracking-wide text-muted-foreground">
                        {ch.channel_type}
                      </p>
                    </div>
                    {existingRule && (
                      <Button
                        variant={isEnabled ? "outline" : "secondary"}
                        size="sm"
                        disabled={isToggling}
                        onClick={() => onToggleEnabled(ch)}
                      >
                        {isToggling
                          ? "Updating…"
                          : isEnabled
                          ? "Enabled"
                          : "Disabled"}
                      </Button>
                    )}
                  </div>
                </CardHeader>

                <CardContent className="space-y-6 pt-4">
                  {/* Score threshold */}
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label htmlFor={`threshold-${key}`}>Score threshold</Label>
                      <span className="text-sm font-medium tabular-nums">
                        {threshold}
                      </span>
                    </div>
                    <input
                      id={`threshold-${key}`}
                      type="range"
                      min={0}
                      max={100}
                      step={5}
                      value={threshold}
                      onChange={(e) =>
                        updateDraft(key, {
                          score_threshold: Number(e.target.value),
                        })
                      }
                      className="w-full accent-primary"
                    />
                    <p className="text-xs text-muted-foreground">
                      Only notify when a job scores ≥ {threshold} / 100.
                    </p>
                  </div>

                  {/* Notify mode */}
                  <fieldset className="space-y-2">
                    <legend className="text-sm font-medium leading-none">
                      Delivery mode
                    </legend>
                    <div className="flex gap-6 pt-1">
                      <label className="flex cursor-pointer items-center gap-2 text-sm">
                        <input
                          type="radio"
                          name={`mode-${key}`}
                          value="instant"
                          checked={mode === "instant"}
                          onChange={() =>
                            updateDraft(key, { notify_mode: "instant" })
                          }
                          className="accent-primary"
                        />
                        Instant
                      </label>
                      <label className="flex cursor-pointer items-center gap-2 text-sm">
                        <input
                          type="radio"
                          name={`mode-${key}`}
                          value="digest"
                          checked={mode === "digest"}
                          onChange={() =>
                            updateDraft(key, { notify_mode: "digest" })
                          }
                          className="accent-primary"
                        />
                        Digest
                      </label>
                    </div>
                  </fieldset>

                  {/* Quiet hours — shown for instant mode */}
                  {mode === "instant" && (
                    <div className="space-y-2">
                      <Label>Quiet hours (optional)</Label>
                      <div className="flex items-center gap-3">
                        <div className="flex flex-col gap-1">
                          <span className="text-xs text-muted-foreground">Start</span>
                          <input
                            type="time"
                            value={draft.quiet_hours_start ?? ""}
                            onChange={(e) =>
                              updateDraft(key, {
                                quiet_hours_start: e.target.value || null,
                              })
                            }
                            className="h-9 rounded-md border bg-background px-3 text-sm"
                          />
                        </div>
                        <span className="mt-4 text-muted-foreground">to</span>
                        <div className="flex flex-col gap-1">
                          <span className="text-xs text-muted-foreground">End</span>
                          <input
                            type="time"
                            value={draft.quiet_hours_end ?? ""}
                            onChange={(e) =>
                              updateDraft(key, {
                                quiet_hours_end: e.target.value || null,
                              })
                            }
                            className="h-9 rounded-md border bg-background px-3 text-sm"
                          />
                        </div>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Notifications will be suppressed during these hours.
                      </p>
                    </div>
                  )}

                  {/* Digest send time — shown for digest mode */}
                  {mode === "digest" && (
                    <div className="space-y-2">
                      <Label htmlFor={`digest-time-${key}`}>
                        Daily digest send time
                      </Label>
                      <input
                        id={`digest-time-${key}`}
                        type="time"
                        value={draft.digest_send_time ?? ""}
                        onChange={(e) =>
                          updateDraft(key, {
                            digest_send_time: e.target.value || null,
                          })
                        }
                        className="h-9 rounded-md border bg-background px-3 text-sm"
                      />
                      <p className="text-xs text-muted-foreground">
                        Jobs are batched and sent once a day at this time.
                      </p>
                    </div>
                  )}

                  {/* Save */}
                  <div className="flex items-center gap-3 pt-1">
                    <Button
                      onClick={() => onSave(ch)}
                      disabled={isSaving}
                    >
                      {isSaving ? "Saving…" : existingRule ? "Save changes" : "Create rule"}
                    </Button>
                    {justSaved && (
                      <span className="text-sm text-emerald-400">Saved</span>
                    )}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

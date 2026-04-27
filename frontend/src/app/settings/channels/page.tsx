"use client";

import { useEffect, useState } from "react";

import {
  Channel,
  ChannelTestResult,
  createChannel,
  deleteChannel,
  listChannels,
  testChannel,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";

const CHANNEL_TYPES: Array<{ value: Channel["channel_type"]; label: string; hint: string }> = [
  { value: "slack", label: "Slack", hint: "slack://TokenA/TokenB/TokenC" },
  { value: "discord", label: "Discord", hint: "discord://webhook_id/webhook_token" },
  { value: "telegram", label: "Telegram", hint: "tgram://bot_token/chat_id" },
  { value: "email", label: "Email", hint: "mailtos://user:pass@smtp.gmail.com?to=you@example.com" },
  { value: "webhook", label: "Webhook", hint: "json://your-host/your-path" },
];

export default function ChannelsSettingsPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [channelType, setChannelType] = useState<Channel["channel_type"]>("slack");
  const [displayName, setDisplayName] = useState("");
  const [credential, setCredential] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [lastTest, setLastTest] = useState<Record<number, ChannelTestResult>>({});
  const [testing, setTesting] = useState<number | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const rows = await listChannels();
      setChannels(rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load channels");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onAdd(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await createChannel({
        channel_type: channelType,
        display_name: displayName,
        credential,
      });
      setDisplayName("");
      setCredential("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to add channel");
    } finally {
      setSubmitting(false);
    }
  }

  async function onDelete(id: number) {
    try {
      await deleteChannel(id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to delete channel");
    }
  }

  async function onTest(id: number) {
    setTesting(id);
    try {
      const result = await testChannel(id);
      setLastTest((prev) => ({ ...prev, [id]: result }));
    } catch (err) {
      setLastTest((prev) => ({
        ...prev,
        [id]: { ok: false, error: err instanceof Error ? err.message : "test failed" },
      }));
    } finally {
      setTesting(null);
    }
  }

  const hint = CHANNEL_TYPES.find((t) => t.value === channelType)?.hint ?? "";

  return (
    <div className="mx-auto max-w-3xl space-y-8 py-12">
      <div>
        <h1 className="text-3xl font-semibold">Notification channels</h1>
        <p className="mt-2 text-muted-foreground">
          Send matching jobs to Slack, Discord, Telegram, email, or a webhook.
          High-score matches (≥80) arrive instantly; the rest roll into your daily digest.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Add a channel</CardTitle>
          <CardDescription>
            Credentials are encrypted at rest (Fernet / AES-128-CBC). You can remove a channel anytime.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onAdd} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="type">Channel type</Label>
              <select
                id="type"
                className="h-10 w-full rounded-md border bg-background px-3 text-sm"
                value={channelType}
                onChange={(e) =>
                  setChannelType(e.target.value as Channel["channel_type"])
                }
              >
                {CHANNEL_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="display">Display name</Label>
              <Input
                id="display"
                required
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="e.g. #jobs channel"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="cred">Apprise URL</Label>
              <Input
                id="cred"
                required
                value={credential}
                onChange={(e) => setCredential(e.target.value)}
                placeholder={hint}
                className="font-mono text-xs"
              />
              <p className="text-xs text-muted-foreground">
                Expected shape: <span className="font-mono">{hint}</span>
              </p>
            </div>
            {error && <p className="text-sm text-red-400" role="alert">{error}</p>}
            <Button type="submit" disabled={submitting}>
              {submitting ? "Adding..." : "Add channel"}
            </Button>
          </form>
        </CardContent>
      </Card>

      <div className="space-y-4">
        <h2 className="text-xl font-semibold">Configured channels</h2>
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : channels.length === 0 ? (
          <EmptyState
            title="No channels yet"
            description="Add your first channel above to start receiving job notifications."
          />
        ) : (
          <ul className="space-y-3">
            {channels.map((ch) => {
              const result = lastTest[ch.id];
              return (
                <li key={ch.id}>
                  <Card>
                    <CardContent className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <div className="font-medium">{ch.display_name}</div>
                        <div className="text-xs uppercase tracking-wide text-muted-foreground">
                          {ch.channel_type}
                        </div>
                        {result && (
                          <div
                            role="status"
                            className={`mt-1 text-xs ${
                              result.ok ? "text-emerald-400" : "text-red-400"
                            }`}
                          >
                            {result.ok ? "Test succeeded" : `Test failed: ${result.error ?? "unknown"}`}
                          </div>
                        )}
                      </div>
                      <div className="flex gap-2">
                        <Button
                          variant="outline"
                          aria-label={`Send test notification to ${ch.display_name}`}
                          onClick={() => onTest(ch.id)}
                          disabled={testing === ch.id}
                        >
                          {testing === ch.id ? "Testing…" : "Send test"}
                        </Button>
                        <Button
                          variant="destructive"
                          aria-label={`Remove ${ch.display_name} channel`}
                          onClick={() => onDelete(ch.id)}
                        >
                          Remove
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

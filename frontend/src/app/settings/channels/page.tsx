"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { toast } from "sonner";

import {
  Channel,
  ChannelProviders,
  ChannelTestResult,
  channelConnectUrl,
  connectTelegram,
  createChannel,
  deleteChannel,
  getProviders,
  listChannels,
  pollTelegram,
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

// ---------------------------------------------------------------------------
// OAuth return toast — reads ?connected= from URL
// Must be in its own component so it can be wrapped in <Suspense>
// (Next 16 doc: useSearchParams() inside a client component suspends on
//  prerendered routes unless wrapped; Suspense boundary prevents the whole
//  page tree from being bailed out of static rendering)
// ---------------------------------------------------------------------------

function OAuthReturnToast({ onConnected }: { onConnected: () => void }) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const firedRef = useRef(false);

  useEffect(() => {
    const connected = searchParams.get("connected");
    if (!connected || firedRef.current) return;
    firedRef.current = true;

    const label =
      connected === "slack"
        ? "Slack"
        : connected === "discord"
        ? "Discord"
        : connected === "telegram"
        ? "Telegram"
        : null;

    if (label) {
      toast.success(`${label} connected!`);
      onConnected();
    }

    // Strip the query param so a refresh doesn't re-toast
    router.replace("/settings/channels");
  }, [searchParams, onConnected, router]);

  return null;
}

// ---------------------------------------------------------------------------
// Connect row — Slack / Discord / Telegram
// ---------------------------------------------------------------------------

interface ConnectRowProps {
  providers: ChannelProviders;
  onRefresh: () => void;
}

function ConnectRow({ providers, onRefresh }: ConnectRowProps) {
  const anyEnabled = providers.slack || providers.discord || providers.telegram;

  const [telegramPolling, setTelegramPolling] = useState(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const POLL_INTERVAL_MS = 2500;
  const POLL_TIMEOUT_MS = 5 * 60 * 1000;

  function stopPolling() {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }

  useEffect(() => {
    return () => stopPolling();
  }, []);

  async function handleTelegram() {
    try {
      const { deep_link, state } = await connectTelegram();
      window.open(deep_link, "_blank");
      setTelegramPolling(true);

      const deadline = Date.now() + POLL_TIMEOUT_MS;

      pollingRef.current = setInterval(async () => {
        if (Date.now() > deadline) {
          stopPolling();
          setTelegramPolling(false);
          toast.error("Telegram connect timed out. Please try again.");
          return;
        }
        try {
          const { connected } = await pollTelegram(state);
          if (connected) {
            stopPolling();
            setTelegramPolling(false);
            toast.success("Telegram connected!");
            onRefresh();
          }
        } catch {
          // transient; keep polling
        }
      }, POLL_INTERVAL_MS);
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Failed to start Telegram connect";
      toast.error(msg);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-3">
        {providers.slack ? (
          <Button
            variant="outline"
            onClick={() => {
              window.location.href = channelConnectUrl("slack");
            }}
          >
            Connect Slack
          </Button>
        ) : (
          <Button
            variant="outline"
            disabled
            title="Slack isn't set up on this server yet — an admin needs to add its API keys"
          >
            Connect Slack
          </Button>
        )}

        {providers.discord ? (
          <Button
            variant="outline"
            onClick={() => {
              window.location.href = channelConnectUrl("discord");
            }}
          >
            Connect Discord
          </Button>
        ) : (
          <Button
            variant="outline"
            disabled
            title="Discord isn't set up on this server yet — an admin needs to add its API keys"
          >
            Connect Discord
          </Button>
        )}

        {providers.telegram ? (
          <Button
            variant="outline"
            onClick={handleTelegram}
            disabled={telegramPolling}
          >
            {telegramPolling
              ? "Waiting for you to tap Start in Telegram…"
              : "Connect Telegram"}
          </Button>
        ) : (
          <Button
            variant="outline"
            disabled
            title="Telegram isn't set up on this server yet — an admin needs to add its bot token"
          >
            Connect Telegram
          </Button>
        )}
      </div>
      {!anyEnabled && (
        <p className="text-xs text-muted-foreground">
          Slack, Discord, and Telegram need API keys configured on the server
          before you can connect them — these buttons turn on automatically once
          they&apos;re added. Email and webhook below work right now.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Email add form
// ---------------------------------------------------------------------------

function EmailAddForm({ onRefresh }: { onRefresh: () => void }) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function isValidEmail(v: string) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!isValidEmail(email)) {
      setError("Please enter a valid email address.");
      return;
    }

    setSubmitting(true);
    try {
      await createChannel({
        channel_type: "email",
        display_name: email,
        credential: email,
      });
      setEmail("");
      await onRefresh();
      toast.success("Email channel added");
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Failed to add email channel";
      setError(msg);
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3" noValidate>
      <div className="space-y-2">
        <Label htmlFor="email-cred">Your email address</Label>
        <div className="flex gap-2">
          <Input
            id="email-cred"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <Button type="submit" disabled={submitting}>
            {submitting ? "Adding…" : "Add"}
          </Button>
        </div>
      </div>
      {error && (
        <p className="text-sm text-red-400" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Webhook add form
// ---------------------------------------------------------------------------

function WebhookAddForm({ onRefresh }: { onRefresh: () => void }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!url.startsWith("https://") && !url.startsWith("http://")) {
      setError("Webhook URL must start with https:// or http://");
      return;
    }

    setSubmitting(true);
    try {
      await createChannel({
        channel_type: "webhook",
        display_name: "Webhook",
        credential: url,
      });
      setUrl("");
      await onRefresh();
      toast.success("Webhook added");
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Failed to add webhook";
      setError(msg);
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3" noValidate>
      <div className="space-y-2">
        <Label htmlFor="webhook-url">Webhook URL (https://&hellip;)</Label>
        <p className="text-xs text-muted-foreground">
          Job360 will POST a JSON payload with matching job details to this URL.
        </p>
        <div className="flex gap-2">
          <Input
            id="webhook-url"
            type="url"
            placeholder="https://your-host/your-path"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="font-mono text-xs"
          />
          <Button type="submit" disabled={submitting}>
            {submitting ? "Adding…" : "Add"}
          </Button>
        </div>
      </div>
      {error && (
        <p className="text-sm text-red-400" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ChannelsSettingsPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [providers, setProviders] = useState<ChannelProviders | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [lastTest, setLastTest] = useState<Record<number, ChannelTestResult>>({});
  const [testing, setTesting] = useState<number | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const [rows, provs] = await Promise.all([listChannels(), getProviders()]);
      setChannels(rows);
      setProviders(provs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load channels");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onDelete(id: number) {
    try {
      await deleteChannel(id);
      await refresh();
      toast.success("Channel removed");
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Failed to delete channel";
      setError(msg);
      toast.error(msg);
    }
  }

  async function onTest(id: number) {
    setTesting(id);
    try {
      const result = await testChannel(id);
      setLastTest((prev) => ({ ...prev, [id]: result }));
      if (result.ok) {
        toast.success("Test message delivered");
      } else {
        toast.error(result.error ?? "Test failed");
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : "Test failed";
      setLastTest((prev) => ({ ...prev, [id]: { ok: false, error: errMsg } }));
      toast.error(errMsg);
    } finally {
      setTesting(null);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8 py-12">
      {/* OAuth return — reads ?connected= and fires a toast once */}
      <Suspense fallback={null}>
        <OAuthReturnToast onConnected={refresh} />
      </Suspense>

      <div>
        <h1 className="text-3xl font-semibold">Notification channels</h1>
        <p className="mt-2 text-muted-foreground">
          Send matching jobs to Slack, Discord, Telegram, email, or a webhook.
          High-score matches (&ge;80) arrive instantly; the rest roll into your
          daily digest.
        </p>
      </div>

      {/* ----------------------------------------------------------------- */}
      {/* Connect chat providers                                             */}
      {/* ----------------------------------------------------------------- */}
      <Card>
        <CardHeader>
          <CardTitle>One-click connect</CardTitle>
          <CardDescription>
            Connect a chat account. Credentials are encrypted at rest (Fernet /
            AES-128-CBC). You can remove a channel anytime.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {providers ? (
            <ConnectRow providers={providers} onRefresh={refresh} />
          ) : (
            <p className="text-sm text-muted-foreground">Loading&hellip;</p>
          )}
        </CardContent>
      </Card>

      {/* ----------------------------------------------------------------- */}
      {/* Email                                                              */}
      {/* ----------------------------------------------------------------- */}
      <Card>
        <CardHeader>
          <CardTitle>Email</CardTitle>
          <CardDescription>
            Receive job matches in your inbox.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <EmailAddForm onRefresh={refresh} />
        </CardContent>
      </Card>

      {/* ----------------------------------------------------------------- */}
      {/* Webhook                                                            */}
      {/* ----------------------------------------------------------------- */}
      <Card>
        <CardHeader>
          <CardTitle>Webhook</CardTitle>
          <CardDescription>
            Post job matches as JSON to any HTTP endpoint.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <WebhookAddForm onRefresh={refresh} />
        </CardContent>
      </Card>

      {error && (
        <p className="text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      {/* ----------------------------------------------------------------- */}
      {/* Configured channels list                                           */}
      {/* ----------------------------------------------------------------- */}
      <div className="space-y-4">
        <h2 className="text-xl font-semibold">Configured channels</h2>
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading&hellip;</p>
        ) : channels.length === 0 ? (
          <EmptyState
            title="No channels yet"
            description="Connect a chat account or add an email / webhook above to start receiving job notifications."
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
                          {ch.target_label ? (
                            <span className="ml-1 normal-case">
                              &mdash; {ch.target_label}
                            </span>
                          ) : null}
                        </div>
                        {result && (
                          <div
                            role="status"
                            className={`mt-1 text-xs ${
                              result.ok ? "text-emerald-400" : "text-red-400"
                            }`}
                          >
                            {result.ok
                              ? "Test succeeded"
                              : `Test failed: ${result.error ?? "unknown"}`}
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

"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
  createToken,
  listGrants,
  listTokens,
  revokeGrant,
  revokeToken,
  type OAuthGrant,
  type TokenCreated,
  type TokenSummary,
} from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
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

// ---------------------------------------------------------------------------
// Connect an agent — personal API tokens for the MCP server at /api/mcp.
//
// The plain token is shown ONCE, right after minting. The backend stores only
// a hash, so there is no "show again" — the user revokes and mints a new one.
// Minting/revoking needs the browser session (cookie), never a token, so a
// leaked token cannot grow itself more tokens.
// ---------------------------------------------------------------------------

const MAX_NAME = 100;

function mcpUrl(): string {
  // The frontend proxies /api/* to the backend, so the MCP endpoint lives on
  // the same origin the user is looking at — no separate host to explain.
  if (typeof window === "undefined") return "/api/mcp";
  return `${window.location.origin}/api/mcp`;
}

function connectCommand(token: string): string {
  return `claude mcp add --transport http job360 ${mcpUrl()} --header "Authorization: Bearer ${token}"`;
}

function fmtDate(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** Just the host, so the row reads "chatgpt.com" not the full callback URL. */
function redirectHost(uri: string): string {
  try {
    return new URL(uri).host;
  } catch {
    return uri;
  }
}

async function copyText(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${what} copied`);
  } catch {
    toast.error("Copy failed — select the text and copy it by hand.");
  }
}

// ---------------------------------------------------------------------------
// The one-time reveal
// ---------------------------------------------------------------------------

function NewTokenReveal({
  created,
  onDismiss,
}: {
  created: TokenCreated;
  onDismiss: () => void;
}) {
  const cmd = connectCommand(created.token);
  return (
    <Card className="border-emerald-700/40" data-testid="token-reveal">
      <CardHeader>
        <CardTitle>Your new token: {created.name}</CardTitle>
        <CardDescription>
          Copy it now. This is the only time it is shown — we keep a hash,
          not the token.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1">
          <Label htmlFor="new-token">Token</Label>
          <div className="flex gap-2">
            <Input
              id="new-token"
              readOnly
              value={created.token}
              className="font-mono text-xs"
              data-testid="token-value"
              onFocus={(e) => e.currentTarget.select()}
            />
            <Button
              type="button"
              variant="outline"
              onClick={() => copyText(created.token, "Token")}
            >
              Copy
            </Button>
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="connect-cmd">Connect Claude Code</Label>
          <p className="text-xs text-muted-foreground">
            Run this once in a terminal. Then Claude can bring a job, read
            your profile and receipts, and record an application for you.
          </p>
          <textarea
            id="connect-cmd"
            readOnly
            rows={3}
            value={cmd}
            className="w-full rounded-md border border-border/40 bg-muted/30 p-2 font-mono text-xs"
            data-testid="connect-command"
            onFocus={(e) => e.currentTarget.select()}
          />
          <Button
            type="button"
            variant="outline"
            onClick={() => copyText(cmd, "Command")}
          >
            Copy command
          </Button>
        </div>
        <Button type="button" onClick={onDismiss} data-testid="token-reveal-done">
          I have saved it
        </Button>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Connected apps — OAuth grants (ChatGPT, Claude.ai, any spec-following MCP
// client that signed in through the consent screen at /oauth/consent/[rid]).
// One active grant per (user, client); Revoke kills every token under it on
// the client's very next request (spec R8/S5).
// ---------------------------------------------------------------------------

function GrantRow({
  grant,
  onRevoke,
}: {
  grant: OAuthGrant;
  onRevoke: (g: OAuthGrant) => void;
}) {
  const [confirming, setConfirming] = useState(false);

  return (
    <li
      className="flex items-center justify-between gap-4 py-3"
      data-testid="grant-row"
    >
      <div className="min-w-0">
        <p className="truncate font-medium">{grant.client_name}</p>
        <p className="text-xs text-muted-foreground">
          <span className="font-mono">{redirectHost(grant.redirect_uri)}</span>{" "}
          · connected {fmtDate(grant.created_at)} · last used{" "}
          {fmtDate(grant.last_used_at)}
        </p>
      </div>
      {confirming ? (
        <div className="flex shrink-0 items-center gap-2">
          <Button
            type="button"
            variant="destructive"
            size="sm"
            onClick={() => onRevoke(grant)}
            data-testid={`grant-revoke-confirm-${grant.id}`}
          >
            Confirm revoke
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setConfirming(false)}
          >
            Cancel
          </Button>
        </div>
      ) : (
        <Button
          type="button"
          variant="destructive"
          size="sm"
          onClick={() => setConfirming(true)}
          aria-label={`Revoke access for ${grant.client_name}`}
          data-testid={`grant-revoke-${grant.id}`}
        >
          Revoke
        </Button>
      )}
    </li>
  );
}

function ConnectedAppsCard({
  grants,
  loading,
  onRevoke,
}: {
  grants: OAuthGrant[];
  loading: boolean;
  onRevoke: (g: OAuthGrant) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Connected apps</CardTitle>
        <CardDescription>
          Apps you signed in through — ChatGPT, Claude.ai, or any app that
          asked to connect. Revoking cuts that app off right away.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : grants.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="grants-empty">
            No connected apps yet.
          </p>
        ) : (
          <ul className="divide-y divide-border/40" data-testid="grant-list">
            {grants.map((g) => (
              <GrantRow key={g.id} grant={g} onRevoke={onRevoke} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Mint form
// ---------------------------------------------------------------------------

function CreateTokenCard({
  onCreated,
}: {
  onCreated: (t: TokenCreated) => void;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Give the token a name so you know which agent holds it.");
      return;
    }
    setSubmitting(true);
    try {
      const created = await createToken(trimmed);
      setName("");
      onCreated(created);
      toast.success("Token created");
    } catch (err) {
      const msg = apiErrorMessage(err, "Failed to create token.");
      setError(msg);
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Create a token</CardTitle>
        <CardDescription>
          One token per agent or machine. Name it after where it lives
          (&quot;laptop Claude Code&quot;) so revoking later is easy.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} noValidate className="flex items-end gap-2">
          <div className="flex-1 space-y-1">
            <Label htmlFor="token-name">Name</Label>
            <Input
              id="token-name"
              value={name}
              maxLength={MAX_NAME}
              autoComplete="off"
              placeholder="laptop Claude Code"
              aria-invalid={!!error}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <Button type="submit" disabled={submitting}>
            {submitting ? "Creating..." : "Create token"}
          </Button>
        </form>
        {error && (
          <p className="mt-2 text-xs text-red-400" role="alert">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Active tokens list
// ---------------------------------------------------------------------------

function TokenList({
  tokens,
  loading,
  onRevoke,
}: {
  tokens: TokenSummary[];
  loading: boolean;
  onRevoke: (t: TokenSummary) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Active tokens</CardTitle>
        <CardDescription>
          Revoking a token cuts that agent off immediately. It cannot be
          undone — mint a new one instead.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : tokens.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="tokens-empty">
            No tokens yet. Create one above to connect an agent.
          </p>
        ) : (
          <ul className="divide-y divide-border/40" data-testid="token-list">
            {tokens.map((t) => (
              <li
                key={t.id}
                className="flex items-center justify-between gap-4 py-3"
                data-testid="token-row"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium">{t.name}</p>
                  <p className="text-xs text-muted-foreground">
                    <span className="font-mono">{t.prefix}…</span> · created{" "}
                    {fmtDate(t.created_at)} · last used {fmtDate(t.last_used_at)}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  onClick={() => onRevoke(t)}
                  aria-label={`Revoke token ${t.name}`}
                >
                  Revoke
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ConnectAgentPage() {
  const [tokens, setTokens] = useState<TokenSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [created, setCreated] = useState<TokenCreated | null>(null);

  const [grants, setGrants] = useState<OAuthGrant[]>([]);
  const [grantsLoading, setGrantsLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setTokens(await listTokens());
    } catch (err) {
      toast.error(apiErrorMessage(err, "Failed to load tokens."));
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshGrants = useCallback(async () => {
    try {
      setGrants(await listGrants());
    } catch (err) {
      toast.error(apiErrorMessage(err, "Failed to load connected apps."));
    } finally {
      setGrantsLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    refreshGrants();
  }, [refresh, refreshGrants]);

  async function onCreated(t: TokenCreated) {
    setCreated(t);
    await refresh();
  }

  async function onRevoke(t: TokenSummary) {
    try {
      await revokeToken(t.id);
      if (created?.id === t.id) setCreated(null);
      await refresh();
      toast.success("Token revoked");
    } catch (err) {
      toast.error(apiErrorMessage(err, "Failed to revoke token."));
    }
  }

  async function onRevokeGrant(g: OAuthGrant) {
    try {
      await revokeGrant(g.id);
      await refreshGrants();
      toast.success("Access revoked");
    } catch (err) {
      toast.error(apiErrorMessage(err, "Failed to revoke access."));
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8 py-12">
      <div>
        <h1 className="text-3xl font-semibold">Connect an agent</h1>
        <p className="mt-2 text-muted-foreground">
          Let Claude Code (or any MCP client) work your Job360 account: bring
          a job link, read your profile, tailor documents, record that you
          applied. A personal token is the key; you can revoke it any time.
        </p>
      </div>
      <ConnectedAppsCard
        grants={grants}
        loading={grantsLoading}
        onRevoke={onRevokeGrant}
      />
      {created && (
        <NewTokenReveal created={created} onDismiss={() => setCreated(null)} />
      )}
      <CreateTokenCard onCreated={onCreated} />
      <TokenList tokens={tokens} loading={loading} onRevoke={onRevoke} />
    </div>
  );
}

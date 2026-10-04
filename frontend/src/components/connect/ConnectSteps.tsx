"use client";

import posthog from "posthog-js";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

// The three connect steps, shared by /settings/connect and the brand-new-user
// Home. Moved here unchanged (same copy) so both screens say the same thing.

export function mcpUrl(): string {
  // The frontend proxies /api/* to the backend, so the MCP endpoint lives on
  // the same origin the user is looking at — no separate host to explain.
  if (typeof window === "undefined") return "/api/mcp";
  return `${window.location.origin}/api/mcp`;
}

export async function copyText(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${what} copied`);
  } catch {
    toast.error("Copy failed — select the text and copy it by hand.");
  }
}

// ---------------------------------------------------------------------------
// Step 1 — the address every assistant connects to. Kept exactly as it was
// computed before (mcpUrl(), the frontend's own origin) — never hardcoded.
// ---------------------------------------------------------------------------

export function AddressStepCard() {
  const url = mcpUrl();
  return (
    <Card>
      <CardHeader>
        <CardTitle>Step 1 — Copy this address</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1">
        <Label htmlFor="mcp-url">Address</Label>
        <div className="flex gap-2">
          <Input
            id="mcp-url"
            readOnly
            value={url}
            className="font-mono text-xs"
            data-testid="mcp-url"
            onFocus={(e) => e.currentTarget.select()}
          />
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              // Funnel event (owner decision, 2026-09-28): the moment a user
              // actually starts connecting an assistant, not just visits the
              // page. No-op unless PostHog has loaded (consent given).
              posthog.capture("connect_address_copied");
              copyText(url, "Address");
            }}
          >
            Copy
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — one plain line per assistant (owner-approved copy, 2026-09-27).
// Only Claude carries a "Tested: works" line — a hand-checked, dated result
// (2026-09-19, docs/product/VISION.md), never a live reading. The other four
// carry no testing claim at all: neither "works" nor "not tested" — we have
// not run them end-to-end, and a guess here would be a claim we can't back.
// ---------------------------------------------------------------------------

type AssistantStep = {
  name: string;
  /** Plan + how-to, combined into one plain line. */
  line: string;
  /** Only true for an assistant actually run end-to-end, hand-checked and
   *  dated — see the comment above. Never set from a guess. */
  tested?: boolean;
};

const ASSISTANT_STEPS: AssistantStep[] = [
  {
    name: "Claude",
    line: "Settings → Connectors → Add custom connector → paste → Connect. Every plan, including Free.",
    tested: true,
  },
  {
    name: "ChatGPT",
    line: "Business, Enterprise or Edu (Plus and Pro may be read-only). An admin adds it as a custom app.",
  },
  {
    name: "Perplexity",
    line: "Pro or Max. Settings → Connectors → add → paste.",
  },
  {
    name: "Grok",
    line: "Paid accounts. Add a connection with the address.",
  },
  {
    name: "Gemini",
    line: "Business editions only. An admin adds it.",
  },
];

export function AssistantStepsCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Step 2 — Add it in your assistant</CardTitle>
        <CardDescription>
          Same address for every assistant, from Step 1 above.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-4 border-t border-border/40 lg:grid-cols-2 lg:gap-x-8">
          {ASSISTANT_STEPS.map((a) => (
            <li key={a.name} className="space-y-1 border-b border-border/40 py-3">
              <p className="font-medium">{a.name}</p>
              <p className="text-xs text-muted-foreground">{a.line}</p>
              {a.tested && (
                <p
                  className="text-xs text-success"
                  data-testid={`assistant-status-${a.name.toLowerCase()}`}
                >
                  ✅ Tested: works
                </p>
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — say hello. What to actually say, once connected — the two
// workflows the MCP tools carry (docs/product/VISION.md decision 28): build
// the profile, apply to a job. Plain prompts, no marketing.
// ---------------------------------------------------------------------------

export function SayHelloCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Step 3 — Say hello</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm">
          &ldquo;Build my Job360 profile from my CV.&rdquo; Your assistant
          will offer to set up a daily check of your email for job replies —
          just say yes.
        </p>
      </CardContent>
    </Card>
  );
}


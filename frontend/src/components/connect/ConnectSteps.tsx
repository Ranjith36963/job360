"use client";

import posthog from "posthog-js";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

// The three connect steps, shared by /settings/connect and the brand-new-user
// Home. Moved here unchanged (same copy) so both screens say the same thing.

// Shared look for the connect + settings pages (class-only; no copy here).
export const CARD_CLASS =
  "gap-5 rounded-lg border border-border bg-card py-5 shadow-none ring-0";
export const TITLE_CLASS = "font-heading text-xl font-normal tracking-tight";
export const DESC_CLASS = "text-muted-foreground";
export const LABEL_CLASS =
  "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
export const MONO_FIELD_CLASS = "h-9 border-border bg-background font-mono text-xs";
export const QUIET_BTN_CLASS =
  "border-border bg-card font-medium text-muted-foreground hover:text-foreground";
/** Long instruction text folded behind a quiet "Show instructions" link. The
 *  Copy button stays OUTSIDE (beside) this so it is always visible. */
export function ShowInstructions({ children }: { children: React.ReactNode }) {
  return (
    <details className="w-full basis-full text-sm">
      <summary className="inline-block cursor-pointer text-muted-foreground hover:text-foreground">
        Show instructions
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}
export const PRIMARY_BTN_CLASS =
  "bg-foreground text-background hover:bg-foreground/90";
export const DANGER_BTN_CLASS =
  "border-danger/40 bg-transparent text-danger hover:bg-danger-soft dark:bg-transparent dark:hover:bg-danger-soft";
export const DANGER_CONFIRM_BTN_CLASS =
  "bg-danger text-background hover:bg-danger/90 dark:bg-danger dark:hover:bg-danger/90";

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
    <Card className={CARD_CLASS}>
      <CardHeader>
        <CardTitle className={TITLE_CLASS}>Step 1 — Copy this address</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1">
        <Label htmlFor="mcp-url" className={LABEL_CLASS}>
          Address
        </Label>
        <div className="flex gap-2">
          <Input
            id="mcp-url"
            readOnly
            value={url}
            className={MONO_FIELD_CLASS}
            data-testid="mcp-url"
            onFocus={(e) => e.currentTarget.select()}
          />
          <Button
            type="button"
            variant="outline"
            className={`h-9 ${QUIET_BTN_CLASS}`}
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
// Step 2 — one card per assistant, every card the same shape: name, status
// badge, "What you need", numbered steps, the address to paste.
// Only Claude is "Tested": a hand-checked, dated result (2026-09-19,
// docs/product/VISION.md decision 24). The other four are "Not tested yet":
// we have not run them end-to-end. Plan facts come only from the owner-approved
// copy that was already on this card (2026-09-27); where it said nothing the
// card says to check the plan, never a guessed tier.
// ChatGPT Atlas has no card: docs/product/BROWSER_AGENT.md:40 says it is retired.
// ---------------------------------------------------------------------------

type AssistantStep = {
  name: string;
  /** The plan / tier / mode needed to add a custom connector (MCP). */
  need: string;
  /** Numbered steps. The address is pasted in the step that says "paste". */
  steps: string[];
  /** Only true for an assistant actually run end-to-end, hand-checked and
   *  dated — see the comment above. Never set from a guess. */
  tested?: boolean;
};

const CHECK_PLAN = "Check your plan supports custom connectors (MCP).";

export const ASSISTANT_STEPS: AssistantStep[] = [
  {
    name: "Claude",
    need: "Every plan, including Free.",
    steps: [
      "Open Settings, then Connectors.",
      "Choose Add custom connector.",
      "Paste the address below.",
      "Choose Connect and sign in.",
    ],
    tested: true,
  },
  {
    name: "ChatGPT",
    need: "Business, Enterprise or Edu (Plus and Pro may be read-only). An admin adds it as a custom app.",
    steps: [
      "Ask your workspace admin to add a custom app.",
      "Paste the address below.",
      "Sign in when asked.",
    ],
  },
  {
    name: "Gemini",
    need: "Business editions only. An admin adds it.",
    steps: [
      "Ask your workspace admin to add it.",
      "Paste the address below.",
      "Sign in when asked.",
    ],
  },
  {
    name: "Perplexity",
    need: "Pro or Max.",
    steps: [
      "Open Settings, then Connectors.",
      "Choose add.",
      "Paste the address below.",
    ],
  },
  {
    name: "Grok",
    need: "Paid accounts.",
    steps: [
      "Open your connections.",
      "Add a connection.",
      "Paste the address below.",
    ],
  },
];

function AssistantAddress({ name, url }: { name: string; url: string }) {
  const id = `assistant-address-${name.toLowerCase()}`;
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className={LABEL_CLASS}>
        Address to paste
      </Label>
      <div className="flex gap-2">
        <Input
          id={id}
          readOnly
          value={url}
          className={MONO_FIELD_CLASS}
          data-testid={id}
          onFocus={(e) => e.currentTarget.select()}
        />
        <Button
          type="button"
          variant="outline"
          className={`h-9 ${QUIET_BTN_CLASS}`}
          aria-label={`Copy address for ${name}`}
          onClick={() => {
            posthog.capture("connect_address_copied");
            copyText(url, "Address");
          }}
        >
          Copy
        </Button>
      </div>
    </div>
  );
}

export function AssistantStepsCard() {
  const url = mcpUrl();
  return (
    <Card className={CARD_CLASS}>
      <CardHeader>
        <CardTitle className={TITLE_CLASS}>Step 2 — Add it in your assistant</CardTitle>
        <CardDescription className={DESC_CLASS}>
          Same address for every assistant, from Step 1 above.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-4 lg:grid-cols-2">
          {ASSISTANT_STEPS.map((a) => {
            const key = a.name.toLowerCase();
            return (
              <li
                key={a.name}
                data-testid={`assistant-card-${key}`}
                className="flex min-w-0 flex-col gap-3 rounded-lg border border-border p-4"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="font-normal">{a.name}</p>
                  <span
                    className={`font-mono text-[11px] ${a.tested ? "text-brand" : "text-muted-foreground"}`}
                    data-testid={`assistant-status-${key}`}
                  >
                    {a.tested ? "Tested" : "Not tested yet"}
                  </span>
                </div>
                <div className="space-y-1">
                  <p className={LABEL_CLASS}>What you need</p>
                  <p className="text-sm text-muted-foreground">{a.need || CHECK_PLAN}</p>
                </div>
                <ol className="list-decimal space-y-1 pl-5 text-sm">
                  {a.steps.map((st) => (
                    <li key={st}>{st}</li>
                  ))}
                </ol>
                <div className="mt-auto">
                  <AssistantAddress name={a.name} url={url} />
                </div>
              </li>
            );
          })}
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
    <Card className={CARD_CLASS}>
      <CardHeader>
        <CardTitle className={TITLE_CLASS}>Step 3 — Say hello</CardTitle>
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


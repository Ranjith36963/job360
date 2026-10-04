import Link from "next/link";
import {
  Radar,
  Globe,
  Target,
  Layers,
  Brain,
  ArrowRight,
  Upload,
  Zap,
  Shield,
  Plug,
} from "lucide-react";

// R14 (docs/plans/2026-09-04-application-spine/spec.md) — the landing copy
// no longer advertises a job-source count. Job360 never sources, ranks or
// recommends jobs (VISION rule 4); the pitch is the memory layer AFTER the
// click, for the seeker's own AI agent — not a search or matching engine.
// See docs/product/VISION.md.
// Owner-approved CATEGORY LINE (2026-09-28) — the ONLY positioning sentence
// in use; do not invent another. Same line as the MCP server's own
// description/INSTRUCTIONS (backend/src/api/mcp_server.py) and the
// <title>/meta description (layout.tsx) and the Connect page intro
// (settings/connect/page.tsx). Audience: people who already use an AI
// assistant. First action: connect it. The assistant judges and writes, the
// user applies — Job360 never applies for anyone.
const CATEGORY_LINE =
  "The job tracker your AI assistant fills in for you — every CV version, every reply, every receipt.";
// Split once, at the em dash, so the hero headline below renders from THIS
// constant (no second hand-typed copy of the sentence to drift out of sync).
const [CATEGORY_LINE_LEAD, CATEGORY_LINE_TAIL] = CATEGORY_LINE.split(" — ");
const ASSISTANTS = "Claude, ChatGPT, Perplexity, Grok or Gemini";

const FEATURES = [
  {
    icon: Radar,
    title: "Context",
    description:
      "Your assistant starts every chat already knowing your CV, LinkedIn, GitHub and what you want next. No more pasting your CV in again.",
    stagger: 1,
  },
  {
    icon: Brain,
    title: "Memory",
    description:
      "Every job you bring, every step (applied, interview, offer) and every fit note is kept with its date. Pick up where you left off, in any chat.",
    stagger: 2,
  },
  {
    icon: Layers,
    title: "Every Version",
    description:
      "Every CV and cover letter your assistant writes is saved as a new version. Nothing is overwritten, and the receipt shows exactly what you sent.",
    stagger: 3,
  },
  {
    icon: Globe,
    title: "Bring Any Job",
    description:
      "Paste a link or the text of a job you found anywhere. Job360 keeps the ad, even after the listing disappears.",
    stagger: 4,
  },
  {
    icon: Plug,
    title: "Any Assistant",
    description: `${ASSISTANTS}: they all read and write the same record. Switch assistants and keep everything.`,
    stagger: 5,
  },
  {
    icon: Shield,
    title: "You Stay in Control",
    description:
      "Only the assistants you approve can connect, and you can disconnect any of them at any time.",
    stagger: 6,
  },
] as const;

const STATS = [
  {
    icon: Layers,
    value: "∞",
    label: "Versions",
    description: "Every CV & cover letter kept",
  },
  {
    icon: Plug,
    value: "5",
    label: "Assistants",
    description: "Claude, ChatGPT, Perplexity, Grok, Gemini",
  },
  {
    icon: Clock,
    value: "Every",
    label: "Step",
    description: "Dated and kept",
  },
  {
    icon: Shield,
    value: "You",
    label: "Decide",
    description: "Which assistants can connect",
  },
] as const;

export default function Landing() {
  return (
    <div className="relative">
      {/* ═══════════════════════════════════════════════════
          HERO SECTION
          ═══════════════════════════════════════════════════ */}
      {/* Vertical centring is right on a desktop viewport and wrong on a phone.
          At 390x844 it left ~210px of empty background above the badge and
          pushed "Get Started" down into the consent banner's ~200px, so the
          landing page's only call to action was unreadable and untappable on a
          first visit. Start the content near the top on small screens; keep the
          centred composition from `sm` up, where there is room for it. */}
      <section className="relative flex min-h-[calc(100vh-3.5rem)] flex-col items-center justify-start px-4 pt-8 sm:justify-center sm:px-6 sm:pt-16">
        <div className="mx-auto max-w-4xl text-center">
          {/* Pill badge */}
          <div className="mb-8 inline-flex items-center gap-2 rounded-full border border-border px-4 py-1.5 font-mono text-xs text-muted-foreground">
            <span>Works with {ASSISTANTS}</span>
          </div>

          {/* Headline — the owner-approved category line (CATEGORY_LINE
              above), split at the em dash into two staggered lines for
              layout only — never reworded. */}
          <h1
            className="text-balance font-heading text-4xl font-normal leading-[1.05] tracking-[-0.022em] sm:text-5xl lg:text-6xl"
          >
            <span className="block">
              {CATEGORY_LINE_LEAD}
            </span>
            <span className="mt-1 block">
              {` — ${CATEGORY_LINE_TAIL}`}
            </span>
          </h1>

          {/* Subtitle */}
          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-muted-foreground sm:text-xl">
            Context, memory and every version, in one place any assistant can
            read and write. Your assistant judges fit and writes the CV. You
            apply. Job
            <span className="font-semibold text-brand">
              360
            </span>{" "}
            keeps the record.
          </p>

          {/* CTAs — connecting the assistant comes first (owner, 2026-09-24):
              the product does nothing until an assistant reads and writes.
              A signed-out visitor is bounced to /login?next=... by
              middleware.ts, same as any other protected route. */}
          <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link
              href="/settings/connect"
              className="group inline-flex h-12 items-center gap-2 rounded-lg bg-primary px-8 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90"
            >
              <Plug className="h-4 w-4" />
              Connect your assistant
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
            </Link>
            <Link
              href="/profile"
              className="inline-flex h-12 items-center gap-2 rounded-lg border border-border bg-transparent px-8 text-sm font-semibold text-foreground transition-colors hover:bg-muted"
            >
              <Upload className="h-4 w-4" />
              Upload your CV
            </Link>
          </div>
        </div>

        {/* Scroll indicator — below CTAs with spacing */}
        <div className="mt-16 hidden sm:flex flex-col items-center gap-2 text-faint">
          <span className="text-[10px] tracking-[0.2em] uppercase">Scroll</span>
          <div className="h-6 w-px bg-border" />
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════
          STATS BAR
          ═══════════════════════════════════════════════════ */}
      <section className="relative px-4 py-16 sm:px-6">
        <div className="mx-auto max-w-5xl">
          <div className="grid grid-cols-2 gap-px border-y border-border bg-border lg:grid-cols-4">
            {STATS.map(({ value, label, description }) => (
              <div
                key={label}
                className="flex flex-col gap-1 bg-background px-5 py-8"
              >
                <p className="font-heading text-4xl font-normal tracking-tight text-foreground">
                  {value}
                </p>
                <p className="font-mono text-xs uppercase tracking-wider text-faint">
                  {label}
                </p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════
          FEATURE GRID
          ═══════════════════════════════════════════════════ */}
      <section className="relative px-4 py-16 sm:px-6 lg:py-24">
        <div className="mx-auto max-w-7xl">
          {/* Section header */}
          <div className="mx-auto max-w-2xl text-center mb-12 lg:mb-16">
            <p className="font-mono text-xs uppercase tracking-widest text-faint">
              What Job360 keeps
            </p>
            <h2 className="mt-3 text-balance font-heading text-3xl font-normal tracking-tight sm:text-4xl">
              Context, memory and every version
            </h2>
            <p className="mt-4 text-muted-foreground text-lg">
              Not a job board, and no AI of its own. Job360 keeps your whole
              job hunt in one place, so your assistant can pick it up in any
              chat.
            </p>
          </div>

          {/* Cards grid */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map(
              ({ icon: Icon, title, description }) => (
                <div
                  key={title}
                  className="rounded-xl border border-border bg-card p-6"
                >
                  <Icon className="mb-4 h-5 w-5 text-muted-foreground" />
                  <h3 className="font-heading text-lg font-normal tracking-tight">
                    {title}
                  </h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                    {description}
                  </p>
                </div>
              )
            )}
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════
          HOW IT WORKS — 3-step flow
          ═══════════════════════════════════════════════════ */}
      <section className="relative px-4 py-16 sm:px-6 lg:py-24">
        <div className="mx-auto max-w-5xl">
          <div className="mx-auto max-w-2xl text-center mb-12">
            <p className="font-mono text-xs uppercase tracking-widest text-faint">
              How it works
            </p>
            <h2 className="mt-3 text-balance font-heading text-3xl font-normal tracking-tight sm:text-4xl">
              Three steps to your next role
            </h2>
          </div>

          <div className="grid gap-8 md:grid-cols-3">
            {[
              {
                step: "01",
                title: "Connect your assistant",
                description: `Connect ${ASSISTANTS}. Add your CV, LinkedIn and GitHub, and your assistant fills in your skills, roles and history.`,
                icon: Plug,
              },
              {
                step: "02",
                title: "Bring the job you found",
                description:
                  "Paste a link or the text of a job you found anywhere. Job360 keeps the ad and starts one record for that application.",
                icon: Zap,
              },
              {
                step: "03",
                title: "Your assistant works, Job360 remembers",
                description:
                  "Your assistant judges fit and writes the CV. You apply. Job360 keeps every version, every step and the receipt of what you sent.",
                icon: Target,
              },
            ].map(({ step, title, description, icon: Icon }) => (
              <div key={step} className="relative">
                <div className="flex h-full flex-col rounded-xl border border-border bg-card p-6">
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <span className="font-mono text-sm text-faint">
                      {step}
                    </span>
                    <Icon className="h-5 w-5 text-muted-foreground" />
                  </div>
                  <h3 className="font-heading text-lg font-normal tracking-tight">
                    {title}
                  </h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground flex-1">
                    {description}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════
          BOTTOM CTA
          ═══════════════════════════════════════════════════ */}
      <section className="relative px-4 py-20 sm:px-6 lg:py-28">
        <div className="mx-auto max-w-3xl text-center">
          <div className="relative">
            <h2 className="text-balance font-heading text-3xl font-normal leading-[1.1] tracking-tight sm:text-4xl lg:text-5xl">
              Give your{" "}
              <span className="text-brand">
                assistant
              </span>{" "}
              the full story
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-lg text-muted-foreground">
              Connect your assistant, bring the jobs you find, and keep your
              whole job hunt in one shared record.
            </p>
            <div className="mt-8 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
              <Link
                href="/settings/connect"
                className="group inline-flex h-14 items-center gap-3 rounded-lg bg-primary px-10 text-base font-semibold text-primary-foreground transition-opacity hover:opacity-90"
              >
                <Plug className="h-5 w-5" />
                Connect your assistant
                <ArrowRight className="h-5 w-5 transition-transform group-hover:translate-x-1" />
              </Link>
              <Link
                href="/profile"
                className="inline-flex h-14 items-center gap-3 rounded-lg border border-border bg-transparent px-10 text-base font-semibold text-foreground transition-colors hover:bg-muted"
              >
                <Upload className="h-5 w-5" />
                Upload your CV
              </Link>
            </div>
            <p className="mt-6 text-xs text-faint">
              No spam, no fluff.
            </p>
          </div>
        </div>
      </section>

      {/* ── Footer spacer ─────────────────────────────── */}
      <div className="h-12" />
    </div>
  );
}

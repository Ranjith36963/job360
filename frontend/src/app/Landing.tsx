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
  Clock,
  Shield,
  Sparkles,
} from "lucide-react";

// R14 (docs/plans/2026-09-04-application-spine/spec.md) — the landing copy
// no longer advertises a job-source count. Job360 never sources, ranks or
// recommends jobs (VISION rule 4); the pitch is the memory layer AFTER the
// click, for the seeker's own AI agent — not a search or matching engine.
// See docs/product/VISION.md.
const FEATURES = [
  {
    icon: Globe,
    title: "Bring Any Job",
    description:
      "Paste a job you found anywhere — a URL or the raw text. Job360 stores the ad and keeps it even after the listing disappears.",
    stagger: 1,
  },
  {
    icon: Target,
    title: "One Application, One Record",
    description:
      "Every job you bring becomes a single application: every CV and cover-letter version, every event, and the receipt of what you actually sent.",
    stagger: 2,
  },
  {
    icon: Layers,
    title: "Every Version, Forever",
    description:
      "Every CV, cover letter and edit you ever save is kept — nothing is overwritten. Come back a year later and read exactly what you sent.",
    stagger: 3,
  },
  {
    icon: Radar,
    title: "Built From Your Real History",
    description:
      "Your profile comes from your CV, LinkedIn and GitHub — the real facts your agent needs, kept in one place.",
    stagger: 4,
  },
  {
    icon: Brain,
    title: "Your Agent's Memory",
    description:
      "Connect your own Claude or ChatGPT over MCP so it can read your profile and applications, and write straight back into them.",
    stagger: 5,
  },
  {
    icon: Shield,
    title: "Secure by Design",
    description:
      "OAuth 2.1 sign-in means only the agents you approve can act for you. Built and hosted in the UK at job360.uk.",
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
    icon: Brain,
    value: "MCP",
    label: "Agent access",
    description: "Your own Claude or ChatGPT reads and writes",
  },
  {
    icon: Clock,
    value: "Every",
    label: "Event",
    description: "Typed, timestamped, kept",
  },
  {
    icon: Shield,
    value: "UK",
    label: "Hosted",
    description: "job360.uk, OAuth 2.1 secured",
  },
] as const;

export default function Landing() {
  return (
    <div className="relative">
      {/* ── Hero ambient glow — dramatic multi-layer aurora ── */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 overflow-hidden"
      >
        {/* Primary top beam — strong, animated */}
        <div
          className="absolute -top-[20%] left-1/2 h-[900px] w-[1200px] -translate-x-1/2 rounded-full bg-primary/[0.15] blur-[140px]"
          style={{ animation: 'aurora-drift 8s ease-in-out infinite' }}
        />
        {/* Secondary top-right accent */}
        <div
          className="absolute -top-[10%] right-[5%] h-[500px] w-[500px] rounded-full bg-primary/[0.08] blur-[100px]"
          style={{ animation: 'aurora-drift 12s ease-in-out infinite reverse' }}
        />
        {/* Left side beam */}
        <div className="absolute top-[20%] -left-[15%] h-[600px] w-[400px] rounded-full bg-primary/[0.10] blur-[100px]" />
        {/* Right side beam */}
        <div className="absolute top-[40%] -right-[10%] h-[500px] w-[400px] rounded-full bg-primary/[0.06] blur-[80px]" />
        {/* Bottom center glow */}
        <div className="absolute -bottom-[15%] left-1/2 -translate-x-1/2 h-[400px] w-[800px] rounded-full bg-primary/[0.08] blur-[120px]" />
        {/* Horizontal scan line effect */}
        <div
          className="absolute inset-0 opacity-[0.03]"
          style={{
            backgroundImage: 'repeating-linear-gradient(0deg, transparent, transparent 2px, oklch(0.89 0.29 128 / 0.15) 2px, transparent 4px)',
            backgroundSize: '100% 4px',
          }}
        />
      </div>

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
          <div className="animate-fade-in-up stagger-1 mb-8 inline-flex items-center gap-2 rounded-full border border-primary/20 bg-primary/[0.06] px-4 py-1.5 text-sm text-primary">
            <Sparkles className="h-3.5 w-3.5" />
            <span className="font-medium">Your Career Memory Layer</span>
          </div>

          {/* Headline — each line staggers in, neon glow */}
          <h1
            className="font-heading text-5xl font-bold tracking-tight sm:text-6xl lg:text-7xl"
            style={{ textShadow: '0 0 80px oklch(0.89 0.29 128 / 0.15), 0 0 40px oklch(0.89 0.29 128 / 0.08)' }}
          >
            <span className="animate-fade-in-up stagger-2 block">
              Your CV.
            </span>
            <span className="animate-fade-in-up stagger-3 block mt-1">
              Every Application.
            </span>
            <span className="animate-fade-in-up stagger-4 block mt-1">
              One{" "}
              <span className="bg-gradient-to-r from-primary via-lime-300 to-primary bg-clip-text text-transparent">
                Record
              </span>
              .
            </span>
          </h1>

          {/* Subtitle */}
          <p className="animate-fade-in-up stagger-5 mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-muted-foreground sm:text-xl">
            Upload your CV, bring the jobs you find, and let Job
            <span className="bg-gradient-to-r from-primary via-lime-300 to-primary bg-clip-text text-transparent font-semibold">
              360
            </span>{" "}
            keep every document, every event, and the receipt — so your own
            AI agent always has the full story.
          </p>

          {/* CTAs */}
          <div className="animate-fade-in-up stagger-6 mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link
              href="/profile"
              className="group inline-flex h-12 items-center gap-2 rounded-xl bg-primary px-8 text-sm font-semibold text-primary-foreground shadow-[0_0_30px_oklch(0.89_0.29_128/0.4)] transition-all hover:shadow-[0_0_50px_oklch(0.89_0.29_128/0.6)] hover:brightness-110 hover:scale-105"
            >
              <Upload className="h-4 w-4" />
              Get Started
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
            </Link>
          </div>
        </div>

        {/* Scroll indicator — below CTAs with spacing */}
        <div className="animate-fade-in-up stagger-7 mt-16 hidden sm:flex flex-col items-center gap-2 text-muted-foreground/30">
          <span className="text-[10px] tracking-[0.2em] uppercase">Scroll</span>
          <div className="h-6 w-[1px] bg-gradient-to-b from-muted-foreground/20 to-transparent" />
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════
          STATS BAR
          ═══════════════════════════════════════════════════ */}
      <section className="relative px-4 py-16 sm:px-6">
        <div className="mx-auto max-w-5xl">
          <div className="glass-card rounded-2xl p-2">
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
              {STATS.map(({ icon: Icon, value, label, description }, i) => (
                <div
                  key={label}
                  className={`animate-fade-in-up stagger-${i + 1} flex flex-col items-center gap-3 rounded-xl px-4 py-6 text-center transition-colors hover:bg-primary/[0.04]`}
                >
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/20">
                    <Icon className="h-5 w-5 text-primary" />
                  </div>
                  <div>
                    <p className="font-mono text-2xl font-bold tracking-tight text-foreground">
                      {value}
                    </p>
                    <p className="text-sm font-semibold text-foreground/90">
                      {label}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {description}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════
          FEATURE GRID
          ═══════════════════════════════════════════════════ */}
      <section className="relative px-4 py-16 sm:px-6 lg:py-24">
        <div className="mx-auto max-w-7xl">
          {/* Section header */}
          <div className="animate-fade-in-up stagger-1 mx-auto max-w-2xl text-center mb-12 lg:mb-16">
            <p className="text-sm font-semibold uppercase tracking-widest text-primary">
              Everything you need
            </p>
            <h2 className="font-heading mt-3 text-3xl font-bold tracking-tight sm:text-4xl">
              Built for serious job seekers
            </h2>
            <p className="mt-4 text-muted-foreground text-lg">
              Not another job board. Job360 is the memory layer that keeps
              every job, document and event straight — so your own AI agent
              can act on it.
            </p>
          </div>

          {/* Cards grid */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map(
              ({ icon: Icon, title, description, stagger }) => (
                <div
                  key={title}
                  className={`animate-fade-in-up stagger-${stagger} glass-card group rounded-xl p-6`}
                >
                  <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/20 transition-all group-hover:bg-primary/15 group-hover:ring-primary/40">
                    <Icon className="h-5 w-5 text-primary" />
                  </div>
                  <h3 className="font-heading text-lg font-semibold tracking-tight">
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
          <div className="animate-fade-in-up stagger-1 mx-auto max-w-2xl text-center mb-12">
            <p className="text-sm font-semibold uppercase tracking-widest text-primary">
              How it works
            </p>
            <h2 className="font-heading mt-3 text-3xl font-bold tracking-tight sm:text-4xl">
              Three steps to your next role
            </h2>
          </div>

          <div className="grid gap-8 md:grid-cols-3">
            {[
              {
                step: "01",
                title: "Upload your CV",
                description:
                  "Drop your PDF or DOCX, add LinkedIn and GitHub. Job360 extracts your skills, titles, experience and education automatically.",
                icon: Upload,
              },
              {
                step: "02",
                title: "Bring the job you found",
                description:
                  "Paste a URL or the raw text for a job you found anywhere. Job360 stores the ad and starts one application record, even after the listing disappears.",
                icon: Zap,
              },
              {
                step: "03",
                title: "Let your agent take it from there",
                description:
                  "Your own Claude or ChatGPT reads your profile and application over MCP — judges fit, drafts the CV, and applies. Job360 remembers everything it did.",
                icon: Target,
              },
            ].map(({ step, title, description, icon: Icon }, i) => (
              <div
                key={step}
                className={`animate-fade-in-up stagger-${i + 2} relative`}
              >
                {/* Connector line (hidden on last card and mobile) */}
                {i < 2 && (
                  <div
                    aria-hidden
                    className="absolute right-0 top-10 hidden h-[1px] w-8 translate-x-full bg-gradient-to-r from-primary/30 to-transparent md:block"
                  />
                )}
                <div className="glass-card rounded-xl p-6 h-full flex flex-col">
                  <div className="mb-4 flex items-center gap-3">
                    <span className="font-mono text-3xl font-bold text-primary/30">
                      {step}
                    </span>
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/20">
                      <Icon className="h-5 w-5 text-primary" />
                    </div>
                  </div>
                  <h3 className="font-heading text-lg font-semibold">
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
          {/* Ambient glow behind CTA */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0 flex items-center justify-center"
          >
            <div className="h-[500px] w-[800px] rounded-full bg-primary/[0.12] blur-[120px]" />
          </div>

          <div className="relative">
            <h2 className="animate-fade-in-up stagger-1 font-heading text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">
              Ready to give your{" "}
              <span className="bg-gradient-to-r from-primary via-lime-300 to-primary bg-clip-text text-transparent">
                agent
              </span>{" "}
              a memory?
            </h2>
            <p className="animate-fade-in-up stagger-2 mx-auto mt-4 max-w-xl text-lg text-muted-foreground">
              Upload your CV, bring the jobs you find, and give your own AI
              agent everything it needs to apply well.
            </p>
            <div className="animate-fade-in-up stagger-3 mt-8 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
              <Link
                href="/profile"
                className="group inline-flex h-14 items-center gap-3 rounded-xl bg-primary px-10 text-base font-semibold text-primary-foreground shadow-[0_0_30px_oklch(0.89_0.29_128/0.4)] transition-all hover:shadow-[0_0_50px_oklch(0.89_0.29_128/0.6)] hover:brightness-110 hover:scale-105"
              >
                <Upload className="h-5 w-5" />
                Upload Your CV
                <ArrowRight className="h-5 w-5 transition-transform group-hover:translate-x-1" />
              </Link>
            </div>
            <p className="animate-fade-in-up stagger-4 mt-6 text-xs text-muted-foreground/60">
              UK-hosted at job360.uk. No spam, no fluff.
            </p>
          </div>
        </div>
      </section>

      {/* ── Footer spacer ─────────────────────────────── */}
      <div className="h-12" />
    </div>
  );
}

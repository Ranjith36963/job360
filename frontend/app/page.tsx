import Link from "next/link";
import {
  Radar,
  Globe,
  Target,
  Layers,
  Kanban,
  Brain,
  ArrowRight,
  Upload,
  Zap,
  Clock,
  Shield,
  ChevronRight,
  Sparkles,
} from "lucide-react";

const FEATURES = [
  {
    icon: Radar,
    title: "8-Dimensional Scoring",
    description:
      "Role, Skill, Seniority, Experience, Credentials, Location, Recency, and Semantic similarity — every job scored 0-100 with evidence-backed reasons.",
    stagger: 1,
  },
  {
    icon: Globe,
    title: "50 Job Sources",
    description:
      "APIs, ATS boards, RSS feeds, and intelligent scrapers. From Greenhouse to HackerNews, Reed to RemoteOK — all aggregated in real time.",
    stagger: 2,
  },
  {
    icon: Target,
    title: "Skill Gap Analysis",
    description:
      "Instantly see matched, missing, and transferable skills for every listing. Know exactly where you stand before you apply.",
    stagger: 3,
  },
  {
    icon: Layers,
    title: "Smart Deduplication",
    description:
      "Two-pass dedup with normalized keys and semantic similarity. No more seeing the same role posted across five boards.",
    stagger: 4,
  },
  {
    icon: Kanban,
    title: "Application Pipeline",
    description:
      "Track every opportunity from discovery to offer. Bookmarks, applications, interviews, and outcomes — all in one view.",
    stagger: 5,
  },
  {
    icon: Brain,
    title: "Career Intelligence",
    description:
      "AI-powered matching with 424 synonym groups, a 563-edge skill graph, and cross-encoder reranking. It understands your career, not just keywords.",
    stagger: 6,
  },
] as const;

const STATS = [
  {
    icon: Globe,
    value: "50",
    label: "Sources",
    description: "APIs, ATS, RSS & scrapers",
  },
  {
    icon: Radar,
    value: "8D",
    label: "Scoring",
    description: "Multi-dimensional match engine",
  },
  {
    icon: Clock,
    value: "Real-time",
    label: "Updates",
    description: "Fresh jobs every run",
  },
  {
    icon: Shield,
    value: "Any",
    label: "Domain",
    description: "Tech, finance, health & more",
  },
] as const;

export default function Home() {
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
      <section className="relative flex min-h-[calc(100vh-3.5rem)] flex-col items-center justify-center px-4 pt-16 sm:px-6">
        <div className="mx-auto max-w-4xl text-center">
          {/* Pill badge */}
          <div className="animate-fade-in-up stagger-1 mb-8 inline-flex items-center gap-2 rounded-full border border-primary/20 bg-primary/[0.06] px-4 py-1.5 text-sm text-primary">
            <Sparkles className="h-3.5 w-3.5" />
            <span className="font-medium">Career Command Center</span>
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
              50 Sources.
            </span>
            <span className="animate-fade-in-up stagger-4 block mt-1">
              One{" "}
              <span className="bg-gradient-to-r from-primary via-lime-300 to-primary bg-clip-text text-transparent">
                Dashboard
              </span>
              .
            </span>
          </h1>

          {/* Subtitle */}
          <p className="animate-fade-in-up stagger-5 mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-muted-foreground sm:text-xl">
            Upload your CV and let Job
            <span className="bg-gradient-to-r from-primary via-lime-300 to-primary bg-clip-text text-transparent font-semibold">
              360
            </span>
            &apos;s 8-dimensional scoring engine find your perfect match across
            50 job sources.
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
            <Link
              href="/dashboard"
              className="inline-flex h-12 items-center gap-2 rounded-xl border border-border bg-card/50 px-8 text-sm font-semibold text-foreground backdrop-blur-sm transition-all hover:border-primary/30 hover:bg-primary/[0.06]"
            >
              View Dashboard
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
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
              Not another job board. A professional-grade search engine that
              understands your career and finds roles you&apos;d actually want.
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
                  "Drop your PDF or DOCX. Our parser extracts skills, titles, experience, education, and certifications automatically.",
                icon: Upload,
              },
              {
                step: "02",
                title: "We search everywhere",
                description:
                  "50 sources queried in parallel — APIs, ATS boards, RSS feeds. Every job scored against your profile in 8 dimensions.",
                icon: Zap,
              },
              {
                step: "03",
                title: "Review top matches",
                description:
                  "Deduplicated, reranked, and sorted. See exactly why each job matched with evidence-backed scoring breakdowns.",
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
              Ready to find your{" "}
              <span className="bg-gradient-to-r from-primary via-lime-300 to-primary bg-clip-text text-transparent">
                next role
              </span>
              ?
            </h2>
            <p className="animate-fade-in-up stagger-2 mx-auto mt-4 max-w-xl text-lg text-muted-foreground">
              Upload your CV and let the engine do the heavy lifting. No
              accounts, no spam, no fluff — just relevant matches.
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
              Free and open source. Your data stays on your machine.
            </p>
          </div>
        </div>
      </section>

      {/* ── Footer spacer ─────────────────────────────── */}
      <div className="h-12" />
    </div>
  );
}

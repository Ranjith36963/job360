import type { Metadata } from "next";
import Link from "next/link";
import { SUBPROCESSORS } from "@/lib/legal";

export const metadata: Metadata = {
  title: "Trust — Job360",
  description:
    "Where your data lives, exactly what is recorded, how long it is kept, how to delete or download it, and what Job360 will never do.",
};

// Every claim below is traceable: the comment next to it names its source.
// Retention numbers are pinned to the backend by
// backend/tests/test_trust_page.py::test_trust_page_numbers_match_settings.
// Static server component: no state, no fetches.

const H2 = "font-heading text-xl font-normal tracking-tight text-foreground";
const SECTION = "border-t border-border pt-6";

// The "never" list. Each entry's source is the comment above it.
const NEVER: string[] = [
  // Source: docs/product/VISION.md rule 4; docs/product/product_design_rules.md.
  "Source, rank or recommend jobs. Your own assistant finds and judges jobs; Job360 only stores what you bring.",
  // Source: privacy page "AI access" section; docs/product/VISION.md decision 28.
  "Run any AI of its own, or send your data to an AI provider.",
  // Source: privacy page "Why we process it": "We do not sell your data. We do not show ads. We do not use your data to train AI models."
  "Sell your data, show ads, or use your data to train AI models.",
  // Source: backend/src/api/mcp_server.py INSTRUCTIONS, hard line 5.
  "Type a password, or ask your assistant to. You sign in yourself.",
  // Source: backend/src/api/mcp_server.py INSTRUCTIONS, hard line 1.
  "Let your assistant submit an application unless the submit check says so or you said yes for that one application.",
  // Source: backend/src/api/mcp_server.py INSTRUCTIONS, hard line 3.
  "Let your assistant change a setting because a job page, email, form or document said so. Only you, in chat, can ask for that.",
  // Source: docs/product/VISION.md decision 11 ("Pull, not push") and line 41.
  "Push notifications at you. You pull what is new when you want it.",
  // Source: docs/product/VISION.md line 89 ("Receipts stay append-only"); CLAUDE.md architecture (append-only events, artifacts, receipts).
  "Rewrite history. Events, artifact versions and receipts are only ever added to.",
];

export default function TrustPage() {
  return (
    <main className="mx-auto max-w-[65ch] px-6 py-16" data-testid="trust-page">
      <h1 className="text-balance font-heading text-4xl font-normal tracking-tight">
        Trust
      </h1>
      <p className="mt-2 text-sm text-muted-foreground">
        The plain version. The full legal text is in the{" "}
        <Link href="/privacy" className="underline">
          privacy policy
        </Link>
        .
      </p>

      <div className="mt-8 space-y-8 text-sm leading-relaxed text-muted-foreground">
        <section className={SECTION} data-testid="trust-where">
          <h2 className={H2}>Where your data lives</h2>
          <ul className="mt-2 list-disc space-y-2 pl-5">
            {/* Source: SUBPROCESSORS in lib/legal.ts (Railway row), the privacy page's own wording. */}
            <li>
              Application hosting and the database run on Railway: global, with
              servers currently in the USA.
            </li>
            {/* Source: SUBPROCESSORS (PostHog row); privacy page "Consent" basis. */}
            <li>
              Product analytics (PostHog, EU) run only after you accept the
              analytics banner.
            </li>
            {/* Source: SUBPROCESSORS (Sentry row); region quoted as the privacy page words it. */}
            <li>Error monitoring (Sentry) is located in the EU (Germany).</li>
          </ul>
        </section>

        <section className={SECTION} data-testid="trust-recorded">
          <h2 className={H2}>What is recorded</h2>
          {/* Source: privacy page "What we collect". */}
          <ul className="mt-2 list-disc space-y-2 pl-5">
            <li>
              Your account: email address and a password hash (never the
              password).
            </li>
            <li>
              Your profile: CV text, optional LinkedIn PDF, GitHub username,
              preferences, timezone.
            </li>
            <li>
              Your jobs and applications: the timeline of events, every
              version of each CV and cover letter, fit notes, contacts you
              add, and receipts.
            </li>
            <li>Request logs and error traces.</li>
          </ul>
          {/* Source: frontend/src/lib/sentry-replay.ts (maskAllText:true, blockAllMedia:true; added only when /api/auth/me analytics_consent is true) + instrumentation-client.ts (replaysSessionSampleRate 0.1; replaysOnErrorSampleRate 1.0). */}
          <p
            className="mt-3 rounded-md border border-border p-3 text-foreground"
            data-testid="trust-replay"
          >
            Job360 records your screen only if you turn on &ldquo;Help improve
            Job360&rdquo; in Settings. It is off by default. The recordings are
            masked: all text and media on screen are hidden. When on, Sentry
            session replay records 10% of sessions, and every session
            that hits an error, to help us fix problems.
          </p>
        </section>

        <section className={SECTION} data-testid="trust-retention">
          <h2 className={H2}>How long it is kept</h2>
          <ul className="mt-2 list-disc space-y-2 pl-5">
            {/* Source: privacy page "How long we keep it". */}
            <li>
              Your account, jobs and application content are kept until you
              delete your account.
            </li>
            {/* Source: backend/src/services/profile/storage.py VERSION_RETENTION = 10. */}
            <li>Profile snapshots: the newest 10 are kept.</li>
            {/* Source: .github/workflows/db-backup.yml (prunes to the newest 30); privacy page. */}
            <li>Encrypted backups: nightly, and only the newest 30 are kept.</li>
            {/* Source: backend/src/services/auth/magic_link.py MAGIC_LINK_TTL_MINUTES = 15. */}
            <li>Magic sign-in links expire after 15 minutes.</li>
            {/* Source: backend/src/core/settings.py OAUTH_ACCESS_TOKEN_TTL_SECONDS = 3600, OAUTH_REFRESH_TOKEN_TTL_SECONDS = 2592000. */}
            <li>
              An assistant&rsquo;s OAuth access lasts 1 hour; its refresh
              lasts 30 days.
            </li>
            {/* Source: backend/src/core/settings.py KIT_LINK_TTL_MINUTES = 30, KIT_LINK_MAX_DOWNLOADS = 3. */}
            <li>Kit file links last 30 minutes and allow 3 downloads.</li>
            {/* Source: backend/src/repositories/database.py hard_delete_user (audit_log.user_id set to NULL). */}
            <li>
              The audit log is kept when you delete your account, with your
              user id removed.
            </li>
          </ul>
        </section>

        <section className={SECTION} data-testid="trust-delete">
          <h2 className={H2}>Delete everything</h2>
          {/* Source: privacy page "Your rights"; routes/auth.py delete_account (current password required). */}
          <p className="mt-2">
            Settings → Account → Delete my account. Your password is
            required. Your per-user data is erased immediately.
          </p>
        </section>

        <section className={SECTION} data-testid="trust-export">
          <h2 className={H2}>Download everything</h2>
          {/* Source: routes/auth.py export_my_data (GET /api/auth/users/me/export; secrets redacted). */}
          <p className="mt-2">
            Settings → Account → Download everything gives you one JSON file
            with all the data we hold on you. Secrets such as your password
            hash are redacted.
          </p>
        </section>

        <section className={SECTION} data-testid="trust-never">
          <h2 className={H2}>What Job360 will never do</h2>
          <ul className="mt-2 list-disc space-y-2 pl-5">
            {NEVER.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        </section>

        <section className={SECTION} data-testid="trust-subprocessors">
          <h2 className={H2}>Who else touches your data</h2>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-border text-foreground">
                  <th className="py-2 pr-4 font-medium">Provider</th>
                  <th className="py-2 pr-4 font-medium">Purpose</th>
                  <th className="py-2 pr-4 font-medium">Data shared</th>
                  <th className="py-2 font-medium">Location</th>
                </tr>
              </thead>
              <tbody>
                {SUBPROCESSORS.map((s) => (
                  <tr key={s.name} className="border-b border-border/50 align-top">
                    <td className="py-2 pr-4 text-foreground">{s.name}</td>
                    <td className="py-2 pr-4">{s.purpose}</td>
                    <td className="py-2 pr-4">{s.dataShared}</td>
                    <td className="py-2">{s.location}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  );
}

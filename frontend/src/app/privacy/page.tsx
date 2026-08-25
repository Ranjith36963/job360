import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Privacy Policy — Job360",
  description:
    "What data Job360 collects, who we share it with, how long we keep it, and how to exercise your rights.",
};

// A legal document states when it changed; never derive this at render time.
const LAST_UPDATED = "24 August 2026";

const SUBPROCESSORS: {
  name: string;
  purpose: string;
  dataShared: string;
  location: string;
}[] = [
  {
    name: "OpenAI",
    purpose: "CV parsing and tailored-document generation (primary AI provider)",
    dataShared: "Text extracted from your CV / LinkedIn PDF; job descriptions",
    location: "USA",
  },
  {
    name: "Google (Gemini), Groq, Cerebras",
    purpose: "CV parsing (fallback AI providers, used only if the primary fails)",
    dataShared: "Text extracted from your CV / LinkedIn PDF",
    location: "USA",
  },
  {
    name: "Railway",
    purpose: "Application hosting and database",
    dataShared: "All service data (encrypted in transit and at rest)",
    location: "EU/USA",
  },
  {
    name: "Resend",
    purpose: "Transactional email (login links, verification, notifications)",
    dataShared: "Your email address and message content",
    location: "USA",
  },
  {
    name: "Cloudflare R2",
    purpose: "Encrypted database backups",
    dataShared:
      "Ciphertext only — backups are encrypted before upload; Cloudflare never sees readable data",
    location: "EU",
  },
  {
    name: "PostHog (EU)",
    purpose: "Product analytics (which features are used)",
    dataShared: "Usage events and pseudonymous identifiers",
    location: "EU",
  },
  {
    name: "Sentry",
    purpose: "Error monitoring",
    dataShared: "Error traces and request metadata (request bodies are scrubbed)",
    location: "EU/USA",
  },
  {
    name: "GitHub",
    purpose: "Fetching your public repositories, if you link a GitHub username",
    dataShared: "The GitHub username you provide",
    location: "USA",
  },
];

export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-3xl font-semibold">Privacy Policy</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Last updated: {LAST_UPDATED}
      </p>

      <div className="mt-8 space-y-8 text-sm leading-relaxed text-muted-foreground">
        <section>
          <h2 className="text-lg font-medium text-foreground">Who we are</h2>
          <p className="mt-2">
            Job360 (&ldquo;we&rdquo;, &ldquo;us&rdquo;) operates the job-search
            service at job360.uk. We are the data controller for the personal
            data described here. To contact us about anything in this policy,
            email{" "}
            <a href="mailto:privacy@job360.uk" className="underline">
              privacy@job360.uk
            </a>
            .
          </p>
        </section>

        <section>
          <h2 className="text-lg font-medium text-foreground">
            What we collect
          </h2>
          <ul className="mt-2 list-disc space-y-2 pl-5">
            <li>
              <span className="text-foreground">Account data</span> — your
              email address and a password. Passwords are stored only as an
              argon2id hash; we cannot read them. If you sign in by magic link,
              no password is stored at all.
            </li>
            <li>
              <span className="text-foreground">Profile data you upload</span>{" "}
              — the text of your CV (PDF/DOCX), an optional LinkedIn profile
              PDF, an optional GitHub username, your job preferences, and your
              timezone.
            </li>
            <li>
              <span className="text-foreground">Activity data</span> — jobs you
              save or dismiss, applications you track, notification settings,
              and documents you generate.
            </li>
            <li>
              <span className="text-foreground">Technical data</span> — request
              logs, error traces, and product-usage analytics.
            </li>
          </ul>
          <p className="mt-2">
            Job listings themselves are aggregated from public third-party
            sources and are not personal data about you.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-medium text-foreground">
            Why we process it (lawful basis)
          </h2>
          <ul className="mt-2 list-disc space-y-2 pl-5">
            <li>
              <span className="text-foreground">Contract</span> — matching and
              scoring jobs against your profile, generating tailored documents
              you request, and operating your account.
            </li>
            <li>
              <span className="text-foreground">Consent</span> — sending
              notifications to channels you connect (email or a webhook you
              control). You can disconnect a channel at any time.
            </li>
            <li>
              <span className="text-foreground">Legitimate interest</span> —
              keeping the service secure, fixing errors, and understanding
              which features are used.
            </li>
          </ul>
          <p className="mt-2">
            We do not sell your data. We do not show ads. We do not use your
            data to train AI models.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-medium text-foreground">
            AI processing — read this part
          </h2>
          <p className="mt-2">
            When you upload a CV or LinkedIn PDF, or generate a tailored CV or
            cover letter, the text is sent to the AI providers listed below to
            be parsed or drafted. This is core to how Job360 works — without
            it, we cannot build your profile or score jobs. We use these
            providers&rsquo; business APIs, which contractually do not use your
            content to train their models. Your original files and the
            extracted profile stay on our servers; the AI providers process
            text transiently.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-medium text-foreground">
            Who we share data with (subprocessors)
          </h2>
          <p className="mt-2">
            We share data only with the service providers below, only for the
            purposes stated, and never for their own marketing.
          </p>
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
          <p className="mt-2">
            Where a provider is outside the UK, transfers rely on that
            provider&rsquo;s standard contractual clauses / UK addendum. We
            will update this list before adding any new subprocessor.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-medium text-foreground">
            How long we keep it
          </h2>
          <ul className="mt-2 list-disc space-y-2 pl-5">
            <li>
              Aggregated job listings are deleted after 30 days automatically.
            </li>
            <li>
              Your account and profile data are kept until you delete your
              account.
            </li>
            <li>
              Encrypted backups are retained on a rolling basis and expire
              automatically; deleted accounts age out of backups with that
              cycle.
            </li>
          </ul>
        </section>

        <section>
          <h2 className="text-lg font-medium text-foreground">
            How we protect it
          </h2>
          <p className="mt-2">
            All traffic is encrypted in transit (HTTPS/HSTS). Passwords are
            argon2id-hashed. Notification-channel credentials (webhooks, bot
            tokens) are encrypted at rest. Database backups are encrypted
            before they leave our infrastructure, so the backup-storage
            provider only ever holds ciphertext.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-medium text-foreground">Your rights</h2>
          <p className="mt-2">
            Under UK GDPR you can access, correct, export, or delete your
            personal data, object to processing, and withdraw consent.
            Deleting your account (Settings → Account → Delete) erases your
            per-user data immediately — this is our built-in Article 17
            (&ldquo;right to erasure&rdquo;) mechanism. For anything else,
            email{" "}
            <a href="mailto:privacy@job360.uk" className="underline">
              privacy@job360.uk
            </a>{" "}
            and we will respond within one month. You also have the right to
            complain to the UK Information Commissioner&rsquo;s Office (
            <a
              href="https://ico.org.uk"
              className="underline"
              rel="noopener noreferrer"
              target="_blank"
            >
              ico.org.uk
            </a>
            ).
          </p>
        </section>

        <section>
          <h2 className="text-lg font-medium text-foreground">
            Changes to this policy
          </h2>
          <p className="mt-2">
            If we make material changes, we will update the date at the top
            and notify registered users by email before the changes take
            effect.
          </p>
        </section>
      </div>
    </main>
  );
}

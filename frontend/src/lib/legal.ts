// Shared legal facts: one list, rendered by BOTH /privacy and /trust so the two
// pages cannot drift apart. Edit here only.

export const SUBPROCESSORS: {
  name: string;
  purpose: string;
  dataShared: string;
  location: string;
}[] = [
  {
    name: "Railway",
    purpose: "Application hosting and database",
    dataShared: "All service data (encrypted in transit and at rest)",
    location: "Global (servers currently in the USA)",
  },
  {
    name: "Resend",
    purpose: "Transactional email (login links, email verification, password reset)",
    dataShared: "Your email address and message content",
    location: "USA",
  },
  {
    name: "Cloudflare R2",
    purpose: "Nightly encrypted database backups (kept as the newest 30)",
    dataShared:
      "Ciphertext only — backups are encrypted (AES-256) before upload; Cloudflare never sees readable data",
    location: "Global (Cloudflare network)",
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
    dataShared:
      "Error traces and request metadata (request bodies are scrubbed). Only if you turn on \"Help improve Job360\" in Account: masked screen recordings (session replay, all text and media hidden) of about 1 in 10 sessions and every session where an error happens",
    location: "EU (Germany)",
  },
  {
    name: "GitHub",
    purpose: "Fetching your public repositories, if you link a GitHub username",
    dataShared: "The GitHub username you provide",
    location: "USA",
  },
];

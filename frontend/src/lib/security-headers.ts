// ---------------------------------------------------------------------------
// M15 — baseline security headers, extracted from next.config.ts so they're
// unit-testable without importing the whole (Sentry-wrapped) Next config.
//
// Kept permissive enough on script/connect to not break Sentry (direct-to-
// ingest, no tunnel configured — see src/instrumentation-client.ts) or
// PostHog (src/components/providers/PostHogProviderWrapper.tsx, api_host
// defaults to eu.i.posthog.com but is env-overridable via
// NEXT_PUBLIC_POSTHOG_HOST). Tighten later with nonces if/when Sentry gets a
// tunnel route.
// ---------------------------------------------------------------------------

export const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  // Next.js ships inline bootstrap scripts and no nonce infra exists yet;
  // 'unsafe-eval' is required in dev (React Refresh) and harmless to keep in
  // prod since 'self' already scopes script origins.
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  // Sentry (no tunnel — ingests directly) + PostHog (EU/US cloud, both
  // possible via NEXT_PUBLIC_POSTHOG_HOST) + same-origin API/websocket.
  "connect-src 'self' https://*.sentry.io https://*.ingest.sentry.io https://eu.i.posthog.com https://us.i.posthog.com https://app.posthog.com",
  // Belt-and-suspenders with X-Frame-Options below — no one may frame us.
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

export const SECURITY_HEADERS: { key: string; value: string }[] = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Content-Security-Policy", value: CONTENT_SECURITY_POLICY },
];

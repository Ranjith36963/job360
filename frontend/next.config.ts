import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

// Backend origin the `/api/*` proxy forwards to. Set BACKEND_ORIGIN in the
// deploy env; defaults to the local dev backend.
const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || "http://localhost:8000";

const nextConfig: NextConfig = {
  // Same-origin proxy: the browser calls `/api/*` on the frontend origin and
  // Next forwards it to the backend. This keeps frontend + backend same-origin
  // so the host-only session cookie is always sent and the middleware auth gate
  // works regardless of where the backend is actually hosted.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_ORIGIN}/api/:path*`,
      },
    ];
  },
};

export default withSentryConfig(nextConfig, {
  // Suppress Sentry build-time logs unless we are in CI (where we want to see them).
  silent: !process.env.CI,

  // Source-map upload is opt-in: set SENTRY_ORG + SENTRY_PROJECT + SENTRY_AUTH_TOKEN
  // in CI to enable it. Without them the build still works — errors are captured but
  // stack traces show minified names.
});

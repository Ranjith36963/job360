import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";
import { SECURITY_HEADERS } from "./src/lib/security-headers";

// Backend origin the `/api/*` proxy forwards to. Set BACKEND_ORIGIN in the
// deploy env; defaults to the local dev backend.
const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || "http://localhost:8000";

const nextConfig: NextConfig = {
  // L6 — emit a self-contained server bundle in `.next/standalone`, so the
  // runtime image copies just that plus static assets instead of the whole
  // `node_modules` tree. Next traces the modules actually reached at runtime;
  // dev/build-only dependencies never make it into the image.
  //
  // This is purely a packaging change: same code paths, same rewrites, same
  // headers — the app is byte-identical, it just ships without the build
  // toolchain riding along. Smaller image = faster deploys and a smaller
  // surface for dependency CVEs to be reported against.
  output: "standalone",

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

  // M15 — no security headers were set at all before this. Applies to every
  // route (including /api/* rewrites, which pass through Next's own response
  // pipeline for the matched pages/assets).
  async headers() {
    return [
      {
        source: "/:path*",
        headers: SECURITY_HEADERS,
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

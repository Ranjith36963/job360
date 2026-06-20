import type { NextConfig } from "next";

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

export default nextConfig;

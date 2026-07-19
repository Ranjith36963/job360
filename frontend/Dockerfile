# ─── Stage 1: install ALL deps (including dev) for the build ───────────────
FROM node:20-alpine AS builder
WORKDIR /app

COPY package.json package-lock.json ./
# Install dev deps too — TypeScript, ESLint, Tailwind, etc. are needed for build.
RUN npm ci

COPY . .
# next.config.ts reads BACKEND_ORIGIN when it builds the /api proxy rewrite, and
# NEXT_PUBLIC_* are inlined into the client bundle — BOTH happen at BUILD time, so
# they must be present here, not just at runtime. Railway passes service variables
# to Dockerfile builds as --build-arg, so declaring the ARGs picks them up.
ARG BACKEND_ORIGIN
ARG NEXT_PUBLIC_SENTRY_DSN
ARG NEXT_PUBLIC_POSTHOG_KEY
ARG NEXT_PUBLIC_POSTHOG_HOST
ENV BACKEND_ORIGIN=$BACKEND_ORIGIN
ENV NEXT_PUBLIC_SENTRY_DSN=$NEXT_PUBLIC_SENTRY_DSN
ENV NEXT_PUBLIC_POSTHOG_KEY=$NEXT_PUBLIC_POSTHOG_KEY
ENV NEXT_PUBLIC_POSTHOG_HOST=$NEXT_PUBLIC_POSTHOG_HOST
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ─── Stage 2: production node_modules only ──────────────────────────────────
FROM node:20-alpine AS prod-deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# ─── Stage 3: minimal runner image ──────────────────────────────────────────
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Non-root user
RUN addgroup --system nodejs && adduser --system --ingroup nodejs nextjs

# L6 — standalone output (next.config.ts `output: "standalone"`).
#
# Next traces the modules actually reachable at runtime and emits a
# self-contained server in `.next/standalone`, including its own minimal
# node_modules. So the runner copies three things instead of the whole
# dependency tree:
#   .next/standalone  — the server + only the modules it really needs
#   .next/static      — hashed build assets (NOT included in standalone)
#   public            — static files served as-is
#
# The prod-deps stage is intentionally no longer copied from: shipping the full
# production node_modules was the finding (L6). Keep that stage for now — it is
# still a useful cache layer and a fallback if standalone is ever reverted.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static     ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public           ./public

USER nextjs

EXPOSE 3000

# standalone emits its own server.js — `npm start` (next start) is NOT used
# here and would fail, since the Next CLI is not part of the traced bundle.
# PORT/HOSTNAME are read by that server; bind 0.0.0.0 so the container is
# reachable from outside (default would be localhost only).
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

CMD ["node", "server.js"]

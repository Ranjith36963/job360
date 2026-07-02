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

# Next.js standalone output is NOT configured in next.config.ts, so we
# ship the full .next/ build output + production node_modules.
COPY --from=builder  --chown=nextjs:nodejs /app/.next        ./.next
COPY --from=builder  --chown=nextjs:nodejs /app/public       ./public
COPY --from=builder  --chown=nextjs:nodejs /app/next.config.ts ./next.config.ts
COPY --from=builder  --chown=nextjs:nodejs /app/package.json  ./package.json
COPY --from=prod-deps --chown=nextjs:nodejs /app/node_modules ./node_modules

USER nextjs

EXPOSE 3000

CMD ["npm", "start"]

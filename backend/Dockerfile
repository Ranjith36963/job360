FROM python:3.12-slim

# curl is needed for the HEALTHCHECK.
# psycopg[binary] ships pre-compiled wheels, so libpq-dev is NOT required.
# pdfplumber and python-docx are pure-Python — no extra OS deps.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the whole backend directory.
# .dockerignore excludes data/, tests/, __pycache__, .venv, etc.
COPY . .

# Install runtime deps only. The [semantic]/[esco] extra (sentence-transformers
# — ESCO skill-canonicalisation, hard rule #28) and the ARQ worker went with
# slice 5 (#483) / the mission sweep: no code path in this image imports them,
# and hard rule #28 says installing the extra alone still turns nothing on. No
# [dev] either.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

# The Job360 browser agent (docs/product/BROWSER_AGENT.md) — only when the
# owner turns it on. Railway hands service variables to the build as ARGs, so
# setting BROWSER_AGENT_ENABLED=1 on the backend service is the one switch:
# the build installs Playwright + Chromium (with its OS libraries) and the
# runtime flag turns the `browser` MCP tool on. Unset = the same image as before.
ARG BROWSER_AGENT_ENABLED=0
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN if [ "$BROWSER_AGENT_ENABLED" = "1" ] || [ "$BROWSER_AGENT_ENABLED" = "true" ]; then \
      pip install --no-cache-dir ".[browser]" \
      && python -m playwright install --with-deps chromium \
      && chmod -R a+rX /ms-playwright \
      && rm -rf /var/lib/apt/lists/*; \
    fi

# Drop privileges — never run the app as root in production.
RUN addgroup --system job360 \
 && adduser --system --ingroup job360 --no-create-home job360 \
 && chown -R job360:job360 /app
USER job360

EXPOSE 8000

# Hit the /api/health endpoint. $PORT is injected by Railway (dynamic); fall
# back to 8000 for local `docker run`. Shell-form so ${PORT} expands.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:${PORT:-8000}/api/health || exit 1

# Shell form (not exec) so ${PORT} is expanded by the shell. Railway assigns a
# dynamic port and routes to it; the app MUST listen on $PORT or it's unreachable.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}

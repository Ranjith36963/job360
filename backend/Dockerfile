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

# Install runtime deps + the [semantic] extra (sentence-transformers / chromadb)
# so Engine 3 (semantic search) can run. Install CPU-only torch FIRST so pip
# doesn't pull the ~2.5 GB CUDA build (there's no GPU on Railway). No [dev].
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir ".[semantic]" arq

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

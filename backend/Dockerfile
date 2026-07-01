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

# Install runtime deps only.
# - No [dev] extras (pytest, mypy, aioresponses …).
# - No [semantic] extra (~300 MB sentence-transformers/chromadb).
# - arq is the ARQ task-queue runner used by the worker process; it is NOT
#   declared in pyproject.toml yet, so we install it alongside the package.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir . arq

# Drop privileges — never run the app as root in production.
RUN addgroup --system job360 \
 && adduser --system --ingroup job360 --no-create-home job360 \
 && chown -R job360:job360 /app
USER job360

EXPOSE 8000

# Hit the existing /health endpoint (backend/src/api/routes/health.py).
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

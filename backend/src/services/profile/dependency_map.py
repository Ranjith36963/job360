"""Dependency-name → display-skill map for GitHub dependency-file parsing.

Batch 1.2 (Pillar 1). Maps package-manager dependency names across 7
ecosystems to human-readable skill names. Each dependency keys into
one canonical skill; aliases (e.g. ``@types/react`` → ``React``) land
on the same skill so dedup works downstream.

Ecosystem scopes the dependency to its registry so cross-ecosystem
collisions (``nose`` the Python testing lib vs ``nose`` the npm pkg)
resolve cleanly.

The ~250 mappings below cover the most-cited libraries in the 2024
StackOverflow + State-of-JS + PyPI top-downloads datasets — i.e.
signals a recruiter / job-matching engine would want to see. This is
not exhaustive; untracked dependencies simply don't contribute a
skill and fall back to the language-level signal from the existing
``LANGUAGE_TO_SKILL`` map in ``github_enricher.py``.
"""

from __future__ import annotations

Ecosystem = str  # Literal["npm", "pypi", "cargo", "rubygems", "go", "composer"]


# ── npm / JavaScript + TypeScript (package.json) ────────────────────

_NPM: dict[str, str] = {
    # React / Next.js
    "react": "React",
    "react-dom": "React",
    "next": "Next.js",
    "@next/font": "Next.js",
    "@types/react": "React",
    "@types/react-dom": "React",
    # Vue / Angular / Svelte / Solid
    "vue": "Vue.js",
    "@vue/cli": "Vue.js",
    "nuxt": "Nuxt.js",
    "@angular/core": "Angular",
    "@angular/cli": "Angular",
    "svelte": "Svelte",
    "@sveltejs/kit": "SvelteKit",
    "solid-js": "SolidJS",
    # State / data layer
    "redux": "Redux",
    "@reduxjs/toolkit": "Redux",
    "zustand": "Zustand",
    "mobx": "MobX",
    "recoil": "Recoil",
    "jotai": "Jotai",
    "@tanstack/react-query": "TanStack Query",
    "react-query": "React Query",
    "swr": "SWR",
    "apollo-client": "Apollo GraphQL",
    "@apollo/client": "Apollo GraphQL",
    "graphql": "GraphQL",
    "urql": "GraphQL",
    "relay-runtime": "Relay",
    # Styling
    "tailwindcss": "Tailwind CSS",
    "@tailwindcss/postcss": "Tailwind CSS",
    "styled-components": "styled-components",
    "@emotion/react": "Emotion",
    "sass": "Sass/SCSS",
    "postcss": "PostCSS",
    "material-ui": "Material UI",
    "@mui/material": "Material UI",
    "@chakra-ui/react": "Chakra UI",
    "antd": "Ant Design",
    "@headlessui/react": "Headless UI",
    "shadcn": "shadcn/ui",
    "lucide-react": "Lucide",
    "framer-motion": "Framer Motion",
    "motion": "Framer Motion",
    # Node.js runtimes / servers
    "express": "Express.js",
    "fastify": "Fastify",
    "koa": "Koa",
    "hapi": "Hapi",
    "nestjs": "NestJS",
    "@nestjs/core": "NestJS",
    "hono": "Hono",
    "bun": "Bun",
    "deno": "Deno",
    # Bundlers / build
    "webpack": "Webpack",
    "vite": "Vite",
    "rollup": "Rollup",
    "esbuild": "esbuild",
    "turbo": "Turbopack",
    "parcel": "Parcel",
    # Testing
    "jest": "Jest",
    "@jest/core": "Jest",
    "vitest": "Vitest",
    "mocha": "Mocha",
    "chai": "Chai",
    "@testing-library/react": "React Testing Library",
    "cypress": "Cypress",
    "playwright": "Playwright",
    "@playwright/test": "Playwright",
    "puppeteer": "Puppeteer",
    # HTTP / networking
    "axios": "Axios",
    "ky": "Ky",
    "node-fetch": "Fetch API",
    "socket.io": "Socket.IO",
    # Data / ORM
    "prisma": "Prisma",
    "@prisma/client": "Prisma",
    "typeorm": "TypeORM",
    "mongoose": "Mongoose",
    "sequelize": "Sequelize",
    "drizzle-orm": "Drizzle ORM",
    "knex": "Knex",
    # Linting / TS
    "typescript": "TypeScript",
    "eslint": "ESLint",
    "prettier": "Prettier",
    "@typescript-eslint/parser": "TypeScript",
    # Monorepo / tooling
    "nx": "Nx",
    "lerna": "Lerna",
    "pnpm": "pnpm",
    "yarn": "Yarn",
    # Auth
    "passport": "Passport.js",
    "next-auth": "NextAuth.js",
    "@auth0/nextjs-auth0": "Auth0",
    "firebase": "Firebase",
    "@supabase/supabase-js": "Supabase",
    # Analytics / telemetry
    "posthog-js": "PostHog",
    "@sentry/nextjs": "Sentry",
    "@sentry/browser": "Sentry",
    # Desktop / mobile
    "electron": "Electron",
    "react-native": "React Native",
    "expo": "Expo",
    "@ionic/react": "Ionic",
}


# ── PyPI / Python (requirements.txt + pyproject.toml) ───────────────

_PYPI: dict[str, str] = {
    # Web frameworks
    "django": "Django",
    "django-rest-framework": "Django REST Framework",
    "djangorestframework": "Django REST Framework",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "starlette": "Starlette",
    "pyramid": "Pyramid",
    "tornado": "Tornado",
    "aiohttp": "aiohttp",
    "sanic": "Sanic",
    "falcon": "Falcon",
    "quart": "Quart",
    "litestar": "Litestar",
    # ASGI servers
    "uvicorn": "Uvicorn",
    "hypercorn": "Hypercorn",
    "gunicorn": "Gunicorn",
    "daphne": "Daphne",
    # Data / DB
    "sqlalchemy": "SQLAlchemy",
    "alembic": "Alembic",
    "asyncpg": "asyncpg",
    "aiosqlite": "aiosqlite",
    "psycopg2": "PostgreSQL",
    "psycopg2-binary": "PostgreSQL",
    "psycopg": "PostgreSQL",
    "pymongo": "MongoDB",
    "motor": "MongoDB",
    "redis": "Redis",
    "elasticsearch": "Elasticsearch",
    "cassandra-driver": "Cassandra",
    "peewee": "Peewee",
    "tortoise-orm": "Tortoise ORM",
    "pony": "Pony ORM",
    # Data science / ML
    "numpy": "NumPy",
    "pandas": "Pandas",
    "scipy": "SciPy",
    "scikit-learn": "scikit-learn",
    "sklearn": "scikit-learn",
    "matplotlib": "Matplotlib",
    "seaborn": "Seaborn",
    "plotly": "Plotly",
    "bokeh": "Bokeh",
    "dash": "Dash",
    "streamlit": "Streamlit",
    "gradio": "Gradio",
    "polars": "Polars",
    "duckdb": "DuckDB",
    "pyarrow": "Apache Arrow",
    "dask": "Dask",
    "ray": "Ray",
    # Deep learning
    "torch": "PyTorch",
    "torchvision": "PyTorch",
    "tensorflow": "TensorFlow",
    "keras": "Keras",
    "jax": "JAX",
    "flax": "JAX",
    "transformers": "Hugging Face Transformers",
    "sentence-transformers": "sentence-transformers",
    "datasets": "Hugging Face Datasets",
    "accelerate": "Hugging Face Accelerate",
    "peft": "PEFT / LoRA",
    "lightning": "PyTorch Lightning",
    "pytorch-lightning": "PyTorch Lightning",
    "xgboost": "XGBoost",
    "lightgbm": "LightGBM",
    "catboost": "CatBoost",
    "onnxruntime": "ONNX",
    "mlflow": "MLflow",
    "wandb": "Weights & Biases",
    "dvc": "DVC",
    # LLM / agentic
    "langchain": "LangChain",
    "langgraph": "LangGraph",
    "llama-index": "LlamaIndex",
    "llama_index": "LlamaIndex",
    "openai": "OpenAI API",
    "anthropic": "Anthropic Claude API",
    "google-generativeai": "Gemini API",
    "groq": "Groq API",
    "cerebras-cloud-sdk": "Cerebras API",
    "chromadb": "ChromaDB",
    "pinecone-client": "Pinecone",
    "weaviate-client": "Weaviate",
    "pgvector": "pgvector",
    "qdrant-client": "Qdrant",
    # CV / audio
    "opencv-python": "OpenCV",
    "pillow": "Pillow",
    "librosa": "Librosa",
    "whisper": "Whisper",
    "openai-whisper": "Whisper",
    # NLP (classic)
    "nltk": "NLTK",
    "spacy": "spaCy",
    "gensim": "Gensim",
    # HTTP / async
    "httpx": "httpx",
    "requests": "Requests",
    "urllib3": "urllib3",
    "aiohttp-cors": "aiohttp",
    # Scraping / automation
    "beautifulsoup4": "BeautifulSoup",
    "bs4": "BeautifulSoup",
    "scrapy": "Scrapy",
    "selenium": "Selenium",
    "playwright": "Playwright",
    # Testing
    "pytest": "pytest",
    "pytest-asyncio": "pytest",
    "pytest-cov": "pytest",
    "unittest2": "unittest",
    "nose2": "nose2",
    "tox": "tox",
    "hypothesis": "Hypothesis",
    # Config / validation
    "pydantic": "Pydantic",
    "pydantic-settings": "Pydantic",
    "marshmallow": "Marshmallow",
    "attrs": "attrs",
    "dataclasses-json": "dataclasses-json",
    # Task queues / workers
    "celery": "Celery",
    "rq": "RQ",
    "arq": "ARQ",
    "dramatiq": "Dramatiq",
    "huey": "Huey",
    # Infra / deployment / cloud
    "boto3": "AWS / boto3",
    "botocore": "AWS / boto3",
    "google-cloud-storage": "Google Cloud",
    "azure-storage-blob": "Azure",
    "kubernetes": "Kubernetes",
    "docker": "Docker",
    "ansible": "Ansible",
    "terraform": "Terraform",
    "pulumi": "Pulumi",
    # Observability
    "opentelemetry-api": "OpenTelemetry",
    "opentelemetry-sdk": "OpenTelemetry",
    "prometheus-client": "Prometheus",
    "sentry-sdk": "Sentry",
    "structlog": "structlog",
    "loguru": "loguru",
    # Typing / linting
    "mypy": "mypy",
    "ruff": "Ruff",
    "black": "Black",
    "isort": "isort",
    "flake8": "Flake8",
    "pylint": "Pylint",
    # Env / DI / CLI
    "click": "Click",
    "typer": "Typer",
    "python-dotenv": "python-dotenv",
    "rich": "Rich",
}


# ── Cargo / Rust (Cargo.toml) ───────────────────────────────────────

_CARGO: dict[str, str] = {
    "tokio": "Tokio",
    "async-std": "async-std",
    "actix-web": "Actix Web",
    "actix": "Actix",
    "axum": "Axum",
    "rocket": "Rocket",
    "warp": "Warp",
    "hyper": "Hyper",
    "reqwest": "reqwest",
    "serde": "Serde",
    "serde_json": "Serde",
    "diesel": "Diesel",
    "sqlx": "SQLx",
    "sea-orm": "SeaORM",
    "tonic": "Tonic / gRPC",
    "clap": "Clap",
    "anyhow": "anyhow",
    "thiserror": "thiserror",
    "bevy": "Bevy",
    "wasm-bindgen": "WebAssembly",
    "wasmtime": "Wasmtime",
    "yew": "Yew",
    "leptos": "Leptos",
    "tauri": "Tauri",
    "rayon": "Rayon",
    "polars": "Polars",
    "tracing": "tracing",
    "tonic-build": "gRPC",
}


# ── RubyGems (Gemfile) ──────────────────────────────────────────────

_GEM: dict[str, str] = {
    "rails": "Ruby on Rails",
    "sinatra": "Sinatra",
    "hanami": "Hanami",
    "devise": "Devise",
    "puma": "Puma",
    "unicorn": "Unicorn",
    "sidekiq": "Sidekiq",
    "rspec": "RSpec",
    "rspec-rails": "RSpec",
    "minitest": "Minitest",
    "capybara": "Capybara",
    "rubocop": "RuboCop",
    "pry": "Pry",
    "activerecord": "ActiveRecord",
    "sequel": "Sequel",
    "graphql": "GraphQL",
    "grape": "Grape",
    "roda": "Roda",
    "stripe": "Stripe",
    "aws-sdk": "AWS / Ruby SDK",
}


# ── Go modules (go.mod) ─────────────────────────────────────────────

_GO: dict[str, str] = {
    "github.com/gin-gonic/gin": "Gin",
    "github.com/labstack/echo": "Echo",
    "github.com/gofiber/fiber": "Fiber",
    "github.com/go-chi/chi": "Chi",
    "github.com/gorilla/mux": "Gorilla Mux",
    "google.golang.org/grpc": "gRPC",
    "google.golang.org/protobuf": "Protocol Buffers",
    "github.com/spf13/cobra": "Cobra",
    "github.com/spf13/viper": "Viper",
    "gorm.io/gorm": "GORM",
    "entgo.io/ent": "Ent ORM",
    "github.com/jmoiron/sqlx": "sqlx",
    "github.com/lib/pq": "PostgreSQL",
    "github.com/jackc/pgx": "pgx / PostgreSQL",
    "github.com/go-redis/redis": "Redis",
    "go.mongodb.org/mongo-driver": "MongoDB",
    "github.com/aws/aws-sdk-go": "AWS / Go SDK",
    "github.com/aws/aws-sdk-go-v2": "AWS / Go SDK v2",
    "k8s.io/client-go": "Kubernetes",
    "github.com/prometheus/client_golang": "Prometheus",
    "go.uber.org/zap": "Zap",
    "go.opentelemetry.io/otel": "OpenTelemetry",
    "github.com/stretchr/testify": "testify",
}


# ── Composer / PHP (composer.json) ──────────────────────────────────

_COMPOSER: dict[str, str] = {
    "laravel/framework": "Laravel",
    "laravel/laravel": "Laravel",
    "symfony/symfony": "Symfony",
    "symfony/framework-bundle": "Symfony",
    "cakephp/cakephp": "CakePHP",
    "yiisoft/yii2": "Yii",
    "slim/slim": "Slim",
    "doctrine/orm": "Doctrine ORM",
    "phpunit/phpunit": "PHPUnit",
    "composer/composer": "Composer",
    "guzzlehttp/guzzle": "Guzzle",
    "monolog/monolog": "Monolog",
    "twig/twig": "Twig",
    "phpstan/phpstan": "PHPStan",
    "filp/whoops": "Whoops",
}


# ── Consolidated lookup ─────────────────────────────────────────────

ECOSYSTEM_MAPS: dict[Ecosystem, dict[str, str]] = {
    "npm": _NPM,
    "pypi": _PYPI,
    "cargo": _CARGO,
    "rubygems": _GEM,
    "go": _GO,
    "composer": _COMPOSER,
}


def lookup_skill(ecosystem: Ecosystem, dep_name: str) -> str | None:
    """Return the display-skill for a dependency, or ``None`` if unmapped.

    Lookup is case-insensitive to absorb package-name capitalisation
    drift across registries (PyPI normalises to lowercase on publish
    but npm does not).
    """
    table = ECOSYSTEM_MAPS.get(ecosystem)
    if not table:
        return None
    if dep_name in table:
        return table[dep_name]
    return table.get(dep_name.lower())


def total_mappings() -> int:
    """Return total entry count — used by tests to guard against accidental deletions."""
    return sum(len(m) for m in ECOSYSTEM_MAPS.values())

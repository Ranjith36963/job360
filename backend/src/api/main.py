"""FastAPI application for Job360 backend."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.dependencies import close_db, init_db
from src.api.routes import (
    actions,
    auth,
    channels,
    health,
    jobs,
    notifications,
    pipeline,
    profile,
    search,
)
from src.core.settings import LOG_LEVEL
from src.utils.logger import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tier-A Step-0 #9 — honour LOG_LEVEL env var at process boot.
    # setup_logging() configures the "job360" subtree; we also set the root
    # logger so libraries (uvicorn, fastapi, httpx) inherit the same level
    # when they haven't been individually configured.
    setup_logging(LOG_LEVEL)
    logging.getLogger().setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    await init_db()
    yield
    await close_db()


app = FastAPI(title="Job360 API", version="1.0.0", lifespan=lifespan)

# CORS — env-driven so dev / staging / prod can differ without a rebuild.
# Default keeps Batch 1 behaviour (localhost:3000) so existing dev flows work.
_origins = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(actions.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
# Batch 2 — auth + channel config
app.include_router(auth.router, prefix="/api")
app.include_router(channels.router, prefix="/api")
# Step-1.5 S3-D — notification ledger reader
app.include_router(notifications.router, prefix="/api")

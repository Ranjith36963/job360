"""FastAPI application for Job360 backend."""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.dependencies import init_db, close_db
from src.api.routes import (
    actions,
    auth,
    channels,
    health,
    jobs,
    pipeline,
    profile,
    search,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
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

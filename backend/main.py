"""Job360 FastAPI entrypoint.

Run from the backend/ directory:
    uvicorn main:app --reload
    python -m uvicorn main:app --host 0.0.0.0 --port 8000

The actual app wiring (routes, middleware, lifespan) lives in
src/api/main.py. This file exists as the canonical module path
that uvicorn and deployment platforms import.
"""
from src.api.main import app

__all__ = ["app"]


if __name__ == "__main__":
    import asyncio
    import sys

    import uvicorn

    # psycopg async needs the selector loop on Windows. uvicorn's default
    # ("auto") installs the ProactorEventLoop, which psycopg refuses. Run the
    # server under an explicit selector loop instead of uvicorn.run(loop="auto").
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        config = uvicorn.Config(app, host="127.0.0.1", port=8000, loop="asyncio")
        asyncio.run(uvicorn.Server(config).serve())
    else:
        uvicorn.run(app, host="127.0.0.1", port=8000)

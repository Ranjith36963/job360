"""Global exception logging (full-lifecycle logging gap E).

Without this, an unhandled route exception returns a 500 to the client and the
access log records `status 500` — but the *stack trace* only goes to the server's
stdout, untied to the request. This handler writes the exception (with traceback)
into data/logs/ on `job360.error`, tagged with the request_id, so a production
500 is always traceable to its cause.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.utils.logger import get_logger, get_request_id

_err_log = get_logger("error")  # "job360.error" → main job360 handlers (data/logs/)


async def log_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Log an unhandled exception with its traceback + request_id, return 500."""
    # request_id: the middleware resets the contextvar before this outermost
    # handler runs, so prefer the copy stashed on request.state.
    rid = getattr(getattr(request, "state", None), "request_id", None) or get_request_id()
    _err_log.error(
        "unhandled_exception",
        extra={
            "event": "unhandled_exception",
            "method": request.method,
            "path": request.url.path,
            "request_id": rid,
            "error": repr(exc),
        },
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


def register_exception_logging(app: FastAPI) -> None:
    """Attach the unhandled-exception logger to the app."""
    app.add_exception_handler(Exception, log_unhandled_exception)

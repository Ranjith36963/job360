"""Global exception + HTTP-error logging (logging gaps E + 3b).

- E: an unhandled route exception is logged WITH its traceback + request_id
  (the stack used to only hit stdout, untied to the request), then a clean 500.
- 3b: 4xx/5xx HTTPExceptions and 422 validation errors log their *reason*
  (detail / which field failed), so "why did this 422 / 403 / 404?" is
  answerable from data/logs/ — not just a bare status code in the access log.

All handlers reproduce FastAPI's default response exactly, so client behaviour
is unchanged. Routine 401s (the logged-out /me auth check) are NOT logged here —
they're high-volume and low-value, and the access log already records the status.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.utils.logger import get_logger, get_request_id

_err_log = get_logger("error")  # "job360.error" → main job360 handlers (data/logs/)


def _rid(request: Request):
    # The middleware resets the contextvar before the outermost handler runs,
    # so prefer the copy stashed on request.state.
    return getattr(getattr(request, "state", None), "request_id", None) or get_request_id()


async def log_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Log an unhandled exception with its traceback + request_id, return 500."""
    _err_log.error(
        "unhandled_exception",
        extra={
            "event": "unhandled_exception",
            "method": request.method,
            "path": request.url.path,
            "request_id": _rid(request),
            "error": repr(exc),
        },
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


async def log_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Log a 4xx/5xx HTTPException's reason, then return the standard response."""
    if exc.status_code != 401:  # skip the routine logged-out auth check (noisy)
        log = _err_log.error if exc.status_code >= 500 else _err_log.warning
        log(
            "http_error",
            extra={
                "event": "http_error",
                "method": request.method,
                "path": request.url.path,
                "status_code": exc.status_code,
                "detail": str(exc.detail),
                "request_id": _rid(request),
            },
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


async def log_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Log a 422 validation failure (which field / why), then return the standard 422."""
    errors = jsonable_encoder(exc.errors())
    _err_log.warning(
        "validation_error",
        extra={
            "event": "validation_error",
            "method": request.method,
            "path": request.url.path,
            "status_code": 422,
            "errors": errors[:10],
            "request_id": _rid(request),
        },
    )
    return JSONResponse(status_code=422, content={"detail": errors})


def register_exception_logging(app: FastAPI) -> None:
    """Attach the exception + HTTP-error loggers to the app."""
    app.add_exception_handler(Exception, log_unhandled_exception)
    app.add_exception_handler(StarletteHTTPException, log_http_exception)
    app.add_exception_handler(RequestValidationError, log_validation_error)

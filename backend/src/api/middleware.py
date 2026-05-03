"""HTTP middleware for Job360 FastAPI app."""
from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.utils.logger import _request_id_var, set_request_id


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Stamp every request with a correlation id.

    Honours an incoming ``X-Request-Id`` header (e.g. from a load-balancer or
    upstream service) so request chains stay traceable end-to-end. When no
    header is present a fresh 16-hex-char id is generated.

    The id is stored in ``_request_id_var`` so every ``JSONFormatter`` log line
    emitted during the request lifetime automatically carries it. It is also
    echoed back in the ``X-Request-Id`` response header.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
        token = set_request_id(rid)
        try:
            response = await call_next(request)
        finally:
            _request_id_var.reset(token)
        response.headers["X-Request-Id"] = rid
        return response

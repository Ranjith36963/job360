"""The application spine (docs/plans/2026-09-04-application-spine/spec.md).

An Application is born at `bring_job`, carries a durable job snapshot, and
its whole history is one append-only event log whose last status event is
cached on `applications.status`. Everything here is pure/DB-thin helpers —
the routes in `src.api.routes.applications` and the seven MCP tools in
`src.api.mcp_server` are the callers.
"""

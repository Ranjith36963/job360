"""Tenant / user identity helpers.

The project is single-tenant at CLI boundary (existing pipelines predate Batch 2).
After Batch 2, every per-user row carries a ``user_id``. When existing single-user
code paths INSERT without supplying one, the column DEFAULT lands the row under
the well-known placeholder user.
"""
from __future__ import annotations

DEFAULT_TENANT_ID: str = "00000000-0000-0000-0000-000000000001"
"""UUID of the placeholder user that owns pre-Batch-2 user_actions / applications.

Also used by the CLI run path and any non-authenticated ingestion surface so
legacy behaviour stays working without schema CASE-WHENs.
"""

"""The ONE clock the whole OAuth subsystem reads.

`oauth_flow` writes `created_at`/`expires_at` on `oauth_grants`,
`oauth_authorization_codes` and `oauth_tokens`; `oauth_clients.prune`
DELETES rows from those same tables by comparing those columns against
"now". Two modules, one set of timestamps — so they must share one time
frame or housekeeping can delete a row that was written a moment earlier
(issue #530: the sampled post-token prune wiped a just-issued refresh
token, and the next refresh answered "refresh token not found or already
used").

Deliberately a module-level function called as ``oauth_clock.now()``, never
imported by value: there is exactly one patch target, so a test cannot move
half the subsystem through time and leave the other half behind.
"""
from __future__ import annotations

from datetime import datetime, timezone


def now() -> datetime:
    """Current UTC time for every OAuth read, write and prune."""
    return datetime.now(timezone.utc)

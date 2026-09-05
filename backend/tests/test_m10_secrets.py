"""M10 — secrets must fail CLOSED, never default to an empty value.

Two guarantees pinned here:

1. ``src.core.settings`` must NOT expose ``SESSION_SECRET`` as a module
   constant. It used to be bound as ``os.getenv(NAME, "")`` — dead (nothing
   imported it) but actively misleading, because an empty-string default
   reads as "an empty secret is a valid state". A future caller reading
   that constant would silently sign cookies with "".

2. The REAL accessor reads the env var at call time and raises when it is
   missing. That is the actual enforcement point and must stay fail-closed.
"""

import os
from unittest.mock import patch

import pytest


def test_settings_does_not_export_empty_defaulted_secrets():
    from src.core import settings

    assert not hasattr(settings, "SESSION_SECRET"), (
        "settings.SESSION_SECRET must not exist — an empty-default constant "
        "invites silently signing with ''. Use auth_deps._secret() instead."
    )


def test_required_prod_vars_still_lists_the_secrets():
    """Removing the constants must NOT remove them from the prod env check."""
    from src.core import settings

    assert "SESSION_SECRET" in settings._REQUIRED_PROD_VARS
    assert "DATABASE_URL" in settings._REQUIRED_PROD_VARS


def test_session_secret_accessor_fails_closed_when_unset():
    from src.api.auth_deps import _secret

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SESSION_SECRET", None)
        with pytest.raises(RuntimeError, match="SESSION_SECRET"):
            _secret()


def test_prod_validation_raises_when_secrets_missing():
    """validate_required_env must still hard-fail in production."""
    from src.core import settings

    with patch.dict(
        os.environ,
        {"APP_ENV": "production", "SESSION_SECRET": "", "DATABASE_URL": ""},
        clear=False,
    ):
        with pytest.raises(RuntimeError, match="Missing required environment variables"):
            settings.validate_required_env()

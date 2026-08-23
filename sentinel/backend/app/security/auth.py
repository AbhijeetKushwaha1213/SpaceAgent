"""SENTINEL API Authentication (app/security/auth.py)

Validates incoming X-API-Key or Bearer token authentication headers against
configured SENTINEL_API_KEY.
"""

from __future__ import annotations

from app.security.config import SecurityConfig


def verify_api_key(
    provided_key: str | None,
    config: SecurityConfig | None = None,
) -> bool:
    """Verify a provided API key against the configured SENTINEL_API_KEY.

    Contract of THIS function alone: with no key configured it returns True.
    That is NOT the system's access decision. ``SecurityMiddleware`` fails
    CLOSED when authentication is required and no key is set — it returns 401
    *before* calling this function (see app/security/middleware.py:74) and only
    invokes ``verify_api_key`` once a key IS configured. So in the running
    system this is reached solely to compare a request-supplied key against a
    configured one.
    """
    sec_cfg = config or SecurityConfig.from_env()
    if not sec_cfg.api_key:
        return True  # No key configured: never reached fail-open; middleware fails closed first

    if not provided_key:
        return False

    cleaned_key = provided_key.replace("Bearer ", "").strip()
    return cleaned_key == sec_cfg.api_key

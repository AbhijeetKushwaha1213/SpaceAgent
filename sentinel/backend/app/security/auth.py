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
    """Verify if provided API key matches the configured SENTINEL_API_KEY.

    If SENTINEL_API_KEY is not configured (None or empty), authentication
    is not enforced (returns True).
    """
    sec_cfg = config or SecurityConfig.from_env()
    if not sec_cfg.api_key:
        return True  # Unauthenticated access permitted when key is unconfigured

    if not provided_key:
        return False

    cleaned_key = provided_key.replace("Bearer ", "").strip()
    return cleaned_key == sec_cfg.api_key

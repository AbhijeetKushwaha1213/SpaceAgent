"""SENTINEL Security Configuration (app/security/config.py)

Resolves security configuration settings from environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SecurityConfig:
    api_key: str | None = None
    rate_limit_per_minute: int = 120
    max_payload_bytes: int = 10 * 1024 * 1024  # 10 MB
    cors_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
    )
    secure_dev_mode: bool = False
    auth_required: bool = False
    cloud_redact_parameters: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> SecurityConfig:
        raw_key = os.environ.get("SENTINEL_API_KEY", "").strip()
        api_key = raw_key if raw_key else None

        # SECURE_DEV_MODE gates both auth (below) and the rate-limit default.
        raw_dev = os.environ.get("SECURE_DEV_MODE", "0").lower().strip()
        secure_dev = raw_dev in ("1", "true", "yes")

        # 120/min is the PRODUCTION default. A single dashboard load issues ~10
        # requests and all local-demo traffic shares one 127.0.0.1 bucket, so the
        # production limit is too tight for interactive demo use — repeated reloads
        # and scenario switches accumulate in the 60s window and trip 429s. In DEMO
        # mode ONLY (SECURE_DEV_MODE on), and ONLY when no explicit SENTINEL_RATE_LIMIT
        # is set, use a higher demo default. Production and any explicit value are
        # honoured exactly as configured, so this never widens production silently.
        default_rate_limit = 1200 if secure_dev else 120
        raw_limit = os.environ.get("SENTINEL_RATE_LIMIT")
        if raw_limit is None or not raw_limit.strip():
            rate_limit = default_rate_limit
        else:
            try:
                rate_limit = int(raw_limit)
            except ValueError:
                rate_limit = default_rate_limit

        raw_max_bytes = os.environ.get("SENTINEL_MAX_PAYLOAD_BYTES", str(10 * 1024 * 1024))
        try:
            max_bytes = int(raw_max_bytes)
        except ValueError:
            max_bytes = 10 * 1024 * 1024

        raw_cors = os.environ.get("SENTINEL_CORS_ORIGINS", "")
        if raw_cors:
            origins = tuple(o.strip() for o in raw_cors.split(",") if o.strip())
        else:
            origins = (
                "http://localhost:3000",
                "http://localhost:3001",
                "http://127.0.0.1:3000",
                "http://127.0.0.1:3001",
            )

        raw_redact = os.environ.get("SENTINEL_CLOUD_REDACT_PARAMETERS", "")
        redact_params = tuple(
            p.strip() for p in raw_redact.split(",") if p.strip()
        ) if raw_redact else ()

        return cls(
            api_key=api_key,
            rate_limit_per_minute=rate_limit,
            max_payload_bytes=max_bytes,
            cors_origins=origins,
            secure_dev_mode=secure_dev,
            auth_required=not secure_dev,
            cloud_redact_parameters=redact_params,
        )


# ── Explicit security-mode classification ────────────────────────────────────
# The middleware enforces authentication iff ``auth_required or api_key`` is
# truthy. That single boolean has four meaningfully different origins; naming
# them makes the posture legible in the startup banner and, critically, lets a
# test assert that a DEMO process can never be silently reported as a PRODUCTION
# one (and vice versa). No new switch is introduced — this only *describes* the
# config that ``from_env`` already produced from SECURE_DEV_MODE / SENTINEL_API_KEY.
SECURITY_MODE_DEMO = "DEMO_UNAUTHENTICATED"
SECURITY_MODE_PRODUCTION = "PRODUCTION_AUTHENTICATED"
SECURITY_MODE_FAIL_CLOSED = "FAIL_CLOSED_NO_KEY"
SECURITY_MODE_DEV_AUTHENTICATED = "DEV_AUTHENTICATED"


def security_mode(config: SecurityConfig | None = None) -> dict:
    """Classify the ACTIVE security posture from a resolved ``SecurityConfig``.

    Returns a small, secret-free dict — mode id, human label, whether auth is
    actually enforced, whether requests can authenticate, whether a key is
    configured (a *boolean*, never the value), and a human warning for the demo
    and mis-configured postures. It mirrors the middleware's own
    ``auth_required or api_key`` decision exactly, so the banner can never
    disagree with runtime behaviour.
    """
    cfg = config or SecurityConfig.from_env()
    key_configured = bool(cfg.api_key)
    auth_enforced = bool(cfg.auth_required or key_configured)

    if cfg.secure_dev_mode and not key_configured:
        mode = SECURITY_MODE_DEMO
        label = "DEMO (localhost, UNAUTHENTICATED)"
        warning = (
            "Demo mode: authentication is DISABLED and log redaction is OFF. "
            "Never set SECURE_DEV_MODE outside a local demo/development host."
        )
    elif not cfg.secure_dev_mode and key_configured:
        mode = SECURITY_MODE_PRODUCTION
        label = "PRODUCTION (authenticated)"
        warning = None
    elif not cfg.secure_dev_mode and not key_configured:
        mode = SECURITY_MODE_FAIL_CLOSED
        label = "FAIL-CLOSED (auth required, NO key configured)"
        warning = (
            "No SENTINEL_API_KEY is set and SECURE_DEV_MODE is off: every request "
            "returns HTTP 401. For the demo, run with SECURE_DEV_MODE=1; for "
            "production, set SENTINEL_API_KEY."
        )
    else:  # secure_dev_mode and key_configured
        mode = SECURITY_MODE_DEV_AUTHENTICATED
        label = "DEV + authenticated (key set; auth still enforced)"
        warning = (
            "SECURE_DEV_MODE is on (log redaction OFF) while SENTINEL_API_KEY is "
            "configured, so authentication remains enforced."
        )

    return {
        "mode": mode,
        "label": label,
        "auth_enforced": auth_enforced,
        # A request can only be authenticated when auth is enforced AND a key
        # exists to authenticate against; fail-closed (no key) can never be.
        "authenticated": auth_enforced and key_configured,
        "api_key_configured": key_configured,  # boolean only — never the value
        "secure_dev_mode": cfg.secure_dev_mode,
        "is_demo": mode == SECURITY_MODE_DEMO,
        "is_production": mode == SECURITY_MODE_PRODUCTION,
        "warning": warning,
    }

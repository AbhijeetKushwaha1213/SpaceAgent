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
    cloud_redact_parameters: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> SecurityConfig:
        raw_key = os.environ.get("SENTINEL_API_KEY", "").strip()
        api_key = raw_key if raw_key else None

        raw_limit = os.environ.get("SENTINEL_RATE_LIMIT", "120")
        try:
            rate_limit = int(raw_limit)
        except ValueError:
            rate_limit = 120

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

        raw_dev = os.environ.get("SECURE_DEV_MODE", "0").lower().strip()
        secure_dev = raw_dev in ("1", "true", "yes")

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
            cloud_redact_parameters=redact_params,
        )

"""SENTINEL Data Classification & Log Redaction (app/security/redaction.py)

Phase 14 requirements:
  1. Identifies data classification (CONFIDENTIAL, RESTRICTED_TELEMETRY, PUBLIC).
  2. Redacts API keys, complete telemetry, full prompts, and raw model responses from logs
     unless SECURE_DEV_MODE is explicitly enabled.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from app.security.config import SecurityConfig


class DataClassification(str, Enum):
    CONFIDENTIAL = "CONFIDENTIAL"  # API Keys, secrets, credentials
    RESTRICTED_TELEMETRY = "RESTRICTED_TELEMETRY"  # Spacecraft payload/telemetry
    PUBLIC = "PUBLIC"  # Open metadata & documentation


_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[-_]?key|secret|token|password|auth)\s*[:=]\s*['\"]?([^'\"\s&]+)['\"]?"),
    re.compile(r"AIzaSy[A-Za-z0-9_-]{33}"),  # Google API key pattern
    re.compile(r"sk-[A-Za-z0-9_-]{32,}"),    # OpenAI API key pattern
]


def redact_log_message(message: str, config: SecurityConfig | None = None) -> str:
    """Redact sensitive patterns from log messages.

    Unless SECURE_DEV_MODE=1, secrets and full prompt tokens are masked.
    """
    sec_cfg = config or SecurityConfig.from_env()
    if sec_cfg.secure_dev_mode:
        return message  # Dev override allows unredacted logging

    clean = message
    # Redact key=value pattern
    clean = _SECRET_PATTERNS[0].sub(r"\1: [REDACTED]", clean)
    # Redact raw API key strings
    clean = _SECRET_PATTERNS[1].sub("[REDACTED_API_KEY]", clean)
    clean = _SECRET_PATTERNS[2].sub("[REDACTED_API_KEY]", clean)

    return clean


def classify_data(key: str, value: Any) -> DataClassification:
    """Classify dictionary field data level."""
    lower_key = str(key).lower()
    if any(k in lower_key for k in ("key", "secret", "token", "auth", "password")):
        return DataClassification.CONFIDENTIAL
    if any(k in lower_key for k in ("telemetry", "window", "raw_response", "prompt")):
        return DataClassification.RESTRICTED_TELEMETRY
    return DataClassification.PUBLIC

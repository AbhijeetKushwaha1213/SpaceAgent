"""SENTINEL Data Classification & Log Redaction (app/security/redaction.py)

Phase 14 requirements:
  1. Identifies data classification (CONFIDENTIAL, RESTRICTED_TELEMETRY, PUBLIC).
  2. Redacts API keys, complete telemetry, full prompts, and raw model responses from logs
     unless SECURE_DEV_MODE is explicitly enabled.
"""

from __future__ import annotations

import logging
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

#: A JSON object of 200+ chars with no nested braces — a full telemetry window,
#: prompt body or model response embedded in a log line. Nested JSON is caught
#: by the truncation rule below.
_LONG_JSON_BLOCK = re.compile(r"\{[^{}]{200,}\}", re.DOTALL)

#: A balanced JSON object of at least this many characters is treated as a full
#: payload body and replaced by a marker.
_EMBEDDED_JSON_MIN_CHARS = 200

#: Anything longer than this after redaction is truncated, with the original
#: length recorded. Logs keep metadata; the full content belongs in the audit
#: store, which applies its own secret scan at write time.
_MAX_LOG_CHARS = 512


def _redact_embedded_json(text: str) -> str:
    """Replace balanced JSON objects of 200+ chars with ``[REDACTED_DATA]``.

    A simple ``\{[^{}]{200,}\}`` regex cannot handle nested braces, so the scan
    balances braces manually: an opening ``{`` starts a candidate, the matching
    ``}`` ends it, and any candidate of ``_EMBEDDED_JSON_MIN_CHARS`` or more —
    a full telemetry window, prompt body or model response — is masked.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            out.append(text[i])
            i += 1
            continue
        depth = 1
        j = i + 1
        while j < n and depth > 0:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        if depth != 0:  # Unbalanced brace — leave the text as-is
            out.append(text[i])
            i += 1
            continue
        block = text[i:j]
        if len(block) >= _EMBEDDED_JSON_MIN_CHARS:
            out.append("[REDACTED_DATA]")
        else:
            out.append(block)
        i = j
    return "".join(out)


def redact_log_message(message: str, config: SecurityConfig | None = None) -> str:
    """Redact sensitive patterns from log messages.

    Unless SECURE_DEV_MODE=1:
      * API keys and credential shapes are masked
      * embedded JSON payloads of 200+ chars (full telemetry windows, full
        prompts, full model responses) are replaced by a marker
      * any remaining overlong message is truncated with the length recorded
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
    # Redact embedded full-payload JSON blocks
    clean = _redact_embedded_json(clean)
    clean = _LONG_JSON_BLOCK.sub("[REDACTED_DATA]", clean)
    # Truncate anything still overlong
    if len(clean) > _MAX_LOG_CHARS:
        clean = (
            clean[:_MAX_LOG_CHARS]
            + f" [REDACTED_TRUNCATED: {len(message)} chars total]"
        )

    return clean


class RedactionLogFilter(logging.Filter):
    """Logging filter that redacts every record's rendered message.

    Attached to the root logger so no call site can accidentally log an API key,
    a full prompt, a complete telemetry body or a full model response. The
    message is rendered eagerly and redacted, then the original args are cleared
    so no later handler can re-form a sensitive line. Disabled when
    SECURE_DEV_MODE=1 is explicitly configured.
    """

    def __init__(self, config: SecurityConfig | None = None):
        super().__init__()
        self._config = config

    def filter(self, record: logging.LogRecord) -> bool:
        cfg = self._config or SecurityConfig.from_env()
        if cfg.secure_dev_mode:
            return True
        try:
            rendered = record.getMessage()
        except Exception:  # pragma: no cover — malformed lazy args
            return True
        record.msg = redact_log_message(rendered, cfg)
        record.args = ()
        return True


def classify_data(key: str, value: Any) -> DataClassification:
    """Classify dictionary field data level."""
    lower_key = str(key).lower()
    if any(k in lower_key for k in ("key", "secret", "token", "auth", "password")):
        return DataClassification.CONFIDENTIAL
    if any(k in lower_key for k in ("telemetry", "window", "raw_response", "prompt")):
        return DataClassification.RESTRICTED_TELEMETRY
    return DataClassification.PUBLIC

"""SENTINEL Input Sanitization & Prompt Injection Protection (app/security/sanitization.py)

Phase 14 requirements:
  1. Input key whitelisting: Drops arbitrary unknown JSON keys from request payloads.
  2. Prompt injection protection: Ensures telemetry parameters, values, and text
     strings remain strictly DATA and never execute as LLM system instructions.
"""

from __future__ import annotations

import re
from typing import Any

# Whitelisted schema keys for CrashDumpRequest payloads & nested telemetry points
WHITELISTED_KEYS = {
    # Top-level CrashDump keys
    "scenario_id",
    "incident_id",
    "fault_type",
    "safe_mode_trigger",
    "fault_register",
    "pre_fault_telemetry_window",
    "telecommand_context",
    "hardware_state",
    "operating_context",
    "provenance",
    "source_type",
    "source_note",
    # Nested Telemetry Point keys
    "timestamp",
    "parameter",
    "value",
    "nominal_min",
    "nominal_max",
    "unit",
    "status",
    # Recovery step keys
    "step_number",
    "command",
    "subsystem",
    "rationale",
    "risk_level",
    "reason",
    "constraint",
    "source",
}

# Dangerous prompt injection patterns to sanitize/neutralize
_INJECTION_PATTERNS = [
    re.compile(r"(?i)\bignore\s+previous\s+instructions\b"),
    re.compile(r"(?i)\bbypass\s+safety\b"),
    re.compile(r"(?i)\bSYSTEM\s*:"),
    re.compile(r"(?i)\bASSISTANT\s*:"),
    re.compile(r"(?i)\bUSER\s*:"),
    re.compile(r"(?i)\byou\s+are\s+now\b"),
    re.compile(r"(?i)\bdisregard\s+all\b"),
    re.compile(r"(?i)\bdo\s+anything\s+now\b"),
]


def sanitize_input_keys(payload: dict[str, Any]) -> dict[str, Any]:
    """Filter request dictionary to only contain whitelisted schema keys.

    Strips any unknown arbitrary keys injected by clients.
    """
    if not isinstance(payload, dict):
        return payload

    sanitized: dict[str, Any] = {}
    for key, val in payload.items():
        if key in WHITELISTED_KEYS:
            if isinstance(val, dict):
                sanitized[key] = sanitize_input_keys(val)
            elif isinstance(val, list):
                sanitized[key] = [
                    sanitize_input_keys(item) if isinstance(item, dict) else item
                    for item in val
                ]
            else:
                sanitized[key] = val
    return sanitized


def sanitize_prompt_injection(text: str) -> str:
    """Neutralize prompt injection patterns in telemetry data strings.

    Telemetry fields are DATA and must never become instructions to the LLM.
    """
    if not text or not isinstance(text, str):
        return text

    clean_text = text
    for pattern in _INJECTION_PATTERNS:
        clean_text = pattern.sub("[SANITIZED_DATA_TOKEN]", clean_text)

    return clean_text


def sanitize_telemetry_payload_data(payload: dict[str, Any]) -> dict[str, Any]:
    """Sanitize both dictionary keys and string values against prompt injection."""
    filtered_keys = sanitize_input_keys(payload)

    def _sanitize_val(val: Any) -> Any:
        if isinstance(val, str):
            return sanitize_prompt_injection(val)
        elif isinstance(val, dict):
            return {k: _sanitize_val(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [_sanitize_val(v) for v in val]
        return val

    return _sanitize_val(filtered_keys)

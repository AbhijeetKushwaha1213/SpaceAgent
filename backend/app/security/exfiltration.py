"""SENTINEL Cloud Transmission Guard (app/security/exfiltration.py)

Phase 14 requirement 10-11. Before ANY external LLM call in CLOUD mode:

  1. classify the payload — every field is identified as CONFIDENTIAL,
     RESTRICTED_TELEMETRY or PUBLIC;
  2. apply configured redaction — CONFIDENTIAL fields are stripped from the
     transmitted copy, and telemetry parameters listed in
     ``SENTINEL_CLOUD_REDACT_PARAMETERS`` are removed entirely;
  3. record the external transmission in the audit trail, with the
     classification summary and the redaction report.

In LOCAL mode no external call is made anywhere in the codebase; the agent's
``_call_gemini`` refuses outright, and this module additionally records a
BLOCKED transmission entry in the audit trail so an auditor can see that the
guard was engaged rather than merely absent.
"""

from __future__ import annotations

from typing import Any

from app.security.config import SecurityConfig
from app.security.redaction import DataClassification, classify_data


def classify_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Walk a payload and summarise the classification of every field.

    Returns ``{"counts": {classification: n}, "fields": {classification: [...]}}``
    so the audit record carries the classification decision made before the
    external transmission, not a claim made after the fact.
    """
    counts: dict[str, int] = {c.value: 0 for c in DataClassification}
    fields: dict[str, list[str]] = {c.value: [] for c in DataClassification}

    def _walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child = f"{path}.{key}" if path else str(key)
                classification = classify_data(key, item)
                counts[classification.value] += 1
                fields[classification.value].append(child)
                _walk(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                _walk(item, f"{path}[{index}]")

    _walk(payload, "")
    return {"counts": counts, "fields": fields}


def _redact_confidential_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with CONFIDENTIAL-classified fields replaced by [REDACTED]."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if classify_data(key, value) is DataClassification.CONFIDENTIAL:
            out[key] = "[REDACTED]"
        elif isinstance(value, dict):
            out[key] = _redact_confidential_fields(value)
        elif isinstance(value, list):
            out[key] = [
                _redact_confidential_fields(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            out[key] = value
    return out


def _redact_telemetry_parameters(
    payload: dict[str, Any], redact_parameters: tuple[str, ...]
) -> tuple[dict[str, Any], list[str]]:
    """Remove telemetry points whose parameter name is in ``redact_parameters``.

    The points are dropped from the transmitted copy entirely — a value set to a
    sentinel could still carry diagnostic information in its magnitude, whereas
    an absent reading carries nothing.
    """
    if not redact_parameters:
        return payload, []

    blocked = {p.lower() for p in redact_parameters}
    redacted: list[str] = []
    windows = payload.get("pre_fault_telemetry_window")
    if isinstance(windows, list):
        kept: list[Any] = []
        for point in windows:
            if isinstance(point, dict):
                param = str(point.get("parameter", "")).lower()
                if param in blocked:
                    redacted.append(point.get("parameter"))
                    continue
            kept.append(point)
        if redacted:
            payload = {**payload, "pre_fault_telemetry_window": kept}
    return payload, redacted


def apply_cloud_redaction(
    payload: dict[str, Any],
    config: SecurityConfig | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(redacted_copy, report)`` ready for external transmission.

    The original payload is never mutated. The report records what was redacted
    and why, so it can be written straight into the audit trail.
    """
    sec_cfg = config or SecurityConfig.from_env()

    redacted_payload = _redact_confidential_fields(payload)
    redacted_payload, telemetry_redacted = _redact_telemetry_parameters(
        redacted_payload, sec_cfg.cloud_redact_parameters
    )

    report: dict[str, Any] = {
        "redaction_applied": bool(telemetry_redacted) or redacted_payload != payload,
        "confidential_fields_redacted": sorted(
            classify_payload(payload)["fields"][DataClassification.CONFIDENTIAL.value]
        ),
        "telemetry_parameters_redacted": telemetry_redacted,
        "classifications": classify_payload(payload),
        "policy": (
            "CONFIDENTIAL fields are replaced with [REDACTED] before any "
            "external transmission; telemetry parameters listed in "
            "SENTINEL_CLOUD_REDACT_PARAMETERS are removed entirely."
        ),
    }
    return redacted_payload, report


def record_external_transmission(
    recorder: Any,
    *,
    provider: str,
    model: str,
    mode: str,
    classification: dict[str, Any],
    redaction_report: dict[str, Any],
    blocked: bool = False,
    reason: str | None = None,
) -> Any:
    """Record the external LLM transmission decision in the audit trail.

    ``blocked=False`` records an actual CLOUD-mode transmission (post-redaction).
    ``blocked=True`` records that LOCAL mode prevented one — so the record shows
    the guard was engaged, not merely absent.
    """
    from app.audit import Stage, StageStatus

    if blocked:
        status = StageStatus.FAILED
        summary = (
            f"external transmission BLOCKED: {reason or 'local mode prevents it'}"
        )
        claim = (
            "No payload left this host. LOCAL mode refuses cloud LLM calls "
            "at the provider boundary and this record is written before any "
            "external call could be attempted."
        )
    else:
        status = StageStatus.OK
        summary = (
            f"telemetry transmitted to {provider} ({model}) after redaction "
            f"({redaction_report.get('redaction_applied', False)})"
        )
        claim = (
            "External LLM transmission recorded at send time. The payload was "
            "classified and redacted before transmission; what left this host "
            "is the redacted copy."
        )

    return recorder.record(
        Stage.EXTERNAL_TRANSMISSION,
        status,
        summary,
        {
            "destination": provider,
            "model": model,
            "mode": mode,
            "blocked": blocked,
            "reason": reason,
            "classification": classification,
            "redaction": redaction_report,
            "claim": claim,
        },
    )
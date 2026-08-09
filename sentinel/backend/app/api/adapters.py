"""
SENTINEL — Canonical Telemetry Adapter (api/adapters.py)

Phase 3. THE ONLY PLACE the two historical telemetry shapes are reconciled.

The problem this solves
-----------------------
A crash dump could carry telemetry under either or both of:

    pre_fault_telemetry_window   layered: timing + status, NO bounds
    pre_fault_telemetry          legacy:  bounds, NO timing, NO status

Neither is complete on its own, and consumers picked one arbitrarily:

  * ``analytics/anomaly_detector.py`` read only the legacy list
  * ``analytics/early_warning.py`` read only the legacy list
  * ``validation/conditions.py`` read only the legacy list
  * ``detection/fusion.py`` initially preferred the window — and Phase 2
    measured the cost: scenario 6 (a transponder-loss case with five
    out-of-limit channels) reported ZERO anomalies, because every bound lived in
    the legacy list.

Surveyed across the shipped presets: window entries carry bounds 0% of the time,
legacy entries carry them 100% of the time, and the legacy list holds up to five
channels the window omits entirely.

What this module does
---------------------
``canonical_window()`` produces one list of ``TelemetryEntry`` in which:

  * window entries supply timing and status
  * legacy entries supply bounds, and any channel the window omits
  * the Phase 2 channel dictionary supplies bounds and units for declared
    channels that still lack them
  * unusable readings keep their original text ("NaN") in ``value_text`` so a
    dropout stays distinguishable from a missing sample
  * ``relative_time_s`` is parsed once, here, rather than in each consumer

Nothing is invented. A bound absent from both the payload and the channel
dictionary stays absent, and the entry's status stays UNKNOWN rather than
defaulting to NOMINAL.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from app.api.models import TelemetryEntry, TelemetryStatus

#: Fields on a canonical entry that a legacy row may fill in.
_FILLABLE = (
    "nominal_min", "nominal_max", "unit", "baseline_mean", "baseline_std",
    "anomalous", "relative_time_s",
)


def _merge_key(entry: "TelemetryEntry") -> tuple[str, str, str]:
    """Identify an OBSERVATION, independently of how its value was spelled.

    Keying on ``repr(row["value"])`` — the raw input — made the key sensitive to
    representation rather than to identity, and that broke idempotency: a dump
    already carrying a canonical window, re-merged, produced a duplicate reading
    and therefore an extra anomaly. Measured on preset scenario 1, where the
    gyro's unusable T-0s sample appears as the string "NaN" in the legacy array
    and as ``value: null`` + ``value_text: "NaN"`` once canonicalized: the two
    keys differed, both rows survived, and the anomaly count went 10 -> 11.

    Two rows are the same observation when they report the same channel at the
    same instant with the same reading. For an unusable reading the identity is
    its preserved text, so a NaN and an absent sample stay distinct while the
    same NaN written two different ways collapses.
    """
    if entry.value is not None:
        return (entry.parameter, entry.timestamp, repr(entry.value))
    return (entry.parameter, entry.timestamp, entry.value_text or "MISSING")


def _offset_of(row: dict[str, Any]) -> str:
    """Read a row's time offset, whichever key it uses."""
    for key in ("timestamp_offset", "timestamp"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return "T-0s"


def _parse_seconds(offset: str) -> Optional[float]:
    """Parse 'T-120.5s' / 'T+0.000s' / '-60' into seconds.

    Returns None when unparseable, never 0.0 — collapsing every unparseable
    offset to the same instant would flatten a window into a single point and
    make rate and trend detection meaningless.
    """
    if offset is None:
        return None
    text = str(offset).strip()
    if not text:
        return None
    if text[0] in ("T", "t"):
        text = text[1:]
    if text.endswith(("s", "S")):
        text = text[:-1]
    text = text.strip()
    if text.startswith("+"):
        text = text[1:]
    try:
        return float(text)
    except ValueError:
        return None


def _raw_text(value: Any) -> Optional[str]:
    """Preserve a non-numeric reading verbatim, e.g. 'NaN' or 'DEGRADED'."""
    if value is None:
        return "MISSING"
    if isinstance(value, str):
        text = value.strip()
        return text if text else "MISSING"
    if isinstance(value, float):
        import math
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Inf"
    return None


def _to_entry(row: dict[str, Any]) -> Optional[TelemetryEntry]:
    """Build a canonical entry from a row of either shape."""
    parameter = str(row.get("parameter") or "").strip()
    if not parameter:
        return None

    offset = _offset_of(row)
    raw_value = row.get("value")

    # A row that is ALREADY canonical carries value_text, which is the only
    # record of an unusable reading once `value` has been coerced to None.
    # Dropping it here made the adapter lossy on its own output: a re-read
    # canonical NaN came back as "MISSING", so it no longer matched the legacy
    # row it was merged from and detection counted the dropout twice.
    supplied_text = row.get("value_text")
    if not (isinstance(supplied_text, str) and supplied_text.strip()):
        supplied_text = None

    payload: dict[str, Any] = {
        "timestamp": offset,
        "parameter": parameter,
        "value": raw_value,
        "value_text": supplied_text,
        "unit": row.get("unit"),
        "status": row.get("status"),
        "anomalous": row.get("anomalous"),
        "nominal_min": row.get("nominal_min"),
        "nominal_max": row.get("nominal_max"),
        "baseline_mean": row.get("baseline_mean"),
        "baseline_std": row.get("baseline_std"),
        "relative_time_s": (
            row.get("relative_time_s")
            if isinstance(row.get("relative_time_s"), (int, float))
            else _parse_seconds(offset)
        ),
    }

    entry = TelemetryEntry.model_validate(payload)

    # TelemetryEntry.coerce_value maps an unusable reading to None. Keep the
    # original so downstream detection can tell NaN from simply absent. Only
    # derive the text when the row did not supply one — an explicit value_text
    # is authoritative.
    if entry.value is None and supplied_text is None:
        text = _raw_text(raw_value)
        if text is not None:
            entry = entry.model_copy(update={"value_text": text})

    return entry


def _enrich_from_channel_dictionary(entry: TelemetryEntry) -> TelemetryEntry:
    """Fill missing bounds and unit from the Phase 2 channel dictionary.

    Only for channels the dictionary declares. An unknown channel — an
    anonymized ESA-ADB ``channel_*``, for instance — is left exactly as supplied;
    inventing limits for a channel we know nothing about would be fabrication.
    """
    try:
        from app.detection.channels import get_channel_spec
    except Exception:  # pragma: no cover — detection package optional
        return entry

    spec = get_channel_spec(entry.parameter)
    if spec is None:
        return entry

    updates: dict[str, Any] = {}
    if entry.nominal_min is None and spec.limit_min is not None:
        updates["nominal_min"] = spec.limit_min
    if entry.nominal_max is None and spec.limit_max is not None:
        updates["nominal_max"] = spec.limit_max
    if entry.unit is None and spec.unit is not None:
        updates["unit"] = spec.unit

    return entry.model_copy(update=updates) if updates else entry


def canonical_window(
    crash_dump: dict[str, Any],
    enrich_from_dictionary: bool = True,
) -> list[TelemetryEntry]:
    """Return the canonical telemetry window for a crash dump.

    Args:
        crash_dump: Any crash dump dict, carrying either or both telemetry
            fields. A ``CrashDumpRequest.model_dump()`` works too.
        enrich_from_dictionary: Fill missing bounds/units for declared channels
            from the Phase 2 channel dictionary. Set False to see exactly what
            the payload supplied.

    Returns:
        Canonical entries in stable order: window entries first in their supplied
        order, then legacy-only observations. Never raises on malformed input.
    """
    crash_dump = crash_dump or {}

    merged: dict[tuple[str, str, str], TelemetryEntry] = {}
    order: list[tuple[str, str, str]] = []

    for field in ("pre_fault_telemetry_window", "pre_fault_telemetry"):
        rows = crash_dump.get(field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            entry = _to_entry(row)
            if entry is None:
                continue

            key = _merge_key(entry)
            existing = merged.get(key)
            if existing is None:
                merged[key] = entry
                order.append(key)
                continue

            # Same observation from the other field: fill the gaps rather than
            # letting whichever field was read first win.
            updates: dict[str, Any] = {}
            for name in _FILLABLE:
                if getattr(existing, name) is None:
                    incoming = getattr(entry, name)
                    if incoming is not None:
                        updates[name] = incoming
            if existing.status is TelemetryStatus.UNKNOWN and (
                entry.status is not TelemetryStatus.UNKNOWN
            ):
                updates["status"] = entry.status
            if existing.value is None and entry.value is not None:
                updates["value"] = entry.value
                updates["value_text"] = entry.value_text
            if updates:
                merged[key] = existing.model_copy(update=updates)

    entries = [merged[k] for k in order]

    if enrich_from_dictionary:
        entries = [_enrich_from_channel_dictionary(e) for e in entries]

    return entries


def canonical_window_dicts(
    crash_dump: dict[str, Any],
    enrich_from_dictionary: bool = True,
) -> list[dict[str, Any]]:
    """``canonical_window()`` as plain dicts.

    Convenience for the detection layer and the legacy analytics modules, which
    operate on dicts. Includes ``timestamp_offset`` as an alias of ``timestamp``
    so code written against the legacy key keeps working.
    """
    rows: list[dict[str, Any]] = []
    for entry in canonical_window(crash_dump, enrich_from_dictionary):
        row = entry.model_dump(mode="json")
        row["timestamp_offset"] = entry.timestamp
        # Detection's classify_value() reads `value`; surface the preserved
        # original so a NaN is still reported as NAN rather than MISSING.
        if entry.value is None and entry.value_text not in (None, "MISSING"):
            row["value"] = entry.value_text
        rows.append(row)
    return rows


def with_canonical_window(crash_dump: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the dump whose canonical field is fully populated.

    Used at the API boundary so everything downstream sees one complete
    representation. The deprecated legacy field is preserved untouched for
    backward compatibility with any consumer not yet migrated.
    """
    result = dict(crash_dump or {})
    # exclude_none keeps the payload compact — this dict is also serialized into
    # the LLM prompt, and emitting a dozen explicit nulls per reading would spend
    # context on absent data. Pydantic re-fills the defaults on the way out
    # through response_model, so the API surface is unaffected. value_text
    # survives because preserve_unusable_reading() always sets it for an
    # unusable reading, so a NaN stays visible.
    result["pre_fault_telemetry_window"] = [
        e.model_dump(mode="json", exclude_none=True)
        for e in canonical_window(crash_dump)
    ]
    return result


def canonical_channels(crash_dump: dict[str, Any]) -> list[str]:
    """Distinct channel names in the canonical window, in first-seen order."""
    seen: list[str] = []
    for entry in canonical_window(crash_dump):
        if entry.parameter not in seen:
            seen.append(entry.parameter)
    return seen


def coverage_report(crash_dump: dict[str, Any]) -> dict[str, Any]:
    """Diagnostic: what each source field contributed to the canonical window.

    Exists so the merge is auditable rather than a black box — a payload that
    silently loses channels is exactly the failure Phase 3 is fixing.
    """
    crash_dump = crash_dump or {}

    def channels(field: str) -> set[str]:
        rows = crash_dump.get(field)
        if not isinstance(rows, list):
            return set()
        return {
            str(r.get("parameter")) for r in rows
            if isinstance(r, dict) and r.get("parameter")
        }

    window = channels("pre_fault_telemetry_window")
    legacy = channels("pre_fault_telemetry")
    canonical = set(canonical_channels(crash_dump))

    entries = canonical_window(crash_dump)
    return {
        "canonical_entries": len(entries),
        "canonical_channels": sorted(canonical),
        "window_channels": sorted(window),
        "legacy_channels": sorted(legacy),
        "legacy_only_channels": sorted(legacy - window),
        "window_only_channels": sorted(window - legacy),
        "channels_lost": sorted((window | legacy) - canonical),
        "entries_with_bounds": sum(
            1 for e in entries if e.nominal_min is not None or e.nominal_max is not None
        ),
        "entries_with_baseline": sum(
            1 for e in entries if e.baseline_mean is not None
        ),
        "unusable_entries": sum(1 for e in entries if not e.is_usable),
    }

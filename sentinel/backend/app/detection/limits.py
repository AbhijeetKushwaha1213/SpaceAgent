"""
SENTINEL — Deterministic Limit and State Detection (detection/limits.py)

Phase 2, stage 1 and 2 of the pipeline. Pure comparison. No statistics, no
estimated parameters, no thresholds that can be tuned away.

Checks implemented:

  * minimum / maximum violations      value outside a declared engineering limit
  * discrete-state violations         STATUS / FLAG channel outside its expected set
  * counter violations                counter moved off its expected value, or
                                      overflowed, or went backwards
  * data quality                      NaN, Inf, missing, non-numeric

This layer exists because a statistical test cannot express "the transponder
lock bit is 0". The pre-Phase-2 detector had only a statistical test, so five
channels were structurally undetectable:

    channel                 range      example value   old z-score   flagged?
    SEU_counter             (0, 0)     999             0.0           NO
    Transponder_lock        (1, 1)     0               0.0           NO
    Star_tracker_status     (0, 0)     1               0.0           NO
    Fault_register          (0, 0)     8               0.0           NO
    Watchdog_counter        (0, 1000)  1002            2.85          NO

The first four are degenerate ranges: sigma == 0, so the old code returned
z = 0.0 by construction. The fifth is a wide range: sigma == 166.7, so a genuine
overflow sat 0.012 sigma from the mean. Every one of these is caught here by
comparison instead.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from app.detection.channels import (
    BoundOrigin,
    ChannelKind,
    ChannelSpec,
    spec_or_inferred,
)
from app.detection.models import (
    Anomaly,
    AnomalyProvenance,
    BaselineSource,
    Confidence,
    DetectorName,
    Severity,
)

_MODULE = "app.detection.limits"

#: Severity for a data-quality failure on a channel. A NaN on a sensor the
#: attitude loop depends on is not a cosmetic problem, so this is CRITICAL —
#: matching the pre-Phase-2 behaviour, which also treated NaN as CRITICAL.
_NAN_SEVERITY = Severity.CRITICAL


def _provenance(notes: str | None = None) -> AnomalyProvenance:
    """Limits findings are deterministic and carry no statistical assumption."""
    return AnomalyProvenance(
        detector_module=_MODULE,
        baseline_source=BaselineSource.NONE,
        baseline_sample_count=None,
        confidence=Confidence.HIGH,
        deterministic=True,
        notes=notes,
    )


def classify_value(value: Any) -> tuple[Optional[float], Optional[str]]:
    """Coerce a raw telemetry value to a float, or explain why it cannot be.

    Returns ``(float, None)`` on success, or ``(None, reason_code)`` where the
    reason is one of MISSING, NAN, INF, NON_NUMERIC.
    """
    if value is None:
        return None, "MISSING"

    if isinstance(value, bool):
        # Booleans are valid flag values; int(True) == 1.
        return float(value), None

    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None, "MISSING"
        upper = text.upper()
        if upper in ("NAN", "NONE", "N/A", "NULL"):
            return None, "NAN" if upper == "NAN" else "MISSING"
        if upper in ("INF", "+INF", "-INF", "INFINITY", "-INFINITY"):
            return None, "INF"
        try:
            parsed = float(text)
        except ValueError:
            return None, "NON_NUMERIC"
        if math.isnan(parsed):
            return None, "NAN"
        if math.isinf(parsed):
            return None, "INF"
        return parsed, None

    if isinstance(value, (int, float)):
        f = float(value)
        if math.isnan(f):
            return None, "NAN"
        if math.isinf(f):
            return None, "INF"
        return f, None

    return None, "NON_NUMERIC"


_QUALITY_DESCRIPTIONS = {
    "MISSING": "value is missing or empty",
    "NAN": "value is NaN (sensor returned no valid reading)",
    "INF": "value is infinite",
    "NON_NUMERIC": "value is not numeric",
}


# ═══════════════════════════════════════════════════════════════════════════
# CHECK 1 — DATA QUALITY
# ═══════════════════════════════════════════════════════════════════════════

def check_data_quality(
    channel: str,
    timestamp: str,
    value: Any,
    reason: str,
) -> Anomaly:
    """Build a data-quality anomaly for an unusable reading."""
    return Anomaly(
        anomaly_id=Anomaly.make_id(channel, timestamp, DetectorName.DATA_QUALITY, reason),
        channel=channel,
        timestamp=timestamp,
        detector=DetectorName.DATA_QUALITY,
        score=None,
        threshold=None,
        severity=_NAN_SEVERITY if reason in ("NAN", "INF") else Severity.HIGH,
        evidence={
            "value": value if isinstance(value, (int, float, str)) else repr(value),
            "quality_issue": reason,
            "direction": "INVALID",
        },
        provenance=_provenance(
            "Data-quality failures are reported without a statistical claim; "
            "an unusable reading cannot be scored."
        ),
        description=(
            f"{channel} at {timestamp}: {_QUALITY_DESCRIPTIONS.get(reason, reason)}."
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# CHECK 2 — HARD LIMITS
# ═══════════════════════════════════════════════════════════════════════════

def check_hard_limits(
    spec: ChannelSpec,
    timestamp: str,
    value: float,
) -> Optional[Anomaly]:
    """Exact min/max comparison against declared engineering limits.

    This is what catches ``Watchdog_counter = 1002`` against a limit of 1000.
    The old detector expressed the same limit as a sigma band and produced
    z = 2.85, below its 3.0 threshold — so the overflow passed. Comparison has no
    threshold to slip under: 1002 > 1000 is true or it is not.
    """
    lo, hi = spec.limit_min, spec.limit_max

    exceeded: Optional[str] = None
    limit: Optional[float] = None
    if hi is not None and value > hi:
        exceeded, limit = "MAX", float(hi)
    elif lo is not None and value < lo:
        exceeded, limit = "MIN", float(lo)

    if exceeded is None:
        return None

    # Not every bound is an engineering limit. ESA-ADB emits nominal_min/max as
    # baseline_mean +/- 3*baseline_std, so exceeding them is a 3-sigma statistical
    # exceedance, not a physical limit violation. An anonymized channel's bounds
    # have no stated origin at all. Reporting any of these as a physical limit
    # breach would overstate what is known, so the wording says which it is.
    origin = spec.bound_origin or BoundOrigin.UNKNOWN
    statistical_bound = origin is BoundOrigin.STATISTICAL
    bound_kind = {
        BoundOrigin.ENGINEERING: "engineering limit",
        BoundOrigin.STATISTICAL: "3-sigma observed baseline bound",
        BoundOrigin.UNKNOWN: "declared bound (origin not stated)",
    }[origin]

    margin = abs(value - limit)
    span = (
        float(hi) - float(lo)
        if lo is not None and hi is not None and hi > lo else None
    )
    # Severity scales with how far past the limit the value sits, relative to the
    # channel's own operating span. A value 50% of the span beyond its limit is a
    # different situation from one 0.2% beyond it.
    if span:
        overshoot_frac = margin / span
        if overshoot_frac >= 0.5:
            severity = Severity.CRITICAL
        elif overshoot_frac >= 0.1:
            severity = Severity.HIGH
        else:
            severity = Severity.MEDIUM
    else:
        severity = Severity.HIGH

    return Anomaly(
        anomaly_id=Anomaly.make_id(
            spec.name, timestamp, DetectorName.HARD_LIMIT, f"{exceeded}:{value}",
        ),
        channel=spec.name,
        timestamp=timestamp,
        detector=DetectorName.HARD_LIMIT,
        score=margin,
        threshold=limit,
        severity=severity,
        evidence={
            "value": value,
            "limit_min": lo,
            "limit_max": hi,
            "limit_exceeded": exceeded,
            "exceeded_by": round(margin, 6),
            "bound_kind": bound_kind,
            "unit": spec.unit,
            "direction": "HIGH" if exceeded == "MAX" else "LOW",
        },
        provenance=AnomalyProvenance(
            detector_module=_MODULE,
            baseline_source=(
                BaselineSource.OBSERVED_PROVIDED if statistical_bound
                else BaselineSource.NONE
            ),
            confidence=Confidence.HIGH,
            deterministic=True,
            notes={
                BoundOrigin.ENGINEERING: (
                    "Exact comparison against a declared engineering limit. No "
                    "statistical assumption; this check cannot be defeated by a "
                    "wide nominal range."
                ),
                BoundOrigin.STATISTICAL: (
                    "Exact comparison against a bound computed from observed "
                    "baseline statistics (mean +/- 3 sigma), not a physical limit. "
                    "The value is outside the observed operating envelope."
                ),
                BoundOrigin.UNKNOWN: (
                    "Exact comparison against a bound carried by the reading. Its "
                    "origin is not stated — typical of an anonymized channel — so "
                    "no claim is made that this is a physical limit."
                ),
            }[origin],
        ),
        description=(
            f"{spec.name} at {timestamp}: {value:g}"
            f"{' ' + spec.unit if spec.unit else ''} is outside its "
            f"{'upper' if exceeded == 'MAX' else 'lower'} {bound_kind} "
            f"of {limit:g} by {margin:g}."
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# CHECK 3 — DISCRETE STATE
# ═══════════════════════════════════════════════════════════════════════════

def check_discrete_state(
    spec: ChannelSpec,
    timestamp: str,
    value: float,
) -> Optional[Anomaly]:
    """Membership test for STATUS and FLAG channels.

    Catches three of the four degenerate-range blind spots:
    ``Transponder_lock`` (expected 1, observed 0), ``Star_tracker_status``
    (expected 0, observed a fault code) and ``Fault_register`` (expected 0,
    observed set bits). Their nominal ranges were degenerate, so the old
    statistical test returned z = 0.0 and never flagged them.
    """
    if not spec.is_discrete or not spec.expected_states:
        return None

    if any(math.isclose(value, s, rel_tol=0.0, abs_tol=1e-9)
           for s in spec.expected_states):
        return None

    expected = list(spec.expected_states)

    # A lock/health flag leaving its healthy state is a loss of a capability the
    # spacecraft depends on. A fault register with bits set is a reported fault.
    severity = Severity.CRITICAL if spec.kind is ChannelKind.FLAG else Severity.HIGH

    extra: dict[str, Any] = {}
    if spec.unit == "bitmask":
        try:
            bits = int(value)
            extra["set_bits"] = [i for i in range(32) if bits & (1 << i)]
            extra["hex"] = hex(bits)
        except (ValueError, OverflowError):
            pass

    return Anomaly(
        anomaly_id=Anomaly.make_id(
            spec.name, timestamp, DetectorName.DISCRETE_STATE, f"{value}",
        ),
        channel=spec.name,
        timestamp=timestamp,
        detector=DetectorName.DISCRETE_STATE,
        # Categorical: the numeric distance from the expected state carries no
        # physical meaning, so no score is reported rather than a misleading one.
        score=None,
        threshold=None,
        severity=severity,
        evidence={
            "value": value,
            "expected_states": expected,
            "channel_kind": spec.kind.value,
            "unit": spec.unit,
            "direction": "STATE_CHANGE",
            **extra,
        },
        provenance=_provenance(
            "Exact membership test against the channel's expected state set. "
            "This channel has a degenerate nominal range, so statistical "
            "detection returns zero by construction and cannot flag it."
        ),
        description=(
            f"{spec.name} at {timestamp}: state {value:g} is outside its expected "
            f"state(s) {expected}."
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# CHECK 4 — COUNTERS
# ═══════════════════════════════════════════════════════════════════════════

def check_counter(
    spec: ChannelSpec,
    timestamp: str,
    value: float,
    previous_value: Optional[float] = None,
) -> list[Anomaly]:
    """Counter semantics: expected value, and monotonicity.

    Catches ``SEU_counter``: expected 0, so any non-zero reading is a radiation
    event. Magnitude is not the point — the old detector needed the value to be
    3 sigma from a mean, and with a degenerate range there was no sigma at all.

    ``Watchdog_counter`` has no expected value (it legitimately counts up), so
    only its hard limit and monotonicity apply; the limit is handled by
    ``check_hard_limits``.
    """
    if spec.kind is not ChannelKind.COUNTER:
        return []

    found: list[Anomaly] = []

    # --- Expected-value check ---
    if spec.expected_states:
        if not any(math.isclose(value, s, rel_tol=0.0, abs_tol=1e-9)
                   for s in spec.expected_states):
            expected = list(spec.expected_states)
            delta = value - expected[0]
            # A counter that is supposed to read zero and does not has recorded
            # a real event. One event is significant; many indicate a persistent
            # environment.
            severity = Severity.CRITICAL if delta >= 3 else Severity.HIGH
            found.append(Anomaly(
                anomaly_id=Anomaly.make_id(
                    spec.name, timestamp, DetectorName.COUNTER, f"expected:{value}",
                ),
                channel=spec.name,
                timestamp=timestamp,
                detector=DetectorName.COUNTER,
                score=delta,
                threshold=float(expected[0]),
                severity=severity,
                evidence={
                    "value": value,
                    "expected_value": expected[0],
                    "increment_above_expected": round(delta, 6),
                    "unit": spec.unit,
                    "direction": "HIGH" if delta > 0 else "LOW",
                },
                provenance=_provenance(
                    "Counter compared against its expected resting value. This "
                    "channel has a degenerate nominal range, so statistical "
                    "detection returns zero by construction."
                ),
                description=(
                    f"{spec.name} at {timestamp}: counter reads {value:g}, expected "
                    f"{expected[0]:g} — {delta:g} event(s) recorded."
                ),
            ))

    # --- Monotonicity check ---
    # Runs INDEPENDENTLY of the expected-value check. It used to be an elif in
    # effect, because the expected-value branch returned early — so a counter
    # with an expected resting value (SEU_counter) could never have a decrease
    # reported, since any non-zero reading short-circuited first.
    if (
        spec.monotonic_non_decreasing
        and previous_value is not None
        and value < previous_value - 1e-9
    ):
        drop = previous_value - value
        found.append(Anomaly(
            anomaly_id=Anomaly.make_id(
                spec.name, timestamp, DetectorName.COUNTER, f"decrease:{value}",
            ),
            channel=spec.name,
            timestamp=timestamp,
            detector=DetectorName.COUNTER,
            score=drop,
            threshold=0.0,
            severity=Severity.MEDIUM,
            evidence={
                "value": value,
                "previous_value": previous_value,
                "decrease": round(drop, 6),
                "unit": spec.unit,
                "direction": "LOW",
            },
            provenance=_provenance(
                "Counters do not decrease. A decrease indicates a reset, a "
                "rollover, or a corrupted reading — all worth reporting."
            ),
            description=(
                f"{spec.name} at {timestamp}: counter decreased from "
                f"{previous_value:g} to {value:g}, which should not happen."
            ),
        ))

    return found


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def detect_limits(
    readings: list[dict[str, Any]],
    require_declared_limits: bool = False,
) -> list[Anomaly]:
    """Run every deterministic check over a telemetry window.

    Args:
        readings: Telemetry readings. Each should carry ``parameter`` and
            ``value``; ``timestamp_offset``/``timestamp``, ``nominal_min`` and
            ``nominal_max`` are used when present.
        require_declared_limits: When True, only channels present in the channel
            dictionary are limit-checked. When False (default), an unknown
            channel is limit-checked against bounds carried by its own reading,
            which is what makes ESA-ADB's anonymized channels testable.

    Returns:
        Every anomaly found, in reading order. Never raises on malformed input.
    """
    anomalies: list[Anomaly] = []
    previous: dict[str, float] = {}

    for reading in readings or []:
        if not isinstance(reading, dict):
            continue

        channel = str(reading.get("parameter") or "").strip()
        if not channel:
            continue

        timestamp = str(
            reading.get("timestamp_offset")
            or reading.get("timestamp")
            or "T-0s"
        )

        raw = reading.get("value")
        numeric, quality_issue = classify_value(raw)

        # --- Data quality first: an unusable value cannot be compared ---
        if quality_issue is not None:
            anomalies.append(check_data_quality(channel, timestamp, raw, quality_issue))
            continue

        nom_min = reading.get("nominal_min")
        nom_max = reading.get("nominal_max")
        declared = channel in _declared_channels()
        if require_declared_limits and not declared:
            continue

        spec = spec_or_inferred(
            channel,
            nominal_min=_as_float(nom_min),
            nominal_max=_as_float(nom_max),
            # ESA-ADB bounds are mean +/- 3 sigma over observed data, signalled by
            # the reading carrying baseline statistics of its own.
            baseline_derived_bounds=(
                reading.get("baseline_mean") is not None
                and reading.get("baseline_std") is not None
            ),
        )

        # For a declared channel, the dictionary's limits win over whatever the
        # reading claims — the reading's bounds may be statistical, not physical.
        limit_spec = spec
        if not declared and spec.limit_min is None and spec.limit_max is None:
            limit_spec = spec  # nothing to check against

        found = check_hard_limits(limit_spec, timestamp, numeric)
        if found is not None:
            anomalies.append(found)

        found = check_discrete_state(spec, timestamp, numeric)
        if found is not None:
            anomalies.append(found)

        anomalies.extend(check_counter(spec, timestamp, numeric, previous.get(channel)))

        previous[channel] = numeric

    return anomalies


def _as_float(value: Any) -> Optional[float]:
    parsed, issue = classify_value(value)
    return parsed if issue is None else None


def _declared_channels() -> frozenset[str]:
    from app.detection.channels import CHANNEL_SPECS
    return frozenset(CHANNEL_SPECS)

"""
SENTINEL — Statistical Detection (detection/statistical.py)

Phase 2, stage 3. Z-score detection is KEPT as the baseline method. What changed
is where the baseline statistics come from.

Before Phase 2
--------------
Sigma was fabricated from engineering limits for every channel:

    mu = (lo + hi) / 2      sigma = (hi - lo) / 6

and the result was reported as a z-score with nothing to indicate that sigma was
an assumption rather than a measurement. Two consequences, both verified:

  * degenerate range -> sigma == 0 -> z == 0.0 always
  * wide range -> large sigma -> real limit violations score under threshold

After Phase 2
-------------
``BaselineProvider`` resolves observed statistics first, and every finding
records which source was used. Where only engineering limits are available, the
finding is still produced but tagged ``Confidence.LOW`` with an explicit note, so
a weak inference is never mistaken for a measurement.

Statistical detection is also SKIPPED for channels where it is meaningless —
counters and discrete states. Those belong to limits.py, which tests them by
comparison. Running a Gaussian test on a bitmask was the root of the blind spots.

No claim is made that this is better than the previous behaviour on any metric.
It closes specific, demonstrated failures; the two can be compared directly via
``compare_against_range_derived()``.
"""

from __future__ import annotations

from typing import Any, Optional

from app.detection.baseline import BaselineProvider, BaselineStats
from app.detection.channels import ChannelSpec, spec_or_inferred
from app.detection.limits import classify_value
from app.detection.models import (
    Anomaly,
    AnomalyProvenance,
    BaselineSource,
    Confidence,
    DetectorName,
    Severity,
)

_MODULE = "app.detection.statistical"

#: Default |z| threshold. Unchanged from the pre-Phase-2 detector so the two are
#: directly comparable.
DEFAULT_Z_THRESHOLD: float = 3.0

#: Robust (median/MAD) threshold. Slightly higher because MAD-scaled deviations
#: run larger than sigma-scaled ones on heavy-tailed data.
DEFAULT_ROBUST_Z_THRESHOLD: float = 3.5


def _severity_from_z(abs_z: float) -> Severity:
    """Map |z| to severity. Same breakpoints as the pre-Phase-2 detector."""
    if abs_z > 6.0:
        return Severity.CRITICAL
    if abs_z > 4.0:
        return Severity.HIGH
    return Severity.MEDIUM


def _provenance(stats: BaselineStats) -> AnomalyProvenance:
    notes: Optional[str] = None
    if stats.source is BaselineSource.RANGE_DERIVED:
        notes = (
            "WEAK EVIDENCE: sigma was derived from engineering limits as "
            "(max-min)/6, not measured. This assumes the limits are a 3-sigma "
            "band, which is generally false. Treat as a hint, not a statistic."
        )
    elif stats.source is BaselineSource.OBSERVED_WINDOW:
        notes = (
            f"Baseline computed from {stats.sample_count} nominal sample(s) in "
            f"the analysed window; samples already flagged anomalous were excluded."
        )
    elif stats.source is BaselineSource.OBSERVED_PROVIDED:
        notes = (
            "Baseline statistics supplied with the data, computed over a real "
            "observation window external to the sample being judged."
        )
    return AnomalyProvenance(
        detector_module=_MODULE,
        baseline_source=stats.source,
        baseline_sample_count=stats.sample_count or None,
        confidence=stats.confidence,
        deterministic=True,  # reproducible: no sampling anywhere
        notes=notes,
    )


def _make_anomaly(
    spec: ChannelSpec,
    timestamp: str,
    value: float,
    z: float,
    threshold: float,
    stats: BaselineStats,
    detector: DetectorName,
) -> Anomaly:
    abs_z = abs(z)
    severity = _severity_from_z(abs_z)

    # An inference resting on a fabricated sigma must not present as a strong
    # finding, however large the number happens to be.
    if stats.source is BaselineSource.RANGE_DERIVED and severity is Severity.CRITICAL:
        severity = Severity.HIGH

    # Report the statistics that ACTUALLY produced the score. For a
    # window-derived baseline, zscore() uses median/MAD rather than mean/std —
    # see BaselineStats.robust_scale_preferred — so naming the fields
    # "baseline_mean"/"baseline_std" would misdescribe the calculation.
    if detector is DetectorName.ROBUST_ZSCORE:
        centre, scale = stats.median, stats.mad
        centre_name, scale_name = "baseline_median", "baseline_mad"
        basis = "median_mad"
    else:
        centre, scale = stats.effective_center, stats.effective_scale
        centre_name, scale_name = "baseline_center", "baseline_scale"
        basis = stats.scale_basis

    return Anomaly(
        anomaly_id=Anomaly.make_id(
            spec.name, timestamp, detector, f"{round(z, 4)}",
        ),
        channel=spec.name,
        timestamp=timestamp,
        detector=detector,
        score=round(abs_z, 4),
        threshold=threshold,
        severity=severity,
        evidence={
            "value": value,
            centre_name: round(centre, 6) if centre is not None else None,
            scale_name: round(scale, 6) if scale is not None else None,
            "signed_z": round(z, 4),
            "scale_basis": basis,
            "baseline_source": stats.source.value,
            "baseline_samples": stats.sample_count,
            "unit": spec.unit,
            "direction": "HIGH" if z > 0 else "LOW",
        },
        provenance=_provenance(stats),
        description=(
            f"{spec.name} at {timestamp}: {value:g} is {abs_z:.1f} "
            f"{'MAD-scaled ' if detector is DetectorName.ROBUST_ZSCORE else ''}"
            f"deviations from its {stats.source.value.lower().replace('_', ' ')} "
            f"baseline (threshold {threshold:g})."
        ),
    )


def detect_statistical(
    readings: list[dict[str, Any]],
    provider: Optional[BaselineProvider] = None,
    channel_summaries: Optional[list[dict[str, Any]]] = None,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    robust_z_threshold: float = DEFAULT_ROBUST_Z_THRESHOLD,
    allow_range_derived: bool = True,
    include_robust: bool = True,
) -> tuple[list[Anomaly], list[str]]:
    """Run z-score and robust z-score detection over a telemetry window.

    Args:
        readings: Telemetry readings.
        provider: Pre-built baseline provider. Built from ``readings`` when None.
        channel_summaries: ESA-ADB ``channel_summaries``, used as the strongest
            baseline source when available.
        z_threshold: |z| above which a classical finding is emitted.
        robust_z_threshold: |z| above which a robust finding is emitted.
        allow_range_derived: When False, channels with no observed baseline are
            skipped instead of falling back to engineering limits.
        include_robust: Whether to run the median/MAD detector alongside.

    Returns:
        ``(anomalies, warnings)``. Warnings name channels whose statistics could
        not be measured, so the report can say so instead of implying coverage.
    """
    if provider is None:
        provider = BaselineProvider(
            readings or [],
            channel_summaries=channel_summaries,
            allow_range_derived=allow_range_derived,
        )

    anomalies: list[Anomaly] = []
    warnings: list[str] = []
    skipped_kind: set[str] = set()
    weak_baseline: set[str] = set()
    no_baseline: set[str] = set()

    for reading in readings or []:
        if not isinstance(reading, dict):
            continue

        channel = str(reading.get("parameter") or "").strip()
        if not channel:
            continue

        numeric, issue = classify_value(reading.get("value"))
        if issue is not None:
            continue  # data quality is limits.py's business

        timestamp = str(
            reading.get("timestamp_offset") or reading.get("timestamp") or "T-0s"
        )

        spec = spec_or_inferred(
            channel,
            nominal_min=None,
            nominal_max=None,
            baseline_derived_bounds=(
                reading.get("baseline_mean") is not None
                and reading.get("baseline_std") is not None
            ),
        )

        # Skip channels where a Gaussian test is not meaningful. Counters and
        # discrete states are handled deterministically in limits.py — applying a
        # z-score to them is what produced the Phase 2 blind spots.
        if not spec.statistical_detection_meaningful:
            skipped_kind.add(channel)
            continue

        stats = provider.get(spec)

        if stats.source is BaselineSource.NONE:
            no_baseline.add(channel)
            continue
        if stats.source is BaselineSource.RANGE_DERIVED:
            weak_baseline.add(channel)

        z = stats.zscore(numeric)
        if z is not None and abs(z) > z_threshold:
            anomalies.append(_make_anomaly(
                spec, timestamp, numeric, z, z_threshold, stats,
                DetectorName.ZSCORE,
            ))

        if include_robust:
            rz = stats.robust_zscore(numeric)
            if rz is not None and abs(rz) > robust_z_threshold:
                anomalies.append(_make_anomaly(
                    spec, timestamp, numeric, rz, robust_z_threshold, stats,
                    DetectorName.ROBUST_ZSCORE,
                ))

    if skipped_kind:
        warnings.append(
            "Statistical detection skipped for non-continuous channel(s) "
            + ", ".join(sorted(skipped_kind))
            + " — counters and discrete states are checked deterministically "
              "by limits.py instead."
        )
    if weak_baseline:
        warnings.append(
            "No observed baseline for channel(s) "
            + ", ".join(sorted(weak_baseline))
            + "; sigma was derived from engineering limits. Statistical findings "
              "on these channels are WEAK EVIDENCE."
        )
    if no_baseline:
        warnings.append(
            "No baseline available for channel(s) "
            + ", ".join(sorted(no_baseline))
            + "; statistical detection abstained rather than inventing one."
        )

    return anomalies, warnings


# ═══════════════════════════════════════════════════════════════════════════
# BASELINE COMPARISON HARNESS
# ═══════════════════════════════════════════════════════════════════════════

def compare_against_range_derived(
    readings: list[dict[str, Any]],
    channel_summaries: Optional[list[dict[str, Any]]] = None,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
) -> dict[str, Any]:
    """Score the same window with observed vs range-derived baselines.

    Phase 2 explicitly forbids claiming one method is better without showing it.
    This returns the evidence for that comparison rather than asserting an
    outcome: which channels each configuration flags, and where they disagree.

    It is a diagnostic, not a metric. Deciding which is *better* needs labelled
    data, which belongs to the evaluation phase.
    """
    observed_only, _ = detect_statistical(
        readings, channel_summaries=channel_summaries,
        z_threshold=z_threshold, allow_range_derived=False, include_robust=False,
    )
    with_fallback, _ = detect_statistical(
        readings, channel_summaries=channel_summaries,
        z_threshold=z_threshold, allow_range_derived=True, include_robust=False,
    )

    obs_channels = {a.channel for a in observed_only}
    fb_channels = {a.channel for a in with_fallback}

    return {
        "z_threshold": z_threshold,
        "observed_baseline_only": {
            "anomaly_count": len(observed_only),
            "channels": sorted(obs_channels),
        },
        "with_range_derived_fallback": {
            "anomaly_count": len(with_fallback),
            "channels": sorted(fb_channels),
        },
        "flagged_only_with_fallback": sorted(fb_channels - obs_channels),
        "flagged_only_with_observed": sorted(obs_channels - fb_channels),
        "note": (
            "Diagnostic only. No accuracy claim is made for either configuration; "
            "establishing which is better requires labelled data."
        ),
    }

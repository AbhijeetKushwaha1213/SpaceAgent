"""
SENTINEL — Temporal Detection (detection/temporal.py)

Phase 2, stage 4. Adds the time dimension the pre-Phase-2 detector had none of.

The previous detector scored each reading independently, so a channel could
drift steadily across an entire window without any single sample crossing a
threshold, and nothing would notice. Equally, a one-sample spike and a sustained
excursion were indistinguishable — both were just "flagged".

Four detectors:

  RATE_OF_CHANGE   first derivative between adjacent samples exceeds the
                   channel's declared rate limit
  TREND            sustained monotonic drift across the window, measured by
                   least-squares slope plus a monotonicity fraction
  PERSISTENCE      a deviation held for N consecutive samples — the signal that
                   distinguishes a real excursion from sensor noise
  SUDDEN_CHANGE    step change between adjacent samples, scaled by the channel's
                   own observed variability

All four are computed from the telemetry passed in. Nothing is sampled; nothing
is hardcoded. A window with fewer samples than a detector needs produces no
finding from that detector rather than a guess.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
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

_MODULE = "app.detection.temporal"

#: Consecutive out-of-band samples before a deviation counts as persistent.
DEFAULT_PERSISTENCE_SAMPLES: int = 3

#: Step size, in baseline sigmas, that counts as a sudden change.
DEFAULT_STEP_SIGMA: float = 4.0

#: Fraction of consecutive steps that must move the same way for a trend.
DEFAULT_MONOTONIC_FRACTION: float = 0.8

#: Minimum samples needed before a trend is asserted.
MIN_TREND_SAMPLES: int = 4


@dataclass(frozen=True)
class Sample:
    """One numeric reading on one channel, with its parsed time offset."""
    timestamp: str
    seconds: float
    value: float


def parse_offset_seconds(offset: str) -> Optional[float]:
    """Parse 'T-120.5s' / 'T+0.000s' / '-60' into seconds. None if unparseable.

    Returns None rather than 0.0 on failure: silently mapping every unparseable
    offset to the same instant would collapse a window into one point and make
    rates meaningless.
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


def build_series(readings: list[dict[str, Any]]) -> dict[str, list[Sample]]:
    """Group readings into per-channel time series, sorted by time.

    Readings whose offset cannot be parsed are dropped from the temporal view —
    they remain visible to limits.py and statistical.py, which do not need time.
    """
    series: dict[str, list[Sample]] = {}
    for reading in readings or []:
        if not isinstance(reading, dict):
            continue
        channel = str(reading.get("parameter") or "").strip()
        if not channel:
            continue
        numeric, issue = classify_value(reading.get("value"))
        if issue is not None:
            continue
        raw_offset = reading.get("timestamp_offset") or reading.get("timestamp")
        seconds = parse_offset_seconds(raw_offset)
        if seconds is None:
            seconds = _as_float(reading.get("relative_time_s"))
        if seconds is None:
            continue
        series.setdefault(channel, []).append(
            Sample(timestamp=str(raw_offset or f"T{seconds:+g}s"),
                   seconds=seconds, value=numeric)
        )
    for samples in series.values():
        samples.sort(key=lambda s: s.seconds)
    return series


def _as_float(value: Any) -> Optional[float]:
    parsed, issue = classify_value(value)
    return parsed if issue is None else None


def _provenance(
    note: str,
    stats: Optional[BaselineStats] = None,
    sample_count: Optional[int] = None,
) -> AnomalyProvenance:
    source = stats.source if stats is not None else BaselineSource.NONE
    confidence = Confidence.HIGH
    if stats is not None and stats.source is BaselineSource.RANGE_DERIVED:
        confidence = Confidence.LOW
        note += (
            " Scaling sigma was derived from engineering limits, not measured — "
            "WEAK EVIDENCE."
        )
    return AnomalyProvenance(
        detector_module=_MODULE,
        baseline_source=source,
        baseline_sample_count=sample_count,
        confidence=confidence,
        deterministic=True,
        notes=note,
    )


# ═══════════════════════════════════════════════════════════════════════════
# DETECTOR 1 — RATE OF CHANGE
# ═══════════════════════════════════════════════════════════════════════════

def detect_rate_of_change(
    spec: ChannelSpec,
    samples: list[Sample],
) -> list[Anomaly]:
    """Flag adjacent-sample derivatives that exceed the channel's rate limit.

    Only runs for channels with a declared ``max_rate_per_s``. No rate limit is
    invented for channels without a defensible basis for one.
    """
    limit = spec.max_rate_per_s
    if limit is None or limit <= 0 or len(samples) < 2:
        return []

    found: list[Anomaly] = []
    for prev, curr in zip(samples, samples[1:]):
        dt = curr.seconds - prev.seconds
        if dt <= 0:
            continue
        rate = (curr.value - prev.value) / dt
        if abs(rate) <= limit:
            continue

        ratio = abs(rate) / limit
        severity = (
            Severity.CRITICAL if ratio >= 5.0
            else Severity.HIGH if ratio >= 2.0
            else Severity.MEDIUM
        )
        found.append(Anomaly(
            anomaly_id=Anomaly.make_id(
                spec.name, curr.timestamp, DetectorName.RATE_OF_CHANGE,
                f"{round(rate, 6)}",
            ),
            channel=spec.name,
            timestamp=curr.timestamp,
            detector=DetectorName.RATE_OF_CHANGE,
            score=round(abs(rate), 6),
            threshold=limit,
            severity=severity,
            evidence={
                "value": curr.value,
                "previous_value": prev.value,
                "delta": round(curr.value - prev.value, 6),
                "delta_seconds": round(dt, 6),
                "rate_per_s": round(rate, 6),
                "rate_limit_per_s": limit,
                "unit": spec.unit,
                "direction": "HIGH" if rate > 0 else "LOW",
            },
            provenance=_provenance(
                "First derivative between adjacent samples, compared against the "
                "channel's declared maximum plausible rate."
            ),
            description=(
                f"{spec.name} at {curr.timestamp}: changing at {rate:+.4g} "
                f"{spec.unit or 'units'}/s, above its {limit:g}/s limit."
            ),
        ))
    return found


# ═══════════════════════════════════════════════════════════════════════════
# DETECTOR 2 — TREND
# ═══════════════════════════════════════════════════════════════════════════

def _least_squares_slope(samples: list[Sample]) -> Optional[float]:
    """Slope of the best-fit line, in value units per second."""
    n = len(samples)
    if n < 2:
        return None
    xs = [s.seconds for s in samples]
    ys = [s.value for s in samples]
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def detect_trend(
    spec: ChannelSpec,
    samples: list[Sample],
    stats: BaselineStats,
    monotonic_fraction: float = DEFAULT_MONOTONIC_FRACTION,
) -> list[Anomaly]:
    """Flag sustained monotonic drift across the window.

    A trend needs two things together: a consistent direction (most consecutive
    steps moving the same way) and a total excursion large enough to matter
    relative to the channel's own variability. Requiring both avoids flagging
    noise that happens to end higher than it started.
    """
    if len(samples) < MIN_TREND_SAMPLES:
        return []

    deltas = [b.value - a.value for a, b in zip(samples, samples[1:])]
    non_zero = [d for d in deltas if d != 0]
    if not non_zero:
        return []

    rising = sum(1 for d in non_zero if d > 0)
    falling = len(non_zero) - rising
    dominant = max(rising, falling)
    frac = dominant / len(non_zero)
    if frac < monotonic_fraction:
        return []

    slope = _least_squares_slope(samples)
    if slope is None or slope == 0:
        return []

    total_change = samples[-1].value - samples[0].value
    span_seconds = samples[-1].seconds - samples[0].seconds

    # SCALING A TREND
    # ---------------
    # A trend cannot be scaled by variability estimated from the SAME window,
    # because on a monotonic ramp the trend *is* the variability. Measured on a
    # 7-sample SoC ramp from 85% to 40%: sigma over the window is 15.5, so a
    # 45-point drop scores 2.9 sigma and falls under a 3.0 threshold — the
    # clearest possible trend, invisible.
    #
    # So: use sigma only when the baseline was measured on a SEPARATE window
    # (OBSERVED_PROVIDED). Otherwise scale by the channel's operating span, which
    # is independent of the data being judged.
    external_baseline = (
        stats.source is BaselineSource.OBSERVED_PROVIDED and stats.usable_for_zscore
    )
    if external_baseline:
        change_in_sigma = abs(total_change) / stats.effective_scale
        if change_in_sigma < 3.0:
            return []
        severity = Severity.HIGH if change_in_sigma >= 6.0 else Severity.MEDIUM
        score = round(change_in_sigma, 4)
        threshold = 3.0
        scale_note = "external observed baseline sigma"
    else:
        if spec.limit_min is None or spec.limit_max is None:
            return []
        span = float(spec.limit_max) - float(spec.limit_min)
        if span <= 0 or abs(total_change) / span < 0.25:
            return []
        severity = Severity.MEDIUM
        score = round(abs(total_change) / span, 4)
        threshold = 0.25
        scale_note = "fraction of the channel's operating span"

    if stats.source is BaselineSource.RANGE_DERIVED and severity is Severity.HIGH:
        severity = Severity.MEDIUM

    direction = "HIGH" if total_change > 0 else "LOW"
    return [Anomaly(
        anomaly_id=Anomaly.make_id(
            spec.name, samples[-1].timestamp, DetectorName.TREND,
            f"{round(slope, 6)}",
        ),
        channel=spec.name,
        timestamp=samples[-1].timestamp,
        detector=DetectorName.TREND,
        score=score,
        threshold=threshold,
        severity=severity,
        evidence={
            "value": samples[-1].value,
            "first_value": samples[0].value,
            "total_change": round(total_change, 6),
            "slope_per_s": round(slope, 8),
            "window_seconds": round(span_seconds, 3),
            "samples": len(samples),
            "monotonic_fraction": round(frac, 3),
            "steps_rising": rising,
            "steps_falling": falling,
            "baseline_source": stats.source.value,
            "score_basis": scale_note,
            "unit": spec.unit,
            "direction": direction,
        },
        provenance=_provenance(
            f"Least-squares slope over {len(samples)} samples with "
            f"{frac:.0%} of steps moving in one direction, scored as "
            f"{scale_note}.",
            stats if external_baseline else None,
            stats.sample_count or None,
        ),
        description=(
            f"{spec.name}: sustained {'rise' if total_change > 0 else 'fall'} of "
            f"{abs(total_change):.4g}{' ' + spec.unit if spec.unit else ''} over "
            f"{span_seconds:g}s ({frac:.0%} monotonic across {len(samples)} samples)."
        ),
    )]


# ═══════════════════════════════════════════════════════════════════════════
# DETECTOR 3 — PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════

def detect_persistence(
    spec: ChannelSpec,
    samples: list[Sample],
    stats: BaselineStats,
    min_consecutive: int = DEFAULT_PERSISTENCE_SAMPLES,
    z_threshold: float = 3.0,
) -> list[Anomaly]:
    """Flag deviations that HELD for several consecutive samples.

    This is the detector that separates a real excursion from a transient. A
    single out-of-band sample is a spike — reported by the statistical detector
    but not here. Only a run of at least ``min_consecutive`` samples produces a
    persistence finding, and the run's length is the evidence.
    """
    if not stats.usable_for_zscore or len(samples) < min_consecutive:
        return []

    found: list[Anomaly] = []
    run: list[Sample] = []

    def flush(run_samples: list[Sample]) -> None:
        if len(run_samples) < min_consecutive:
            return
        zs = [abs(stats.zscore(s.value) or 0.0) for s in run_samples]
        peak = max(zs)
        duration = run_samples[-1].seconds - run_samples[0].seconds
        severity = (
            Severity.CRITICAL if len(run_samples) >= min_consecutive * 2
            else Severity.HIGH
        )
        if stats.source is BaselineSource.RANGE_DERIVED:
            severity = Severity.MEDIUM
        found.append(Anomaly(
            anomaly_id=Anomaly.make_id(
                spec.name, run_samples[0].timestamp, DetectorName.PERSISTENCE,
                f"{len(run_samples)}:{round(peak, 3)}",
            ),
            channel=spec.name,
            timestamp=run_samples[0].timestamp,
            detector=DetectorName.PERSISTENCE,
            score=float(len(run_samples)),
            threshold=float(min_consecutive),
            severity=severity,
            evidence={
                "value": run_samples[-1].value,
                "consecutive_samples": len(run_samples),
                "required_consecutive": min_consecutive,
                "duration_seconds": round(duration, 3),
                "first_offset": run_samples[0].timestamp,
                "last_offset": run_samples[-1].timestamp,
                "peak_abs_z": round(peak, 4),
                "z_threshold": z_threshold,
                "baseline_source": stats.source.value,
                "unit": spec.unit,
                "direction": (
                    "HIGH" if (stats.zscore(run_samples[-1].value) or 0) > 0
                    else "LOW"
                ),
            },
            provenance=_provenance(
                f"Deviation exceeded |z|>{z_threshold:g} on "
                f"{len(run_samples)} consecutive samples, so it is sustained "
                f"rather than a single-sample transient.",
                stats, stats.sample_count or None,
            ),
            description=(
                f"{spec.name}: deviation persisted for {len(run_samples)} "
                f"consecutive samples ({duration:g}s), peak |z|={peak:.1f}."
            ),
        ))

    for s in samples:
        z = stats.zscore(s.value)
        if z is not None and abs(z) > z_threshold:
            run.append(s)
        else:
            flush(run)
            run = []
    flush(run)

    return found


# ═══════════════════════════════════════════════════════════════════════════
# DETECTOR 4 — SUDDEN CHANGE
# ═══════════════════════════════════════════════════════════════════════════

def detect_sudden_change(
    spec: ChannelSpec,
    samples: list[Sample],
    stats: BaselineStats,
    step_sigma: float = DEFAULT_STEP_SIGMA,
) -> list[Anomaly]:
    """Flag step changes between adjacent samples.

    Scaled by the channel's own sigma, so the same threshold is meaningful
    across channels with different units and magnitudes. Distinct from
    rate-of-change: a step is judged on magnitude relative to variability, not
    on speed relative to a physical rate limit — which means it still works for
    channels with no declared rate limit.
    """
    if not stats.usable_for_zscore or len(samples) < 2:
        return []

    # Use the same location/scale pair the z-score uses, so a step and a
    # deviation are measured on one consistent footing. For a window-derived
    # baseline that means median/MAD: raw sigma is inflated by the very step
    # being measured, which makes a large step score small.
    scale = stats.effective_scale
    if not scale:
        return []

    found: list[Anomaly] = []
    for prev, curr in zip(samples, samples[1:]):
        step = curr.value - prev.value
        step_in_sigma = abs(step) / scale
        if step_in_sigma <= step_sigma:
            continue

        severity = (
            Severity.HIGH if step_in_sigma >= step_sigma * 2 else Severity.MEDIUM
        )
        if stats.source is BaselineSource.RANGE_DERIVED:
            severity = Severity.MEDIUM

        found.append(Anomaly(
            anomaly_id=Anomaly.make_id(
                spec.name, curr.timestamp, DetectorName.SUDDEN_CHANGE,
                f"{round(step, 6)}",
            ),
            channel=spec.name,
            timestamp=curr.timestamp,
            detector=DetectorName.SUDDEN_CHANGE,
            score=round(step_in_sigma, 4),
            threshold=step_sigma,
            severity=severity,
            evidence={
                "value": curr.value,
                "previous_value": prev.value,
                "step": round(step, 6),
                "step_in_sigma": round(step_in_sigma, 4),
                "baseline_scale": round(scale, 6),
                "scale_basis": stats.scale_basis,
                "baseline_source": stats.source.value,
                "delta_seconds": round(curr.seconds - prev.seconds, 6),
                "unit": spec.unit,
                "direction": "HIGH" if step > 0 else "LOW",
            },
            provenance=_provenance(
                "Step change between adjacent samples, scaled by the channel's "
                "baseline sigma.",
                stats, stats.sample_count or None,
            ),
            description=(
                f"{spec.name} at {curr.timestamp}: stepped {step:+.4g}"
                f"{' ' + spec.unit if spec.unit else ''} from {prev.value:g} "
                f"({step_in_sigma:.1f} sigma) between adjacent samples."
            ),
        ))
    return found


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def detect_temporal(
    readings: list[dict[str, Any]],
    provider: Optional[BaselineProvider] = None,
    channel_summaries: Optional[list[dict[str, Any]]] = None,
    persistence_samples: int = DEFAULT_PERSISTENCE_SAMPLES,
    step_sigma: float = DEFAULT_STEP_SIGMA,
    z_threshold: float = 3.0,
    allow_range_derived: bool = True,
) -> tuple[list[Anomaly], list[str]]:
    """Run all four temporal detectors over a telemetry window.

    Returns ``(anomalies, warnings)``. Warnings record channels that had too few
    samples for temporal analysis, so the report does not imply coverage it
    did not have.
    """
    if provider is None:
        provider = BaselineProvider(
            readings or [],
            channel_summaries=channel_summaries,
            allow_range_derived=allow_range_derived,
        )

    series = build_series(readings)
    anomalies: list[Anomaly] = []
    too_short: list[str] = []

    for channel, samples in sorted(series.items()):
        spec = spec_or_inferred(channel)
        stats = provider.get(spec)

        if len(samples) < 2:
            too_short.append(channel)
            continue

        anomalies.extend(detect_rate_of_change(spec, samples))

        # Trend / persistence / sudden change are only meaningful for quantities
        # that vary continuously. A counter rising is expected behaviour, and a
        # status code stepping between fault codes is limits.py's business.
        if spec.statistical_detection_meaningful:
            anomalies.extend(detect_trend(spec, samples, stats))
            anomalies.extend(detect_persistence(
                spec, samples, stats,
                min_consecutive=persistence_samples, z_threshold=z_threshold,
            ))
            anomalies.extend(detect_sudden_change(
                spec, samples, stats, step_sigma=step_sigma,
            ))

    warnings: list[str] = []
    if too_short:
        warnings.append(
            "Temporal detection needs at least 2 samples per channel; "
            + ", ".join(sorted(too_short))
            + " had fewer and were not analysed temporally."
        )
    if not series:
        warnings.append(
            "No parseable time offsets in the window, so no temporal detection "
            "was possible."
        )

    return anomalies, warnings

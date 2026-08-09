"""
SENTINEL — Anomaly Fusion (detection/fusion.py)

Phase 2, stage 5. Runs the detection stages in order and assembles the unified
``AnomalyReport``:

    telemetry
      -> hard-limit detection      (limits.py)
      -> discrete-state detection  (limits.py)
      -> statistical detection     (statistical.py)
      -> temporal detection        (temporal.py)
      -> fusion                    (here)

What fusion does, and deliberately does not do
----------------------------------------------
It groups findings by channel, records which detectors agreed, and orders
everything by severity. It does NOT invent a combined score or collapse
independent findings into one number. Each detector's score means something
different — sigmas, seconds, sample counts, units past a limit — and averaging
them would produce a figure with no interpretation.

Corroboration (two or more independent detectors flagging the same channel) is
recorded as a flag and can raise a channel's severity by one step, because
independent agreement is real evidence. It is capped so corroboration alone can
never manufacture CRITICAL out of two LOW findings.

Determinism
-----------
The same telemetry always produces a byte-identical report. Anomaly IDs are
content hashes, ordering is a total sort, and no detector samples randomness.
``assert_deterministic()`` checks this property directly.
"""

from __future__ import annotations

from typing import Any, Optional

from app.detection.baseline import BaselineProvider
from app.detection.limits import detect_limits
from app.detection.models import (
    Anomaly,
    AnomalyReport,
    ChannelFinding,
    DetectorName,
    DetectorRunInfo,
    Severity,
    severity_rank,
)
from app.detection.statistical import (
    DEFAULT_ROBUST_Z_THRESHOLD,
    DEFAULT_Z_THRESHOLD,
    detect_statistical,
)
from app.detection.temporal import (
    DEFAULT_PERSISTENCE_SAMPLES,
    DEFAULT_STEP_SIGMA,
    detect_temporal,
)

#: Detectors that constitute independent evidence for corroboration purposes.
#: ZSCORE and ROBUST_ZSCORE are NOT independent of each other — they read the
#: same baseline — so they count once. Likewise PERSISTENCE builds on the z-score,
#: so it shares that family.
_EVIDENCE_FAMILY: dict[DetectorName, str] = {
    DetectorName.HARD_LIMIT: "limit",
    DetectorName.DISCRETE_STATE: "state",
    DetectorName.COUNTER: "state",
    DetectorName.DATA_QUALITY: "quality",
    DetectorName.ZSCORE: "statistical",
    DetectorName.ROBUST_ZSCORE: "statistical",
    DetectorName.PERSISTENCE: "statistical",
    DetectorName.RATE_OF_CHANGE: "temporal",
    DetectorName.TREND: "temporal",
    DetectorName.SUDDEN_CHANGE: "temporal",
}

_ALL_DETECTORS: tuple[DetectorName, ...] = tuple(DetectorName)

_SEVERITY_LADDER: tuple[Severity, ...] = (
    Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL,
)


def _raise_one_step(severity: Severity, ceiling: Severity = Severity.HIGH) -> Severity:
    """Raise a severity by one step, capped at ``ceiling``."""
    idx = _SEVERITY_LADDER.index(severity)
    raised = _SEVERITY_LADDER[min(idx + 1, len(_SEVERITY_LADDER) - 1)]
    if severity_rank(raised) > severity_rank(ceiling):
        return max(severity, ceiling, key=severity_rank)
    return raised


def _sort_key(a: Anomaly) -> tuple:
    """Total order: severity desc, channel, detector, offset, id."""
    return (
        -severity_rank(a.severity),
        a.channel,
        a.detector.value,
        a.timestamp,
        a.anomaly_id,
    )


def _extract_channel_summaries(crash_dump: dict[str, Any]) -> Optional[list[dict]]:
    """Pull ESA-ADB ``channel_summaries`` from a dump, if present.

    These carry real observed baseline_mean / baseline_std over a real baseline
    window, which is the strongest statistical evidence available anywhere in
    this repository.
    """
    summaries = crash_dump.get("channel_summaries")
    if isinstance(summaries, list) and summaries:
        return summaries
    return None


def extract_readings(crash_dump: dict[str, Any]) -> list[dict[str, Any]]:
    """Get the canonical telemetry window from a crash dump.

    Phase 3: this delegates to ``app.api.adapters.canonical_window_dicts()``,
    which is now the single place the legacy and canonical telemetry shapes are
    reconciled. Phase 2 implemented the merge here; keeping a second copy of that
    logic is precisely the duplication Phase 3 exists to remove.

    Falls back to a direct read of the two fields only if the adapter cannot be
    imported, so the detection package remains usable standalone.
    """
    crash_dump = crash_dump or {}
    try:
        from app.api.adapters import canonical_window_dicts

        return canonical_window_dicts(crash_dump)
    except Exception:  # pragma: no cover — adapter always available in-tree
        rows: list[dict[str, Any]] = []
        for field in ("pre_fault_telemetry_window", "pre_fault_telemetry"):
            source = crash_dump.get(field)
            if isinstance(source, list):
                rows.extend(r for r in source if isinstance(r, dict))
        return rows


def run_detection(
    readings: list[dict[str, Any]],
    channel_summaries: Optional[list[dict[str, Any]]] = None,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    robust_z_threshold: float = DEFAULT_ROBUST_Z_THRESHOLD,
    persistence_samples: int = DEFAULT_PERSISTENCE_SAMPLES,
    step_sigma: float = DEFAULT_STEP_SIGMA,
    allow_range_derived: bool = True,
    enable_limits: bool = True,
    enable_statistical: bool = True,
    enable_temporal: bool = True,
) -> AnomalyReport:
    """Run the detection pipeline over a telemetry window.

    Args:
        readings: Telemetry readings, each with ``parameter`` and ``value``.
        channel_summaries: ESA-ADB channel summaries carrying observed baseline
            statistics, when available.
        z_threshold: |z| threshold for the classical statistical detector.
        robust_z_threshold: threshold for the median/MAD detector.
        persistence_samples: consecutive out-of-band samples before a deviation
            is reported as persistent.
        step_sigma: step size in sigmas that counts as a sudden change.
        allow_range_derived: when False, statistical detection abstains on
            channels with no observed baseline rather than deriving sigma from
            engineering limits.
        enable_limits / enable_statistical / enable_temporal: stage switches, for
            ablation studies.

    Returns:
        A fully populated AnomalyReport. Never raises on malformed input.
    """
    readings = [r for r in (readings or []) if isinstance(r, dict)]

    provider = BaselineProvider(
        readings,
        channel_summaries=channel_summaries,
        allow_range_derived=allow_range_derived,
    )

    all_anomalies: list[Anomaly] = []
    warnings: list[str] = []

    # ── Stage 1 + 2: hard limits, discrete states, counters, data quality ──
    limit_anomalies: list[Anomaly] = []
    if enable_limits:
        limit_anomalies = detect_limits(readings)
        all_anomalies.extend(limit_anomalies)

    # ── Stage 3: statistical ───────────────────────────────────────────────
    stat_anomalies: list[Anomaly] = []
    if enable_statistical:
        stat_anomalies, stat_warnings = detect_statistical(
            readings,
            provider=provider,
            z_threshold=z_threshold,
            robust_z_threshold=robust_z_threshold,
        )
        all_anomalies.extend(stat_anomalies)
        warnings.extend(stat_warnings)

    # ── Stage 4: temporal ──────────────────────────────────────────────────
    temporal_anomalies: list[Anomaly] = []
    if enable_temporal:
        temporal_anomalies, temporal_warnings = detect_temporal(
            readings,
            provider=provider,
            persistence_samples=persistence_samples,
            step_sigma=step_sigma,
            z_threshold=z_threshold,
        )
        all_anomalies.extend(temporal_anomalies)
        warnings.extend(temporal_warnings)

    # ── Stage 5: fusion ────────────────────────────────────────────────────
    all_anomalies.sort(key=_sort_key)

    channels = _fuse_by_channel(all_anomalies)
    all_channel_names = {
        str(r.get("parameter")) for r in readings if r.get("parameter")
    }

    detectors_run = _detector_accounting(
        all_anomalies, readings,
        enable_limits=enable_limits,
        enable_statistical=enable_statistical,
        enable_temporal=enable_temporal,
    )

    max_severity = (
        max((c.severity for c in channels), key=severity_rank)
        if channels else Severity.INFO
    )

    report = AnomalyReport(
        anomalies=all_anomalies,
        channels=channels,
        detectors_run=detectors_run,
        total_readings=len(readings),
        total_channels=len(all_channel_names),
        anomalous_channels=len(channels),
        anomaly_count=len(all_anomalies),
        max_severity=max_severity,
        summary=_build_summary(all_anomalies, channels, readings, all_channel_names),
        warnings=warnings,
    )
    return report


def run_detection_on_crash_dump(
    crash_dump: dict[str, Any],
    **kwargs: Any,
) -> AnomalyReport:
    """Convenience wrapper: extract the window from a crash dump and detect."""
    crash_dump = crash_dump or {}
    return run_detection(
        extract_readings(crash_dump),
        channel_summaries=_extract_channel_summaries(crash_dump),
        **kwargs,
    )


# ═══════════════════════════════════════════════════════════════════════════
# FUSION INTERNALS
# ═══════════════════════════════════════════════════════════════════════════

def _fuse_by_channel(anomalies: list[Anomaly]) -> list[ChannelFinding]:
    """Group anomalies per channel and apply corroboration."""
    grouped: dict[str, list[Anomaly]] = {}
    for a in anomalies:
        grouped.setdefault(a.channel, []).append(a)

    findings: list[ChannelFinding] = []
    for channel, items in grouped.items():
        detectors: list[DetectorName] = []
        for a in items:
            if a.detector not in detectors:
                detectors.append(a.detector)

        families = {_EVIDENCE_FAMILY.get(d, d.value) for d in detectors}
        corroborated = len(families) >= 2

        base_severity = max((a.severity for a in items), key=severity_rank)
        severity = (
            _raise_one_step(base_severity) if corroborated else base_severity
        )

        scores = [a.score for a in items if a.score is not None]
        # Earliest offset, by parsed time where possible so 'T-300s' sorts
        # before 'T-60s' rather than lexicographically.
        first_seen = _earliest_offset(items)

        findings.append(ChannelFinding(
            channel=channel,
            severity=severity,
            detectors=detectors,
            anomaly_count=len(items),
            first_seen=first_seen,
            max_score=max(scores) if scores else None,
            corroborated=corroborated,
            anomalies=sorted(items, key=_sort_key),
        ))

    findings.sort(key=lambda c: (-severity_rank(c.severity), c.channel))
    return findings


def _earliest_offset(items: list[Anomaly]) -> Optional[str]:
    from app.detection.temporal import parse_offset_seconds

    best_ts: Optional[str] = None
    best_sec: Optional[float] = None
    for a in items:
        sec = parse_offset_seconds(a.timestamp)
        if sec is None:
            if best_ts is None:
                best_ts = a.timestamp
            continue
        if best_sec is None or sec < best_sec:
            best_sec, best_ts = sec, a.timestamp
    return best_ts


def _detector_accounting(
    anomalies: list[Anomaly],
    readings: list[dict[str, Any]],
    enable_limits: bool,
    enable_statistical: bool,
    enable_temporal: bool,
) -> list[DetectorRunInfo]:
    """Report what every detector did — including the ones that found nothing.

    A detector that finds nothing is information. The pre-Phase-2 report could
    not express this, so a silently-blind detector was indistinguishable from a
    clean spacecraft.
    """
    enabled_by_module = {
        "limit": enable_limits, "state": enable_limits, "quality": enable_limits,
        "statistical": enable_statistical, "temporal": enable_temporal,
    }

    info: list[DetectorRunInfo] = []
    for det in _ALL_DETECTORS:
        found = [a for a in anomalies if a.detector is det]
        family = _EVIDENCE_FAMILY.get(det, "")
        enabled = enabled_by_module.get(family, True)
        info.append(DetectorRunInfo(
            detector=det,
            enabled=enabled,
            readings_examined=len(readings) if enabled else 0,
            anomalies_found=len(found),
            channels_flagged=len({a.channel for a in found}),
            notes=None if enabled else "stage disabled for this run",
        ))
    return info


def _build_summary(
    anomalies: list[Anomaly],
    channels: list[ChannelFinding],
    readings: list[dict[str, Any]],
    all_channels: set[str],
) -> str:
    if not anomalies:
        return (
            f"No anomalies detected across {len(readings)} reading(s) on "
            f"{len(all_channels)} channel(s)."
        )

    top = channels[0]
    detector_counts: dict[str, int] = {}
    for a in anomalies:
        detector_counts[a.detector.value] = detector_counts.get(a.detector.value, 0) + 1
    breakdown = ", ".join(
        f"{name}={count}" for name, count in sorted(detector_counts.items())
    )
    corroborated = [c.channel for c in channels if c.corroborated]

    parts = [
        f"{len(anomalies)} anomaly(ies) on {len(channels)} of "
        f"{len(all_channels)} channel(s) across {len(readings)} reading(s).",
        f"Highest severity: {top.severity.value} on {top.channel}.",
        f"By detector: {breakdown}.",
    ]
    if corroborated:
        parts.append(
            f"Corroborated by independent detectors: {', '.join(sorted(corroborated))}."
        )
    return " ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# DETERMINISM CHECK
# ═══════════════════════════════════════════════════════════════════════════

def assert_deterministic(
    readings: list[dict[str, Any]],
    runs: int = 3,
    **kwargs: Any,
) -> bool:
    """Run detection several times and confirm the reports are identical.

    Phase 2 forbids random detector outputs. This exercises that property rather
    than asserting it in a comment. Raises AssertionError on any divergence.
    """
    first = run_detection(readings, **kwargs).model_dump_json()
    for i in range(1, runs):
        again = run_detection(readings, **kwargs).model_dump_json()
        if again != first:
            raise AssertionError(
                f"Detection is not deterministic: run {i} differs from run 0."
            )
    return True

"""
SENTINEL — Anomaly Detection Contract (detection/models.py)

Phase 2. The unified anomaly record every detector produces and the report
fusion assembles.

Every ``Anomaly`` carries the nine fields Phase 2 requires:

    anomaly_id   stable identifier, derived from content (not random)
    channel      telemetry channel the anomaly was found on
    timestamp    relative offset the anomaly was observed at
    detector     which detector produced it
    score        the detector's numeric output
    threshold    the value the score was compared against
    severity     operator-facing severity
    evidence     the observed values the verdict rests on
    provenance   how the decision was reached, and how much to trust it

Two rules the whole package obeys:

  * NO RANDOMNESS. No detector output is ever sampled. Given the same telemetry,
    the report is byte-identical. ``anomaly_id`` is a content hash, so it is
    stable across runs and machines.
  * NO HARDCODED RESULTS. Nothing in this package contains a pre-written
    anomaly. Every record is computed from the telemetry passed in.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class DetectorName(str, Enum):
    """Which detector produced a finding."""

    HARD_LIMIT = "HARD_LIMIT"
    """limits.py — value outside a declared min/max limit."""

    DISCRETE_STATE = "DISCRETE_STATE"
    """limits.py — status/flag/bitmask channel not in its expected state."""

    COUNTER = "COUNTER"
    """limits.py — counter moved when it was expected to stay put, or overflowed."""

    DATA_QUALITY = "DATA_QUALITY"
    """limits.py — NaN, Inf, missing or non-numeric where a number is required."""

    ZSCORE = "ZSCORE"
    """statistical.py — deviation from a mean/sigma baseline."""

    ROBUST_ZSCORE = "ROBUST_ZSCORE"
    """statistical.py — deviation from a median/MAD baseline (outlier-resistant)."""

    RATE_OF_CHANGE = "RATE_OF_CHANGE"
    """temporal.py — first derivative exceeds its limit."""

    TREND = "TREND"
    """temporal.py — sustained monotonic drift across the window."""

    PERSISTENCE = "PERSISTENCE"
    """temporal.py — a deviation held for consecutive samples (not a spike)."""

    SUDDEN_CHANGE = "SUDDEN_CHANGE"
    """temporal.py — step change between adjacent samples."""


class Severity(str, Enum):
    """Operator-facing severity of a single anomaly."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


_SEVERITY_RANK: dict[str, int] = {
    Severity.CRITICAL.value: 4,
    Severity.HIGH.value: 3,
    Severity.MEDIUM.value: 2,
    Severity.LOW.value: 1,
    Severity.INFO.value: 0,
}


def severity_rank(severity: "Severity | str") -> int:
    """Numeric rank for ordering severities. CRITICAL is highest."""
    key = severity.value if isinstance(severity, Severity) else str(severity)
    return _SEVERITY_RANK.get(key, 0)


class BaselineSource(str, Enum):
    """Where the statistics a detector compared against came from.

    This is the single most important provenance field in Phase 2. The
    pre-Phase-2 detector derived sigma as ``(hi - lo) / 6`` from *engineering
    limits* and then reported the result as a statistical z-score. That is not a
    statistic — it assumes the limits are a 3-sigma band, which for a channel
    like ``Watchdog_counter`` (0..1000) makes a genuine overflow score z=2.85 and
    slip under a 3.0 threshold.

    Findings whose baseline is RANGE_DERIVED are explicitly marked as weak
    evidence so nothing downstream mistakes them for observed statistics.
    """

    OBSERVED_PROVIDED = "OBSERVED_PROVIDED"
    """mean/sigma supplied with the data, computed from real observations
    (e.g. ESA-ADB baseline_mean / baseline_std over 252 baseline rows)."""

    OBSERVED_WINDOW = "OBSERVED_WINDOW"
    """Computed from the nominal-labelled samples in the supplied window."""

    RANGE_DERIVED = "RANGE_DERIVED"
    """Fabricated from engineering limits as midpoint +/- (hi-lo)/6. WEAK
    EVIDENCE — retained only as a last-resort fallback."""

    NONE = "NONE"
    """No baseline was available; no statistical claim is made."""


class Confidence(str, Enum):
    """How much weight a finding's evidence deserves."""
    HIGH = "HIGH"
    """Deterministic check, or a statistic from observed data."""
    MEDIUM = "MEDIUM"
    """Statistic from a small observed sample."""
    LOW = "LOW"
    """Statistic derived from engineering limits rather than observations."""


class AnomalyProvenance(BaseModel):
    """How a finding was reached, and how far it can be trusted."""

    detector_module: str = Field(
        ...,
        description="Module that produced the finding, e.g. 'app.detection.limits'",
    )
    baseline_source: BaselineSource = Field(
        default=BaselineSource.NONE,
        description="Where the comparison statistics came from",
    )
    baseline_sample_count: Optional[int] = Field(
        default=None,
        description="Number of observations behind the baseline, when known",
    )
    confidence: Confidence = Field(
        default=Confidence.HIGH,
        description="Weight this evidence deserves",
    )
    deterministic: bool = Field(
        default=True,
        description=(
            "True when the check involves no estimated statistics. Always True "
            "for limits.py; True for statistical/temporal detectors too, in the "
            "sense that they are reproducible — no detector samples randomness."
        ),
    )
    notes: Optional[str] = Field(
        default=None,
        description="Any caveat an operator should read alongside the finding",
    )


class Anomaly(BaseModel):
    """One anomaly finding from one detector."""

    anomaly_id: str = Field(
        ...,
        description=(
            "Stable content-derived identifier. Same telemetry always yields the "
            "same id; it is a hash, never a random value or a counter."
        ),
    )
    channel: str = Field(..., min_length=1, description="Telemetry channel name")
    timestamp: str = Field(
        ...,
        description="Relative offset the anomaly was observed at, e.g. 'T-60s'",
    )
    detector: DetectorName = Field(..., description="Detector that produced this")
    score: Optional[float] = Field(
        default=None,
        description=(
            "The detector's numeric output. None when the finding is categorical "
            "(a discrete-state violation has no meaningful magnitude)."
        ),
    )
    threshold: Optional[float] = Field(
        default=None,
        description="Value the score was compared against, when numeric",
    )
    severity: Severity = Field(..., description="Operator-facing severity")
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Observed values the verdict rests on, e.g. "
            "{'value': 1002.0, 'limit_max': 1000.0, 'exceeded_by': 2.0}. "
            "Only values actually read from the telemetry appear here."
        ),
    )
    provenance: AnomalyProvenance = Field(
        ..., description="How the finding was reached and how far to trust it",
    )
    description: str = Field(
        ...,
        min_length=1,
        description="One-line operator-facing explanation",
    )

    @staticmethod
    def make_id(
        channel: str,
        timestamp: str,
        detector: "DetectorName | str",
        discriminator: str = "",
    ) -> str:
        """Build a stable anomaly_id from the finding's identity.

        Deliberately a content hash rather than a counter or a UUID: a report
        generated twice from the same telemetry must be byte-identical, which
        makes reports diffable and testable.
        """
        det = detector.value if isinstance(detector, DetectorName) else str(detector)
        raw = f"{det}|{channel}|{timestamp}|{discriminator}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return f"AN-{det[:4]}-{digest}"


class ChannelFinding(BaseModel):
    """All anomalies on one channel, after fusion."""

    channel: str = Field(..., min_length=1)
    severity: Severity = Field(
        ..., description="Highest severity among this channel's anomalies",
    )
    detectors: list[DetectorName] = Field(
        default_factory=list,
        description="Distinct detectors that flagged this channel",
    )
    anomaly_count: int = Field(..., ge=0)
    first_seen: Optional[str] = Field(
        default=None, description="Earliest offset this channel was flagged at",
    )
    max_score: Optional[float] = Field(
        default=None, description="Largest finite score across this channel",
    )
    corroborated: bool = Field(
        default=False,
        description=(
            "True when two or more INDEPENDENT detectors flagged this channel. "
            "Corroboration raises confidence; it does not by itself raise severity."
        ),
    )
    anomalies: list[Anomaly] = Field(default_factory=list)


class DetectorRunInfo(BaseModel):
    """Per-detector accounting, so a silent detector is visible."""

    detector: DetectorName
    enabled: bool = True
    readings_examined: int = 0
    anomalies_found: int = 0
    channels_flagged: int = 0
    notes: Optional[str] = None


class AnomalyReport(BaseModel):
    """The unified output of the detection pipeline."""

    schema_version: str = Field(
        default="2.0",
        description="Report schema version. 1.x was the pre-Phase-2 z-score dict.",
    )
    anomalies: list[Anomaly] = Field(
        default_factory=list,
        description="Every finding, sorted by severity then channel then offset",
    )
    channels: list[ChannelFinding] = Field(
        default_factory=list,
        description="Findings grouped by channel",
    )
    detectors_run: list[DetectorRunInfo] = Field(
        default_factory=list,
        description="What each detector examined and found",
    )
    total_readings: int = Field(default=0, ge=0)
    total_channels: int = Field(default=0, ge=0)
    anomalous_channels: int = Field(default=0, ge=0)
    anomaly_count: int = Field(default=0, ge=0)
    max_severity: Severity = Field(
        default=Severity.INFO,
        description="Highest severity anywhere in the report",
    )
    summary: str = Field(default="", description="One-sentence plain-text summary")
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Caveats about the detection run itself, e.g. that a channel had no "
            "observed baseline so only weak statistical evidence was available."
        ),
    )

    # ---- convenience accessors used by callers and the SSE layer ----

    def anomalous_channel_names(self) -> list[str]:
        """Channel names with at least one finding, highest severity first."""
        return [c.channel for c in self.channels]

    def by_detector(self, detector: DetectorName) -> list[Anomaly]:
        return [a for a in self.anomalies if a.detector is detector]

    def top_anomaly(self) -> Optional[Anomaly]:
        return self.anomalies[0] if self.anomalies else None

    def legacy_dict(self) -> dict[str, Any]:
        """Render as the pre-Phase-2 ``ZScoreAnomalyDetector.detect()`` shape.

        Kept so existing consumers (agent.py's streaming stage, test_pipeline)
        keep working while the richer report becomes available alongside. The
        legacy shape cannot express detector, threshold, evidence or provenance,
        which is why it is not the primary contract.
        """
        params: list[dict[str, Any]] = []
        for c in self.channels:
            top = max(
                c.anomalies,
                key=lambda a: (severity_rank(a.severity), a.score or 0.0),
            )
            score = top.score
            params.append({
                "parameter": c.channel,
                "value": top.evidence.get("value"),
                "z_score": abs(score) if score is not None else float("inf"),
                "anomaly_severity": c.severity.value,
                "direction": top.evidence.get("direction", "UNKNOWN"),
                "detector": top.detector.value,
            })
        return {
            "anomalous_parameters": params,
            "total_parameters_checked": self.total_channels,
            "anomaly_count": len(params),
            "top_anomaly": params[0]["parameter"] if params else "none",
            "summary": self.summary,
        }

"""
SENTINEL — Anomaly Detection Package.

Phase 2 ("Correct Anomaly Detection"). Replaces a single range-derived z-score
test with a staged pipeline:

    telemetry
      -> hard-limit detection      limits.py
      -> discrete-state detection  limits.py
      -> statistical detection     statistical.py   (z-score, observed baselines)
      -> temporal detection        temporal.py
      -> fusion                    fusion.py        -> AnomalyReport

Z-score detection is KEPT as the baseline method. What changed is that its
baseline statistics now come from observed data where observed data exists, and
that every finding records which baseline source it used. No claim is made that
any method is more accurate than another — establishing that needs labelled data
and belongs to the evaluation phase.

The legacy ``app.analytics.anomaly_detector.ZScoreAnomalyDetector`` is left
untouched and still works. It is the documented comparison baseline.

Quick start:

    from app.detection import run_detection_on_crash_dump

    report = run_detection_on_crash_dump(crash_dump)
    print(report.summary)
    for anomaly in report.anomalies:
        print(anomaly.channel, anomaly.detector.value, anomaly.severity.value)
"""

from app.detection.baseline import (  # noqa: F401
    MIN_OBSERVED_SAMPLES,
    BaselineProvider,
    BaselineStats,
    compute_stats,
)
from app.detection.channels import (  # noqa: F401
    CHANNEL_SPECS,
    KNOWN_BLIND_SPOT_CHANNELS,
    ChannelKind,
    ChannelSpec,
    channel_dictionary_status,
    get_channel_spec,
    spec_or_inferred,
)
from app.detection.fusion import (  # noqa: F401
    assert_deterministic,
    extract_readings,
    run_detection,
    run_detection_on_crash_dump,
)
from app.detection.limits import (  # noqa: F401
    classify_value,
    detect_limits,
)
from app.detection.models import (  # noqa: F401
    Anomaly,
    AnomalyProvenance,
    AnomalyReport,
    BaselineSource,
    ChannelFinding,
    Confidence,
    DetectorName,
    DetectorRunInfo,
    Severity,
    severity_rank,
)
from app.detection.statistical import (  # noqa: F401
    DEFAULT_ROBUST_Z_THRESHOLD,
    DEFAULT_Z_THRESHOLD,
    compare_against_range_derived,
    detect_statistical,
)
from app.detection.temporal import (  # noqa: F401
    DEFAULT_PERSISTENCE_SAMPLES,
    DEFAULT_STEP_SIGMA,
    Sample,
    build_series,
    detect_persistence,
    detect_rate_of_change,
    detect_sudden_change,
    detect_temporal,
    detect_trend,
    parse_offset_seconds,
)

__all__ = [
    # entry points
    "run_detection",
    "run_detection_on_crash_dump",
    "extract_readings",
    "assert_deterministic",
    # contract
    "Anomaly",
    "AnomalyReport",
    "AnomalyProvenance",
    "ChannelFinding",
    "DetectorName",
    "DetectorRunInfo",
    "Severity",
    "BaselineSource",
    "Confidence",
    "severity_rank",
    # channels
    "CHANNEL_SPECS",
    "ChannelKind",
    "ChannelSpec",
    "KNOWN_BLIND_SPOT_CHANNELS",
    "channel_dictionary_status",
    "get_channel_spec",
    "spec_or_inferred",
    # baseline
    "BaselineProvider",
    "BaselineStats",
    "compute_stats",
    "MIN_OBSERVED_SAMPLES",
    # detectors
    "detect_limits",
    "detect_statistical",
    "detect_temporal",
    "detect_rate_of_change",
    "detect_trend",
    "detect_persistence",
    "detect_sudden_change",
    "compare_against_range_derived",
    "classify_value",
    "build_series",
    "parse_offset_seconds",
    "Sample",
    "DEFAULT_Z_THRESHOLD",
    "DEFAULT_ROBUST_Z_THRESHOLD",
    "DEFAULT_PERSISTENCE_SAMPLES",
    "DEFAULT_STEP_SIGMA",
]

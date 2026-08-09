"""
SENTINEL — Baseline Statistics (detection/baseline.py)

Phase 2. Supplies the mean/sigma (and robust median/MAD) that statistical
detection compares against, and — critically — records WHERE they came from.

Resolution order, strongest evidence first:

  1. OBSERVED_PROVIDED  mean/sigma shipped with the data.
                        ESA-ADB dumps carry ``baseline_mean`` / ``baseline_std``
                        computed over a real 2-hour baseline window (252 rows for
                        Mission 1 id_109). These are genuine observed statistics
                        and are used verbatim.

  2. OBSERVED_WINDOW    computed from the nominal-labelled samples in the window
                        being analysed. Requires MIN_OBSERVED_SAMPLES points.
                        Samples already flagged anomalous are excluded, so a
                        fault does not contaminate the baseline it is measured
                        against.

  3. RANGE_DERIVED      midpoint +/- (hi - lo) / 6 from engineering limits.
                        THIS IS NOT A STATISTIC. It assumes the limits are a
                        3-sigma band, which is what made a Watchdog_counter
                        overflow score z=2.85. Used only as a last resort, only
                        for CONTINUOUS channels, and always tagged
                        Confidence.LOW so nothing downstream mistakes it for
                        observed data.

  4. NONE               no baseline; the statistical detector abstains rather
                        than inventing one.

The pre-Phase-2 detector went straight to (3) for everything, including channels
where it is meaningless, and reported the result as a z-score with no indication
that sigma had been fabricated.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from app.detection.channels import ChannelKind, ChannelSpec
from app.detection.models import BaselineSource, Confidence

#: Minimum observations required before a window-derived baseline is trusted.
#: Below this, sigma is too unstable to threshold against.
MIN_OBSERVED_SAMPLES: int = 5

#: Scale factor converting MAD to a sigma-equivalent for normal data.
#: 1 / Phi^-1(0.75) = 1.4826
MAD_TO_SIGMA: float = 1.4826

#: Floor applied to sigma to avoid divide-by-zero on a perfectly constant
#: channel. Expressed relative to |mean| so it scales with the signal.
_SIGMA_RELATIVE_FLOOR: float = 1e-9


@dataclass(frozen=True)
class BaselineStats:
    """Baseline statistics for one channel, with their provenance."""

    channel: str
    mean: Optional[float]
    std: Optional[float]
    median: Optional[float]
    mad: Optional[float]
    sample_count: int
    source: BaselineSource

    @property
    def confidence(self) -> Confidence:
        if self.source is BaselineSource.OBSERVED_PROVIDED:
            return Confidence.HIGH
        if self.source is BaselineSource.OBSERVED_WINDOW:
            return (
                Confidence.HIGH if self.sample_count >= 20 else Confidence.MEDIUM
            )
        if self.source is BaselineSource.RANGE_DERIVED:
            return Confidence.LOW
        return Confidence.LOW

    @property
    def robust_scale_preferred(self) -> bool:
        """True when mean/std cannot be trusted as the location/scale pair.

        A baseline computed from the SAME window that contains the fault is
        contaminated by it: the excursion pulls the mean toward itself and
        inflates sigma, so the deviation it should reveal scores low. Measured on
        a 12-sample window with 4 fault samples, a 6.8-unit excursion scored
        |z| = 1.5 against the contaminated mean/std, and |z| = 61 against
        median/MAD.

        Median and MAD tolerate up to half the samples being contaminated, so
        they are used as the location/scale pair for window-derived baselines.
        An externally supplied baseline (OBSERVED_PROVIDED) is measured over a
        separate window and needs no such protection, so its mean/std are used
        directly.
        """
        return self.source is BaselineSource.OBSERVED_WINDOW

    @property
    def effective_center(self) -> Optional[float]:
        """The location estimate a z-score should be taken against."""
        if self.robust_scale_preferred and self.median is not None:
            return self.median
        return self.mean

    @property
    def effective_scale(self) -> Optional[float]:
        """The scale estimate a z-score should be divided by."""
        if self.robust_scale_preferred and self.mad is not None and self.mad > 0:
            return self.mad * MAD_TO_SIGMA
        return self.std

    @property
    def scale_basis(self) -> str:
        """Which statistics ``zscore()`` actually used — reported as evidence."""
        return "median_mad" if self.robust_scale_preferred else "mean_std"

    @property
    def usable_for_zscore(self) -> bool:
        scale = self.effective_scale
        return (
            self.effective_center is not None
            and scale is not None
            and scale > 0.0
            and self.source is not BaselineSource.NONE
        )

    @property
    def usable_for_robust(self) -> bool:
        return (
            self.median is not None
            and self.mad is not None
            and self.mad > 0.0
        )

    def zscore(self, value: float) -> Optional[float]:
        """Deviation in standard-deviation units, or None if unsupported.

        Uses mean/std for an externally supplied baseline and median/MAD for a
        window-derived one — see ``robust_scale_preferred`` for why. The choice is
        reported in each finding's evidence as ``scale_basis``, so a reader can
        always tell which statistics produced the number.
        """
        if not self.usable_for_zscore:
            return None
        return (value - self.effective_center) / self.effective_scale

    def robust_zscore(self, value: float) -> Optional[float]:
        """Median/MAD z-score. Resistant to outliers in the baseline itself."""
        if not self.usable_for_robust:
            return None
        return (value - self.median) / (self.mad * MAD_TO_SIGMA)

    @staticmethod
    def none(channel: str) -> "BaselineStats":
        return BaselineStats(
            channel=channel, mean=None, std=None, median=None, mad=None,
            sample_count=0, source=BaselineSource.NONE,
        )


def _finite(value: Any) -> Optional[float]:
    """Coerce to a finite float, or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.upper() in ("NAN", "NONE", "", "N/A", "NULL", "INF", "-INF"):
            return None
        try:
            value = float(text)
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    f = float(value)
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def compute_stats(
    channel: str,
    values: Iterable[float],
    source: BaselineSource,
) -> BaselineStats:
    """Compute mean/std/median/MAD from observed values."""
    vals = [v for v in (_finite(v) for v in values) if v is not None]
    n = len(vals)
    if n == 0:
        return BaselineStats.none(channel)

    mean = statistics.fmean(vals)
    std = statistics.stdev(vals) if n > 1 else 0.0
    median = statistics.median(vals)
    mad = statistics.median([abs(v - median) for v in vals])

    # Guard against a perfectly constant channel producing sigma == 0, which
    # would make every subsequent z-score infinite. A constant channel is the
    # limits/discrete detectors' business, not the statistical detector's.
    floor = max(abs(mean) * _SIGMA_RELATIVE_FLOOR, 0.0)
    if std < floor:
        std = 0.0

    return BaselineStats(
        channel=channel, mean=mean, std=std, median=median, mad=mad,
        sample_count=n, source=source,
    )


class BaselineProvider:
    """Resolves the best available baseline for each channel in a dump.

    Build once per crash dump, then query per channel. Resolution is cached, so
    a report over N readings does not recompute statistics N times.
    """

    def __init__(
        self,
        readings: list[dict[str, Any]],
        channel_summaries: Optional[list[dict[str, Any]]] = None,
        allow_range_derived: bool = True,
    ) -> None:
        """
        Args:
            readings: The telemetry window being analysed.
            channel_summaries: ESA-ADB ``channel_summaries``, when present. This
                is the strongest source: real statistics over a real baseline
                window, external to the sample being judged.
            allow_range_derived: When False, the provider refuses the
                engineering-limit fallback and returns NONE instead. Used by
                tests and by any caller that wants observed evidence only.
        """
        self._readings = readings or []
        self._allow_range_derived = allow_range_derived
        self._cache: dict[str, BaselineStats] = {}

        # --- Source 1: statistics supplied with the data ---
        self._provided: dict[str, tuple[float, float, Optional[int]]] = {}

        for row in channel_summaries or []:
            if not isinstance(row, dict):
                continue
            name = row.get("channel") or row.get("parameter")
            mean = _finite(row.get("baseline_mean"))
            std = _finite(row.get("baseline_std"))
            if name and mean is not None and std is not None:
                rows = row.get("baseline_rows")
                self._provided[str(name)] = (
                    mean, std, int(rows) if isinstance(rows, int) else None,
                )

        # Readings may also carry their own baseline_mean/baseline_std.
        for r in self._readings:
            if not isinstance(r, dict):
                continue
            name = r.get("parameter")
            if not name or name in self._provided:
                continue
            mean = _finite(r.get("baseline_mean"))
            std = _finite(r.get("baseline_std"))
            if mean is not None and std is not None:
                self._provided[str(name)] = (mean, std, None)

        # --- Source 2: nominal-labelled samples from the window itself ---
        self._window_values: dict[str, list[float]] = {}
        for r in self._readings:
            if not isinstance(r, dict):
                continue
            name = r.get("parameter")
            if not name:
                continue
            # Exclude samples already marked anomalous, and any explicit
            # ANOMALOUS/CRITICAL status, so the fault does not pollute the
            # baseline it will be measured against.
            if r.get("anomalous") is True:
                continue
            status = str(r.get("status", "") or "").upper()
            if status in ("ANOMALOUS", "CRITICAL"):
                continue
            v = _finite(r.get("value"))
            if v is not None:
                self._window_values.setdefault(str(name), []).append(v)

    # ------------------------------------------------------------------

    def get(self, spec: ChannelSpec) -> BaselineStats:
        """Resolve the strongest available baseline for a channel."""
        if spec.name in self._cache:
            return self._cache[spec.name]
        stats = self._resolve(spec)
        self._cache[spec.name] = stats
        return stats

    def _resolve(self, spec: ChannelSpec) -> BaselineStats:
        name = spec.name

        # 1. Statistics provided with the data (strongest).
        provided = self._provided.get(name)
        if provided is not None:
            mean, std, rows = provided
            # Median/MAD are not provided by ESA-ADB, so approximate the robust
            # pair from the window when possible; otherwise leave them unset
            # rather than deriving them from mean/std (which would just restate
            # the Gaussian assumption under a different name).
            window = self._window_values.get(name, [])
            median = statistics.median(window) if window else None
            mad = (
                statistics.median([abs(v - median) for v in window])
                if window and median is not None else None
            )
            return BaselineStats(
                channel=name, mean=mean, std=std, median=median, mad=mad,
                sample_count=rows if rows is not None else len(window),
                source=BaselineSource.OBSERVED_PROVIDED,
            )

        # 2. Observed samples from the window under analysis.
        window = self._window_values.get(name, [])
        if len(window) >= MIN_OBSERVED_SAMPLES:
            return compute_stats(name, window, BaselineSource.OBSERVED_WINDOW)

        # 3. Engineering limits, as a last resort and only where a Gaussian
        #    assumption is not obviously wrong.
        if (
            self._allow_range_derived
            and spec.kind is ChannelKind.CONTINUOUS
            and spec.limit_min is not None
            and spec.limit_max is not None
            and spec.limit_max > spec.limit_min
        ):
            lo, hi = float(spec.limit_min), float(spec.limit_max)
            mean = (lo + hi) / 2.0
            std = (hi - lo) / 6.0
            return BaselineStats(
                channel=name, mean=mean, std=std, median=mean, mad=None,
                sample_count=0, source=BaselineSource.RANGE_DERIVED,
            )

        # 4. Abstain.
        return BaselineStats.none(name)

    # ------------------------------------------------------------------

    def observed_sample_count(self, channel: str) -> int:
        return len(self._window_values.get(channel, []))

    def summary(self) -> dict[str, Any]:
        """What baselines were resolved, and from where."""
        by_source: dict[str, list[str]] = {}
        for name, stats in sorted(self._cache.items()):
            by_source.setdefault(stats.source.value, []).append(name)
        return {
            "resolved_channels": len(self._cache),
            "channels_per_source": {k: sorted(v) for k, v in sorted(by_source.items())},
            "provided_statistics_available_for": sorted(self._provided),
            "range_derived_allowed": self._allow_range_derived,
        }

"""
Phase 2 regression tests — anomaly detection.

Two things are being guaranteed:

  1. Every detector failure identified in the repository audit is fixed, and
     stays fixed. Each is reproduced against the ORIGINAL detector first, so the
     test proves the bug was real rather than asserting a fix in the abstract.

  2. The pipeline behaves correctly on the required scenario set: normal
     telemetry, limit violation, discrete-state violation, NaN, persistent
     anomaly, transient anomaly, sudden rate change.

Run:
    cd sentinel/backend && python3 -m unittest tests.test_phase2_detection -v
"""

from __future__ import annotations

import json
import math
import os
import sys
import unittest

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.analytics.anomaly_detector import (  # noqa: E402
    SATELLITE_NOMINAL_RANGES,
    ZScoreAnomalyDetector,
)
from app.detection import (  # noqa: E402
    CHANNEL_SPECS,
    KNOWN_BLIND_SPOT_CHANNELS,
    AnomalyReport,
    BaselineProvider,
    BaselineSource,
    ChannelKind,
    Confidence,
    DetectorName,
    Severity,
    assert_deterministic,
    build_series,
    channel_dictionary_status,
    classify_value,
    compare_against_range_derived,
    compute_stats,
    detect_limits,
    detect_statistical,
    detect_temporal,
    extract_readings,
    parse_offset_seconds,
    run_detection,
    run_detection_on_crash_dump,
    spec_or_inferred,
)

ESA_DIR = os.path.join(_BACKEND_ROOT, "data", "esa_crash_dumps")


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def reading(
    parameter: str,
    value,
    offset: str = "T-0s",
    nominal_min=None,
    nominal_max=None,
    **extra,
) -> dict:
    """Build one telemetry reading. Bounds default to the declared range."""
    if nominal_min is None or nominal_max is None:
        rng = SATELLITE_NOMINAL_RANGES.get(parameter)
        if rng:
            nominal_min = rng[0] if nominal_min is None else nominal_min
            nominal_max = rng[1] if nominal_max is None else nominal_max
    row = {
        "parameter": parameter,
        "value": value,
        "timestamp_offset": offset,
    }
    if nominal_min is not None:
        row["nominal_min"] = nominal_min
    if nominal_max is not None:
        row["nominal_max"] = nominal_max
    row.update(extra)
    return row


def series(parameter: str, pairs, **extra) -> list[dict]:
    """Build a time series: pairs of (seconds_offset, value)."""
    return [
        reading(parameter, v, offset=f"T{sec:+g}s", **extra) for sec, v in pairs
    ]


#: The nominal-range table exactly as it stood when the Phase 2 blind spots were
#: measured. Frozen here on purpose.
#:
#: Phase 5 moved the live table into app/ingest/channel_dict.py and, while doing
#: so, added nine channels the repository shipped but no table declared. That
#: exposed an order-dependence in the legacy detector:
#: fit_from_nominal_ranges() draws window_size random samples per channel from a
#: SINGLE shared random.Random(0) stream, iterating the dict in insertion order.
#: Every channel's baseline therefore depends on how many channels precede it, so
#: adding unrelated channels changed Watchdog_counter's empirical sigma and its
#: z-score moved from 2.85 to 5.28.
#:
#: That fragility belongs to the detector this suite is documenting as broken, and
#: it is one more reason the Phase 2 pipeline replaced it. The reproduction must
#: be faithful to the historical state, so it uses the historical table rather
#: than whatever the dictionary currently holds.
LEGACY_NOMINAL_RANGES: dict[str, tuple[float, float]] = {
    "V_bat": (28.0, 33.6),
    "SoC_pct": (20.0, 100.0),
    "I_sa": (0.0, 12.0),
    "V_bus": (26.6, 29.4),
    "Heater_power_W": (0.0, 50.0),
    "RW_speed_rpm": (-6000.0, 6000.0),
    "Gyro_rate_degs": (0.0, 7.0),
    "Star_tracker_status": (0.0, 0.0),
    "Sun_sensor_angle_deg": (0.0, 90.0),
    "Attitude_error_deg": (0.0, 0.01),
    "OBC_temp_C": (-10.0, 60.0),
    "CPU_load_pct": (0.0, 70.0),
    "Memory_usage_MB": (0.0, 500.0),
    "Watchdog_counter": (0.0, 1000.0),
    "SEU_counter": (0.0, 0.0),
    "Fault_register": (0.0, 0.0),
    "Safe_mode_entry_count": (0.0, 5.0),
    "Transponder_lock": (1.0, 1.0),
    "SNR_dB": (10.0, 40.0),
    "Component_temp_C": (-20.0, 65.0),
    "Heater_enable_flag": (0.0, 1.0),
}


def legacy_detector() -> ZScoreAnomalyDetector:
    """The pre-Phase-2 detector, configured exactly as agent.py configured it."""
    d = ZScoreAnomalyDetector(z_threshold=3.0, window_size=10)
    d.fit_from_nominal_ranges(LEGACY_NOMINAL_RANGES)
    return d


class TestLegacyTableIsStillTheSameNumbers(unittest.TestCase):
    """The frozen historical table must still match the live derived one.

    Phase 5 derives SATELLITE_NOMINAL_RANGES from the channel dictionary. This
    pins the 21 original channels so a change to a hard limit cannot slip through
    unnoticed just because the reproduction above uses a frozen copy.
    """

    def test_every_historical_channel_still_has_the_same_limits(self):
        for channel, want in LEGACY_NOMINAL_RANGES.items():
            with self.subTest(channel=channel):
                self.assertIn(channel, SATELLITE_NOMINAL_RANGES)
                self.assertEqual(SATELLITE_NOMINAL_RANGES[channel], want)

    def test_the_live_table_may_only_have_grown(self):
        self.assertGreaterEqual(len(SATELLITE_NOMINAL_RANGES),
                                len(LEGACY_NOMINAL_RANGES))


def flagged_channels(report: AnomalyReport) -> set[str]:
    return {a.channel for a in report.anomalies}


# ═══════════════════════════════════════════════════════════════════════════
# 1. KNOWN DETECTOR FAILURES FROM THE AUDIT
# ═══════════════════════════════════════════════════════════════════════════

class TestAuditBlindSpots(unittest.TestCase):
    """The five channels the original detector could not flag.

    Audit findings, reproduced by execution:

        channel               range      value   old z-score  old verdict
        SEU_counter           (0, 0)     999     0.0          not flagged
        Transponder_lock      (1, 1)     0       0.0          not flagged
        Star_tracker_status   (0, 0)     1       0.0          not flagged
        Fault_register        (0, 0)     8       0.0          not flagged
        Watchdog_counter      (0, 1000)  1002    2.85         not flagged

    The first four are degenerate ranges: sigma = (hi-lo)/6 = 0, so the old code
    returned z = 0.0 unconditionally. The fifth is a wide range: sigma = 166.7,
    so a real overflow scored below the 3.0 threshold.
    """

    CASES = [
        # (channel, value, expected_detectors)
        ("SEU_counter", 999.0, {DetectorName.COUNTER}),
        ("Transponder_lock", 0.0, {DetectorName.DISCRETE_STATE}),
        ("Star_tracker_status", 1.0, {DetectorName.DISCRETE_STATE}),
        ("Fault_register", 8.0, {DetectorName.DISCRETE_STATE}),
        ("Watchdog_counter", 1002.0, {DetectorName.HARD_LIMIT}),
    ]

    def test_all_five_blind_spots_are_declared(self):
        self.assertEqual(
            {c for c, _, _ in self.CASES}, set(KNOWN_BLIND_SPOT_CHANNELS),
        )

    def test_old_detector_really_did_miss_them(self):
        """Prove the bug, so the fix below is not asserted in a vacuum."""
        old = legacy_detector()
        for channel, value, _ in self.CASES:
            with self.subTest(channel=channel, detector="legacy"):
                rep = old.detect([reading(channel, value)])
                self.assertEqual(
                    rep["anomaly_count"], 0,
                    msg=f"{channel}={value} was expected to be MISSED by the "
                        f"pre-Phase-2 detector; if this now passes the premise "
                        f"of the Phase 2 fix has changed",
                )

    def test_old_detector_zscore_was_structurally_useless(self):
        """Degenerate ranges gave z=0.0; the wide range gave z below threshold."""
        old = legacy_detector()
        degenerate = ["SEU_counter", "Transponder_lock", "Star_tracker_status",
                      "Fault_register"]
        for channel in degenerate:
            with self.subTest(channel=channel):
                z = old.compute_z_score(channel, 999.0)
                self.assertEqual(
                    z, 0.0,
                    msg=f"{channel} has a degenerate range so sigma is 0 and any "
                        f"value scores z=0.0",
                )
        z = old.compute_z_score("Watchdog_counter", 1002.0)
        self.assertIsNotNone(z)
        self.assertLess(
            abs(z), 3.0,
            msg="a Watchdog_counter overflow scored below the 3.0 threshold",
        )

    def test_new_pipeline_catches_every_blind_spot(self):
        for channel, value, expected in self.CASES:
            with self.subTest(channel=channel):
                rep = run_detection([reading(channel, value)])
                self.assertGreaterEqual(
                    rep.anomaly_count, 1,
                    msg=f"{channel}={value} must now be flagged",
                )
                self.assertIn(channel, flagged_channels(rep))
                detectors = {a.detector for a in rep.anomalies}
                self.assertTrue(
                    expected & detectors,
                    msg=f"{channel} should be caught by {expected}, got {detectors}",
                )

    def test_blind_spot_findings_are_deterministic_not_statistical(self):
        """These are comparisons, so they carry no fabricated sigma."""
        for channel, value, _ in self.CASES:
            with self.subTest(channel=channel):
                rep = run_detection([reading(channel, value)])
                for a in rep.anomalies:
                    self.assertTrue(a.provenance.deterministic)
                    self.assertNotEqual(
                        a.provenance.baseline_source, BaselineSource.RANGE_DERIVED,
                        msg="a limits finding must not rest on a derived sigma",
                    )

    def test_watchdog_overflow_is_caught_by_comparison_not_threshold(self):
        """Even 1 count over the limit is caught — no threshold to slip under."""
        for value in (1000.5, 1001.0, 1002.0, 5000.0):
            with self.subTest(value=value):
                rep = run_detection([reading("Watchdog_counter", value)])
                limit_hits = rep.by_detector(DetectorName.HARD_LIMIT)
                self.assertEqual(len(limit_hits), 1)
                self.assertEqual(limit_hits[0].threshold, 1000.0)

    def test_seu_counter_single_event_is_caught(self):
        """A single SEU matters; magnitude is not the signal."""
        rep = run_detection([reading("SEU_counter", 1.0)])
        hits = rep.by_detector(DetectorName.COUNTER)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].evidence["expected_value"], 0.0)
        self.assertEqual(hits[0].evidence["increment_above_expected"], 1.0)

    def test_statistical_detection_is_skipped_for_these_channels(self):
        """A Gaussian test on a bitmask is meaningless; it is not attempted."""
        disabled = channel_dictionary_status()["statistical_detection_disabled_for"]
        for channel in KNOWN_BLIND_SPOT_CHANNELS:
            with self.subTest(channel=channel):
                self.assertIn(channel, disabled)


class TestAuditFalseCleanReport(unittest.TestCase):
    """Audit finding: the schema fork produced a false 'no anomalies' report.

    ``pre_fault_telemetry_window`` entries in scenarios.py carry no
    nominal_min/nominal_max, while the legacy ``pre_fault_telemetry`` list does.
    Reading only the window field made scenario 6 — a transponder-loss case with
    five out-of-limit channels — report ZERO anomalies.
    """

    def _scenario(self, sid: int) -> dict:
        from app.api.scenarios import get_all_scenarios
        return {s["scenario_id"]: s for s in get_all_scenarios()}[sid]

    def test_window_only_no_longer_reports_nothing(self):
        """The original bug is now fixed twice over, so it cannot be reproduced.

        Phase 2's finding was that scenario 6's window entries carry no
        nominal_min/nominal_max, so reading the window alone produced ZERO
        findings on a transponder-loss case. Phase 2 fixed it by merging both
        schema fields (see the test below).

        Phase 5 fixed the underlying cause as well: the window's channels are now
        in the channel dictionary, so bounds no longer have to travel with the
        reading at all. Feeding the raw window to the detector now yields findings
        on its own.

        Asserting zero here would mean asserting the bug is still present.
        """
        s = self._scenario(6)
        window = s.get("pre_fault_telemetry_window") or []
        self.assertGreater(len(window), 0)
        self.assertGreater(
            run_detection(window).anomaly_count, 0,
            "the channel dictionary should supply bounds the window omits",
        )

    def test_window_entries_still_carry_no_bounds_of_their_own(self):
        """The data has not changed; only where the bounds come from has."""
        s = self._scenario(6)
        for row in s.get("pre_fault_telemetry_window") or []:
            with self.subTest(parameter=row.get("parameter")):
                self.assertIsNone(row.get("nominal_min"))
                self.assertIsNone(row.get("nominal_max"))

    def test_merged_extraction_recovers_the_findings(self):
        s = self._scenario(6)
        rep = run_detection(extract_readings(s))
        self.assertGreater(
            rep.anomaly_count, 0,
            msg="a transponder-loss scenario must not report zero anomalies",
        )

    def test_extraction_merges_both_schema_fields(self):
        s = self._scenario(6)
        merged = extract_readings(s)
        legacy = s.get("pre_fault_telemetry") or []
        window = s.get("pre_fault_telemetry_window") or []
        self.assertGreaterEqual(len(merged), max(len(legacy), len(window)))
        # Bounds from the legacy list must survive the merge.
        by_param = {r["parameter"]: r for r in merged}
        self.assertIn("Link_margin_dB", by_param)
        self.assertIsNotNone(by_param["Link_margin_dB"].get("nominal_min"))

    def test_no_shipped_scenario_reports_a_clean_fault(self):
        """Every shipped scenario describes a fault, so none may look clean."""
        from app.api.scenarios import get_all_scenarios
        for s in get_all_scenarios():
            with self.subTest(scenario_id=s["scenario_id"]):
                rep = run_detection_on_crash_dump(s)
                self.assertGreater(
                    rep.anomaly_count, 0,
                    msg=f"scenario {s['scenario_id']} ({s.get('fault_type')}) "
                        f"reports no anomalies",
                )

    def test_deduplication_prevents_double_counting(self):
        """A reading present in both fields is counted once."""
        row = reading("V_bat", 21.0)
        dump = {
            "pre_fault_telemetry": [dict(row)],
            "pre_fault_telemetry_window": [dict(row)],
        }
        self.assertEqual(len(extract_readings(dump)), 1)


class TestAuditRangeDerivedSigma(unittest.TestCase):
    """Audit finding: sigma was fabricated from engineering limits.

    ``sigma = (hi - lo) / 6`` presumes the nominal range is a 3-sigma band. It is
    not a statistic, and the old detector reported the result as a z-score with
    no indication of that.
    """

    def test_observed_baseline_is_preferred(self):
        rows = series("V_bat", [(-100, 30.0), (-80, 30.1), (-60, 29.9),
                                (-40, 30.05), (-20, 30.0), (0, 30.02)])
        provider = BaselineProvider(rows)
        stats = provider.get(spec_or_inferred("V_bat"))
        self.assertEqual(stats.source, BaselineSource.OBSERVED_WINDOW)
        self.assertEqual(stats.confidence, Confidence.MEDIUM)
        self.assertAlmostEqual(stats.mean, 30.0116666, places=4)

    def test_provided_statistics_win_over_the_window(self):
        rows = [reading("channel_41", 0.96, baseline_mean=0.8118,
                        baseline_std=0.004754, nominal_min=0.7975,
                        nominal_max=0.8261)]
        provider = BaselineProvider(rows)
        stats = provider.get(spec_or_inferred("channel_41"))
        self.assertEqual(stats.source, BaselineSource.OBSERVED_PROVIDED)
        self.assertEqual(stats.confidence, Confidence.HIGH)
        self.assertEqual(stats.mean, 0.8118)

    def test_range_derived_is_last_resort_and_marked_weak(self):
        rows = [reading("V_bat", 21.0)]  # one sample: no observed baseline
        provider = BaselineProvider(rows)
        stats = provider.get(spec_or_inferred("V_bat"))
        self.assertEqual(stats.source, BaselineSource.RANGE_DERIVED)
        self.assertEqual(stats.confidence, Confidence.LOW)

    def test_range_derived_can_be_refused_entirely(self):
        rows = [reading("V_bat", 21.0)]
        provider = BaselineProvider(rows, allow_range_derived=False)
        stats = provider.get(spec_or_inferred("V_bat"))
        self.assertEqual(stats.source, BaselineSource.NONE)
        self.assertFalse(stats.usable_for_zscore)

    def test_weak_findings_are_labelled_and_capped(self):
        rows = [reading("V_bat", 5.0)]  # far out, but only a derived sigma
        anomalies, warnings = detect_statistical(rows)
        stat = [a for a in anomalies if a.detector is DetectorName.ZSCORE]
        self.assertEqual(len(stat), 1)
        a = stat[0]
        self.assertEqual(a.provenance.baseline_source, BaselineSource.RANGE_DERIVED)
        self.assertEqual(a.provenance.confidence, Confidence.LOW)
        self.assertIn("WEAK EVIDENCE", a.provenance.notes)
        self.assertNotEqual(
            a.severity, Severity.CRITICAL,
            msg="a finding resting on a fabricated sigma must not read CRITICAL",
        )
        self.assertTrue(any("WEAK EVIDENCE" in w for w in warnings))

    def test_report_warns_when_no_observed_baseline_exists(self):
        rep = run_detection([reading("V_bat", 21.0)])
        self.assertTrue(
            any("engineering limits" in w for w in rep.warnings),
            msg=f"expected a weak-baseline warning, got {rep.warnings}",
        )

    def test_comparison_harness_makes_no_accuracy_claim(self):
        result = compare_against_range_derived([reading("V_bat", 21.0)])
        self.assertIn("note", result)
        self.assertIn("No accuracy claim", result["note"])
        self.assertIn("observed_baseline_only", result)
        self.assertIn("with_range_derived_fallback", result)


# ═══════════════════════════════════════════════════════════════════════════
# 2. REQUIRED SCENARIO COVERAGE
# ═══════════════════════════════════════════════════════════════════════════

class TestNormalTelemetry(unittest.TestCase):
    """Normal telemetry must produce no findings."""

    NORMAL = [
        ("V_bat", 30.5), ("SoC_pct", 85.0), ("I_sa", 8.0), ("V_bus", 28.0),
        ("OBC_temp_C", 25.0), ("CPU_load_pct", 40.0), ("Memory_usage_MB", 200.0),
        ("SEU_counter", 0.0), ("Transponder_lock", 1.0),
        ("Star_tracker_status", 0.0), ("Fault_register", 0.0),
        ("Watchdog_counter", 500.0), ("SNR_dB", 25.0),
        ("Component_temp_C", 20.0), ("Heater_enable_flag", 0.0),
        ("Attitude_error_deg", 0.005), ("Gyro_rate_degs", 3.0),
    ]

    def test_no_anomalies_on_nominal_values(self):
        rows = [reading(p, v) for p, v in self.NORMAL]
        rep = run_detection(rows)
        self.assertEqual(
            rep.anomaly_count, 0,
            msg=f"false positives: {[(a.channel, a.detector.value, a.description) for a in rep.anomalies]}",
        )
        self.assertEqual(rep.max_severity, Severity.INFO)
        self.assertIn("No anomalies detected", rep.summary)

    def test_values_exactly_on_the_limit_are_not_violations(self):
        """A limit is inclusive: at the limit is in-spec, past it is not."""
        for channel, value in (("V_bat", 28.0), ("V_bat", 33.6),
                               ("Watchdog_counter", 1000.0), ("SoC_pct", 20.0)):
            with self.subTest(channel=channel, value=value):
                hits = detect_limits([reading(channel, value)])
                self.assertEqual(
                    [h.detector for h in hits], [],
                    msg=f"{channel}={value} sits exactly on its limit",
                )

    def test_steady_series_produces_no_temporal_findings(self):
        rows = series("V_bat", [(-120, 30.0), (-90, 30.01), (-60, 29.99),
                                (-30, 30.0), (0, 30.02)])
        anomalies, _ = detect_temporal(rows)
        self.assertEqual(
            anomalies, [],
            msg=f"steady telemetry produced {[a.detector.value for a in anomalies]}",
        )

    def test_empty_window_is_handled(self):
        rep = run_detection([])
        self.assertEqual(rep.anomaly_count, 0)
        self.assertEqual(rep.total_readings, 0)


class TestLimitViolation(unittest.TestCase):

    def test_above_maximum(self):
        hits = detect_limits([reading("Component_temp_C", 95.0)])
        self.assertEqual(len(hits), 1)
        a = hits[0]
        self.assertEqual(a.detector, DetectorName.HARD_LIMIT)
        self.assertEqual(a.evidence["limit_exceeded"], "MAX")
        self.assertEqual(a.threshold, 65.0)
        self.assertEqual(a.evidence["direction"], "HIGH")
        self.assertAlmostEqual(a.evidence["exceeded_by"], 30.0)

    def test_below_minimum(self):
        hits = detect_limits([reading("V_bat", 21.8)])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].evidence["limit_exceeded"], "MIN")
        self.assertEqual(hits[0].threshold, 28.0)
        self.assertEqual(hits[0].evidence["direction"], "LOW")

    def test_severity_scales_with_overshoot(self):
        small = detect_limits([reading("SoC_pct", 19.0)])[0]      # 1% of an 80 span
        large = detect_limits([reading("SoC_pct", -50.0)])[0]     # 70/80 of the span
        self.assertEqual(small.severity, Severity.MEDIUM)
        self.assertEqual(large.severity, Severity.CRITICAL)

    def test_unknown_channel_uses_its_own_bounds(self):
        """ESA-ADB's anonymized channels must still be limit-checked."""
        hits = detect_limits([
            reading("channel_99", 0.96, nominal_min=0.79, nominal_max=0.83),
        ])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].channel, "channel_99")

    def test_bound_origin_is_reported_honestly(self):
        """A declared limit, a statistical bound and an unknown bound differ."""
        engineering = detect_limits([reading("V_bat", 21.0)])[0]
        self.assertEqual(engineering.evidence["bound_kind"], "engineering limit")

        statistical = detect_limits([
            reading("channel_41", 0.96, nominal_min=0.797, nominal_max=0.826,
                    baseline_mean=0.8118, baseline_std=0.004754),
        ])[0]
        self.assertIn("baseline", statistical.evidence["bound_kind"])

        unknown = detect_limits([
            reading("channel_77", 5.0, nominal_min=0.0, nominal_max=1.0),
        ])[0]
        self.assertIn("not stated", unknown.evidence["bound_kind"])

    def test_no_bounds_means_no_limit_finding(self):
        hits = detect_limits([{"parameter": "mystery", "value": 1e9}])
        self.assertEqual(hits, [])


class TestDiscreteStateViolation(unittest.TestCase):

    def test_flag_leaving_its_healthy_state(self):
        hits = detect_limits([reading("Transponder_lock", 0.0)])
        state = [h for h in hits if h.detector is DetectorName.DISCRETE_STATE]
        self.assertEqual(len(state), 1)
        self.assertEqual(state[0].severity, Severity.CRITICAL)
        self.assertEqual(state[0].evidence["expected_states"], [1.0])
        self.assertIsNone(
            state[0].score,
            msg="a categorical finding must not report a numeric magnitude",
        )

    def test_status_code_outside_expected_set(self):
        hits = detect_limits([reading("Star_tracker_status", 3.0)])
        state = [h for h in hits if h.detector is DetectorName.DISCRETE_STATE]
        self.assertEqual(len(state), 1)
        self.assertEqual(state[0].severity, Severity.HIGH)

    def test_bitmask_set_bits_are_decoded(self):
        hits = detect_limits([reading("Fault_register", 10.0)])
        state = [h for h in hits if h.detector is DetectorName.DISCRETE_STATE][0]
        self.assertEqual(state.evidence["set_bits"], [1, 3])
        self.assertEqual(state.evidence["hex"], "0xa")

    def test_flag_in_an_allowed_state_is_clean(self):
        hits = detect_limits([reading("Heater_enable_flag", 1.0)])
        self.assertEqual(hits, [])

    def test_counter_decrease_is_reported(self):
        rows = [
            reading("SEU_counter", 5.0, offset="T-60s"),
            reading("SEU_counter", 2.0, offset="T-30s"),
        ]
        hits = [h for h in detect_limits(rows)
                if h.detector is DetectorName.COUNTER
                and "decrease" in h.evidence]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].evidence["decrease"], 3.0)


class TestDataQuality(unittest.TestCase):

    def test_string_nan(self):
        hits = detect_limits([reading("Gyro_rate_degs", "NaN")])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].detector, DetectorName.DATA_QUALITY)
        self.assertEqual(hits[0].evidence["quality_issue"], "NAN")
        self.assertEqual(hits[0].severity, Severity.CRITICAL)

    def test_float_nan(self):
        hits = detect_limits([reading("Gyro_rate_degs", float("nan"))])
        self.assertEqual(hits[0].evidence["quality_issue"], "NAN")

    def test_infinity(self):
        for value in (float("inf"), float("-inf"), "Inf"):
            with self.subTest(value=value):
                hits = detect_limits([reading("V_bat", value)])
                self.assertEqual(hits[0].evidence["quality_issue"], "INF")

    def test_missing_and_empty(self):
        for value in (None, "", "   "):
            with self.subTest(value=repr(value)):
                hits = detect_limits([reading("V_bat", value)])
                self.assertEqual(hits[0].evidence["quality_issue"], "MISSING")

    def test_non_numeric(self):
        hits = detect_limits([reading("V_bat", "DEGRADED")])
        self.assertEqual(hits[0].evidence["quality_issue"], "NON_NUMERIC")

    def test_unusable_value_is_not_also_limit_checked(self):
        """A NaN cannot be compared to a limit; only one finding is produced."""
        hits = detect_limits([reading("V_bat", "NaN")])
        self.assertEqual(len(hits), 1)

    def test_numeric_strings_are_accepted(self):
        value, issue = classify_value("30.5")
        self.assertIsNone(issue)
        self.assertEqual(value, 30.5)

    def test_booleans_are_valid_flag_values(self):
        self.assertEqual(classify_value(True), (1.0, None))
        self.assertEqual(classify_value(False), (0.0, None))

    def test_nan_carries_no_fabricated_score(self):
        hits = detect_limits([reading("V_bat", "NaN")])
        self.assertIsNone(hits[0].score)
        self.assertIsNone(hits[0].threshold)


class TestPersistentVsTransient(unittest.TestCase):
    """The distinction the pre-Phase-2 detector could not express."""

    #: A stable baseline followed by either one excursion or a sustained one.
    BASE = [(-300, 30.0), (-280, 30.1), (-260, 29.9), (-240, 30.05),
            (-220, 30.0), (-200, 29.95), (-180, 30.02), (-160, 30.01)]

    def test_transient_spike_is_not_persistent(self):
        rows = series("V_bat", self.BASE + [(-140, 40.0), (-120, 30.0),
                                            (-100, 30.05), (-80, 29.98)])
        anomalies, _ = detect_temporal(rows)
        persistence = [a for a in anomalies if a.detector is DetectorName.PERSISTENCE]
        self.assertEqual(
            persistence, [],
            msg="a single-sample spike must not be reported as persistent",
        )

    def test_transient_is_still_detected_by_another_detector(self):
        """Not persistent does not mean invisible."""
        rows = series("V_bat", self.BASE + [(-140, 40.0), (-120, 30.0)])
        rep = run_detection(rows)
        self.assertGreater(rep.anomaly_count, 0)
        detectors = {a.detector for a in rep.anomalies}
        self.assertTrue(
            detectors & {DetectorName.ZSCORE, DetectorName.ROBUST_ZSCORE,
                         DetectorName.SUDDEN_CHANGE, DetectorName.HARD_LIMIT},
        )

    def test_sustained_excursion_is_persistent(self):
        rows = series("V_bat", self.BASE + [(-140, 24.0), (-120, 23.8),
                                            (-100, 23.5), (-80, 23.2)])
        anomalies, _ = detect_temporal(rows)
        persistence = [a for a in anomalies if a.detector is DetectorName.PERSISTENCE]
        self.assertEqual(len(persistence), 1)
        a = persistence[0]
        self.assertGreaterEqual(a.evidence["consecutive_samples"], 3)
        self.assertEqual(a.threshold, 3.0)
        self.assertIn(a.severity, (Severity.HIGH, Severity.CRITICAL, Severity.MEDIUM))

    def test_persistence_records_its_duration(self):
        rows = series("V_bat", self.BASE + [(-140, 24.0), (-120, 23.8),
                                            (-100, 23.5), (-80, 23.2)])
        anomalies, _ = detect_temporal(rows)
        a = [x for x in anomalies if x.detector is DetectorName.PERSISTENCE][0]
        self.assertEqual(a.evidence["duration_seconds"], 60.0)
        self.assertEqual(a.evidence["first_offset"], "T-140s")
        self.assertEqual(a.evidence["last_offset"], "T-80s")

    def test_persistence_requires_an_observed_baseline(self):
        """Two samples cannot establish what 'normal' is."""
        rows = series("V_bat", [(-60, 24.0), (-30, 23.5)])
        anomalies, _ = detect_temporal(rows)
        self.assertEqual(
            [a for a in anomalies if a.detector is DetectorName.PERSISTENCE], [],
        )


class TestSuddenChangeAndRate(unittest.TestCase):

    def test_sudden_step_is_detected(self):
        rows = series("V_bat", [(-300, 30.0), (-280, 30.1), (-260, 29.9),
                                (-240, 30.0), (-220, 30.05), (-200, 22.0)])
        anomalies, _ = detect_temporal(rows)
        steps = [a for a in anomalies if a.detector is DetectorName.SUDDEN_CHANGE]
        self.assertGreaterEqual(len(steps), 1)
        a = steps[0]
        self.assertLess(a.evidence["step"], 0)
        self.assertGreater(a.evidence["step_in_sigma"], a.threshold)

    def test_rate_of_change_uses_the_declared_rate_limit(self):
        # Component_temp_C has max_rate_per_s = 8.5; 40 degC in 1s is far above it.
        rows = series("Component_temp_C", [(-2, 20.0), (-1, 60.0)])
        anomalies, _ = detect_temporal(rows)
        rates = [a for a in anomalies if a.detector is DetectorName.RATE_OF_CHANGE]
        self.assertEqual(len(rates), 1)
        self.assertEqual(rates[0].threshold, 8.5)
        self.assertAlmostEqual(rates[0].evidence["rate_per_s"], 40.0)

    def test_slow_change_within_the_rate_limit_is_clean(self):
        rows = series("Component_temp_C", [(-100, 20.0), (-50, 25.0), (0, 30.0)])
        anomalies, _ = detect_temporal(rows)
        self.assertEqual(
            [a for a in anomalies if a.detector is DetectorName.RATE_OF_CHANGE], [],
        )

    def test_no_rate_limit_means_no_rate_finding(self):
        """No threshold is invented for a channel with no declared rate."""
        spec = spec_or_inferred("channel_41")
        self.assertIsNone(spec.max_rate_per_s)
        rows = series("channel_41", [(-1, 0.0), (0, 1000.0)])
        anomalies, _ = detect_temporal(rows)
        self.assertEqual(
            [a for a in anomalies if a.detector is DetectorName.RATE_OF_CHANGE], [],
        )

    def test_trend_detects_sustained_drift(self):
        rows = series("SoC_pct", [(-300, 85.0), (-250, 78.0), (-200, 70.0),
                                  (-150, 62.0), (-100, 55.0), (-50, 47.0),
                                  (0, 40.0)])
        anomalies, _ = detect_temporal(rows)
        trends = [a for a in anomalies if a.detector is DetectorName.TREND]
        self.assertEqual(len(trends), 1)
        a = trends[0]
        self.assertEqual(a.evidence["direction"], "LOW")
        self.assertEqual(a.evidence["monotonic_fraction"], 1.0)
        self.assertLess(a.evidence["slope_per_s"], 0)

    def test_noise_is_not_reported_as_a_trend(self):
        rows = series("V_bat", [(-300, 30.0), (-250, 30.1), (-200, 29.9),
                                (-150, 30.05), (-100, 29.95), (-50, 30.02),
                                (0, 29.98)])
        anomalies, _ = detect_temporal(rows)
        self.assertEqual(
            [a for a in anomalies if a.detector is DetectorName.TREND], [],
        )

    def test_time_offsets_are_parsed(self):
        self.assertEqual(parse_offset_seconds("T-120.5s"), -120.5)
        self.assertEqual(parse_offset_seconds("T+0.000s"), 0.0)
        self.assertEqual(parse_offset_seconds("T-0s"), 0.0)
        self.assertEqual(parse_offset_seconds("-60"), -60.0)

    def test_unparseable_offset_is_dropped_not_defaulted_to_zero(self):
        """Mapping every bad offset to 0 would collapse the window to one point."""
        self.assertIsNone(parse_offset_seconds("yesterday"))
        rows = [reading("V_bat", 30.0, offset="yesterday")]
        self.assertEqual(build_series(rows), {})

    def test_series_is_sorted_by_time(self):
        rows = series("V_bat", [(0, 30.0), (-200, 29.0), (-100, 28.0)])
        samples = build_series(rows)["V_bat"]
        self.assertEqual([s.seconds for s in samples], [-200.0, -100.0, 0.0])


# ═══════════════════════════════════════════════════════════════════════════
# 3. REPORT CONTRACT, FUSION, DETERMINISM
# ═══════════════════════════════════════════════════════════════════════════

class TestAnomalyReportContract(unittest.TestCase):

    REQUIRED_FIELDS = (
        "anomaly_id", "channel", "timestamp", "detector", "score",
        "threshold", "severity", "evidence", "provenance",
    )

    def setUp(self):
        self.report = run_detection([
            reading("V_bat", 21.0),
            reading("SEU_counter", 4.0),
            reading("Transponder_lock", 0.0),
            reading("Gyro_rate_degs", "NaN"),
        ])

    def test_every_anomaly_has_all_required_fields(self):
        self.assertGreater(len(self.report.anomalies), 0)
        for a in self.report.anomalies:
            payload = json.loads(a.model_dump_json())
            for field in self.REQUIRED_FIELDS:
                with self.subTest(anomaly=a.anomaly_id, field=field):
                    self.assertIn(field, payload)

    def test_anomaly_ids_are_unique_and_stable(self):
        ids = [a.anomaly_id for a in self.report.anomalies]
        self.assertEqual(len(ids), len(set(ids)))
        again = run_detection([
            reading("V_bat", 21.0),
            reading("SEU_counter", 4.0),
            reading("Transponder_lock", 0.0),
            reading("Gyro_rate_degs", "NaN"),
        ])
        self.assertEqual(ids, [a.anomaly_id for a in again.anomalies])

    def test_anomaly_ids_are_not_random(self):
        """A hash, not a UUID: two processes must agree on the id."""
        for a in self.report.anomalies:
            self.assertTrue(a.anomaly_id.startswith("AN-"))
            self.assertEqual(len(a.anomaly_id.split("-")), 3)

    def test_evidence_is_never_empty(self):
        for a in self.report.anomalies:
            with self.subTest(anomaly=a.anomaly_id):
                self.assertGreater(len(a.evidence), 0)

    def test_provenance_names_its_module(self):
        for a in self.report.anomalies:
            with self.subTest(anomaly=a.anomaly_id):
                self.assertTrue(
                    a.provenance.detector_module.startswith("app.detection."),
                )

    def test_report_counts_are_self_consistent(self):
        r = self.report
        self.assertEqual(r.anomaly_count, len(r.anomalies))
        self.assertEqual(r.anomalous_channels, len(r.channels))
        self.assertEqual(
            sum(c.anomaly_count for c in r.channels), r.anomaly_count,
        )

    def test_anomalies_are_ordered_by_severity(self):
        from app.detection.models import severity_rank
        ranks = [severity_rank(a.severity) for a in self.report.anomalies]
        self.assertEqual(ranks, sorted(ranks, reverse=True))

    def test_report_serialises_to_json(self):
        payload = json.loads(self.report.model_dump_json())
        self.assertEqual(payload["schema_version"], "2.0")
        for key in ("anomalies", "channels", "detectors_run", "summary",
                    "warnings", "max_severity"):
            self.assertIn(key, payload)

    def test_detector_accounting_includes_silent_detectors(self):
        """A detector that found nothing is information, not absence."""
        names = {d.detector for d in self.report.detectors_run}
        self.assertEqual(names, set(DetectorName))
        silent = [d for d in self.report.detectors_run if d.anomalies_found == 0]
        self.assertGreater(len(silent), 0)

    def test_legacy_shape_is_still_available(self):
        legacy = self.report.legacy_dict()
        for key in ("anomalous_parameters", "total_parameters_checked",
                    "anomaly_count", "top_anomaly", "summary"):
            self.assertIn(key, legacy)


class TestFusion(unittest.TestCase):

    def test_corroboration_requires_independent_detectors(self):
        """Z-score and robust z-score read the same baseline, so they are one."""
        rows = series("V_bat", [(-300, 30.0), (-280, 30.1), (-260, 29.9),
                                (-240, 30.0), (-220, 30.05), (-200, 30.02),
                                (-180, 21.0)])
        rep = run_detection(rows)
        finding = next(c for c in rep.channels if c.channel == "V_bat")
        detectors = set(finding.detectors)
        # A limit violation plus statistical evidence is genuine corroboration.
        self.assertIn(DetectorName.HARD_LIMIT, detectors)
        self.assertTrue(finding.corroborated)

    def test_statistical_family_alone_is_not_corroboration(self):
        rows = series("channel_41", [
            (-300, 0.81), (-280, 0.812), (-260, 0.809), (-240, 0.811),
            (-220, 0.810), (-200, 0.8105), (-180, 0.8098), (-160, 0.8112),
        ], baseline_mean=0.8109, baseline_std=0.0009)
        # Nudge one sample out statistically but keep it inside its bounds.
        rows.append(reading("channel_41", 0.820, offset="T-140s",
                            nominal_min=0.70, nominal_max=0.90,
                            baseline_mean=0.8109, baseline_std=0.0009))
        rep = run_detection(rows)
        finding = next((c for c in rep.channels if c.channel == "channel_41"), None)
        self.assertIsNotNone(finding)
        families = {d for d in finding.detectors}
        if families <= {DetectorName.ZSCORE, DetectorName.ROBUST_ZSCORE,
                        DetectorName.PERSISTENCE}:
            self.assertFalse(
                finding.corroborated,
                msg="the statistical detectors share a baseline and are not "
                    "independent evidence",
            )

    def test_corroboration_cannot_manufacture_critical(self):
        from app.detection.fusion import _raise_one_step
        self.assertEqual(_raise_one_step(Severity.LOW), Severity.MEDIUM)
        self.assertEqual(_raise_one_step(Severity.MEDIUM), Severity.HIGH)
        self.assertEqual(
            _raise_one_step(Severity.HIGH), Severity.HIGH,
            msg="corroboration alone must not escalate to CRITICAL",
        )

    def test_channel_first_seen_uses_parsed_time(self):
        rows = [
            reading("V_bat", 21.0, offset="T-300s"),
            reading("V_bat", 20.0, offset="T-60s"),
        ]
        rep = run_detection(rows)
        finding = next(c for c in rep.channels if c.channel == "V_bat")
        self.assertEqual(
            finding.first_seen, "T-300s",
            msg="offsets must sort by parsed time, not lexicographically",
        )

    def test_stages_can_be_disabled_for_ablation(self):
        rows = [reading("V_bat", 21.0), reading("SEU_counter", 4.0)]
        no_limits = run_detection(rows, enable_limits=False)
        self.assertEqual(no_limits.by_detector(DetectorName.HARD_LIMIT), [])
        self.assertEqual(no_limits.by_detector(DetectorName.COUNTER), [])
        no_stats = run_detection(rows, enable_statistical=False)
        self.assertEqual(no_stats.by_detector(DetectorName.ZSCORE), [])

    def test_malformed_input_does_not_raise(self):
        for bad in (
            [None], [{"no_parameter": 1}], [{"parameter": ""}],
            [{"parameter": "X"}], ["a string"], [[]],
        ):
            with self.subTest(input=bad):
                rep = run_detection(bad)
                self.assertIsInstance(rep, AnomalyReport)


class TestDeterminism(unittest.TestCase):
    """Phase 2 forbids random detector outputs."""

    ROWS = [
        reading("V_bat", 21.0, offset="T-300s"),
        reading("V_bat", 22.0, offset="T-200s"),
        reading("SEU_counter", 4.0, offset="T-100s"),
        reading("Transponder_lock", 0.0, offset="T-50s"),
        reading("Gyro_rate_degs", "NaN", offset="T-0s"),
    ]

    def test_repeated_runs_are_byte_identical(self):
        self.assertTrue(assert_deterministic(self.ROWS, runs=5))

    def test_no_detection_module_imports_random(self):
        import pathlib
        import re
        pkg = pathlib.Path(_BACKEND_ROOT) / "app" / "detection"
        for path in sorted(pkg.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            body = re.sub(r"^\s*#.*$", "", source, flags=re.MULTILINE)
            body = re.sub(r'"""[\s\S]*?"""', "", body)
            with self.subTest(module=path.name):
                self.assertNotIn("import random", body)
                self.assertNotIn("random.", body)
                self.assertNotIn("uuid", body)

    def test_reordering_readings_does_not_change_the_finding_set(self):
        forward = run_detection(self.ROWS)
        backward = run_detection(list(reversed(self.ROWS)))
        self.assertEqual(
            {a.anomaly_id for a in forward.anomalies},
            {a.anomaly_id for a in backward.anomalies},
        )


# ═══════════════════════════════════════════════════════════════════════════
# 4. REAL ESA DATA
# ═══════════════════════════════════════════════════════════════════════════

class TestRealEsaTelemetry(unittest.TestCase):
    """Phase 2 requires using the existing ESA baseline statistics."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(ESA_DIR, "esa_mission1_id_109_crash_dump.json")
        if not os.path.isfile(path):
            raise unittest.SkipTest("ESA id_109 crash dump not present")
        with open(path, "r", encoding="utf-8") as fh:
            cls.dump = json.load(fh)
        cls.report = run_detection_on_crash_dump(cls.dump)

    def test_channel_summaries_carry_observed_statistics(self):
        summaries = self.dump["channel_summaries"]
        self.assertGreater(len(summaries), 0)
        for row in summaries:
            self.assertIsNotNone(row.get("baseline_mean"))
            self.assertIsNotNone(row.get("baseline_std"))
            self.assertGreater(row.get("baseline_rows", 0), 0)

    def test_detection_uses_the_observed_baseline(self):
        statistical = [
            a for a in self.report.anomalies
            if a.detector in (DetectorName.ZSCORE, DetectorName.ROBUST_ZSCORE)
        ]
        self.assertGreater(len(statistical), 0)
        for a in statistical:
            with self.subTest(anomaly=a.anomaly_id):
                self.assertEqual(
                    a.provenance.baseline_source,
                    BaselineSource.OBSERVED_PROVIDED,
                    msg="real observed statistics are available and must be used",
                )
                self.assertEqual(a.provenance.confidence, Confidence.HIGH)

    def test_no_range_derived_baseline_is_used_when_observed_exists(self):
        weak = [
            a for a in self.report.anomalies
            if a.provenance.baseline_source is BaselineSource.RANGE_DERIVED
        ]
        self.assertEqual(weak, [])

    def test_the_labelled_anomaly_is_detected(self):
        """ESA-ADB labels id_109 as an anomaly; the pipeline must find something.

        No root-cause claim is made — ESA-ADB provides no root-cause label, and
        Phase 0 recorded that constraint. This asserts detection only.
        """
        self.assertGreater(self.report.anomaly_count, 0)
        self.assertGreaterEqual(self.report.anomalous_channels, 1)

    def test_channel_42_dropout_to_zero_is_caught(self):
        """channel_42 drops from ~0.785 to 0.0 — the clearest signal in the dump."""
        self.assertIn("channel_42", flagged_channels(self.report))

    def test_report_is_deterministic_on_real_data(self):
        self.assertTrue(
            assert_deterministic(extract_readings(self.dump), runs=3),
        )

    def test_compact_esa_file_also_detects(self):
        path = os.path.join(ESA_DIR, "esa_mission1_id_109_sentinel_only.json")
        with open(path, "r", encoding="utf-8") as fh:
            compact = json.load(fh)
        rep = run_detection_on_crash_dump(compact)
        self.assertGreater(rep.anomaly_count, 0)


# ═══════════════════════════════════════════════════════════════════════════
# 5. CHANNEL DICTIONARY AND BASELINE UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

class TestChannelDictionary(unittest.TestCase):

    def test_every_declared_range_has_a_spec(self):
        for name in SATELLITE_NOMINAL_RANGES:
            with self.subTest(channel=name):
                self.assertIn(name, CHANNEL_SPECS)

    def test_limits_come_from_the_existing_ranges(self):
        """The dictionary classifies existing data; it does not invent limits."""
        for name, spec in CHANNEL_SPECS.items():
            lo, hi = SATELLITE_NOMINAL_RANGES[name]
            with self.subTest(channel=name):
                self.assertEqual(spec.limit_min, lo)
                self.assertEqual(spec.limit_max, hi)

    def test_degenerate_ranges_are_classified_as_discrete_or_counter(self):
        for name, (lo, hi) in SATELLITE_NOMINAL_RANGES.items():
            if lo != hi:
                continue
            with self.subTest(channel=name):
                self.assertIn(
                    CHANNEL_SPECS[name].kind,
                    (ChannelKind.STATUS, ChannelKind.FLAG, ChannelKind.COUNTER),
                    msg=f"{name} has a degenerate range so it cannot be treated "
                        f"as a continuous Gaussian variable",
                )

    def test_discrete_channels_declare_their_expected_states(self):
        for name, spec in CHANNEL_SPECS.items():
            if spec.is_discrete:
                with self.subTest(channel=name):
                    self.assertGreater(len(spec.expected_states), 0)

    def test_unknown_channel_claims_no_subsystem(self):
        """Phase 5 changed the marker from None to the explicit "UNKNOWN".

        Phase 2 signalled "not attributable" with None. Phase 5 requires unknown
        channels to be marked UNKNOWN, because None is also what an unpopulated
        field looks like, and a consumer could not tell "we cannot attribute this
        channel" from "nobody filled this in". The guarantee under test is
        unchanged: no subsystem is inferred from a channel name.
        """
        spec = spec_or_inferred("channel_41")
        self.assertEqual(spec.subsystem, "UNKNOWN")
        self.assertFalse(spec.subsystem_is_known)
        self.assertEqual(spec.expected_states, ())
        self.assertEqual(spec.kind, ChannelKind.CONTINUOUS)
        self.assertIsNone(spec.max_rate_per_s)

    def test_no_subsystem_is_inferred_from_a_suggestive_channel_name(self):
        """A name that looks like a known channel must not borrow its subsystem."""
        for name in ("GYRO_SOMETHING_ELSE", "V_bat_backup", "channel_7",
                     "MY_BATTERY_VOLTAGE_2"):
            with self.subTest(channel=name):
                spec = spec_or_inferred(name)
                self.assertEqual(spec.subsystem, "UNKNOWN")


class TestBaselineUtilities(unittest.TestCase):

    def test_compute_stats_matches_manual_calculation(self):
        stats = compute_stats("X", [1.0, 2.0, 3.0, 4.0, 5.0],
                              BaselineSource.OBSERVED_WINDOW)
        self.assertEqual(stats.mean, 3.0)
        self.assertEqual(stats.median, 3.0)
        self.assertAlmostEqual(stats.std, 1.5811, places=3)
        self.assertEqual(stats.mad, 1.0)
        self.assertEqual(stats.sample_count, 5)

    def test_constant_series_yields_zero_sigma_and_abstains(self):
        stats = compute_stats("X", [7.0] * 10, BaselineSource.OBSERVED_WINDOW)
        self.assertEqual(stats.std, 0.0)
        self.assertFalse(stats.usable_for_zscore)
        self.assertIsNone(stats.zscore(99.0))

    def test_anomalous_samples_are_excluded_from_the_baseline(self):
        rows = [
            reading("V_bat", 30.0, offset="T-300s"),
            reading("V_bat", 30.1, offset="T-280s"),
            reading("V_bat", 29.9, offset="T-260s"),
            reading("V_bat", 30.0, offset="T-240s"),
            reading("V_bat", 30.05, offset="T-220s"),
            reading("V_bat", 5.0, offset="T-200s", anomalous=True),
            reading("V_bat", 6.0, offset="T-180s", status="CRITICAL"),
        ]
        provider = BaselineProvider(rows)
        stats = provider.get(spec_or_inferred("V_bat"))
        self.assertEqual(stats.sample_count, 5)
        self.assertAlmostEqual(stats.mean, 30.01, places=2)

    def test_median_mad_resists_a_contaminated_baseline(self):
        """The property that motivates preferring median/MAD on a window.

        Compares the two estimator pairs directly, since ``zscore()`` already
        selects median/MAD for a window-derived baseline.
        """
        contaminated = [30.0, 30.1, 29.9, 30.0, 30.05, 200.0]
        stats = compute_stats("V_bat", contaminated, BaselineSource.OBSERVED_WINDOW)

        raw = abs((24.0 - stats.mean) / stats.std)
        robust = abs(stats.robust_zscore(24.0))
        self.assertGreater(
            robust, raw,
            msg="the outlier inflates sigma and drags the mean, so the raw "
                "mean/std pair understates the deviation",
        )
        self.assertLess(raw, 1.0, msg="raw scoring barely registers the deviation")
        self.assertGreater(robust, 10.0, msg="robust scoring registers it clearly")

    def test_window_baseline_prefers_robust_statistics(self):
        stats = compute_stats("V_bat", [30.0] * 5 + [200.0],
                              BaselineSource.OBSERVED_WINDOW)
        self.assertTrue(stats.robust_scale_preferred)
        self.assertEqual(stats.scale_basis, "median_mad")
        self.assertEqual(stats.effective_center, stats.median)

    def test_external_baseline_uses_mean_and_std_directly(self):
        """An externally measured baseline is not contaminated, so use it as-is."""
        rows = [reading("channel_41", 0.96, baseline_mean=0.8118,
                        baseline_std=0.004754)]
        stats = BaselineProvider(rows).get(spec_or_inferred("channel_41"))
        self.assertFalse(stats.robust_scale_preferred)
        self.assertEqual(stats.scale_basis, "mean_std")
        self.assertEqual(stats.effective_center, 0.8118)
        self.assertEqual(stats.effective_scale, 0.004754)

    def test_provider_summary_reports_sources(self):
        rows = [reading("V_bat", 21.0)]
        provider = BaselineProvider(rows)
        provider.get(spec_or_inferred("V_bat"))
        summary = provider.summary()
        self.assertEqual(summary["resolved_channels"], 1)
        self.assertIn("RANGE_DERIVED", summary["channels_per_source"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

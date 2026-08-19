"""
SENTINEL — Phase 15: Evidence Pipeline Contract Tests

Covers the telemetry-window adequacy contract (app/estimation/window_adequacy),
the ESA channel mapping layer (app/ingest/esa_mapping), the resampled preset
scenarios, and the physics verdicts they now produce.

The ten required cases:
  1. adequate window          -> ADEQUATE_FOR_PHYSICS
  2. under-sampled window     -> UNDER_SAMPLED (UNDER_SAMPLED_FOR_PHYSICS)
  3. missing required channel -> MISSING_REQUIRED_CHANNELS
  4. invalid timestamps       -> INVALID_TIMESTAMPS
  5. ESA mapped channel       -> MAPPED confidence, provenance preserved
  6. ESA unmapped channel     -> UNMAPPED_CHANNEL, never guessed
  7. synthetic presets        -> every synthetic scenario is adequately sampled
  8. physics VALID            -> hypothesis corroborated by decided residuals
  9. physics INVALID          -> hypothesis contradicted by decided residuals
 10. physics UNCERTAIN        -> no applicable constraint decided

Nothing here weakens an existing assertion. Windows that cannot support a step
must be reported UNDER_SAMPLED_FOR_PHYSICS, never silently passed.
"""

from __future__ import annotations

import unittest

from app.api.scenarios import get_all_scenarios
from app.detection import run_detection_on_crash_dump
from app.diagnosis import generate_hypotheses
from app.estimation import (
    WindowAdequacyStatus,
    assess_window_adequacy,
    compute_residuals,
    estimate_states,
)
from app.validation.physics import PhysicsStatus, validate_hypotheses

UNDER_SAMPLED_PHRASE = "UNDER_SAMPLED_FOR_PHYSICS"


def _window(scenario_id: int) -> dict:
    return next(
        s for s in get_all_scenarios() if s["scenario_id"] == scenario_id)


def _dump_with(window: list[dict]) -> dict:
    return {"pre_fault_telemetry_window": window}


# ═══════════════════════════════════════════════════════════════════════════
# 1. ADEQUATE WINDOW
# ═══════════════════════════════════════════════════════════════════════════

class TestAdequateWindow(unittest.TestCase):
    def test_two_fresh_samples_of_a_modelled_channel_are_adequate(self):
        dump = _dump_with([
            {"timestamp": "T-300s", "parameter": "SoC_pct", "value": 85.0},
            {"timestamp": "T-0s", "parameter": "SoC_pct", "value": 84.5},
            {"timestamp": "T-0s", "parameter": "I_sa", "value": 8.4},
            {"timestamp": "T-0s", "parameter": "V_bus", "value": 30.0},
        ])
        report = assess_window_adequacy(dump)
        self.assertIs(
            report.status, WindowAdequacyStatus.ADEQUATE_FOR_PHYSICS)
        self.assertTrue(report.is_adequate)
        soc = next(c for c in report.channels if c.channel == "SoC_pct")
        self.assertTrue(soc.checkable)
        self.assertGreater(soc.last_step_dt_s, 0.0)
        self.assertEqual(
            report.summary,
            "Adequate for physics: 1 modelled channel(s) can be stepped "
            "(SoC_pct).",
        )

    def test_residuals_run_on_an_adequate_window(self):
        dump = _window(2)
        report = compute_residuals(dump)
        self.assertIs(
            report.window_adequacy.status,
            WindowAdequacyStatus.ADEQUATE_FOR_PHYSICS,
        )
        self.assertTrue(report.residuals, "an adequate window must produce "
                                          "residuals, not silence")


# ═══════════════════════════════════════════════════════════════════════════
# 2. UNDER-SAMPLED WINDOW
# ═══════════════════════════════════════════════════════════════════════════

class TestUnderSampledWindow(unittest.TestCase):
    def test_single_fresh_sample_is_under_sampled(self):
        dump = _dump_with([
            {"timestamp": "T-0s", "parameter": "SoC_pct", "value": 85.0},
        ])
        report = assess_window_adequacy(dump)
        self.assertIs(report.status, WindowAdequacyStatus.UNDER_SAMPLED)
        self.assertFalse(report.is_adequate)
        self.assertIn(UNDER_SAMPLED_PHRASE, report.summary)
        soc = next(c for c in report.channels if c.channel == "SoC_pct")
        self.assertFalse(soc.checkable)

    def test_residual_summary_reports_under_sampled_explicitly(self):
        dump = _dump_with([
            {"timestamp": "T-0s", "parameter": "SoC_pct", "value": 85.0},
        ])
        report = compute_residuals(dump)
        self.assertIn(UNDER_SAMPLED_PHRASE, report.summary)
        self.assertFalse(report.residuals)
        self.assertIsNone(report.physically_consistent)

    def test_no_fresh_samples_is_under_sampled_not_clean(self):
        dump = _dump_with([
            {"timestamp": "T-0s", "parameter": "SoC_pct", "value": "NaN"},
        ])
        report = assess_window_adequacy(dump)
        self.assertIs(report.status, WindowAdequacyStatus.UNDER_SAMPLED)
        self.assertIn(UNDER_SAMPLED_PHRASE, report.summary)


# ═══════════════════════════════════════════════════════════════════════════
# 3. MISSING REQUIRED CHANNEL
# ═══════════════════════════════════════════════════════════════════════════

class TestMissingRequiredChannels(unittest.TestCase):
    def test_no_modelled_channel_is_missing_required_channels(self):
        dump = _dump_with([
            {"timestamp": "T-120s", "parameter": "OBC_temp_C", "value": 24.5},
            {"timestamp": "T-0s", "parameter": "OBC_temp_C", "value": 24.6},
        ])
        report = assess_window_adequacy(dump)
        self.assertIs(
            report.status, WindowAdequacyStatus.MISSING_REQUIRED_CHANNELS)
        self.assertIn(UNDER_SAMPLED_PHRASE, report.summary)

    def test_esa_dump_without_modelled_channels_is_missing_required(self):
        report = assess_window_adequacy(_window(4))
        self.assertIs(
            report.status, WindowAdequacyStatus.MISSING_REQUIRED_CHANNELS)


# ═══════════════════════════════════════════════════════════════════════════
# 4. INVALID TIMESTAMPS
# ═══════════════════════════════════════════════════════════════════════════

class TestInvalidTimestamps(unittest.TestCase):
    def test_unparseable_offset_is_invalid_timestamps(self):
        dump = _dump_with([
            {"timestamp": "T-300s", "parameter": "SoC_pct", "value": 85.0},
            {"timestamp": "T-later", "parameter": "SoC_pct", "value": 84.0},
        ])
        report = assess_window_adequacy(dump)
        self.assertIs(report.status, WindowAdequacyStatus.INVALID_TIMESTAMPS)
        self.assertIn(UNDER_SAMPLED_PHRASE, report.summary)
        self.assertGreaterEqual(report.untimed_or_unparseable, 1)

    def test_residual_stage_never_claims_physics_on_invalid_timestamps(self):
        dump = _dump_with([
            {"timestamp": "T-300s", "parameter": "SoC_pct", "value": 85.0},
            {"timestamp": "T-later", "parameter": "SoC_pct", "value": 84.0},
        ])
        report = compute_residuals(dump)
        self.assertEqual(report.window_adequacy.status.value,
                         "INVALID_TIMESTAMPS")
        self.assertFalse(report.residuals)
        self.assertIn(UNDER_SAMPLED_PHRASE, report.summary)


# ═══════════════════════════════════════════════════════════════════════════
# 5. ESA MAPPED CHANNEL
# ═══════════════════════════════════════════════════════════════════════════

class TestEsaMappedChannel(unittest.TestCase):
    def test_curated_mapping_is_mapped_with_provenance(self):
        from app.ingest.esa_mapping import (
            CURATED_ESA_MAPPINGS,
            EsaChannelMapping,
            MappingConfidence,
            map_esa_channel,
        )

        entry = EsaChannelMapping(
            esa_channel="channel_7",
            canonical_channel="SoC_pct",
            subsystem_bucket="subsystem_3",
            physical_quantity="battery_state_of_charge",
            unit_bucket="physical_unit_4",
            provenance="ESA-ADB channels.csv via ingestion job 2026-07-01",
            confidence=MappingConfidence.MAPPED,
        )
        CURATED_ESA_MAPPINGS["channel_7"] = entry
        try:
            mapping = map_esa_channel("channel_7")
            self.assertIsNotNone(mapping)
            assert mapping is not None
            self.assertEqual(mapping.confidence, MappingConfidence.MAPPED)
            self.assertEqual(mapping.canonical_channel, "SoC_pct")
            self.assertEqual(
                mapping.provenance,
                "ESA-ADB channels.csv via ingestion job 2026-07-01",
            )
            self.assertEqual(mapping.as_dict()["status"], "MAPPED")
        finally:
            CURATED_ESA_MAPPINGS.pop("channel_7", None)

    def test_non_esa_channel_is_not_an_esa_mapping(self):
        from app.ingest.esa_mapping import map_esa_channel
        self.assertIsNone(map_esa_channel("V_bat"))
        self.assertIsNone(map_esa_channel(41))


# ═══════════════════════════════════════════════════════════════════════════
# 6. ESA UNMAPPED CHANNEL
# ═══════════════════════════════════════════════════════════════════════════

class TestEsaUnmappedChannel(unittest.TestCase):
    def test_anonymised_channel_is_unmapped_not_guessed(self):
        from app.ingest.esa_mapping import MappingConfidence, map_esa_channel
        mapping = map_esa_channel("channel_41")
        self.assertIsNotNone(mapping)
        assert mapping is not None
        self.assertEqual(mapping.confidence, MappingConfidence.UNMAPPED)
        self.assertIsNone(mapping.canonical_channel)
        self.assertIsNone(mapping.physical_quantity)
        self.assertEqual(mapping.as_dict()["status"], "UNMAPPED_CHANNEL")
        self.assertIn("anonymised", mapping.provenance)

    def test_shipped_esa_scenarios_carry_no_invented_mappings(self):
        from app.diagnosis import generate_hypotheses
        from app.detection import run_detection_on_crash_dump

        esa_ids = [4, 200, 201, 202, 203]
        for sid in esa_ids:
            with self.subTest(scenario_id=sid):
                s = _window(sid)
                det = run_detection_on_crash_dump(s)
                hyp = generate_hypotheses(det, s)
                self.assertTrue(hyp.esa_channel_mappings,
                                "ESA scenarios must record channel mappings")
                self.assertTrue(all(
                    m["confidence"] == "UNMAPPED"
                    for m in hyp.esa_channel_mappings))
                self.assertFalse(hyp.hypotheses,
                                 "an unmapped channel supports no hypothesis")


# ═══════════════════════════════════════════════════════════════════════════
# 7. SYNTHETIC PRESETS ARE ADEQUATELY SAMPLED
# ═══════════════════════════════════════════════════════════════════════════

class TestSyntheticPresetSampling(unittest.TestCase):
    def test_all_synthetic_presets_are_adequate_for_physics(self):
        for sid in (1, 2, 3, 5, 6):
            with self.subTest(scenario_id=sid):
                report = assess_window_adequacy(_window(sid))
                self.assertIs(
                    report.status,
                    WindowAdequacyStatus.ADEQUATE_FOR_PHYSICS,
                    msg=(
                        f"scenario {sid} must be resampled so its modelled "
                        f"channels can be stepped; was {report.status.value}"
                    ),
                )

    def test_each_synthetic_preset_decides_at_least_one_residual(self):
        for sid in (1, 2, 3, 5, 6):
            with self.subTest(scenario_id=sid):
                report = compute_residuals(_window(sid))
                self.assertTrue(
                    any(r.status.is_decided for r in report.residuals),
                    f"scenario {sid} must decide at least one residual",
                )

    def test_detection_still_finds_the_fault_in_every_preset(self):
        for sid in (1, 2, 3, 5, 6):
            with self.subTest(scenario_id=sid):
                det = run_detection_on_crash_dump(_window(sid))
                self.assertGreater(
                    det.anomaly_count, 0,
                    msg=f"scenario {sid} must still report its fault",
                )


# ═══════════════════════════════════════════════════════════════════════════
# 8. PHYSICS VALID
# ═══════════════════════════════════════════════════════════════════════════

class TestPhysicsValid(unittest.TestCase):
    def test_scenario_2_corroborates_the_energy_fault(self):
        s = _window(2)
        det = run_detection_on_crash_dump(s)
        hyp = generate_hypotheses(det, s)
        seq = estimate_states(s)
        res = compute_residuals(s, seq)
        phys = validate_hypotheses(hyp, res, seq)
        self.assertIn("EPS_SOLAR_UNDERVOLT", phys.validated)
        self.assertEqual(
            phys.verdict_for_fault("EPS_SOLAR_UNDERVOLT").validation_status,
            PhysicsStatus.VALID,
        )
        self.assertTrue(phys.window_adequacy["adequate_for_physics"])

    def test_scenario_5_corroborates_the_thermal_fault(self):
        s = _window(5)
        det = run_detection_on_crash_dump(s)
        hyp = generate_hypotheses(det, s)
        seq = estimate_states(s)
        res = compute_residuals(s, seq)
        phys = validate_hypotheses(hyp, res, seq)
        self.assertIn("TCS_THERMAL_RUNAWAY", phys.validated)


# ═══════════════════════════════════════════════════════════════════════════
# 9. PHYSICS INVALID
# ═══════════════════════════════════════════════════════════════════════════

class TestPhysicsInvalid(unittest.TestCase):
    def test_scenario_3_contradicts_the_energy_fault(self):
        s = _window(3)
        det = run_detection_on_crash_dump(s)
        hyp = generate_hypotheses(det, s)
        seq = estimate_states(s)
        res = compute_residuals(s, seq)
        phys = validate_hypotheses(hyp, res, seq)
        self.assertIn("EPS_SOLAR_UNDERVOLT", phys.invalidated)
        self.assertEqual(
            phys.verdict_for_fault("EPS_SOLAR_UNDERVOLT").validation_status,
            PhysicsStatus.INVALID,
        )

    def test_scenario_6_contradicts_the_energy_fault(self):
        s = _window(6)
        det = run_detection_on_crash_dump(s)
        hyp = generate_hypotheses(det, s)
        seq = estimate_states(s)
        res = compute_residuals(s, seq)
        phys = validate_hypotheses(hyp, res, seq)
        self.assertIn("EPS_SOLAR_UNDERVOLT", phys.invalidated)


# ═══════════════════════════════════════════════════════════════════════════
# 10. PHYSICS UNCERTAIN
# ═══════════════════════════════════════════════════════════════════════════

class TestPhysicsUncertain(unittest.TestCase):
    def test_uncertain_is_not_a_pass(self):
        s = _window(6)
        det = run_detection_on_crash_dump(s)
        hyp = generate_hypotheses(det, s)
        seq = estimate_states(s)
        res = compute_residuals(s, seq)
        phys = validate_hypotheses(hyp, res, seq)
        self.assertIn("COMMS_TRANSPONDER_LOSS", phys.uncertain)
        self.assertNotIn("COMMS_TRANSPONDER_LOSS", phys.validated)
        self.assertNotIn("COMMS_TRANSPONDER_LOSS", phys.invalidated)

    def test_esa_scenarios_reach_no_physics_verdict_at_all(self):
        s = _window(4)
        det = run_detection_on_crash_dump(s)
        hyp = generate_hypotheses(det, s)
        seq = estimate_states(s)
        res = compute_residuals(s, seq)
        phys = validate_hypotheses(hyp, res, seq)
        self.assertFalse(phys.verdicts)
        self.assertEqual(
            res.window_adequacy.status,
            WindowAdequacyStatus.MISSING_REQUIRED_CHANNELS,
        )
        self.assertIn("UNDER_SAMPLED_FOR_PHYSICS", res.summary)


if __name__ == "__main__":
    unittest.main()
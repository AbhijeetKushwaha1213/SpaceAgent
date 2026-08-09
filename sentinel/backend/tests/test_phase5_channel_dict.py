"""
SENTINEL — Phase 5 channel dictionary tests (test_phase5_channel_dict.py)

Run:
    python3 -m unittest tests.test_phase5_channel_dict -v

Grouped by the guarantee under test:

  1. COMPLETENESS      every required field, and every channel the spec names
  2. SINGLE AUTHORITY  no consumer declares its own thresholds any more
  3. NO DRIFT          the dictionary reproduces both source tables exactly
  4. UNKNOWN CHANNELS  explicitly UNKNOWN, with no subsystem ever inferred
  5. PROMPT            carries no authoritative engineering threshold
  6. VALIDATION        the dictionary checks itself, and the five inherited
                       conflicts stay visible
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.agent.prompts import (                                     # noqa: E402
    CHANNEL_SEMANTICS,
    NOMINAL_THRESHOLDS,
    SAFETY_RULES,
    SYSTEM_PROMPT,
    build_channel_semantics_section,
)
from app.analytics.anomaly_detector import (                        # noqa: E402
    SATELLITE_NOMINAL_RANGES,
)
from app.api.scenarios import get_all_scenarios                     # noqa: E402
from app.detection.channels import (                                # noqa: E402
    CHANNEL_SPECS,
    UNKNOWN_SUBSYSTEM,
    BoundOrigin,
    ChannelKind,
    get_channel_spec,
    spec_or_inferred,
)
from app.ingest.channel_dict import (                               # noqa: E402
    CHANNEL_DICT_VERSION,
    CHANNELS,
    KNOWN_NOMINAL_LIMIT_CONFLICTS,
    Criticality,
    DataType,
    Provenance,
    SamplingRate,
    Subsystem,
    ValueClass,
    all_channels,
    channels_for_subsystem,
    dictionary_status,
    get_channel,
    hard_limits,
    is_known_channel,
    nominal_range,
    resolve_channel,
    resolve_subsystem,
    safety_ceiling,
    safety_floor,
    subsystem_of,
    validate_dictionary,
)
from app.validation.conditions import (                             # noqa: E402
    BATTERY_FLOOR_SOC,
    THERMAL_SURVIVAL_LIMIT,
)
from simulation.fault_simulator import (                            # noqa: E402
    SIMULATED_CHANNELS,
    SatelliteFaultSimulator,
)

# The detector table exactly as it read before Phase 5. The dictionary must
# reproduce it, otherwise the migration changed what counts as a violation.
PRE_PHASE5_DETECTOR_TABLE: dict[str, tuple[float, float]] = {
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

# The simulator's per-attribute table exactly as it read before Phase 5.
PRE_PHASE5_SIMULATOR_TABLE: dict[str, tuple[float, float]] = {
    "V_bat": (28.0, 33.0),
    "SoC_pct": (60.0, 95.0),
    "I_sa": (3.5, 6.5),
    "V_bus": (27.5, 32.5),
    "Heater_power_W": (0.0, 10.0),
    "RW_speed_rpm": (-5000.0, 5000.0),
    "Gyro_rate_degs": (-0.5, 0.5),
    "Star_tracker_status": (0.0, 0.0),
    "Sun_sensor_angle_deg": (0.0, 90.0),
    "Attitude_error_deg": (0.0, 1.0),
    "OBC_temp_C": (10.0, 50.0),
    "CPU_load_pct": (10.0, 70.0),
    "Memory_usage_MB": (50.0, 200.0),
    "Watchdog_counter": (0.0, 200.0),
    "SEU_counter": (0.0, 5.0),
    "Fault_register": (0.0, 0.0),
    "Safe_mode_entry_count": (0.0, 3.0),
    "Transponder_lock": (1.0, 1.0),
    "SNR_dB": (10.0, 25.0),
    "Component_temp_C": (-10.0, 70.0),
    "Heater_enable_flag": (0.0, 0.0),
}

#: The channels the Phase 5 specification requires, by subsystem.
SPEC_REQUIRED: dict[str, dict[str, str]] = {
    "EPS": {
        "battery voltage": "V_bat",
        "battery current": "V_bus",
        "solar array current": "I_sa",
    },
    "AOCS": {
        "angular velocity": "Gyro_rate_degs",
        "attitude error": "Attitude_error_deg",
        "reaction wheel speed": "RW_speed_rpm",
    },
    "TCS": {"temperature": "Component_temp_C"},
    "OBC": {
        "CPU load": "CPU_load_pct",
        "memory": "Memory_usage_MB",
        "watchdog counter": "Watchdog_counter",
        "fault register": "Fault_register",
    },
    "COMMS": {
        "transponder lock": "Transponder_lock",
        # The spec's "link status" is the link-up flag. SNR_dB is link quality,
        # a separate quantity, and is also present.
        "link status": "Link_status",
    },
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# 1 — COMPLETENESS
# ═══════════════════════════════════════════════════════════════════════════

class TestRequiredFields(unittest.TestCase):
    """Each of the 13 fields the specification names, on every channel."""

    def test_dictionary_is_not_empty(self):
        self.assertGreaterEqual(len(CHANNELS), 21)
        self.assertRegex(CHANNEL_DICT_VERSION, r"^\d+\.\d+\.\d+$")

    def test_channel_id(self):
        for definition in all_channels():
            with self.subTest(channel=definition.channel_id):
                self.assertTrue(definition.channel_id.strip())
                self.assertEqual(definition.channel_id,
                                 CHANNELS[definition.channel_id].channel_id)

    def test_display_name(self):
        for definition in all_channels():
            with self.subTest(channel=definition.channel_id):
                self.assertTrue(definition.display_name.strip())
                self.assertNotEqual(definition.display_name,
                                    definition.channel_id,
                                    "display_name should be human-readable")

    def test_subsystem(self):
        for definition in all_channels():
            with self.subTest(channel=definition.channel_id):
                self.assertIsInstance(definition.subsystem, Subsystem)
                self.assertTrue(definition.subsystem.is_known,
                                "a declared channel must have a subsystem")

    def test_unit(self):
        for definition in all_channels():
            with self.subTest(channel=definition.channel_id):
                self.assertTrue(definition.unit and definition.unit.strip())

    def test_datatype(self):
        for definition in all_channels():
            with self.subTest(channel=definition.channel_id):
                self.assertIsInstance(definition.datatype, DataType)

    def test_nominal_range(self):
        for definition in all_channels():
            with self.subTest(channel=definition.channel_id):
                self.assertEqual(len(definition.nominal_range), 2)
                low, high = definition.nominal_range
                if low is not None and high is not None:
                    self.assertLessEqual(low, high)

    def test_hard_limits(self):
        for definition in all_channels():
            with self.subTest(channel=definition.channel_id):
                self.assertEqual(len(definition.hard_limits), 2)
                low, high = definition.hard_limits
                self.assertIsNotNone(low)
                self.assertIsNotNone(high)
                self.assertLessEqual(low, high)

    def test_criticality(self):
        for definition in all_channels():
            with self.subTest(channel=definition.channel_id):
                self.assertIsInstance(definition.criticality, Criticality)

    def test_discrete_or_continuous(self):
        for definition in all_channels():
            with self.subTest(channel=definition.channel_id):
                self.assertIsInstance(definition.value_class, ValueClass)
                self.assertEqual(definition.is_discrete,
                                 definition.value_class.is_discrete)

    def test_expected_sampling_rate(self):
        for definition in all_channels():
            with self.subTest(channel=definition.channel_id):
                self.assertIsInstance(definition.sampling_rate, SamplingRate)
                self.assertIsNot(
                    definition.sampling_rate, SamplingRate.UNKNOWN,
                    "a declared channel should state a cadence class",
                )

    def test_description(self):
        for definition in all_channels():
            with self.subTest(channel=definition.channel_id):
                self.assertGreater(len(definition.description.strip()), 15)

    def test_provenance(self):
        for definition in all_channels():
            with self.subTest(channel=definition.channel_id):
                self.assertIsInstance(definition.provenance, Provenance)
                self.assertIsInstance(definition.limits_provenance, Provenance)
                self.assertIsNot(
                    definition.limits_provenance, Provenance.UNKNOWN,
                    "a declared limit must say where it came from",
                )

    def test_physical_meaning(self):
        for definition in all_channels():
            with self.subTest(channel=definition.channel_id):
                self.assertGreater(len(definition.physical_meaning.strip()), 30)
                self.assertNotEqual(definition.physical_meaning,
                                    definition.description)


class TestSpecifiedChannelsPresent(unittest.TestCase):
    """Every channel the Phase 5 specification names, under the right subsystem."""

    def test_all_named_channels_exist(self):
        for subsystem, items in SPEC_REQUIRED.items():
            for label, channel_id in items.items():
                with self.subTest(subsystem=subsystem, channel=label):
                    definition = get_channel(channel_id)
                    self.assertIsNotNone(definition, f"{channel_id} missing")
                    self.assertEqual(definition.subsystem.value, subsystem)

    def test_every_required_subsystem_is_populated(self):
        for subsystem in ("EPS", "AOCS", "TCS", "OBC", "COMMS"):
            with self.subTest(subsystem=subsystem):
                self.assertGreater(len(channels_for_subsystem(subsystem)), 0)

    def test_aocs_and_adcs_name_the_same_subsystem(self):
        """The rest of the repository says ADCS; the specification says AOCS."""
        self.assertIs(resolve_subsystem("ADCS"), Subsystem.AOCS)
        self.assertIs(resolve_subsystem("AOCS"), Subsystem.AOCS)
        self.assertIs(resolve_subsystem("adcs"), Subsystem.AOCS)
        self.assertEqual(channels_for_subsystem("ADCS"),
                         channels_for_subsystem("AOCS"))

    def test_every_channel_in_a_shipped_scenario_is_known_or_anonymized(self):
        """A channel the repository ships should be attributable.

        The only permitted exceptions are ESA-ADB's anonymized channel_NN names,
        which must NOT be given a subsystem.
        """
        unattributed: set[str] = set()
        for scenario in get_all_scenarios():
            for field in ("pre_fault_telemetry", "pre_fault_telemetry_window"):
                for row in scenario.get(field) or []:
                    if not isinstance(row, dict):
                        continue
                    name = row.get("parameter")
                    if name and not is_known_channel(name):
                        unattributed.add(str(name))

        anonymized = {n for n in unattributed if re.fullmatch(r"channel_\d+", n)}
        self.assertEqual(
            unattributed - anonymized, set(),
            "these shipped channels are not in the dictionary",
        )
        self.assertTrue(anonymized, "expected the ESA-ADB anonymized channels")


# ═══════════════════════════════════════════════════════════════════════════
# 2 — SINGLE AUTHORITY
# ═══════════════════════════════════════════════════════════════════════════

class TestNoConsumerDeclaresThresholds(unittest.TestCase):
    """Requirement: remove duplicated thresholds from the four named consumers."""

    def test_anomaly_detector_derives_its_table(self):
        source = _read(_BACKEND / "app" / "analytics" / "anomaly_detector.py")
        self.assertIn("_hard_limits_by_channel", source)
        # The literal table is gone: no channel-name-to-tuple lines remain.
        literal_rows = re.findall(
            r'^\s*"(?:V_bat|SoC_pct|Watchdog_counter|SEU_counter)":\s*\(',
            source, re.MULTILINE,
        )
        self.assertEqual(literal_rows, [],
                         "anomaly_detector.py still declares literal ranges")

    def test_fault_simulator_derives_its_ranges(self):
        source = _read(_BACKEND / "simulation" / "fault_simulator.py")
        self.assertIn("_dictionary_ranges", source)
        for banned in ('"nominal_min": 28.0', '"nominal_min": 60.0',
                       '"nominal_max": 33.0', '"nominal_max": 6.5'):
            with self.subTest(literal=banned):
                self.assertNotIn(banned, source)

    def test_detection_channels_declares_nothing(self):
        source = _read(_BACKEND / "app" / "detection" / "channels.py")
        self.assertIn("from app.ingest.channel_dict import", source)
        self.assertNotIn("SATELLITE_NOMINAL_RANGES", source)
        # No local spec-builder helpers remain. Anchored on a line start so it
        # does not match channel_dictionary_status() or dictionary_status().
        self.assertEqual(
            re.findall(r"^def _(?:continuous|counter|status|flag)\(",
                       source, re.MULTILINE),
            [],
            "detection/channels.py still builds specs from local literals",
        )

    def test_scenarios_do_not_repeat_known_channel_bounds(self):
        """Only anonymized channels may still carry literal bounds."""
        source = _read(_BACKEND / "app" / "api" / "scenarios.py")
        rows = re.findall(
            r'\{"parameter": "([A-Za-z0-9_]+)",[^}]*?"nominal_min"',
            source, re.DOTALL,
        )
        offenders = [name for name in rows if is_known_channel(name)]
        self.assertEqual(
            offenders, [],
            f"scenarios.py repeats bounds for known channels: {offenders}",
        )

    def test_validator_thresholds_come_from_the_dictionary(self):
        self.assertEqual(BATTERY_FLOOR_SOC, safety_floor("SoC_pct"))
        self.assertEqual(THERMAL_SURVIVAL_LIMIT,
                         safety_ceiling("Component_temp_C"))
        source = _read(_BACKEND / "app" / "validation" / "conditions.py")
        self.assertIn("_policy_threshold", source)
        self.assertNotIn("BATTERY_FLOOR_SOC: float = 15.0", source)
        self.assertNotIn("THERMAL_SURVIVAL_LIMIT: float = 85.0", source)

    def test_validator_threshold_values_are_unchanged(self):
        """The source moved; the numbers did not."""
        self.assertEqual(BATTERY_FLOOR_SOC, 15.0)
        self.assertEqual(THERMAL_SURVIVAL_LIMIT, 85.0)

    def test_safety_thresholds_are_distinct_from_hard_limits(self):
        """They are different quantities and must not be conflated."""
        self.assertNotEqual(safety_floor("SoC_pct"),
                            hard_limits("SoC_pct")[0])
        self.assertNotEqual(safety_ceiling("Component_temp_C"),
                            hard_limits("Component_temp_C")[1])

    def test_safety_threshold_lookup_refuses_an_unknown_channel(self):
        """Answering "no floor" for a typo would turn it into a permitted command."""
        for name in ("SoC_pcnt", "not_a_channel", ""):
            with self.subTest(channel=name):
                with self.assertRaises(KeyError):
                    safety_floor(name)
                with self.assertRaises(KeyError):
                    safety_ceiling(name)


# ═══════════════════════════════════════════════════════════════════════════
# 3 — NO DRIFT
# ═══════════════════════════════════════════════════════════════════════════

class TestReproducesBothSourceTables(unittest.TestCase):
    """The consolidation must not have changed any behaviour."""

    def test_hard_limits_match_the_pre_phase5_detector_table(self):
        for name, want in PRE_PHASE5_DETECTOR_TABLE.items():
            with self.subTest(channel=name):
                definition = get_channel(name)
                self.assertIsNotNone(definition)
                self.assertEqual(definition.hard_limits, want)

    def test_derived_detector_view_matches_the_old_literal(self):
        for name, want in PRE_PHASE5_DETECTOR_TABLE.items():
            with self.subTest(channel=name):
                self.assertEqual(SATELLITE_NOMINAL_RANGES[name], want)

    def test_nominal_ranges_match_the_pre_phase5_simulator_table(self):
        for name, want in PRE_PHASE5_SIMULATOR_TABLE.items():
            with self.subTest(channel=name):
                definition = get_channel(name)
                self.assertIsNotNone(definition)
                self.assertEqual(definition.nominal_range, want)

    def test_simulator_generates_the_same_bounds_it_always_did(self):
        sim = SatelliteFaultSimulator(seed=42)
        for name, want in PRE_PHASE5_SIMULATOR_TABLE.items():
            with self.subTest(channel=name):
                entry = sim._param_ranges[name]
                self.assertEqual(
                    (entry["nominal_min"], entry["nominal_max"]), want,
                )

    def test_simulator_channel_list_is_complete(self):
        self.assertEqual(set(SIMULATED_CHANNELS),
                         set(PRE_PHASE5_SIMULATOR_TABLE))

    def test_simulator_instances_do_not_share_range_dicts(self):
        """A fault generator mutates a range in place to stage a fault."""
        first = SatelliteFaultSimulator(seed=1)
        second = SatelliteFaultSimulator(seed=1)
        first.V_bat["nominal_min"] = -999.0
        self.assertEqual(second.V_bat["nominal_min"], 28.0)
        self.assertEqual(get_channel("V_bat").nominal_range[0], 28.0,
                         "the dictionary itself must not be mutated")

    def test_simulator_output_is_deterministic(self):
        for fault in ("EPS_SOLAR_UNDERVOLT", "ADCS_GYRO_SEU",
                      "TCS_THERMAL_RUNAWAY"):
            with self.subTest(fault=fault):
                a = SatelliteFaultSimulator(seed=7).generate_crash_dump(
                    fault, scenario_id=1)
                b = SatelliteFaultSimulator(seed=7).generate_crash_dump(
                    fault, scenario_id=1)
                self.assertEqual(json.dumps(a, sort_keys=True),
                                 json.dumps(b, sort_keys=True))

    def test_detection_specs_are_projections_of_the_dictionary(self):
        self.assertEqual(set(CHANNEL_SPECS), set(CHANNELS))
        for name, spec in CHANNEL_SPECS.items():
            definition = CHANNELS[name]
            with self.subTest(channel=name):
                self.assertEqual((spec.limit_min, spec.limit_max),
                                 definition.hard_limits)
                self.assertEqual(spec.kind.value, definition.value_class.value)
                self.assertEqual(spec.expected_states,
                                 tuple(definition.expected_states))
                self.assertEqual(spec.max_rate_per_s, definition.max_rate_per_s)
                self.assertEqual(spec.unit, definition.unit)

    def test_kind_mapping_is_total(self):
        from app.detection.channels import _KIND_FOR_VALUE_CLASS

        self.assertEqual(set(_KIND_FOR_VALUE_CLASS), set(ValueClass))
        self.assertEqual({k.value for k in _KIND_FOR_VALUE_CLASS.values()},
                         {k.value for k in ChannelKind})

    def test_scenario_derived_limits_are_not_called_engineering(self):
        """A bound from one scenario is weaker than one applied to every dump.

        Marking it ENGINEERING made the statistical detectors treat it as a
        3-sigma band, which took scenario 5 from 7 findings to 12.
        """
        for definition in all_channels():
            spec = CHANNEL_SPECS[definition.channel_id]
            with self.subTest(channel=definition.channel_id):
                if definition.limits_provenance is Provenance.REPO_SCENARIO_DATA:
                    self.assertIs(spec.bound_origin, BoundOrigin.UNKNOWN)
                elif definition.limits_provenance is (
                    Provenance.REPO_DETECTOR_TABLE
                ):
                    self.assertIs(spec.bound_origin, BoundOrigin.ENGINEERING)

    def test_degenerate_limit_channels_are_still_identified(self):
        degenerate = {c.channel_id for c in all_channels()
                      if c.degenerate_hard_limits}
        for name in ("SEU_counter", "Star_tracker_status", "Fault_register",
                     "Transponder_lock"):
            with self.subTest(channel=name):
                self.assertIn(name, degenerate)

    def test_blind_spot_channels_are_not_statistically_tested(self):
        for name in ("SEU_counter", "Star_tracker_status", "Fault_register",
                     "Transponder_lock", "Watchdog_counter"):
            with self.subTest(channel=name):
                self.assertFalse(
                    CHANNELS[name].statistical_detection_meaningful,
                    "a z-score on this channel is what created the blind spot",
                )


# ═══════════════════════════════════════════════════════════════════════════
# 4 — UNKNOWN CHANNELS
# ═══════════════════════════════════════════════════════════════════════════

class TestUnknownChannelsAreExplicit(unittest.TestCase):

    UNKNOWN_NAMES = ("channel_41", "channel_999", "totally_made_up", "",
                     "  ", "V_bat_backup", "GYRO_SOMETHING", None)

    def test_resolve_marks_them_unknown(self):
        for name in self.UNKNOWN_NAMES:
            with self.subTest(channel=name):
                definition = resolve_channel(name)
                self.assertIs(definition.subsystem, Subsystem.UNKNOWN)
                self.assertIs(definition.provenance, Provenance.UNKNOWN)
                self.assertFalse(definition.is_known)
                self.assertIs(definition.sampling_rate, SamplingRate.UNKNOWN)

    def test_no_subsystem_is_inferred_from_a_suggestive_name(self):
        """The whole point: a name that resembles a known channel gets nothing."""
        for name in ("V_bat_2", "BATTERY_VOLTAGE_BACKUP", "gyro_rate_degs_b",
                     "OBC_temp_C_redundant", "Transponder_lock_B"):
            with self.subTest(channel=name):
                self.assertIs(subsystem_of(name), Subsystem.UNKNOWN)
                self.assertIs(resolve_channel(name).subsystem,
                              Subsystem.UNKNOWN)

    def test_nothing_is_invented_for_an_unknown_channel(self):
        definition = resolve_channel("channel_41")
        self.assertEqual(definition.expected_states, ())
        self.assertIsNone(definition.max_rate_per_s)
        self.assertIsNone(definition.unit)
        self.assertEqual(definition.nominal_range, (None, None))
        self.assertEqual(definition.safety_limits, (None, None))
        self.assertFalse(definition.monotonic_non_decreasing)

    def test_reading_bounds_are_used_but_not_attributed(self):
        definition = resolve_channel("channel_41", nominal_min=0.1,
                                     nominal_max=0.9)
        self.assertEqual(definition.hard_limits, (0.1, 0.9))
        self.assertIs(definition.limits_provenance, Provenance.UNKNOWN)

    def test_lookup_helpers_report_unknown_rather_than_guessing(self):
        self.assertIsNone(get_channel("channel_41"))
        self.assertFalse(is_known_channel("channel_41"))
        self.assertEqual(hard_limits("channel_41"), (None, None))
        self.assertEqual(nominal_range("channel_41"), (None, None))

    def test_detection_layer_marks_unknown_explicitly(self):
        spec = spec_or_inferred("channel_41")
        self.assertEqual(spec.subsystem, UNKNOWN_SUBSYSTEM)
        self.assertEqual(spec.subsystem, "UNKNOWN")
        self.assertFalse(spec.subsystem_is_known)
        self.assertIs(spec.bound_origin, BoundOrigin.UNKNOWN)
        self.assertIsNone(get_channel_spec("channel_41"))

    def test_declared_channels_report_a_known_subsystem(self):
        for definition in all_channels():
            spec = CHANNEL_SPECS[definition.channel_id]
            with self.subTest(channel=definition.channel_id):
                self.assertTrue(spec.subsystem_is_known)

    def test_unrecognised_subsystem_string_is_unknown(self):
        for name in ("EPSS", "power", "", None, "MADE_UP"):
            with self.subTest(value=name):
                self.assertIs(resolve_subsystem(name), Subsystem.UNKNOWN)


class TestAliasResolution(unittest.TestCase):

    def test_aliases_resolve_to_the_canonical_channel(self):
        cases = {
            "BATTERY_VOLTAGE": "V_bat",
            "GYRO_A_RATE": "Gyro_rate_degs",
            "GYRO_B_RATE": "Gyro_rate_degs",
            "IMU_A_ANGULAR_RATE_X": "Gyro_rate_degs",
            "TEMP_OBC": "OBC_temp_C",
            "SOC": "SoC_pct",
            "BATTERY_SOC_PCT": "SoC_pct",
            "RWA_SPEED_RPM": "RW_speed_rpm",
            # Overall link state, not carrier lock — the link can be down with
            # the carrier still locked.
            "COMMS_LINK_STATUS": "Link_status",
        }
        for alias, expected in cases.items():
            with self.subTest(alias=alias):
                definition = get_channel(alias)
                self.assertIsNotNone(definition, f"{alias} did not resolve")
                self.assertEqual(definition.channel_id, expected)

    def test_lookup_is_case_insensitive_for_channel_names(self):
        for spelling in ("V_bat", "v_bat", "V_BAT", "  V_bat  "):
            with self.subTest(spelling=spelling):
                self.assertEqual(get_channel(spelling).channel_id, "V_bat")

    def test_no_alias_collides(self):
        """Two DIFFERENT channels claiming one alias would make resolution arbitrary.

        A channel listing its own id in another case — CPU_load_pct declaring
        "CPU_LOAD_PCT" — is a self-alias and harmless, since lookup lower-cases.
        """
        seen: dict[str, str] = {}
        for definition in all_channels():
            for alias in (definition.channel_id, *definition.aliases):
                key = alias.strip().lower()
                owner = seen.get(key)
                with self.subTest(alias=alias):
                    self.assertIn(
                        owner, (None, definition.channel_id),
                        f"{alias} claimed by both {owner} and "
                        f"{definition.channel_id}",
                    )
                seen[key] = definition.channel_id

    def test_detection_lookup_resolves_aliases_too(self):
        spec = get_channel_spec("GYRO_A_RATE")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.name, "Gyro_rate_degs")


# ═══════════════════════════════════════════════════════════════════════════
# 5 — THE LLM PROMPT
# ═══════════════════════════════════════════════════════════════════════════

class TestPromptCarriesNoThresholds(unittest.TestCase):
    """Requirement: the prompt must NOT contain authoritative thresholds."""

    NUMBER_WITH_UNIT = re.compile(
        r"-?\d+(?:\.\d+)?\s*"
        r"(?:V\b|A\b|%|deg\b|deg/s|degC|\u00b0C|dB\b|dBm\b|MB\b|rpm\b|W\b)"
    )

    def test_channel_section_has_no_numeric_threshold(self):
        hits = self.NUMBER_WITH_UNIT.findall(CHANNEL_SEMANTICS)
        self.assertEqual(hits, [],
                         f"threshold values leaked into the prompt: {hits}")

    def test_channel_section_states_it_holds_no_limits(self):
        self.assertIn("NO thresholds", CHANNEL_SEMANTICS)
        self.assertIn("deterministic detection layer", CHANNEL_SEMANTICS)

    def test_the_old_literal_table_is_gone(self):
        for banned in ("28.0\u201333.6V", "20\u2013100% nominal",
                       "0\u20137 deg/s nominal", "-10 to +50\u00b0C"):
            with self.subTest(literal=banned):
                self.assertNotIn(banned, SYSTEM_PROMPT)

    def test_no_channel_limit_value_appears_in_the_prompt(self):
        """No hard-limit number from the dictionary may appear in the prompt.

        Checked against the dictionary itself, so adding a channel cannot
        reintroduce the duplication without failing here.
        """
        leaked: list[str] = []
        for definition in all_channels():
            for bound in definition.hard_limits:
                if bound is None or bound in (0.0, 1.0):
                    # 0 and 1 are too common in prose to be evidence.
                    continue
                for spelling in (f"{bound}", f"{bound:g}"):
                    if re.search(rf"(?<![\d.]){re.escape(spelling)}(?![\d.])",
                                 CHANNEL_SEMANTICS):
                        leaked.append(f"{definition.channel_id}={spelling}")
        self.assertEqual(sorted(set(leaked)), [])

    def test_safety_rules_no_longer_state_the_numeric_floor(self):
        self.assertNotIn("15%", SAFETY_RULES)
        self.assertNotIn("below 20%", SAFETY_RULES)
        self.assertIn("state of charge is already low", SAFETY_RULES)

    def test_the_rule_itself_survives(self):
        """Removing the number must not remove the constraint."""
        self.assertIn("power-consuming command", SAFETY_RULES)
        self.assertIn("refuse", SAFETY_RULES.lower())

    def test_section_is_generated_and_deterministic(self):
        self.assertEqual(build_channel_semantics_section(), CHANNEL_SEMANTICS)
        self.assertIs(NOMINAL_THRESHOLDS, CHANNEL_SEMANTICS)

    def test_section_is_in_the_system_prompt(self):
        self.assertIn(CHANNEL_SEMANTICS, SYSTEM_PROMPT)

    def test_every_declared_channel_is_described_to_the_model(self):
        for definition in all_channels():
            with self.subTest(channel=definition.channel_id):
                self.assertIn(definition.channel_id, CHANNEL_SEMANTICS)

    def test_prompt_carries_semantic_grounding(self):
        """Removing numbers must not leave the model with nothing."""
        for expected in ("Battery Voltage", "criticality CRITICAL",
                         "Radiation hits on the processor"):
            with self.subTest(text=expected):
                self.assertIn(expected, CHANNEL_SEMANTICS)


# ═══════════════════════════════════════════════════════════════════════════
# 6 — SELF-VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

class TestDictionaryValidation(unittest.TestCase):

    def test_no_errors(self):
        findings = validate_dictionary()
        self.assertEqual(findings["errors"], [],
                         "the channel dictionary reports internal errors")

    def test_the_five_inherited_conflicts_are_reported_not_hidden(self):
        findings = validate_dictionary()
        self.assertEqual(len(findings["known_conflicts"]), 5)
        for channel_id in ("Attitude_error_deg", "Component_temp_C",
                           "Gyro_rate_degs", "SEU_counter", "V_bus"):
            with self.subTest(channel=channel_id):
                self.assertIn(channel_id, KNOWN_NOMINAL_LIMIT_CONFLICTS)
                self.assertIs(
                    CHANNELS[channel_id].nominal_within_hard_limits, False,
                )

    def test_a_new_conflict_would_be_an_error_not_a_known_conflict(self):
        """The known list must not act as a blanket exemption."""
        import dataclasses

        from app.ingest import channel_dict as module

        victim = "V_bat"
        self.assertNotIn(victim, KNOWN_NOMINAL_LIMIT_CONFLICTS)
        original = CHANNELS[victim]
        broken = dataclasses.replace(original, nominal_range=(0.0, 999.0))
        CHANNELS[victim] = broken
        try:
            findings = module.validate_dictionary()
            self.assertTrue(
                any(victim in message for message in findings["errors"]),
                "a new conflict was not reported as an error",
            )
        finally:
            CHANNELS[victim] = original
        self.assertEqual(module.validate_dictionary()["errors"], [])

    def test_a_resolved_conflict_produces_a_cleanup_warning(self):
        """The known list cannot rot silently."""
        import dataclasses

        from app.ingest import channel_dict as module

        victim = "V_bus"
        original = CHANNELS[victim]
        fixed = dataclasses.replace(original, nominal_range=(27.0, 29.0))
        CHANNELS[victim] = fixed
        try:
            findings = module.validate_dictionary()
            self.assertTrue(
                any(victim in message and "no longer conflicts" in message
                    for message in findings["warnings"]),
                "resolving a conflict should prompt removing it from the list",
            )
        finally:
            CHANNELS[victim] = original

    def test_consistent_channels_report_containment(self):
        self.assertIs(CHANNELS["V_bat"].nominal_within_hard_limits, True)

    def test_unspecified_nominal_band_is_not_treated_as_agreement(self):
        self.assertIsNone(CHANNELS["Link_margin_dB"].nominal_within_hard_limits)

    def test_status_reports_what_a_caller_needs(self):
        status = dictionary_status()
        for key in ("channel_dict_version", "total_channels",
                    "channels_per_subsystem", "channels_per_value_class",
                    "degenerate_hard_limits",
                    "statistical_detection_disabled_for", "validation"):
            with self.subTest(key=key):
                self.assertIn(key, status)
        self.assertEqual(status["total_channels"], len(CHANNELS))
        self.assertEqual(status["validation"]["known_conflict_count"], 5)

    def test_the_conflicts_are_documented(self):
        doc = _BACKEND.parent / "docs" / "phase5_channel_conflicts.md"
        self.assertTrue(doc.is_file(), "the conflict document is missing")
        text = _read(doc)
        for channel_id in KNOWN_NOMINAL_LIMIT_CONFLICTS:
            with self.subTest(channel=channel_id):
                self.assertIn(channel_id, text)

    def test_cli_gate_passes(self):
        import subprocess

        result = subprocess.run(
            [sys.executable, "-m", "app.ingest.channel_dict"],
            capture_output=True, text=True, cwd=str(_BACKEND),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class TestChannelApi(unittest.TestCase):

    def test_endpoints_are_registered(self):
        import app.main as main

        paths = {getattr(r, "path", "") for r in main.app.routes}
        self.assertIn("/api/v1/channels", paths)
        self.assertIn("/api/v1/channels/{channel_id}", paths)

    def test_dictionary_endpoint_serves_every_field(self):
        import app.main as main

        payload = main.channel_dictionary_endpoint()
        self.assertEqual(len(payload["channels"]), len(CHANNELS))
        required = {"channel_id", "display_name", "subsystem", "unit",
                    "datatype", "value_class", "nominal_range", "hard_limits",
                    "criticality", "sampling_rate", "description",
                    "physical_meaning", "provenance"}
        for entry in payload["channels"]:
            with self.subTest(channel=entry["channel_id"]):
                self.assertTrue(required.issubset(entry))

    def test_dictionary_endpoint_exposes_the_conflicts(self):
        import app.main as main

        payload = main.channel_dictionary_endpoint()
        self.assertEqual(payload["validation"]["known_conflict_count"], 5)

    def test_lookup_endpoint_reports_unknown_without_a_404(self):
        import app.main as main

        result = main.channel_lookup_endpoint("channel_41")
        self.assertFalse(result["is_known"])
        self.assertEqual(result["subsystem"], "UNKNOWN")
        self.assertEqual(result["requested"], "channel_41")

    def test_lookup_endpoint_resolves_an_alias(self):
        import app.main as main

        result = main.channel_lookup_endpoint("GYRO_A_RATE")
        self.assertTrue(result["is_known"])
        self.assertEqual(result["channel_id"], "Gyro_rate_degs")
        self.assertEqual(result["subsystem"], "AOCS")

    def test_generated_contract_publishes_the_channel_vocabulary(self):
        contract = _BACKEND.parent.parent / "contracts" / "frontend" / "contract.js"
        text = _read(contract)
        self.assertIn("CHANNEL_API", text)
        self.assertIn("UNKNOWN_SUBSYSTEM", text)
        match = re.search(
            r"export const SUBSYSTEM = Object\.freeze\(\{(.*?)\}\);",
            text, re.DOTALL,
        )
        self.assertIsNotNone(match, "SUBSYSTEM not exported to the frontend")
        published = set(re.findall(r"(\w+):\s*\"", match.group(1)))
        self.assertEqual(published, {s.value for s in Subsystem})


if __name__ == "__main__":
    unittest.main(verbosity=2)

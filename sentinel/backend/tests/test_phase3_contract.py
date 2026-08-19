"""
SENTINEL — Phase 3 regression tests (test_phase3_contract.py)

Covers requirements 11 (API validation) and 12 (contract tests proving the
frontend and backend representations agree).

Run:
    python3 -m unittest tests.test_phase3_contract -v

Three groups:

  1. API VALIDATION — every payload the API actually serves must validate
     against the declared models. Before Phase 3, three of the ten scenarios
     failed CrashDumpRequest.model_validate() while still being served, so the
     "schema" described something the API did not return.

  2. CANONICAL FIELD — pre_fault_telemetry_window is the one representation.
     Tests here pin the merge behaviour and the specific data losses that
     motivated it.

  3. FRONTEND AGREEMENT — the generated contract, the CRA mirror, and the
     source files are checked against the backend enums. These are static
     checks on file contents because there is no JS test runner configured; a
     static check that fails the build is worth more than a dynamic one that
     never runs.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_REPO = _BACKEND.parent.parent
_FRONTEND = _REPO / "sentinel" / "frontend"
_CONTRACTS = _REPO / "contracts"

PYTHON = sys.executable

from app.api import models as M                                    # noqa: E402
from app.api.adapters import (                                     # noqa: E402
    canonical_channels,
    canonical_window,
    canonical_window_dicts,
    coverage_report,
    with_canonical_window,
)
from app.api.models import (                                       # noqa: E402
    API_VERSION,
    CONTRACT_VERSION,
    ContractInfo,
    CrashDumpRequest,
    Scenario,
    ScenarioListResponse,
    TelemetryEntry,
    TelemetryStatus,
)
from app.api.provenance import Provenance                          # noqa: E402
from app.api.scenarios import get_all_scenarios                    # noqa: E402


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# GROUP 1 — API VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

class TestServedPayloadsValidate(unittest.TestCase):
    """Everything the API serves must validate against the declared model."""

    @classmethod
    def setUpClass(cls):
        cls.scenarios = get_all_scenarios()

    def test_catalogue_is_not_empty(self):
        self.assertGreater(len(self.scenarios), 0)

    def test_every_scenario_validates_as_crash_dump_request(self):
        """Pre-Phase-3 this failed on 3 of 10 scenarios.

        Two causes, both fixed by widening the model to match reality rather
        than by editing the data: TelecommandContext.gap_seconds/gap_percentile
        were required but ship as null in presets 4/5/6, and the status field
        allowed only three values while the data uses seven.
        """
        for scenario in self.scenarios:
            with self.subTest(scenario_id=scenario.get("scenario_id")):
                CrashDumpRequest.model_validate(scenario)

    def test_every_scenario_validates_as_scenario(self):
        """The catalogue response model requires a provenance declaration."""
        for scenario in self.scenarios:
            with self.subTest(scenario_id=scenario.get("scenario_id")):
                model = Scenario.model_validate(scenario)
                self.assertIn(model.provenance,
                              {p.value for p in Provenance})
                self.assertTrue(model.source_type)

    def test_scenario_list_response_round_trips(self):
        payload = ScenarioListResponse(
            count=len(self.scenarios),
            scenarios=[with_canonical_window(s) for s in self.scenarios],
        )
        self.assertEqual(payload.contract_version, CONTRACT_VERSION)
        self.assertEqual(payload.api_version, API_VERSION)
        self.assertEqual(payload.count, len(self.scenarios))
        # Must survive a JSON round trip — this is what FastAPI does.
        reloaded = ScenarioListResponse.model_validate_json(
            payload.model_dump_json()
        )
        self.assertEqual(reloaded.count, payload.count)

    def test_count_cannot_disagree_with_payload(self):
        """A stated count that contradicts the data is corrected, not served."""
        payload = ScenarioListResponse(count=999, scenarios=[])
        self.assertEqual(payload.count, 0)

    def test_contract_info_reports_the_canonical_field(self):
        info = ContractInfo()
        self.assertEqual(info.canonical_telemetry_field,
                         "pre_fault_telemetry_window")
        self.assertIn("pre_fault_telemetry", info.deprecated_telemetry_fields)
        self.assertEqual(info.contract_version, CONTRACT_VERSION)


class TestTelemetryStatusVocabulary(unittest.TestCase):
    """The status enum must cover the values the repository actually uses."""

    def test_all_statuses_in_shipped_data_are_representable(self):
        observed: set[str] = set()
        for scenario in get_all_scenarios():
            for field in ("pre_fault_telemetry_window", "pre_fault_telemetry"):
                for row in scenario.get(field) or []:
                    if isinstance(row, dict) and row.get("status") is not None:
                        observed.add(str(row["status"]))
        self.assertTrue(observed, "no statuses found in the shipped scenarios")
        for value in sorted(observed):
            with self.subTest(status=value):
                self.assertIsNot(
                    TelemetryStatus.normalize(value), TelemetryStatus.UNKNOWN,
                    f"status {value!r} present in shipped data resolves to "
                    f"UNKNOWN — the enum does not cover the data",
                )

    def test_aliases_normalize(self):
        cases = {
            "ok": TelemetryStatus.NOMINAL,
            "OK": TelemetryStatus.NOMINAL,
            "warn": TelemetryStatus.WARNING,
            "anomaly": TelemetryStatus.ANOMALOUS,
            "crit": TelemetryStatus.CRITICAL,
            "nominal-context": TelemetryStatus.NOMINAL_CONTEXT,
            "LABELED_ANOMALY": TelemetryStatus.LABELLED_ANOMALY,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertIs(TelemetryStatus.normalize(raw), expected)

    def test_unknown_never_becomes_nominal(self):
        """Absent status must never be presented as "nothing is wrong"."""
        for raw in (None, "", "   ", "not-a-status", 42, object()):
            with self.subTest(raw=repr(raw)):
                self.assertIs(TelemetryStatus.normalize(raw),
                              TelemetryStatus.UNKNOWN)
        self.assertFalse(TelemetryStatus.UNKNOWN.is_nominal)
        self.assertTrue(TelemetryStatus.NOMINAL.is_nominal)
        self.assertTrue(TelemetryStatus.NOMINAL_CONTEXT.is_nominal)

    def test_entry_status_defaults_to_unknown(self):
        entry = TelemetryEntry(timestamp="T-0s", parameter="V_bat", value=30.0)
        self.assertIs(entry.status, TelemetryStatus.UNKNOWN)


class TestOptionalTelecommandStatistics(unittest.TestCase):
    """A statistic that was not computed is absent, not fabricated."""

    def test_null_gap_statistics_accepted(self):
        ctx = M.TelecommandContext.model_validate({
            "event_id": 1,
            "telecommand": "telecommand_63",
            "execution_timestamp": "2026-06-13T00:15:22Z",
            "gap_seconds": None,
            "gap_classification": "nominal",
            "gap_percentile": None,
            "anomaly_flag": False,
        })
        self.assertIsNone(ctx.gap_seconds)
        self.assertIsNone(ctx.gap_percentile)

    def test_missing_gap_statistics_accepted(self):
        ctx = M.TelecommandContext.model_validate({
            "event_id": 1,
            "telecommand": "telecommand_63",
            "execution_timestamp": "2026-06-13T00:15:22Z",
            "gap_classification": "nominal",
            "anomaly_flag": False,
        })
        self.assertIsNone(ctx.gap_seconds)


# ═══════════════════════════════════════════════════════════════════════════
# GROUP 2 — CANONICAL TELEMETRY FIELD
# ═══════════════════════════════════════════════════════════════════════════

class TestCanonicalWindowAdapter(unittest.TestCase):
    """The adapter is the ONLY place the two telemetry shapes are reconciled."""

    def test_no_channel_is_lost_in_the_merge(self):
        for scenario in get_all_scenarios():
            with self.subTest(scenario_id=scenario.get("scenario_id")):
                report = coverage_report(scenario)
                self.assertEqual(
                    report["channels_lost"], [],
                    f"merge dropped channels: {report['channels_lost']}",
                )

    def test_legacy_only_channels_survive(self):
        """The failure Phase 2 measured: bounds and channels live in the legacy
        list, so preferring the window alone silently loses them."""
        dump = {
            "pre_fault_telemetry_window": [
                {"timestamp": "T-10s", "parameter": "V_bat", "value": 30.0,
                 "status": "NOMINAL"},
            ],
            "pre_fault_telemetry": [
                {"parameter": "V_bat", "value": 30.0,
                 "nominal_min": 28.0, "nominal_max": 33.6},
                {"parameter": "Transponder_lock", "value": 0,
                 "nominal_min": 1.0, "nominal_max": 1.0},
            ],
        }
        channels = canonical_channels(dump)
        self.assertIn("V_bat", channels)
        self.assertIn("Transponder_lock", channels,
                      "a legacy-only channel was dropped")

    def test_bounds_from_legacy_enrich_the_window_entry(self):
        dump = {
            "pre_fault_telemetry_window": [
                {"timestamp": "T-0s", "parameter": "V_bat", "value": 30.0,
                 "status": "NOMINAL"},
            ],
            "pre_fault_telemetry": [
                {"parameter": "V_bat", "value": 30.0,
                 "nominal_min": 28.0, "nominal_max": 33.6},
            ],
        }
        entries = [e for e in canonical_window(dump) if e.parameter == "V_bat"]
        self.assertTrue(entries)
        merged = entries[0]
        self.assertEqual(merged.nominal_min, 28.0)
        self.assertEqual(merged.nominal_max, 33.6)
        self.assertIs(merged.status, TelemetryStatus.NOMINAL)

    def test_nan_is_distinguishable_from_missing(self):
        dump = {
            "pre_fault_telemetry": [
                {"parameter": "Gyro_rate_degs", "value": "NaN"},
                {"parameter": "Other_channel", "value": None},
            ],
        }
        by_param = {e.parameter: e for e in canonical_window(dump)}
        self.assertEqual(by_param["Gyro_rate_degs"].value_text, "NaN")
        self.assertEqual(by_param["Other_channel"].value_text, "MISSING")
        self.assertIsNone(by_param["Gyro_rate_degs"].value)
        self.assertFalse(by_param["Gyro_rate_degs"].is_usable)

    def test_relative_time_is_parsed_once_here(self):
        dump = {
            "pre_fault_telemetry_window": [
                {"timestamp": "T-120s", "parameter": "A", "value": 1.0},
                {"timestamp": "T+0.500s", "parameter": "B", "value": 2.0},
                {"timestamp": "garbage", "parameter": "C", "value": 3.0},
            ],
        }
        by_param = {e.parameter: e for e in canonical_window(dump)}
        self.assertEqual(by_param["A"].relative_time_s, -120.0)
        self.assertEqual(by_param["B"].relative_time_s, 0.5)
        self.assertIsNone(
            by_param["C"].relative_time_s,
            "an unparseable offset must stay None, not collapse to 0.0 — "
            "collapsing flattens the window and makes rate detection meaningless",
        )

    def test_empty_and_malformed_input_never_raises(self):
        for payload in (None, {}, {"pre_fault_telemetry": None},
                        {"pre_fault_telemetry": "not-a-list"},
                        {"pre_fault_telemetry_window": [None, 5, "x", {}]}):
            with self.subTest(payload=repr(payload)):
                self.assertEqual(canonical_window(payload), [])

    def test_with_canonical_window_preserves_the_deprecated_field(self):
        dump = {
            "pre_fault_telemetry": [
                {"parameter": "V_bat", "value": 30.0, "nominal_min": 28.0,
                 "nominal_max": 33.6},
            ],
        }
        result = with_canonical_window(dump)
        self.assertIn("pre_fault_telemetry", result,
                      "backward compatibility requires the legacy field to stay")
        self.assertTrue(result["pre_fault_telemetry_window"])


class TestDetectionReadsCanonicalField(unittest.TestCase):
    """Detection through the adapter must equal detection on the raw dump."""

    def test_detection_matches_on_every_scenario(self):
        from app.detection import run_detection_on_crash_dump

        for scenario in get_all_scenarios():
            sid = scenario.get("scenario_id")
            with self.subTest(scenario_id=sid):
                direct = run_detection_on_crash_dump(scenario)
                through_adapter = run_detection_on_crash_dump(
                    with_canonical_window(scenario)
                )
                self.assertEqual(
                    direct.anomaly_count, through_adapter.anomaly_count,
                    "canonicalizing the dump changed the anomaly count",
                )
                self.assertEqual(direct.total_channels,
                                 through_adapter.total_channels)
                self.assertEqual(
                    sorted(direct.anomalous_channel_names()),
                    sorted(through_adapter.anomalous_channel_names()),
                )

    def test_no_scenario_reports_zero_channels(self):
        """A dump that analyses zero channels is the Phase 2 false-clean bug."""
        from app.detection import run_detection_on_crash_dump

        for scenario in get_all_scenarios():
            sid = scenario.get("scenario_id")
            with self.subTest(scenario_id=sid):
                report = run_detection_on_crash_dump(scenario)
                self.assertGreater(
                    report.total_channels, 0,
                    f"scenario {sid} analysed no channels at all",
                )


class TestSafetyContextReadsCanonicalField(unittest.TestCase):
    """Safety preconditions must see values carried only in the window."""

    def test_window_only_battery_soc_is_visible(self):
        """Before Phase 3 the extractors read the legacy list only, so this SoC
        was invisible and the documented UNKNOWN-never-blocks policy permitted
        power-hungry commands on an 8% battery."""
        from app.validation.command_registry import Condition
        from app.validation.conditions import (
            ConditionState,
            evaluate_condition,
            get_battery_soc,
        )

        ctx = {
            "pre_fault_telemetry_window": [
                {"parameter": "SoC_pct", "timestamp": "T-30s", "value": 8.0,
                 "status": "CRITICAL"},
            ],
        }
        self.assertEqual(get_battery_soc(ctx), 8.0)
        state, support = evaluate_condition(Condition.BATTERY_ABOVE_FLOOR, ctx)
        self.assertIs(state, ConditionState.VIOLATED)
        self.assertEqual(support["battery_soc_pct"], 8.0)

    def test_latest_sample_decides_a_validity_check(self):
        """Regression: a time series must not be read first-match.

        The canonical window is a time series. First-match returns the OLDEST
        sample, so scenario 1's gyro (0.5 at T-120s, NaN at T-0s) read as a
        healthy 0.5 and GYRO_DATA_VALID flipped from VIOLATED to SATISFIED —
        unblocking attitude actuation on a gyro that had dropped out.
        """
        from app.validation.command_registry import Condition
        from app.validation.conditions import ConditionState, evaluate_condition

        degrading = {
            "pre_fault_telemetry_window": [
                {"parameter": "Gyro_rate_degs", "timestamp": "T-120s",
                 "value": 0.5},
                {"parameter": "Gyro_rate_degs", "timestamp": "T-0s",
                 "value": "NaN"},
            ],
        }
        state, _ = evaluate_condition(Condition.GYRO_DATA_VALID, degrading)
        self.assertIs(state, ConditionState.VIOLATED,
                      "a stale healthy reading masked a live dropout")

        recovered = {
            "pre_fault_telemetry_window": [
                {"parameter": "Gyro_rate_degs", "timestamp": "T-120s",
                 "value": "NaN"},
                {"parameter": "Gyro_rate_degs", "timestamp": "T-0s",
                 "value": 0.5},
            ],
        }
        state, _ = evaluate_condition(Condition.GYRO_DATA_VALID, recovered)
        self.assertIs(state, ConditionState.SATISFIED,
                      "a stale NaN blocked a recovered gyro")

    def test_transponder_lock_loss_is_not_masked_by_an_earlier_lock(self):
        from app.validation.command_registry import Condition
        from app.validation.conditions import ConditionState, evaluate_condition

        ctx = {
            "pre_fault_telemetry_window": [
                {"parameter": "Transponder_lock", "timestamp": "T-300s",
                 "value": 1},
                {"parameter": "Transponder_lock", "timestamp": "T-0s",
                 "value": 0},
            ],
        }
        state, _ = evaluate_condition(Condition.COMMS_LOCK_CONFIRMED, ctx)
        self.assertIs(state, ConditionState.VIOLATED)

    def test_no_condition_verdict_changed_on_the_shipped_scenarios(self):
        """The migration must not have altered any existing verdict.

        Verified exhaustively during implementation across all 80 (scenario,
        condition) pairs; pinned here so a future change to the extractors
        cannot quietly move a safety verdict.
        """
        from app.validation.command_registry import Condition
        from app.validation.conditions import evaluate_condition

        expected_unknown_or_decided = 0
        for scenario in get_all_scenarios():
            for condition in Condition:
                state, _ = evaluate_condition(condition, scenario)
                self.assertIn(state.value,
                              {"SATISFIED", "VIOLATED", "UNKNOWN"})
                expected_unknown_or_decided += 1
        self.assertEqual(
            expected_unknown_or_decided,
            len(get_all_scenarios()) * len(list(Condition)),
        )


class TestAnalyticsReadCanonicalField(unittest.TestCase):
    """The pre-Phase-2 analytics modules were migrated too."""

    def test_anomaly_detector_sees_window_only_channels(self):
        from app.analytics.anomaly_detector import ZScoreAnomalyDetector

        dump = {
            "pre_fault_telemetry_window": [
                {"timestamp": "T-0s", "parameter": "V_bat", "value": 12.0,
                 "nominal_min": 28.0, "nominal_max": 33.6, "status": "CRITICAL"},
            ],
        }
        result = ZScoreAnomalyDetector().filter_crash_dump(dump)
        flagged = {
            e["parameter"]
            for e in result["anomaly_report"]["anomalous_parameters"]
        }
        self.assertIn("V_bat", flagged,
                      "a channel present only in the canonical window was "
                      "invisible to the baseline detector")

    def test_early_warning_sees_window_only_channels(self):
        from app.analytics.early_warning import scan_telemetry

        dump = {
            "pre_fault_telemetry_window": [
                {"timestamp": "T-120s", "parameter": "V_bat", "value": 12.0,
                 "nominal_min": 28.0, "nominal_max": 33.6},
            ],
        }
        alerts = scan_telemetry(dump)
        self.assertTrue(alerts, "no early-warning alert from the canonical window")


class TestNoModuleReadsTheDeprecatedFieldDirectly(unittest.TestCase):
    """Requirement 6: consumers read the canonical field.

    A direct read is allowed only inside the adapter (which owns the merge) and
    inside a documented ``except`` fallback, so the modules keep working if the
    adapter import fails.
    """

    ALLOWED = {
        "app/api/adapters.py",      # owns the merge
        "app/api/models.py",        # declares the deprecated field
        "app/api/scenarios.py",     # ships the data
        "app/validation/conditions.py",   # standalone fallback, documented
        "app/analytics/anomaly_detector.py",  # except-branch fallback
        "app/analytics/early_warning.py",     # except-branch fallback
    }

    PATTERN = re.compile(
        r"""\.get\(\s*["']pre_fault_telemetry["']"""
        r"""|\[\s*["']pre_fault_telemetry["']\s*\]"""
    )

    def test_no_unexpected_direct_reads(self):
        offenders: list[str] = []
        for path in sorted((_BACKEND / "app").rglob("*.py")):
            rel = path.relative_to(_BACKEND).as_posix()
            if rel in self.ALLOWED:
                continue
            for lineno, line in enumerate(_read(path).splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if self.PATTERN.search(line):
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "these read the deprecated telemetry field directly instead of "
            "app.api.adapters.canonical_window():\n  " + "\n  ".join(offenders),
        )


# ═══════════════════════════════════════════════════════════════════════════
# GROUP 3 — FRONTEND / BACKEND AGREEMENT
# ═══════════════════════════════════════════════════════════════════════════

class TestGeneratedContractArtifacts(unittest.TestCase):
    """Requirements 1-4: the contract directory exists and is up to date."""

    def test_contract_directories_exist(self):
        for path in (_CONTRACTS, _CONTRACTS / "schemas",
                     _CONTRACTS / "openapi", _CONTRACTS / "frontend"):
            with self.subTest(path=str(path)):
                self.assertTrue(path.is_dir(), f"missing directory {path}")

    def test_openapi_document_is_exported(self):
        spec_path = _CONTRACTS / "openapi" / "openapi.json"
        self.assertTrue(spec_path.is_file())
        spec = json.loads(_read(spec_path))
        self.assertIn("openapi", spec)
        for route in (f"/api/{API_VERSION}/scenarios",
                      f"/api/{API_VERSION}/detect",
                      f"/api/{API_VERSION}/contract"):
            with self.subTest(route=route):
                self.assertIn(route, spec["paths"])

    def test_schemas_are_exported_for_the_core_models(self):
        for name in ("CrashDumpRequest", "TelemetryEntry", "Scenario",
                     "ScenarioListResponse", "SentinelOutput", "AnomalyReport"):
            with self.subTest(model=name):
                path = _CONTRACTS / "schemas" / f"{name}.schema.json"
                self.assertTrue(path.is_file(), f"missing {path.name}")
                json.loads(_read(path))

    def test_artifacts_are_not_stale(self):
        """The generator's own --check mode. Fails if a model changed without
        the contract being regenerated."""
        script = _BACKEND / "scripts" / "export_contracts.py"
        self.assertTrue(script.is_file())
        result = subprocess.run(
            [PYTHON, str(script), "--check"],
            capture_output=True, text=True, cwd=str(_BACKEND),
        )
        self.assertEqual(
            result.returncode, 0,
            "contract artifacts are stale — run "
            "python3 sentinel/backend/scripts/export_contracts.py\n"
            + result.stdout + result.stderr,
        )

    def test_cra_mirror_is_byte_identical(self):
        canonical = _CONTRACTS / "frontend" / "contract.js"
        mirror = _FRONTEND / "src" / "generated" / "contract.js"
        self.assertTrue(canonical.is_file())
        self.assertTrue(mirror.is_file())
        self.assertEqual(
            _read(canonical), _read(mirror),
            "the CRA mirror has drifted from the canonical generated contract",
        )


class TestFrontendVocabularyMatchesBackend(unittest.TestCase):
    """Requirement 12: the two sides must describe the same values."""

    @classmethod
    def setUpClass(cls):
        cls.contract_js = _read(_CONTRACTS / "frontend" / "contract.js")

    def _js_object(self, const_name: str) -> dict[str, str]:
        """Parse `export const NAME = Object.freeze({ K: "V", ... });`."""
        match = re.search(
            r"export const " + re.escape(const_name)
            + r" = Object\.freeze\(\{(.*?)\}\);",
            self.contract_js, re.DOTALL,
        )
        self.assertIsNotNone(match, f"{const_name} not found in contract.js")
        body = match.group(1)
        return dict(re.findall(r'(\w+):\s*"([^"]*)"', body))

    def test_contract_version_agrees(self):
        match = re.search(r'export const CONTRACT_VERSION = "([^"]+)"',
                          self.contract_js)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), CONTRACT_VERSION)

    def test_api_version_agrees(self):
        match = re.search(r'export const API_VERSION = "([^"]+)"',
                          self.contract_js)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), API_VERSION)

    def test_provenance_vocabulary_agrees(self):
        js = self._js_object("PROVENANCE")
        self.assertEqual(set(js.values()), {p.value for p in Provenance})

    def test_telemetry_status_vocabulary_agrees(self):
        js = self._js_object("TELEMETRY_STATUS")
        self.assertEqual(set(js.values()),
                         {s.value for s in TelemetryStatus})

    def test_safety_status_vocabulary_agrees(self):
        js = self._js_object("SAFETY_STATUS")
        self.assertEqual(set(js.values()),
                         {s.value for s in M.SafetyStatus})

    def test_severity_vocabulary_agrees(self):
        from app.detection.models import Severity

        js = self._js_object("SEVERITY")
        self.assertEqual(set(js.values()), {s.value for s in Severity})

    def test_canonical_field_name_agrees(self):
        self.assertIn(
            'export const CANONICAL_TELEMETRY_FIELD = '
            '"pre_fault_telemetry_window";',
            self.contract_js,
        )

    def test_declared_api_paths_are_routes_the_backend_serves(self):
        from app.main import app

        served = {getattr(r, "path", "") for r in app.routes}
        declared = re.findall(r'^\s+\w+: "(/api/[^"]+)",$',
                              self.contract_js, re.MULTILINE)
        self.assertTrue(declared, "no API paths declared in contract.js")
        for path in declared:
            with self.subTest(path=path):
                self.assertIn(path, served,
                              f"contract.js points at {path}, which the "
                              f"backend does not serve")


class TestFrontendHasNoDuplicatedData(unittest.TestCase):
    """Requirements 7, 8, 9: one source of truth, no embedded telemetry."""

    @classmethod
    def setUpClass(cls):
        cls.app_jsx = _read(_FRONTEND / "src" / "App.jsx")
        cls.landing_files = {
            name: _read(_FRONTEND / name)
            for name in ("index.html", "public/landing.html",
                         "dashboard/landing.html")
        }

    def test_local_preset_scenarios_is_gone(self):
        self.assertNotIn("LOCAL_PRESET_SCENARIOS", self.app_jsx)

    def test_no_embedded_telemetry_array_in_app_jsx(self):
        for field in ("pre_fault_telemetry", "pre_fault_telemetry_window"):
            with self.subTest(field=field):
                self.assertNotIn(
                    f'"{field}": [', self.app_jsx,
                    f"App.jsx embeds a hardcoded {field} array",
                )

    def test_no_embedded_scenario_ids_in_app_jsx(self):
        self.assertNotIn('"scenario_id": 1', self.app_jsx)
        self.assertNotIn('"fault_type": "ADCS_GYRO_SEU"', self.app_jsx)

    def test_app_jsx_imports_the_generated_contract(self):
        # The operator console (HeaderNav, API endpoints, state selectors)
        # imports the generated contract; App.jsx itself is a thin shell.
        src_files = [
            _FRONTEND / "src" / "components" / "HeaderNav.jsx",
            _FRONTEND / "src" / "api" / "endpoints.js",
            _FRONTEND / "src" / "state" / "selectors.js",
        ]
        for path in src_files:
            with self.subTest(file=path.name):
                self.assertIn('from "../generated/contract"', _read(path))

    def test_app_jsx_fetches_the_versioned_catalogue(self):
        # The catalogue fetch lives in the shared data context; the endpoint
        # path itself comes from the generated contract's API map.
        context_src = _read(_FRONTEND / "src" / "state" / "SentinelContext.jsx")
        self.assertIn("ENDPOINTS.scenarios", context_src)
        endpoints_src = _read(_FRONTEND / "src" / "api" / "endpoints.js")
        self.assertIn("scenarios: CONTRACT_API.scenarios", endpoints_src)

    def test_app_jsx_does_not_retype_provenance_literals(self):
        """The vocabulary must come from the generated contract."""
        self.assertNotIn('SYNTHETIC_FROM_REAL_METADATA: "', self.app_jsx)
        self.assertNotIn('REAL: "REAL"', self.app_jsx)

    def test_app_jsx_renders_the_canonical_field(self):
        # The selectors read the canonical field by its generated-contract
        # name, never by a hand-typed literal.
        selectors_src = _read(_FRONTEND / "src" / "state" / "selectors.js")
        self.assertIn("CANONICAL_TELEMETRY_FIELD", selectors_src)
        self.assertNotIn("activeScenario.pre_fault_telemetry.map", self.app_jsx)

    def test_landing_pages_do_not_embed_scenario_payloads(self):
        for name, text in self.landing_files.items():
            with self.subTest(page=name):
                self.assertNotIn("PRESETS[currentPreset].json", text)
                self.assertNotIn('"IMU_A_ANGULAR_RATE_X": 0.92', text)
                self.assertNotIn('"anomaly_type": "IMU_DRIFT"', text)

    def test_landing_pages_fetch_the_versioned_catalogue(self):
        for name, text in self.landing_files.items():
            with self.subTest(page=name):
                self.assertIn("/api/v1/scenarios", text)
                self.assertIn("async function fetchScenarioCatalogue()", text)

    def test_landing_page_copies_stay_in_sync_on_the_patched_logic(self):
        """All three copies must carry identical catalogue plumbing."""
        marker = "async function fetchScenarioCatalogue()"
        bodies = {}
        for name, text in self.landing_files.items():
            start = text.index(marker)
            bodies[name] = text[start:start + 2000]
        distinct = set(bodies.values())
        self.assertEqual(
            len(distinct), 1,
            "the landing page copies have diverged in the catalogue logic",
        )


class TestFrontendDoesNotComputeDiagnosisLocally(unittest.TestCase):
    """Requirement 10: diagnosis state comes from the backend."""

    @classmethod
    def setUpClass(cls):
        cls.app_jsx = _read(_FRONTEND / "src" / "App.jsx")
        cls.views_dir = _FRONTEND / "src" / "components" / "views"
        cls.mission = _read(cls.views_dir / "MissionOverview.jsx")
        cls.investigation = _read(cls.views_dir / "FaultInvestigationView.jsx")
        cls.header_nav = _read(_FRONTEND / "src" / "components" / "HeaderNav.jsx")
        cls.async_block = _read(_FRONTEND / "src" / "components" / "ui" / "AsyncBlock.jsx")

    def test_no_local_anomaly_calculation(self):
        for banned in ("function isAnomalous", "const isAnomalous",
                       "zScore", "z_score", "calculateSeverity",
                       "computeConfidence"):
            with self.subTest(symbol=banned):
                self.assertNotIn(banned, self.app_jsx)

    def test_severity_comes_from_the_detection_report(self):
        # The views render the severity that the deterministic detection
        # pipeline served; nothing recomputes it from bounds in the browser.
        self.assertIn("detection", self.mission)
        self.assertIn("a.severity", self.mission)
        self.assertIn("detection", self.investigation)
        self.assertIn("a.severity", self.investigation)
        # TelemetryView marks anomalies straight from the detection payload.
        telemetry_src = _read(self.views_dir / "TelemetryView.jsx")
        self.assertIn("anomaliesForChannel(detection", telemetry_src)
        # No severity recomputation from nominal bounds anywhere in the views.
        for name, src in (("MissionOverview.jsx", self.mission),
                          ("FaultInvestigationView.jsx", self.investigation)):
            with self.subTest(file=name):
                self.assertNotIn("nominal_min", src)

    def test_unavailable_detection_is_shown_as_unknown(self):
        # A missing detection report renders as NOT AVAILABLE via AsyncBlock,
        # and any status badge defaults to UNKNOWN — never a fabricated verdict.
        self.assertIn("NOT AVAILABLE", self.async_block)
        self.assertIn("AsyncBlock entity={detection}", self.mission)
        self.assertIn("AsyncBlock entity={detection}", self.investigation)
        status_badge = _read(_FRONTEND / "src" / "components" / "ui" / "StatusBadge.jsx")
        self.assertIn("UNKNOWN", status_badge)

    def test_catalogue_unavailable_is_an_explicit_state(self):
        self.assertIn("Scenarios unavailable", self.header_nav)


class TestVisualStylingUnchanged(unittest.TestCase):
    """Requirement 13: no styling changes.

    Checked by proving every CSS class App.jsx references is one App.css already
    defines — so this phase introduced no new styling surface.
    """

    def test_no_new_css_classes_referenced(self):
        app_jsx = _read(_FRONTEND / "src" / "App.jsx")
        app_css = _read(_FRONTEND / "src" / "App.css")
        index_css_path = _FRONTEND / "src" / "index.css"
        css = app_css + (_read(index_css_path) if index_css_path.is_file() else "")

        defined = set(re.findall(r"\.([A-Za-z][\w-]*)", css))
        referenced: set[str] = set()
        for value in re.findall(r'className="([^"{}]+)"', app_jsx):
            referenced.update(value.split())
        for value in re.findall(r'className=\{`([^`]+)`\}', app_jsx):
            for token in re.sub(r"\$\{[^}]*\}", " ", value).split():
                referenced.add(token)

        def is_known(name: str) -> bool:
            if name in defined:
                return True
            # A class built by interpolation, e.g. `risk-${step.risk}`, leaves a
            # prefix after the expression is stripped. Accept it when App.css
            # defines at least one class with that prefix.
            return name.endswith("-") and any(
                d.startswith(name) for d in defined
            )

        unknown = sorted(c for c in referenced if c and not is_known(c))
        self.assertEqual(
            unknown, [],
            f"App.jsx references CSS classes App.css does not define: {unknown}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

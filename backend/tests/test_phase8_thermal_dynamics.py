"""
SENTINEL — Phase 8: Multi-Node Thermal Dynamics Tests
(tests/test_phase8_thermal_dynamics.py)

Comprehensive verification suite for Phase 8 Multi-Node Spacecraft Thermal Network:
1. Single-node legacy compatibility
2. Two-node inter-node conduction and energy conservation
3. Multi-node 4-node coupled conduction network
4. Stefan-Boltzmann T^4 radiative heat rejection
5. Heater power input validation
6. Internal equipment heat generation tracking
7. Thermal equilibrium steady-state validation
8. Thermal runaway and excessive temperature refutation
9. Physical input validation (negative capacitance, conductance, emissivity, non-finite values)
10. Missing telemetry handling (no temperature fabrication, fail-closed/UNCERTAIN)
11. Deterministic repeated execution (100 runs bitwise identical)
12. Physics REFUTED blocks downstream LLM override and unsafe recovery
13. Coexistence and parallel execution with Phase 7 3-axis attitude dynamics
"""

import math
import unittest
from typing import Any

from app.validation.thermal_dynamics import (
    DEFAULT_SPACE_TEMP_K,
    STEFAN_BOLTZMANN_CONSTANT,
    ZERO_CELSIUS_IN_KELVIN,
    MultiNodeThermalVerdict,
    ThermalNetwork,
    ThermalNode,
    ThermalStatus,
    create_default_spacecraft_thermal_network,
    extract_multinode_thermal_state_from_dump,
    integrate_multinode_thermal_into_physics_report,
    validate_multinode_thermal_from_dump,
    validate_multinode_thermal_sequence,
    validate_multinode_thermal_step,
)


class TestPhase8ThermalDynamics(unittest.TestCase):
    """Test suite covering all Phase 8 Multi-Node Thermal Dynamics requirements."""

    def setUp(self) -> None:
        self.default_network = create_default_spacecraft_thermal_network()

    # ───────────────────────────────────────────────────────────────────────
    # 1. Single-node legacy compatibility
    # ───────────────────────────────────────────────────────────────────────
    def test_01_single_node_legacy_compatibility(self) -> None:
        """A single lumped node with internal power heats up according to C * dT/dt = Q_int."""
        node = ThermalNode(
            name="Component_temp_C",
            capacitance=500.0,      # J/K
            emissivity=0.0,         # Pure conduction/storage, no radiation
            area=0.0,
            internal_power=10.0,    # W
            heater_power=0.0,
        )
        network = ThermalNetwork(nodes={"Component_temp_C": node})

        dt = 10.0  # seconds
        # Expected dT = (Q_int / C) * dt = (10 / 500) * 10 = 0.20 °C
        t0 = {"Component_temp_C": 20.0}
        t1 = {"Component_temp_C": 20.20}

        verdict = validate_multinode_thermal_step(network, t0, t1, dt=dt)
        self.assertTrue(verdict.is_consistent)
        self.assertTrue(verdict.is_valid)
        self.assertFalse(verdict.is_refuted)
        self.assertEqual(verdict.status, ThermalStatus.CONSISTENT)
        self.assertLessEqual(verdict.max_temp_residual_c, 0.05)
        self.assertIn("Component_temp_C", verdict.node_residuals)

    # ───────────────────────────────────────────────────────────────────────
    # 2. Two-node conduction and energy conservation
    # ───────────────────────────────────────────────────────────────────────
    def test_02_two_node_conduction(self) -> None:
        """Two nodes exchange heat via conductance G; heat lost by A equals heat gained by B."""
        node_a = ThermalNode(name="Node_A", capacitance=1000.0, internal_power=0.0)
        node_b = ThermalNode(name="Node_B", capacitance=1000.0, internal_power=0.0)
        g_ab = 10.0  # W/K
        network = ThermalNetwork(
            nodes={"Node_A": node_a, "Node_B": node_b},
            conductances={("Node_A", "Node_B"): g_ab},
        )

        dt = 5.0
        # Node A at 40°C, Node B at 20°C -> deltaT = -20°C
        # Q_cond_to_A = 10 * (20 - 40) = -200 W (A loses 200 W)
        # Q_cond_to_B = +200 W (B gains 200 W)
        # dTa = -200 / 1000 = -0.20 K/s -> in 5s: ~ -1.0 °C
        # dTb = +200 / 1000 = +0.20 K/s -> in 5s: ~ +1.0 °C
        t0 = {"Node_A": 40.0, "Node_B": 20.0}
        pred = network.step_rk4(
            {k: v + ZERO_CELSIUS_IN_KELVIN for k, v in t0.items()},
            dt=dt,
        )
        t1 = {k: v - ZERO_CELSIUS_IN_KELVIN for k, v in pred.items()}

        verdict = validate_multinode_thermal_step(network, t0, t1, dt=dt)
        self.assertTrue(verdict.is_consistent)
        self.assertLessEqual(verdict.max_temp_residual_c, 0.01)

        # Energy conservation check: heat lost by A matches heat gained by B
        delta_q_a = node_a.capacitance * (t1["Node_A"] - t0["Node_A"])
        delta_q_b = node_b.capacitance * (t1["Node_B"] - t0["Node_B"])
        self.assertAlmostEqual(delta_q_a + delta_q_b, 0.0, places=4)

    # ───────────────────────────────────────────────────────────────────────
    # 3. Multi-node conduction across 4 coupled nodes
    # ───────────────────────────────────────────────────────────────────────
    def test_03_multi_node_conduction(self) -> None:
        """4-node network evolves consistently under coupled conduction and dissipation."""
        t0 = {
            "Component_temp_C": 25.0,
            "Battery_temp_C": 18.0,
            "OBC_temp_C": 30.0,
            "Panel_temp_C": 10.0,
        }
        dt = 10.0
        pred_k = self.default_network.step_rk4(
            {k: v + ZERO_CELSIUS_IN_KELVIN for k, v in t0.items()},
            dt=dt,
        )
        t1 = {k: v - ZERO_CELSIUS_IN_KELVIN for k, v in pred_k.items()}

        verdict = validate_multinode_thermal_step(self.default_network, t0, t1, dt=dt)
        self.assertTrue(verdict.is_consistent)
        self.assertEqual(len(verdict.node_residuals), 4)
        for name in t0:
            self.assertTrue(verdict.node_residuals[name].is_consistent)

    # ───────────────────────────────────────────────────────────────────────
    # 4. Radiative cooling following Stefan-Boltzmann T^4 law
    # ───────────────────────────────────────────────────────────────────────
    def test_04_radiative_cooling(self) -> None:
        """Radiative cooling adheres to Q_rad = eps * sigma * A * (T^4 - T_space^4)."""
        emissivity = 0.90
        area = 0.50  # m^2
        capacitance = 1000.0  # J/K
        rad_node = ThermalNode(
            name="Radiator_C",
            capacitance=capacitance,
            emissivity=emissivity,
            area=area,
            internal_power=0.0,
            t_space_k=3.0,
        )
        network = ThermalNetwork(nodes={"Radiator_C": rad_node})

        t0_c = 70.0  # 343.15 K
        t0_k = t0_c + ZERO_CELSIUS_IN_KELVIN
        expected_q_rad = emissivity * STEFAN_BOLTZMANN_CONSTANT * area * (t0_k**4 - 3.0**4)
        self.assertAlmostEqual(rad_node.radiative_heat_loss(t0_k), expected_q_rad, places=4)

        # Integrate forward 10 seconds
        dt = 10.0
        t1_k = network.step_rk4({"Radiator_C": t0_k}, dt=dt)["Radiator_C"]
        t1_c = t1_k - ZERO_CELSIUS_IN_KELVIN

        # Consistent step
        verdict_valid = validate_multinode_thermal_step(
            network, {"Radiator_C": t0_c}, {"Radiator_C": t1_c}, dt=dt
        )
        self.assertTrue(verdict_valid.is_consistent)

        # Refuted step: component heated up instead of radiatively cooling
        t1_wrong = {"Radiator_C": t0_c + 5.0}
        verdict_refuted = validate_multinode_thermal_step(
            network, {"Radiator_C": t0_c}, t1_wrong, dt=dt
        )
        self.assertTrue(verdict_refuted.is_refuted)
        self.assertIn("REFUTED", verdict_refuted.explanation)

    # ───────────────────────────────────────────────────────────────────────
    # 5. Heater input validation
    # ───────────────────────────────────────────────────────────────────────
    def test_05_heater_input(self) -> None:
        """Activating an electrical heater produces verified temperature elevation."""
        node = ThermalNode(
            name="Battery_temp_C",
            capacitance=800.0,
            internal_power=2.0,
            heater_power=0.0,
        )
        network = ThermalNetwork(nodes={"Battery_temp_C": node})

        dt = 20.0
        t0 = {"Battery_temp_C": 5.0}
        heater_power = 20.0  # W heater engaged

        # Predict with heater on: total input = 22 W; dT = (22 / 800) * 20 = 0.55 °C
        t0_k = {"Battery_temp_C": 5.0 + ZERO_CELSIUS_IN_KELVIN}
        pred_k = network.step_rk4(
            t0_k, dt=dt, heater_overrides={"Battery_temp_C": heater_power}
        )
        t1 = {"Battery_temp_C": pred_k["Battery_temp_C"] - ZERO_CELSIUS_IN_KELVIN}

        verdict = validate_multinode_thermal_step(
            network,
            t0,
            t1,
            dt=dt,
            heater_powers={"Battery_temp_C": heater_power},
        )
        self.assertTrue(verdict.is_consistent)

        # If heater was asserted on but temperature dropped, step is REFUTED
        t1_cooled = {"Battery_temp_C": 3.0}
        verdict_bad = validate_multinode_thermal_step(
            network,
            t0,
            t1_cooled,
            dt=dt,
            heater_powers={"Battery_temp_C": heater_power},
        )
        self.assertTrue(verdict_bad.is_refuted)

    # ───────────────────────────────────────────────────────────────────────
    # 6. Internal heat generation tracking
    # ───────────────────────────────────────────────────────────────────────
    def test_06_internal_heat_generation(self) -> None:
        """Internal processor load dissipation tracks observed temperature elevation."""
        obc_node = ThermalNode(
            name="OBC_temp_C",
            capacitance=300.0,
            internal_power=15.0,  # 15 W high load
        )
        network = ThermalNetwork(nodes={"OBC_temp_C": obc_node})

        dt = 10.0
        t0 = {"OBC_temp_C": 25.0}
        # Expected dT = (15 / 300) * 10 = 0.50 °C
        t1 = {"OBC_temp_C": 25.50}

        verdict = validate_multinode_thermal_step(network, t0, t1, dt=dt)
        self.assertTrue(verdict.is_consistent)
        self.assertLessEqual(verdict.max_temp_residual_c, 0.05)

    # ───────────────────────────────────────────────────────────────────────
    # 7. Thermal equilibrium (steady-state)
    # ───────────────────────────────────────────────────────────────────────
    def test_07_thermal_equilibrium(self) -> None:
        """At thermal equilibrium Q_in == Q_rad, dT/dt == 0, temperatures remain constant."""
        q_int = 10.0
        emissivity = 0.80
        area = 0.10
        # Solve for T_eq: Q_int = eps * sigma * A * (T_eq^4 - T_sp^4)
        t_sp_k = 3.0
        t_eq_k = ((q_int / (emissivity * STEFAN_BOLTZMANN_CONSTANT * area)) + t_sp_k**4) ** 0.25
        t_eq_c = t_eq_k - ZERO_CELSIUS_IN_KELVIN

        eq_node = ThermalNode(
            name="EqNode_C",
            capacitance=500.0,
            emissivity=emissivity,
            area=area,
            internal_power=q_int,
            t_space_k=t_sp_k,
        )
        network = ThermalNetwork(nodes={"EqNode_C": eq_node})

        # Over 60 seconds at equilibrium, temperature should remain constant
        dt = 60.0
        t0 = {"EqNode_C": t_eq_c}
        t1 = {"EqNode_C": t_eq_c}

        verdict = validate_multinode_thermal_step(network, t0, t1, dt=dt)
        self.assertTrue(verdict.is_consistent)
        self.assertLessEqual(verdict.max_temp_residual_c, 0.02)
        self.assertLessEqual(verdict.max_heat_residual_w, 0.05)

    # ───────────────────────────────────────────────────────────────────────
    # 8. Thermal runaway and excessive temperature refutation
    # ───────────────────────────────────────────────────────────────────────
    def test_08_thermal_runaway_excessive_temperature(self) -> None:
        """Thermal runaway exceeding physical limits or unphysical heat surge is REFUTED."""
        t0 = {"Component_temp_C": 50.0}
        # Component spikes to 110 °C in 10s (violates max operating limit > 105 °C)
        t1_runaway = {"Component_temp_C": 110.0}

        node = ThermalNode(name="Component_temp_C", capacitance=500.0, internal_power=5.0)
        network = ThermalNetwork(nodes={"Component_temp_C": node})

        verdict = validate_multinode_thermal_step(network, t0, t1_runaway, dt=10.0)
        self.assertTrue(verdict.is_refuted)
        self.assertIn("runaway", verdict.explanation.lower())

    # ───────────────────────────────────────────────────────────────────────
    # 9. Invalid physical parameters validation
    # ───────────────────────────────────────────────────────────────────────
    def test_09_invalid_parameters(self) -> None:
        """Physical parameter bounds are strictly enforced (fail-closed)."""
        # Negative capacitance
        with self.assertRaises(ValueError):
            ThermalNode(name="Bad_Cap", capacitance=-10.0)

        # Zero capacitance
        with self.assertRaises(ValueError):
            ThermalNode(name="Zero_Cap", capacitance=0.0)

        # Emissivity > 1.0 or < 0.0
        with self.assertRaises(ValueError):
            ThermalNode(name="Bad_Eps", capacitance=100.0, emissivity=1.5)
        with self.assertRaises(ValueError):
            ThermalNode(name="Neg_Eps", capacitance=100.0, emissivity=-0.1)

        # Negative area
        with self.assertRaises(ValueError):
            ThermalNode(name="Bad_Area", capacitance=100.0, area=-0.5)

        # Negative conductance in network
        n1 = ThermalNode(name="N1", capacitance=100.0)
        n2 = ThermalNode(name="N2", capacitance=100.0)
        with self.assertRaises(ValueError):
            ThermalNetwork(nodes={"N1": n1, "N2": n2}, conductances={("N1", "N2"): -5.0})

        # Non-finite temperature input
        net = ThermalNetwork(nodes={"N1": n1})
        verdict_nan = validate_multinode_thermal_step(
            net, {"N1": float("nan")}, {"N1": 20.0}, dt=10.0
        )
        self.assertTrue(verdict_nan.is_refuted)

        # Below absolute zero
        verdict_subzero = validate_multinode_thermal_step(
            net, {"N1": -300.0}, {"N1": -290.0}, dt=10.0
        )
        self.assertTrue(verdict_subzero.is_refuted)

        # Invalid timestep dt <= 0
        verdict_bad_dt = validate_multinode_thermal_step(
            net, {"N1": 20.0}, {"N1": 21.0}, dt=0.0
        )
        self.assertTrue(verdict_bad_dt.is_uncertain)

    # ───────────────────────────────────────────────────────────────────────
    # 10. Missing telemetry handling
    # ───────────────────────────────────────────────────────────────────────
    def test_10_missing_telemetry(self) -> None:
        """Missing required nodes returns UNCERTAIN and never fabricates readings."""
        node_a = ThermalNode(name="Component_temp_C", capacitance=500.0)
        node_b = ThermalNode(name="Battery_temp_C", capacitance=800.0)
        network = ThermalNetwork(nodes={"Component_temp_C": node_a, "Battery_temp_C": node_b})

        # Telemetry only carries Component_temp_C, Battery_temp_C is missing
        t0 = {"Component_temp_C": 20.0}
        t1 = {"Component_temp_C": 20.5}

        verdict = validate_multinode_thermal_step(network, t0, t1, dt=10.0)
        self.assertTrue(verdict.is_uncertain)
        self.assertFalse(verdict.is_consistent)
        self.assertIn("Missing required thermal node", verdict.explanation)

        # Test dump extraction with single-node dump returns None (no fabrication)
        class MockDump:
            telemetry_window = [
                {"timestamp": 0.0, "Component_temp_C": 20.0},
                {"timestamp": 10.0, "Component_temp_C": 20.2},
            ]

        extracted = extract_multinode_thermal_state_from_dump(MockDump())
        self.assertIsNone(extracted)

    # ───────────────────────────────────────────────────────────────────────
    # 11. Deterministic repeated execution
    # ───────────────────────────────────────────────────────────────────────
    def test_11_deterministic_repeated_execution(self) -> None:
        """100 repeated executions over multi-node trajectory yield bitwise identical output."""
        samples = [
            {"t": 0.0, "Component_temp_C": 22.0, "Battery_temp_C": 15.0, "OBC_temp_C": 28.0, "Panel_temp_C": 8.0},
            {"t": 10.0, "Component_temp_C": 22.18, "Battery_temp_C": 15.05, "OBC_temp_C": 28.12, "Panel_temp_C": 8.25},
            {"t": 20.0, "Component_temp_C": 22.35, "Battery_temp_C": 15.11, "OBC_temp_C": 28.23, "Panel_temp_C": 8.48},
        ]

        ref_verdict = validate_multinode_thermal_sequence(self.default_network, samples)
        ref_dict = ref_verdict.as_dict()

        for iteration in range(100):
            curr_verdict = validate_multinode_thermal_sequence(self.default_network, samples)
            self.assertEqual(
                curr_verdict.as_dict(),
                ref_dict,
                f"Non-deterministic divergence on iteration {iteration}",
            )

    # ───────────────────────────────────────────────────────────────────────
    # 12. REFUTED thermal result blocks unsafe recovery
    # ───────────────────────────────────────────────────────────────────────
    def test_12_refuted_thermal_result_blocks_unsafe_recovery(self) -> None:
        """When multi-node thermal verdict is REFUTED, safety gate demotes and blocks execution."""
        from app.agent.safety import validate_recovery_plan
        from app.llm.models import (
            EvidenceStatus,
            HypothesisContext,
            LLMRankingInput,
            LLMRankingOutput,
            PhysicsContext,
            RankedHypothesis,
            ViolationType,
        )
        from app.llm.ranker import validate_ranking_output
        from app.validation.physics import PhysicsValidationReport, PhysicsStatus, PhysicsVerdict

        # Create refuted thermal verdict
        node = ThermalNode(name="Component_temp_C", capacitance=500.0, internal_power=5.0)
        net = ThermalNetwork(nodes={"Component_temp_C": node})
        # Massive temperature surge from 30°C to 95°C in 5s (unphysical)
        verdict = validate_multinode_thermal_step(
            net, {"Component_temp_C": 30.0}, {"Component_temp_C": 95.0}, dt=5.0
        )
        self.assertTrue(verdict.is_refuted)

        # Integrate into physics report
        fault_refuted = "TCS_THERMAL_RUNAWAY"
        fault_valid = "EPS_BATTERY_OVERCHARGE"
        base_report = PhysicsValidationReport(
            model_version="test",
            validated=[fault_valid],
            invalidated=[],
            uncertain=[],
            verdicts=[
                PhysicsVerdict(
                    hypothesis_id="h1",
                    fault_id=fault_refuted,
                    validation_status=PhysicsStatus.VALID,
                    model_version="test",
                    explanation="Nominal",
                ),
                PhysicsVerdict(
                    hypothesis_id="h2",
                    fault_id=fault_valid,
                    validation_status=PhysicsStatus.VALID,
                    model_version="test",
                    explanation="Valid",
                ),
            ],
        )

        updated_report = integrate_multinode_thermal_into_physics_report(base_report, verdict)
        self.assertIn(fault_refuted, updated_report.invalidated)
        self.assertNotIn(fault_refuted, updated_report.validated)

        # Test LLM attempt to override refuted thermal fault
        llm_output = LLMRankingOutput(
            ranked_hypotheses=(
                RankedHypothesis(fault_id=fault_refuted, rank=1, confidence=0.95),
                RankedHypothesis(fault_id=fault_valid, rank=2, confidence=0.60),
            )
        )
        ranking_input = LLMRankingInput(
            hypotheses=(
                HypothesisContext(
                    hypothesis_id="h1",
                    fault_id=fault_refuted,
                    fault_name="Thermal Runaway",
                    subsystem="TCS",
                    deterministic_rank=1,
                    deterministic_score=0.9,
                ),
                HypothesisContext(
                    hypothesis_id="h2",
                    fault_id=fault_valid,
                    fault_name="Battery Overcharge",
                    subsystem="EPS",
                    deterministic_rank=2,
                    deterministic_score=0.7,
                ),
            ),
            valid_fault_ids=(fault_refuted, fault_valid),
            physics=PhysicsContext(invalidated=(fault_refuted,), validated=(fault_valid,)),
            evidence_status=EvidenceStatus.ADEQUATE.value,
        )

        guardrail_result = validate_ranking_output(
            output=llm_output,
            ranking_input=ranking_input,
            physics_report=updated_report,
        )

        # Verify PHYSICS_OVERRIDE was triggered and fault demoted
        override_violations = [
            v for v in guardrail_result.violations if v.violation_type == ViolationType.PHYSICS_OVERRIDE
        ]
        self.assertTrue(len(override_violations) > 0)
        self.assertEqual(guardrail_result.corrected_output.ranked_hypotheses[0].fault_id, fault_valid)

        from app.api.models import Hypothesis, RecoveryStep, RiskLevel, SafetyStatus, SentinelOutput

        # Verify downstream safety blocks activating heaters when temperature exceeds limits
        unsafe_output = SentinelOutput(
            hypotheses=[
                Hypothesis(
                    rank=1,
                    root_cause=fault_refuted,
                    affected_component="TCS",
                    confidence=0.9,
                    causal_chain=["Heater stuck on", "Component overheating"],
                ),
                Hypothesis(
                    rank=2,
                    root_cause=fault_valid,
                    affected_component="EPS",
                    confidence=0.06,
                    causal_chain=["Battery charge high", "Cell overvoltage observed"],
                ),
                Hypothesis(
                    rank=3,
                    root_cause="OBC_WATCHDOG_RESET",
                    affected_component="OBC",
                    confidence=0.04,
                    causal_chain=["Watchdog counter tick", "Soft reboot triggered"],
                ),
            ],
            recovery_plan=[
                RecoveryStep(
                    step=1,
                    command="CMD_HEATER_ENABLE",
                    rationale="Attempt to re-enable heater",
                    wait_seconds=5,
                    verify="Verify heater power",
                    risk=RiskLevel.HIGH,
                )
            ],
            confidence=0.9,
            requires_human_review=False,
            reasoning_summary="Thermal runaway mitigation",
        )
        safety_eval = validate_recovery_plan(
            sentinel_output=unsafe_output,
            crash_dump_context={"Component_temp_C": 90.0, "Heater_enable_flag": 1},
        )
        self.assertEqual(safety_eval.safety_status, SafetyStatus.BLOCKED)
        self.assertFalse(safety_eval.is_safe)
        self.assertTrue(len(safety_eval.blocked_steps) > 0)

    # ───────────────────────────────────────────────────────────────────────
    # 13. Coexistence with Phase 7 3-axis physics
    # ───────────────────────────────────────────────────────────────────────
    def test_13_coexistence_with_phase7_physics(self) -> None:
        """Telemetry dumps carrying both 3-axis attitude and multi-node thermal data execute cleanly in parallel."""
        from app.validation.physics import validate_crash_dump

        class MultiPhysicsDump:
            telemetry_window = [
                {
                    "timestamp": 0.0,
                    # Phase 7 3-axis rates
                    "omega_x": 0.0,
                    "omega_y": 0.0,
                    "omega_z": 0.0,
                    "tau_x": 0.0,
                    "tau_y": 0.0,
                    "tau_z": 0.0,
                    # Phase 8 multi-node thermal
                    "Component_temp_C": 22.0,
                    "Battery_temp_C": 15.0,
                    "OBC_temp_C": 28.0,
                    "Panel_temp_C": 8.0,
                    # Legacy 1D channels
                    "Gyro_rate_degs": 0.0,
                    "RW_speed_rpm": 0.0,
                    "Attitude_error_deg": 0.0,
                    "SoC_pct": 80.0,
                    "V_bat": 32.0,
                    "Heater_power_W": 0.0,
                },
                {
                    "timestamp": 1.0,
                    "omega_x": 0.0,
                    "omega_y": 0.0,
                    "omega_z": 0.0,
                    "tau_x": 0.0,
                    "tau_y": 0.0,
                    "tau_z": 0.0,
                    "Component_temp_C": 22.02,
                    "Battery_temp_C": 15.01,
                    "OBC_temp_C": 28.01,
                    "Panel_temp_C": 8.03,
                    "Gyro_rate_degs": 0.0,
                    "RW_speed_rpm": 0.0,
                    "Attitude_error_deg": 0.0,
                    "SoC_pct": 79.9,
                    "V_bat": 32.0,
                    "Heater_power_W": 0.0,
                },
            ]

        physics_report, hyp_set, res_report, state_seq = validate_crash_dump(MultiPhysicsDump())
        self.assertIsNotNone(physics_report)
        self.assertIsNotNone(hyp_set)
        self.assertIsNotNone(res_report)
        self.assertIsNotNone(state_seq)


if __name__ == "__main__":
    unittest.main()

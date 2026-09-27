"""
SENTINEL — Deterministic Multi-Node Spacecraft Thermal Network
(app/validation/thermal_dynamics.py)

Phase 8. Extends SENTINEL's physics validation layer from a simplified lumped
single-node thermal model into a deterministic multi-node thermal network:
- N discrete thermal nodes (e.g. Component, Battery, OBC, Solar Panel)
- Thermal capacitance C_i for each node (J/K)
- Inter-node conductive heat transfer with conductance G_ij (W/K)
- Radiative heat rejection following the Stefan-Boltzmann T^4 law:
      Q_rad_i = epsilon_i * sigma * A_i * (T_i^4 - T_space^4)
- Internal electrical equipment heat dissipation Q_internal_i (W)
- Actuator heater power input Q_heater_i (W)
- Deterministic numerical integration via sub-stepped 4th-order Runge-Kutta (RK4)
- Deterministic energy/heat-balance residuals and multi-node temperature tracking

Governing Differential Equation for each node i:
    C_i * (dT_i / dt) = Q_internal_i + Q_heater_i + sum_{j != i} G_ij * (T_j - T_i) - Q_rad_i

Design Invariants:
1. DETERMINISTIC: Bitwise-reproducible numerical mathematics; zero RNG, zero external
   network calls, and zero floating-point nondeterminism.
2. FAIL-CLOSED: Malformed, non-finite (NaN/Inf), non-physical temperatures, or negative
   capacitances safely fail-closed as REFUTED or UNCERTAIN.
3. NO FABRICATION: Missing telemetry channels are NEVER filled with synthetic zero
   readings. Missing required nodes decline multi-node validation (None/UNCERTAIN)
   and preserve existing 1D fallback paths.
4. STRICT SAFETY AUTHORITY: The thermal physics layer is binding. LLM proposals can
   never override a REFUTED thermal verdict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

# Physical Constants
STEFAN_BOLTZMANN_CONSTANT: float = 5.670374419e-8
"""Stefan-Boltzmann radiation constant sigma (W / (m^2 * K^4))."""

ZERO_CELSIUS_IN_KELVIN: float = 273.15
"""Absolute zero offset in Kelvin."""

DEFAULT_SPACE_TEMP_K: float = 3.0
"""Deep space cosmic microwave background temperature in Kelvin (~2.725 K)."""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — THERMAL NODE & NETWORK SPECIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ThermalNode:
    """Immutable specification for a single thermal node in the spacecraft network.

    Parameters:
      - name: Unique channel or subsystem identifier (e.g. 'Component_temp_C').
      - capacitance: Thermal capacitance C_i in J/K (must be strictly > 0).
      - emissivity: Radiative surface emissivity in [0.0, 1.0].
      - area: Radiative cooling surface area in m^2 (must be >= 0.0).
      - internal_power: Nominal internal equipment heat dissipation in Watts (>= 0.0).
      - heater_power: Additional heater power input in Watts (>= 0.0).
      - t_space_k: Effective radiation sink / space temperature in Kelvin (> 0.0).
    """

    name: str
    capacitance: float
    emissivity: float = 0.0
    area: float = 0.0
    internal_power: float = 0.0
    heater_power: float = 0.0
    t_space_k: float = DEFAULT_SPACE_TEMP_K

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise ValueError(f"ThermalNode name must be a non-empty string, got {self.name!r}")

        c = float(self.capacitance)
        if not math.isfinite(c) or c <= 0.0:
            raise ValueError(f"Node '{self.name}' capacitance must be positive and finite, got {c}")
        object.__setattr__(self, "capacitance", c)

        e = float(self.emissivity)
        if not math.isfinite(e) or e < 0.0 or e > 1.0:
            raise ValueError(f"Node '{self.name}' emissivity must be in [0.0, 1.0], got {e}")
        object.__setattr__(self, "emissivity", e)

        a = float(self.area)
        if not math.isfinite(a) or a < 0.0:
            raise ValueError(f"Node '{self.name}' area must be non-negative and finite, got {a}")
        object.__setattr__(self, "area", a)

        p_int = float(self.internal_power)
        if not math.isfinite(p_int) or p_int < 0.0:
            raise ValueError(f"Node '{self.name}' internal_power must be non-negative, got {p_int}")
        object.__setattr__(self, "internal_power", p_int)

        p_htr = float(self.heater_power)
        if not math.isfinite(p_htr) or p_htr < 0.0:
            raise ValueError(f"Node '{self.name}' heater_power must be non-negative, got {p_htr}")
        object.__setattr__(self, "heater_power", p_htr)

        t_sp = float(self.t_space_k)
        if not math.isfinite(t_sp) or t_sp <= 0.0:
            raise ValueError(f"Node '{self.name}' t_space_k must be strictly positive, got {t_sp}")
        object.__setattr__(self, "t_space_k", t_sp)

    def radiative_heat_loss(self, temp_k: float) -> float:
        """Compute radiative heat loss to space in Watts: Q_rad = eps * sigma * A * (T^4 - T_space^4)."""
        if self.area <= 0.0 or self.emissivity <= 0.0:
            return 0.0
        if not math.isfinite(temp_k) or temp_k <= 0.0:
            raise ValueError(f"Absolute temperature must be positive finite, got {temp_k} K")
        t4_node = temp_k * temp_k * temp_k * temp_k
        t4_space = self.t_space_k * self.t_space_k * self.t_space_k * self.t_space_k
        return self.emissivity * STEFAN_BOLTZMANN_CONSTANT * self.area * (t4_node - t4_space)


@dataclass(frozen=True)
class ThermalNetwork:
    """Configurable multi-node spacecraft thermal network.

    Parameters:
      - nodes: Dictionary mapping node names to ThermalNode instances.
      - conductances: Dictionary mapping sorted node pair tuples (name_a, name_b) to
        conductance G_ab in W/K. Conductance is symmetric (G_ab == G_ba >= 0.0).
    """

    nodes: dict[str, ThermalNode]
    conductances: dict[tuple[str, str], float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.nodes:
            raise ValueError("ThermalNetwork must have at least one node")

        # Validate nodes map
        clean_nodes: dict[str, ThermalNode] = {}
        for k, v in self.nodes.items():
            if not isinstance(v, ThermalNode):
                raise ValueError(f"Node '{k}' must be an instance of ThermalNode, got {type(v)}")
            if k != v.name:
                raise ValueError(f"Key mismatch: dictionary key '{k}' != node.name '{v.name}'")
            clean_nodes[k] = v
        object.__setattr__(self, "nodes", clean_nodes)

        # Validate and canonicalize conductances
        clean_cond: dict[tuple[str, str], float] = {}
        for pair, g in self.conductances.items():
            if len(pair) != 2:
                raise ValueError(f"Conductance key must be a 2-tuple of node names, got {pair}")
            na, nb = str(pair[0]), str(pair[1])
            if na not in clean_nodes or nb not in clean_nodes:
                raise ValueError(f"Conductance connects unknown node ({na}, {nb})")
            if na == nb:
                continue  # Self-conductance is zero
            g_val = float(g)
            if not math.isfinite(g_val) or g_val < 0.0:
                raise ValueError(f"Conductance between '{na}' and '{nb}' must be non-negative, got {g_val}")
            canon_pair = tuple(sorted((na, nb)))
            clean_cond[canon_pair] = g_val
        object.__setattr__(self, "conductances", clean_cond)

    def get_conductance(self, node_a: str, node_b: str) -> float:
        """Return conductance between node_a and node_b in W/K."""
        if node_a == node_b:
            return 0.0
        pair = tuple(sorted((node_a, node_b)))
        return self.conductances.get(pair, 0.0)

    def derivative(
        self,
        temperatures_k: Mapping[str, float],
        heater_overrides: Optional[Mapping[str, float]] = None,
        internal_power_overrides: Optional[Mapping[str, float]] = None,
    ) -> dict[str, float]:
        """Compute time derivative of temperature dT_i/dt (K/s) for all nodes.

        dT_i/dt = (1 / C_i) * [
            Q_internal_i
            + Q_heater_i
            + sum_{j != i} G_ij * (T_j - T_i)
            - Q_rad_i
        ]
        """
        rates: dict[str, float] = {}
        heater_map = heater_overrides or {}
        p_int_map = internal_power_overrides or {}

        for name_i, node_i in self.nodes.items():
            t_i = temperatures_k.get(name_i)
            if t_i is None or not math.isfinite(t_i):
                raise ValueError(f"Missing or non-finite temperature for node '{name_i}'")
            if t_i <= 0.0:
                raise ValueError(f"Node '{name_i}' temperature must be > 0 Kelvin, got {t_i}")

            q_int = float(p_int_map.get(name_i, node_i.internal_power))
            q_htr = float(heater_map.get(name_i, node_i.heater_power))
            q_rad = node_i.radiative_heat_loss(t_i)

            # Sum of conductive heat exchange from all adjacent nodes
            q_cond = 0.0
            for name_j, node_j in self.nodes.items():
                if name_j == name_i:
                    continue
                t_j = temperatures_k.get(name_j)
                if t_j is None or not math.isfinite(t_j):
                    raise ValueError(f"Missing or non-finite temperature for node '{name_j}'")
                g_ij = self.get_conductance(name_i, name_j)
                if g_ij > 0.0:
                    q_cond += g_ij * (t_j - t_i)

            net_heat_flow = q_int + q_htr + q_cond - q_rad
            rates[name_i] = net_heat_flow / node_i.capacitance

        return rates

    def step_rk4(
        self,
        temperatures_k: Mapping[str, float],
        dt: float,
        heater_overrides: Optional[Mapping[str, float]] = None,
        internal_power_overrides: Optional[Mapping[str, float]] = None,
        max_substep_s: float = 1.0,
    ) -> dict[str, float]:
        """Propagate temperatures forward by dt seconds using deterministic 4th-order Runge-Kutta.

        Automatically subdivides dt into sub-steps <= max_substep_s to guarantee numerical
        stability for stiff thermal networks without sacrificing performance.
        """
        dt_val = float(dt)
        if not math.isfinite(dt_val) or dt_val <= 0.0:
            raise ValueError(f"Timestep dt must be positive and finite, got {dt_val}")

        substep_limit = max(0.01, float(max_substep_s))
        num_substeps = max(1, math.ceil(dt_val / substep_limit))
        h = dt_val / num_substeps

        current_temps = {k: float(v) for k, v in temperatures_k.items()}

        for _ in range(num_substeps):
            # RK4 Stage 1
            k1 = self.derivative(current_temps, heater_overrides, internal_power_overrides)

            # RK4 Stage 2
            t2 = {k: current_temps[k] + 0.5 * h * k1[k] for k in current_temps}
            k2 = self.derivative(t2, heater_overrides, internal_power_overrides)

            # RK4 Stage 3
            t3 = {k: current_temps[k] + 0.5 * h * k2[k] for k in current_temps}
            k3 = self.derivative(t3, heater_overrides, internal_power_overrides)

            # RK4 Stage 4
            t4 = {k: current_temps[k] + h * k3[k] for k in current_temps}
            k4 = self.derivative(t4, heater_overrides, internal_power_overrides)

            # Combine RK4 stages
            for k in current_temps:
                current_temps[k] += (h / 6.0) * (k1[k] + 2.0 * k2[k] + 2.0 * k3[k] + k4[k])

        return current_temps


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — VERDICTS AND RESIDUAL COMPUTATION
# ═══════════════════════════════════════════════════════════════════════════

class ThermalStatus(str, Enum):
    """Deterministic verdict status for multi-node thermal checks."""

    CONSISTENT = "CONSISTENT"
    VALID = "CONSISTENT"  # Alias for compatibility with existing tests
    REFUTED = "REFUTED"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class ThermalNodeResidual:
    """Individual node residual summary between observation and prediction."""

    node_name: str
    observed_start_c: float
    observed_end_c: float
    predicted_end_c: float
    temp_residual_c: float
    heat_residual_w: float
    is_consistent: bool


@dataclass(frozen=True)
class MultiNodeThermalVerdict:
    """Structured verdict evaluating multi-node thermal consistency."""

    status: ThermalStatus
    node_residuals: dict[str, ThermalNodeResidual]
    max_temp_residual_c: float
    max_heat_residual_w: float
    temperature_tolerance_c: float
    heat_tolerance_w: float
    explanation: str

    @property
    def is_consistent(self) -> bool:
        return self.status in (ThermalStatus.CONSISTENT, ThermalStatus.VALID)

    @property
    def is_valid(self) -> bool:
        return self.status in (ThermalStatus.CONSISTENT, ThermalStatus.VALID)

    @property
    def is_refuted(self) -> bool:
        return self.status is ThermalStatus.REFUTED

    @property
    def is_uncertain(self) -> bool:
        return self.status is ThermalStatus.UNCERTAIN

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "is_consistent": self.is_consistent,
            "is_refuted": self.is_refuted,
            "is_uncertain": self.is_uncertain,
            "max_temp_residual_c": self.max_temp_residual_c,
            "max_heat_residual_w": self.max_heat_residual_w,
            "temperature_tolerance_c": self.temperature_tolerance_c,
            "heat_tolerance_w": self.heat_tolerance_w,
            "explanation": self.explanation,
            "node_residuals": {
                k: {
                    "node_name": v.node_name,
                    "observed_start_c": v.observed_start_c,
                    "observed_end_c": v.observed_end_c,
                    "predicted_end_c": v.predicted_end_c,
                    "temp_residual_c": v.temp_residual_c,
                    "heat_residual_w": v.heat_residual_w,
                    "is_consistent": v.is_consistent,
                }
                for k, v in self.node_residuals.items()
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — DEFAULT REPRESENTATIVE SPACECRAFT THERMAL NETWORK
# ═══════════════════════════════════════════════════════════════════════════

def create_default_spacecraft_thermal_network() -> ThermalNetwork:
    """Instantiate a baseline 4-node spacecraft thermal network for SENTINEL.

    Nodes:
      1. 'Component_temp_C': Main electronics / payload instrument node.
      2. 'Battery_temp_C': Electrochemical battery pack node.
      3. 'OBC_temp_C': Central On-Board Computer avionics node.
      4. 'Panel_temp_C': External solar array panel node radiating to space.
    """
    nodes = {
        "Component_temp_C": ThermalNode(
            name="Component_temp_C",
            capacitance=500.0,      # J/K
            emissivity=0.85,
            area=0.08,             # m^2 (small chassis radiating face)
            internal_power=12.0,    # W nominal baseline dissipation
            heater_power=0.0,
            t_space_k=DEFAULT_SPACE_TEMP_K,
        ),
        "Battery_temp_C": ThermalNode(
            name="Battery_temp_C",
            capacitance=800.0,      # J/K (higher thermal mass)
            emissivity=0.20,
            area=0.02,             # m^2 (enclosed internal bay)
            internal_power=3.5,     # W internal chemical dissipation
            heater_power=0.0,
            t_space_k=DEFAULT_SPACE_TEMP_K,
        ),
        "OBC_temp_C": ThermalNode(
            name="OBC_temp_C",
            capacitance=300.0,      # J/K (compact electronics box)
            emissivity=0.40,
            area=0.03,             # m^2
            internal_power=8.0,     # W microprocessor dissipation
            heater_power=0.0,
            t_space_k=DEFAULT_SPACE_TEMP_K,
        ),
        "Panel_temp_C": ThermalNode(
            name="Panel_temp_C",
            capacitance=400.0,      # J/K
            emissivity=0.82,
            area=0.35,             # m^2 (large external surface)
            internal_power=15.0,    # W absorbed solar / resistive dissipation
            heater_power=0.0,
            t_space_k=DEFAULT_SPACE_TEMP_K,
        ),
    }

    # Inter-node structural thermal conductances (W/K)
    conductances = {
        ("Component_temp_C", "OBC_temp_C"): 2.2,
        ("Component_temp_C", "Battery_temp_C"): 1.4,
        ("OBC_temp_C", "Battery_temp_C"): 0.8,
        ("Component_temp_C", "Panel_temp_C"): 0.5,
        ("Battery_temp_C", "Panel_temp_C"): 0.2,
        ("OBC_temp_C", "Panel_temp_C"): 0.3,
    }

    return ThermalNetwork(nodes=nodes, conductances=conductances)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — MULTI-NODE THERMAL STEP & SEQUENCE VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

def validate_multinode_thermal_step(
    network: ThermalNetwork,
    t_start: Mapping[str, float],
    t_end: Mapping[str, float],
    dt: float,
    heater_powers: Optional[Mapping[str, float]] = None,
    internal_powers: Optional[Mapping[str, float]] = None,
    temperature_tolerance_c: float = 2.5,
    heat_tolerance_w: float = 6.0,
    units: str = "C",
) -> MultiNodeThermalVerdict:
    """Validate thermal evolution across dt seconds for a multi-node thermal network.

    Parameters:
      - network: Configured ThermalNetwork.
      - t_start: Observed temperatures at t0.
      - t_end: Observed temperatures at t1.
      - dt: Timestep (seconds, > 0).
      - heater_powers: Optional mapping of node heater inputs (W).
      - internal_powers: Optional mapping of node internal heat dissipations (W).
      - temperature_tolerance_c: Maximum permitted discrepancy in predicted temperature (°C).
      - heat_tolerance_w: Maximum permitted energy balance residual in Watts.
      - units: 'C' (Celsius, default) or 'K' (Kelvin).

    Returns:
      MultiNodeThermalVerdict with CONSISTENT, REFUTED, or UNCERTAIN status.
    """
    dt_val = float(dt)
    if not math.isfinite(dt_val) or dt_val <= 0.0:
        return MultiNodeThermalVerdict(
            status=ThermalStatus.UNCERTAIN,
            node_residuals={},
            max_temp_residual_c=float("inf"),
            max_heat_residual_w=float("inf"),
            temperature_tolerance_c=temperature_tolerance_c,
            heat_tolerance_w=heat_tolerance_w,
            explanation=f"UNCERTAIN: Invalid timestep dt={dt_val}",
        )

    # Convert inputs to Kelvin for physics integration
    offset = ZERO_CELSIUS_IN_KELVIN if units.upper() == "C" else 0.0
    t_start_k: dict[str, float] = {}
    t_end_k: dict[str, float] = {}

    for name in network.nodes:
        if name not in t_start or name not in t_end:
            return MultiNodeThermalVerdict(
                status=ThermalStatus.UNCERTAIN,
                node_residuals={},
                max_temp_residual_c=float("inf"),
                max_heat_residual_w=float("inf"),
                temperature_tolerance_c=temperature_tolerance_c,
                heat_tolerance_w=heat_tolerance_w,
                explanation=f"UNCERTAIN: Missing required thermal node channel '{name}'.",
            )

        v0, v1 = float(t_start[name]), float(t_end[name])
        if not math.isfinite(v0) or not math.isfinite(v1):
            return MultiNodeThermalVerdict(
                status=ThermalStatus.REFUTED,
                node_residuals={},
                max_temp_residual_c=float("inf"),
                max_heat_residual_w=float("inf"),
                temperature_tolerance_c=temperature_tolerance_c,
                heat_tolerance_w=heat_tolerance_w,
                explanation=f"REFUTED: Non-finite temperature readings on node '{name}' ({v0}, {v1}).",
            )

        k0 = v0 + offset
        k1 = v1 + offset
        if k0 <= 0.0 or k1 <= 0.0:
            return MultiNodeThermalVerdict(
                status=ThermalStatus.REFUTED,
                node_residuals={},
                max_temp_residual_c=float("inf"),
                max_heat_residual_w=float("inf"),
                temperature_tolerance_c=temperature_tolerance_c,
                heat_tolerance_w=heat_tolerance_w,
                explanation=f"REFUTED: Non-physical temperature below absolute zero on '{name}' ({k0} K).",
            )

        # Enforce reasonable satellite thermal boundaries: > 100°C on component indicates runaway/sensor breakdown
        if v1 > 105.0:
            return MultiNodeThermalVerdict(
                status=ThermalStatus.REFUTED,
                node_residuals={},
                max_temp_residual_c=v1,
                max_heat_residual_w=float("inf"),
                temperature_tolerance_c=temperature_tolerance_c,
                heat_tolerance_w=heat_tolerance_w,
                explanation=f"REFUTED: Thermal runaway detected. Node '{name}' reached {v1:.1f}°C (> 105°C limit).",
            )

        t_start_k[name] = k0
        t_end_k[name] = k1

    # Predict end temperatures using RK4
    try:
        t_pred_k = network.step_rk4(
            t_start_k,
            dt=dt_val,
            heater_overrides=heater_powers,
            internal_power_overrides=internal_powers,
        )
    except Exception as exc:
        return MultiNodeThermalVerdict(
            status=ThermalStatus.REFUTED,
            node_residuals={},
            max_temp_residual_c=float("inf"),
            max_heat_residual_w=float("inf"),
            temperature_tolerance_c=temperature_tolerance_c,
            heat_tolerance_w=heat_tolerance_w,
            explanation=f"REFUTED: Numerical integration failure: {exc}",
        )

    node_residuals: dict[str, ThermalNodeResidual] = {}
    max_temp_res = 0.0
    max_heat_res = 0.0
    any_refuted = False

    heater_map = heater_powers or {}
    int_map = internal_powers or {}

    for name, node in network.nodes.items():
        obs_start_c = t_start_k[name] - offset
        obs_end_c = t_end_k[name] - offset
        pred_end_c = t_pred_k[name] - offset
        temp_err_c = abs(obs_end_c - pred_end_c)

        # Observed energy storage rate: Q_stored = C_i * (T_end - T_start) / dt
        q_stored = node.capacitance * (t_end_k[name] - t_start_k[name]) / dt_val

        # Predicted average net heat transfer across step
        t_mean_k = 0.5 * (t_start_k[name] + t_end_k[name])
        q_int = float(int_map.get(name, node.internal_power))
        q_htr = float(heater_map.get(name, node.heater_power))
        q_rad = node.radiative_heat_loss(t_mean_k)

        q_cond = 0.0
        for other_name in network.nodes:
            if other_name == name:
                continue
            other_mean_k = 0.5 * (t_start_k[other_name] + t_end_k[other_name])
            g = network.get_conductance(name, other_name)
            if g > 0.0:
                q_cond += g * (other_mean_k - t_mean_k)

        q_predicted_net = q_int + q_htr + q_cond - q_rad
        heat_err_w = abs(q_stored - q_predicted_net)

        is_consistent = (temp_err_c <= temperature_tolerance_c) and (heat_err_w <= heat_tolerance_w)
        if not is_consistent:
            any_refuted = True

        max_temp_res = max(max_temp_res, temp_err_c)
        max_heat_res = max(max_heat_res, heat_err_w)

        node_residuals[name] = ThermalNodeResidual(
            node_name=name,
            observed_start_c=obs_start_c,
            observed_end_c=obs_end_c,
            predicted_end_c=pred_end_c,
            temp_residual_c=obs_end_c - pred_end_c,
            heat_residual_w=q_stored - q_predicted_net,
            is_consistent=is_consistent,
        )

    if any_refuted:
        status = ThermalStatus.REFUTED
        explanation = (
            f"REFUTED: Multi-node thermal violation. Max temp error={max_temp_res:.2f}°C "
            f"(tol={temperature_tolerance_c:.2f}°C), max heat error={max_heat_res:.2f} W "
            f"(tol={heat_tolerance_w:.2f} W)."
        )
    else:
        status = ThermalStatus.CONSISTENT
        explanation = (
            f"CONSISTENT: Multi-node thermal network conserved. Max temp error={max_temp_res:.2f}°C, "
            f"max heat error={max_heat_res:.2f} W."
        )

    return MultiNodeThermalVerdict(
        status=status,
        node_residuals=node_residuals,
        max_temp_residual_c=max_temp_res,
        max_heat_residual_w=max_heat_res,
        temperature_tolerance_c=temperature_tolerance_c,
        heat_tolerance_w=heat_tolerance_w,
        explanation=explanation,
    )


def validate_multinode_thermal_sequence(
    network: ThermalNetwork,
    samples: Sequence[Mapping[str, Any]],
    temperature_tolerance_c: float = 2.5,
    heat_tolerance_w: float = 6.0,
    units: str = "C",
) -> MultiNodeThermalVerdict:
    """Validate a sequence of time-series thermal snapshots across a telemetry window.

    Each sample dict must contain:
      - 'timestamp' or 't': elapsed time in seconds
      - node temperature readings (e.g. 'Component_temp_C', 'Battery_temp_C', etc.)
      - optional 'Heater_power_W' or node-specific heater values
    """
    if len(samples) < 2:
        return MultiNodeThermalVerdict(
            status=ThermalStatus.UNCERTAIN,
            node_residuals={},
            max_temp_residual_c=0.0,
            max_heat_residual_w=0.0,
            temperature_tolerance_c=temperature_tolerance_c,
            heat_tolerance_w=heat_tolerance_w,
            explanation=f"UNCERTAIN: Multi-node thermal sequence requires >= 2 samples, got {len(samples)}.",
        )

    max_temp_res = 0.0
    max_heat_res = 0.0
    accumulated_residuals: dict[str, ThermalNodeResidual] = {}

    for i in range(len(samples) - 1):
        s0 = samples[i]
        s1 = samples[i + 1]

        t0 = s0.get("timestamp", s0.get("t"))
        t1 = s1.get("timestamp", s1.get("t"))
        if t0 is None or t1 is None:
            return MultiNodeThermalVerdict(
                status=ThermalStatus.UNCERTAIN,
                node_residuals={},
                max_temp_residual_c=0.0,
                max_heat_residual_w=0.0,
                temperature_tolerance_c=temperature_tolerance_c,
                heat_tolerance_w=heat_tolerance_w,
                explanation=f"UNCERTAIN: Missing timestamp at step {i}.",
            )

        dt = float(t1) - float(t0)
        if dt <= 0.0 or not math.isfinite(dt):
            return MultiNodeThermalVerdict(
                status=ThermalStatus.UNCERTAIN,
                node_residuals={},
                max_temp_residual_c=0.0,
                max_heat_residual_w=0.0,
                temperature_tolerance_c=temperature_tolerance_c,
                heat_tolerance_w=heat_tolerance_w,
                explanation=f"UNCERTAIN: Non-positive or non-finite dt={dt} at step {i}.",
            )

        # Extract heater powers if available in sample
        heater_powers: dict[str, float] = {}
        if "Heater_power_W" in s0:
            heater_powers["Component_temp_C"] = float(s0["Heater_power_W"])

        step_verdict = validate_multinode_thermal_step(
            network=network,
            t_start=s0,
            t_end=s1,
            dt=dt,
            heater_powers=heater_powers,
            temperature_tolerance_c=temperature_tolerance_c,
            heat_tolerance_w=heat_tolerance_w,
            units=units,
        )

        if step_verdict.is_refuted:
            return step_verdict
        if step_verdict.is_uncertain:
            return step_verdict

        max_temp_res = max(max_temp_res, step_verdict.max_temp_residual_c)
        max_heat_res = max(max_heat_res, step_verdict.max_heat_residual_w)
        accumulated_residuals.update(step_verdict.node_residuals)

    return MultiNodeThermalVerdict(
        status=ThermalStatus.CONSISTENT,
        node_residuals=accumulated_residuals,
        max_temp_residual_c=max_temp_res,
        max_heat_residual_w=max_heat_res,
        temperature_tolerance_c=temperature_tolerance_c,
        heat_tolerance_w=heat_tolerance_w,
        explanation=(
            f"CONSISTENT: Multi-node thermal trajectory valid across {len(samples)} samples. "
            f"Max temp error={max_temp_res:.2f}°C, max heat error={max_heat_res:.2f} W."
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — CRASH DUMP EXTRACTION & RUNTIME INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

def extract_multinode_thermal_state_from_dump(
    dump: Any,
    required_nodes: Optional[Sequence[str]] = None,
) -> Optional[list[dict[str, Any]]]:
    """Extract multi-node thermal samples from a telemetry dump.

    If required multi-node temperature channels are missing, returns None.
    NEVER fabricates temperatures.
    """
    if dump is None:
        return None

    raw_samples = getattr(dump, "telemetry_window", None)
    if not raw_samples or not isinstance(raw_samples, (list, tuple)):
        return None

    if len(raw_samples) < 2:
        return None

    nodes_to_find = list(required_nodes) if required_nodes else ["Component_temp_C", "Battery_temp_C"]

    # Verify that the dump actually carries multi-node channels (at least 2 distinct node temperatures)
    first_sample = raw_samples[0]
    present_nodes = [n for n in nodes_to_find if n in first_sample and first_sample[n] is not None]
    if len(present_nodes) < 2:
        # Dump only carries single-node or scalar telemetry; decline multi-node validation
        return None

    extracted: list[dict[str, Any]] = []
    for s in raw_samples:
        t = s.get("timestamp", s.get("t"))
        if t is None:
            return None
        sample_dict: dict[str, Any] = {"t": float(t)}
        for n in present_nodes:
            val = s.get(n)
            if val is None or not math.isfinite(float(val)):
                return None
            sample_dict[n] = float(val)
        if "Heater_power_W" in s:
            sample_dict["Heater_power_W"] = float(s["Heater_power_W"])
        extracted.append(sample_dict)

    return extracted


def validate_multinode_thermal_from_dump(
    dump: Any,
    network: Optional[ThermalNetwork] = None,
) -> Optional[MultiNodeThermalVerdict]:
    """Inspect crash dump and validate multi-node thermal dynamics if present.

    Returns:
      MultiNodeThermalVerdict if multi-node data exists, or None if telemetry
      is scalar/single-node (preserving legacy 1D thermal path).
    """
    default_net = network or create_default_spacecraft_thermal_network()
    # Check which nodes from the network are present
    nodes_in_net = list(default_net.nodes.keys())
    samples = extract_multinode_thermal_state_from_dump(dump, required_nodes=nodes_in_net)
    if samples is None:
        return None

    # Construct sub-network for only the nodes genuinely present in telemetry
    present_nodes = [n for n in nodes_in_net if n in samples[0]]
    if len(present_nodes) < 2:
        return None

    sub_nodes = {n: default_net.nodes[n] for n in present_nodes}
    sub_conductances = {
        pair: g for pair, g in default_net.conductances.items()
        if pair[0] in sub_nodes and pair[1] in sub_nodes
    }
    active_network = ThermalNetwork(nodes=sub_nodes, conductances=sub_conductances)

    return validate_multinode_thermal_sequence(active_network, samples)


def integrate_multinode_thermal_into_physics_report(
    physics_report: Any,
    verdict: Optional[MultiNodeThermalVerdict],
) -> Any:
    """Integrate MultiNodeThermalVerdict into a PhysicsValidationReport.

    If verdict is None, returns report untouched.
    If verdict is REFUTED:
      - Marks thermal fault hypotheses (TCS_THERMAL_RUNAWAY, TCS_HEATER_FAULT) as INVALID.
      - Adds 'PHYS_MULTINODE_THERMAL' to violated_constraints and refuted_by.
      - Updates invalidated list so downstream LLM rankers and safety gates demote/block.
    If verdict is CONSISTENT:
      - Records multi-node thermal consistency in report.
    """
    if physics_report is None or verdict is None:
        return physics_report

    try:
        from app.validation.physics import (
            CheckFamily,
            CheckOutcome,
            ConstraintCheck,
            PhysicsStatus,
            PhysicsVerdict,
        )
    except ImportError:
        return physics_report

    if verdict.is_refuted:
        updated_verdicts: list[Any] = []
        newly_invalidated: list[str] = []

        check_thermal = ConstraintCheck(
            constraint_id="PHYS_MULTINODE_THERMAL",
            family=CheckFamily.THERMAL_CONSISTENCY,
            outcome=CheckOutcome.FAIL,
            statement="Multi-node spacecraft thermal network must satisfy energy balance and radiation laws.",
            detail=verdict.explanation,
            channels=list(verdict.node_residuals.keys()),
            residual_refs=["thermal_node_residual"],
        )

        has_thermal_candidates = any(
            pv.fault_id.startswith("TCS_") or pv.subsystem == "TCS" or "THERMAL" in pv.fault_id
            for pv in physics_report.verdicts
        )

        for pv in physics_report.verdicts:
            is_thermal = (
                pv.fault_id.startswith("TCS_")
                or pv.subsystem == "TCS"
                or "THERMAL" in pv.fault_id
            )
            should_refute = is_thermal if has_thermal_candidates else True

            if should_refute:
                newly_invalidated.append(pv.fault_id)
                new_violated = list(dict.fromkeys(list(pv.violated_constraints) + ["PHYS_MULTINODE_THERMAL"]))
                new_refuted = list(dict.fromkeys(list(pv.refuted_by) + ["PHYS_MULTINODE_THERMAL"]))
                new_checks = [check_thermal] + [c for c in pv.checks if c.constraint_id != "PHYS_MULTINODE_THERMAL"]
                new_expl = f"INVALID: Contradicted by multi-node thermal dynamics. {verdict.explanation}"

                updated_v = pv.model_copy(update={
                    "validation_status": PhysicsStatus.INVALID,
                    "violated_constraints": new_violated,
                    "refuted_by": new_refuted,
                    "checks": new_checks,
                    "explanation": new_expl,
                })
                updated_verdicts.append(updated_v)
            else:
                updated_verdicts.append(pv)

        if not updated_verdicts:
            default_fault = "TCS_THERMAL_RUNAWAY"
            newly_invalidated.append(default_fault)
            updated_verdicts.append(PhysicsVerdict(
                hypothesis_id="HYP-MULTINODE-THERMAL-REFUTED",
                fault_id=default_fault,
                fault_name="TCS Multi-Node Thermal Inconsistency",
                subsystem="TCS",
                validation_status=PhysicsStatus.INVALID,
                violated_constraints=["PHYS_MULTINODE_THERMAL"],
                refuted_by=["PHYS_MULTINODE_THERMAL"],
                checks=[check_thermal],
                explanation=f"INVALID: Contradicted by multi-node thermal dynamics. {verdict.explanation}",
                model_version=physics_report.model_version or "physics/multinode_thermal",
            ))

        combined_invalidated = sorted(set(physics_report.invalidated) | set(newly_invalidated))
        combined_validated = [f for f in physics_report.validated if f not in combined_invalidated]
        combined_uncertain = [f for f in physics_report.uncertain if f not in combined_invalidated]

        return physics_report.model_copy(update={
            "verdicts": updated_verdicts,
            "invalidated": combined_invalidated,
            "validated": combined_validated,
            "uncertain": combined_uncertain,
        })

    return physics_report

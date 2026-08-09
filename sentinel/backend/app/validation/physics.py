"""
SENTINEL — Physics Validation (app/validation/physics.py)

Phase 8. Tests every candidate fault hypothesis against the simplified state
model, and downgrades or rejects the ones the physics cannot support.

    Phase 6 hypotheses  +  Phase 7 residuals  ->  VALID / INVALID / UNCERTAIN

What this closes
----------------
Phase 6 can say which fault signatures match the evidence. Phase 7 can say
whether the telemetry is physically coherent. Until now nothing connected them,
so a hypothesis could rank first on signature evidence while the residuals were
quietly saying the opposite. The audit trail recorded ``physics_validation`` as
NOT_IMPLEMENTED with the note that consistency was "measured but not enforced".
This module is the enforcement.

The worked case, which is also the Phase 8 specification's example:

    Hypothesis:  reaction wheel degradation
    Claim:       the wheel is not delivering the torque its speed change implies
    Residual:    body rate matched the momentum-exchange prediction exactly
    Verdict:     INVALID — the wheel demonstrably delivered its implied torque,
                 so a degraded-authority claim is refuted by the measurement

That refutation is deterministic, and it holds whatever a language model says.

Why refutation is easier than confirmation
------------------------------------------
This module is deliberately asymmetric. A DECIDED contradiction yields INVALID; a
missing corroboration yields UNCERTAIN, never INVALID. The reason is the same one
that governs Phase 1's condition evaluator and Phase 6's evidence states: absence
of evidence is not evidence of absence, and crash-dump telemetry is frequently too
sparse for any residual to be decided at all.

So the useful claim here is negative. Physics validation is good at showing that a
hypothesis CANNOT be right. It is much weaker at showing one IS right, and VALID
is named accordingly in ``PhysicsStatus``: not refuted, and corroborated at least
once, by models whose parameters are partly assumed.

What a verdict does NOT mean
----------------------------
Four of Phase 7's ten model parameters were chosen rather than derived, and every
model is a simplification — one attitude axis, one wheel, one thermal node, no
illumination, a linear battery curve. An INVALID verdict therefore says the
hypothesis is inconsistent with THOSE ASSUMPTIONS. Every verdict carries the
assumption set and the model versions it was computed under so that the
qualification travels with the answer.

The LLM boundary
----------------
``validate_hypotheses()`` takes a hypothesis set and a residual report. It never
takes model output, never calls a model, and has no parameter through which a
model could influence a verdict. ``reconcile_llm_claim()`` exists for the case
where a model asserts a verdict anyway: it returns the deterministic result
unchanged and records the disagreement. There is no code path that lets a model
turn INVALID into VALID.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

PHYSICS_CONSTRAINT_SET_VERSION = "1.0.0"
"""Version of the constraint catalogue below.

Bump when a constraint is added, removed or has its rule changed. Verdicts are
only comparable within one version, because the rules are what produced them.
"""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — VOCABULARIES
# ═══════════════════════════════════════════════════════════════════════════

class PhysicsStatus(str, Enum):
    """Verdict on one hypothesis."""

    VALID = "VALID"
    """Not refuted, and corroborated by at least one decided check.

    NOT "confirmed". The corroborating models carry assumed parameters and are
    deliberate simplifications, so this means the hypothesis survives the physics
    available — not that the physics proves it.
    """

    INVALID = "INVALID"
    """At least one constraint is definitively violated.

    Reached only from a DECIDED residual, never from a missing one. The claim the
    hypothesis makes is contradicted by what the models computed from the
    telemetry.
    """

    UNCERTAIN = "UNCERTAIN"
    """No constraint was violated and none was corroborated.

    The ordinary outcome on sparse telemetry. Explicitly not a pass: it means the
    physics had nothing to say, and a reader must not take it for VALID.
    """

    @property
    def is_decided(self) -> bool:
        return self is not PhysicsStatus.UNCERTAIN

    @property
    def blocks_hypothesis(self) -> bool:
        return self is PhysicsStatus.INVALID


class CheckFamily(str, Enum):
    """The seven check families the Phase 8 specification requires."""

    PHYSICAL_CONSISTENCY = "PHYSICAL_CONSISTENCY"
    """Does the mechanism the fault claims agree with the residual evidence."""

    TELEMETRY_CONSISTENCY = "TELEMETRY_CONSISTENCY"
    """Are the channels the fault claims to affect the ones actually implicated."""

    STATE_TRANSITION_CONSISTENCY = "STATE_TRANSITION_CONSISTENCY"
    """Did the state move in the direction the fault's causal chain requires."""

    ACTUATOR_FEASIBILITY = "ACTUATOR_FEASIBILITY"
    """Could the actuator have produced — or failed to produce — the observed
    motion, given its declared limits."""

    SENSOR_CONSISTENCY = "SENSOR_CONSISTENCY"
    """Do independent sensors corroborate each other, or disagree."""

    ENERGY_CONSISTENCY = "ENERGY_CONSISTENCY"
    """Does the power and stored-energy bookkeeping support the claim."""

    THERMAL_CONSISTENCY = "THERMAL_CONSISTENCY"
    """Does the heat balance support the claim."""


class CheckOutcome(str, Enum):
    """Result of evaluating one constraint against one hypothesis."""

    PASS = "PASS"
    """The constraint is satisfied AND the evidence was decided. Corroboration."""

    FAIL = "FAIL"
    """The constraint is violated, from decided evidence. A refutation."""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    """This constraint says nothing about this fault. Carries no weight either
    way, and is reported so a reader can see the check was considered."""

    INDETERMINATE = "INDETERMINATE"
    """The constraint applies but the evidence could not decide it — the residual
    was UNDECIDABLE, or the channel was never reported. Never a PASS."""

    @property
    def is_decided(self) -> bool:
        return self in (CheckOutcome.PASS, CheckOutcome.FAIL)


class Trend(str, Enum):
    """Observed direction of a channel across the telemetry window."""

    RISING = "RISING"
    FALLING = "FALLING"
    FLAT = "FLAT"
    UNKNOWN = "UNKNOWN"
    """Fewer than two fresh readings, so no direction can be established."""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — THE CONSTRAINT CATALOGUE
# ═══════════════════════════════════════════════════════════════════════════
#
# Every constraint is named, versioned and carries the rule it applies, so a
# verdict can be traced to a statement a reviewer can disagree with. A constraint
# whose rule lives only in code is one an operator has to take on trust.

@dataclass(frozen=True)
class Constraint:
    """One physical constraint a hypothesis can satisfy or violate."""

    constraint_id: str
    family: CheckFamily
    statement: str
    """What must hold, in physical terms."""

    refutation_rule: str
    """Exactly what makes this FAIL. Stated separately from the statement because
    the asymmetry matters: the rule names the DECIDED evidence required, and
    anything short of it yields INDETERMINATE rather than FAIL."""

    caveat: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "family": self.family.value,
            "statement": self.statement,
            "refutation_rule": self.refutation_rule,
            "caveat": self.caveat,
        }


CONSTRAINTS: tuple[Constraint, ...] = (
    Constraint(
        constraint_id="PHYS_ACTUATOR_AUTHORITY",
        family=CheckFamily.ACTUATOR_FEASIBILITY,
        statement=(
            "A fault claiming the actuator has lost torque authority requires the "
            "body's response to fall short of what the actuator's own measured "
            "motion implies, by momentum exchange."
        ),
        refutation_rule=(
            "FAIL when the Gyro_rate_degs residual is CONSISTENT while the wheel "
            "produced a significant predicted body-rate change. A consistent rate "
            "residual means the body moved exactly as the wheel's speed change "
            "implies, so the wheel delivered its torque and a lost-authority "
            "claim is contradicted."
        ),
        caveat=(
            "Cannot separate a healthy wheel from an assumed inertia ratio that "
            "happens to compensate for a degraded one. The ratio is derived from "
            "the channel dictionary rather than measured — see "
            "app/estimation/parameters.py."
        ),
    ),
    Constraint(
        constraint_id="PHYS_ACTUATOR_SATURATION",
        family=CheckFamily.ACTUATOR_FEASIBILITY,
        statement=(
            "A fault claiming momentum saturation requires the wheel to be at or "
            "near its declared speed limit, since saturation is by definition the "
            "wheel having no capacity left to absorb torque."
        ),
        refutation_rule=(
            "FAIL when the wheel speed is observed well below its declared hard "
            "limit. A wheel at a third of its limit has ample capacity, so it "
            "cannot be saturated."
        ),
        caveat=(
            "One wheel is represented. A real vehicle distributes momentum across "
            "three or four, and an individual wheel could saturate while this "
            "single channel reads comfortably."
        ),
    ),
    Constraint(
        constraint_id="PHYS_MOMENTUM_ACCOUNTED",
        family=CheckFamily.PHYSICAL_CONSISTENCY,
        statement=(
            "A fault claiming an external disturbance torque requires body-rate "
            "change that the wheel's motion cannot account for. Angular momentum "
            "does not appear without a source."
        ),
        refutation_rule=(
            "FAIL when the Gyro_rate_degs residual is CONSISTENT. Momentum is "
            "then fully accounted for internally, leaving no unexplained torque "
            "for an external disturbance to supply."
        ),
    ),
    Constraint(
        constraint_id="PHYS_SENSOR_CORROBORATION",
        family=CheckFamily.SENSOR_CONSISTENCY,
        statement=(
            "A fault claiming the rate sensor is corrupt, biased or failed "
            "requires independent corroboration that the sensor disagrees with "
            "the vehicle — pointing error growing by more than the reported rate "
            "can explain."
        ),
        refutation_rule=(
            "FAIL when the Attitude_error_deg bound is satisfied AND the "
            "Gyro_rate_degs residual is CONSISTENT. Both independent checks then "
            "agree with the reported rate, so the sensor is corroborated rather "
            "than contradicted."
        ),
        caveat=(
            "A CONSTANT rate bias cancels out of the momentum-exchange residual "
            "and leaves the attitude bound generous, so this constraint cannot "
            "refute a constant-bias claim. It refutes only a claim of gross "
            "sensor failure against evidence of sensor agreement."
        ),
    ),
    Constraint(
        constraint_id="PHYS_ENERGY_BALANCE",
        family=CheckFamily.ENERGY_CONSISTENCY,
        statement=(
            "A fault claiming lost generation or lost storage capacity requires "
            "the stored-energy bookkeeping to show a gap: charge must move "
            "differently from measured generation minus modelled load."
        ),
        refutation_rule=(
            "FAIL when the SoC_pct residual is CONSISTENT. Stored energy is then "
            "tracking measured generation and modelled load, so neither "
            "generation nor storage is behaving anomalously."
        ),
        caveat=(
            "The state-of-charge prediction is inversely proportional to an "
            "ASSUMED battery capacity and uses a constant baseline load, so a "
            "load-mode change presents identically to an energy fault."
        ),
    ),
    Constraint(
        constraint_id="PHYS_ENERGY_DIRECTION",
        family=CheckFamily.STATE_TRANSITION_CONSISTENCY,
        statement=(
            "A fault whose causal chain drains the battery requires stored energy "
            "to be observed falling across the window."
        ),
        refutation_rule=(
            "FAIL when state of charge is observed RISING across the window. A "
            "battery gaining charge is not being drained."
        ),
        caveat=(
            "Reported state of charge is itself an onboard estimate on a real "
            "vehicle, with its own drift, so this compares a model against "
            "another model rather than against a direct measurement."
        ),
    ),
    Constraint(
        constraint_id="PHYS_HEAT_BALANCE",
        family=CheckFamily.THERMAL_CONSISTENCY,
        statement=(
            "A fault claiming excess heat input requires the thermal residual to "
            "show warming that measured heater power and modelled dissipation do "
            "not account for."
        ),
        refutation_rule=(
            "FAIL when the Component_temp_C residual is CONSISTENT. The node is "
            "then tracking the modelled heat balance, so there is no unexplained "
            "heat for the fault to have introduced."
        ),
        caveat=(
            "This model represents no illumination, so it cannot distinguish an "
            "unmodelled heat path from an eclipse transition."
        ),
    ),
    Constraint(
        constraint_id="PHYS_THERMAL_DIRECTION",
        family=CheckFamily.STATE_TRANSITION_CONSISTENCY,
        statement=(
            "A fault claiming a thermal runaway requires temperature to be "
            "observed rising across the window."
        ),
        refutation_rule=(
            "FAIL when the modelled node is observed FALLING across the window. "
            "A cooling component is not in runaway."
        ),
    ),
    Constraint(
        constraint_id="PHYS_TELEMETRY_OVERLAP",
        family=CheckFamily.TELEMETRY_CONSISTENCY,
        statement=(
            "For physics to bear on a hypothesis at all, at least one channel the "
            "hypothesis claims is affected must be a channel the models actually "
            "predict."
        ),
        refutation_rule=(
            "Never FAILs. A hypothesis about channels no model covers is outside "
            "this layer's reach, which makes every family NOT_APPLICABLE and the "
            "verdict UNCERTAIN. Reporting FAIL here would turn the model's own "
            "coverage gap into evidence against the spacecraft."
        ),
        caveat=(
            "The models predict five channels: Gyro_rate_degs, "
            "Attitude_error_deg, SoC_pct, V_bat and Component_temp_C. OBC and "
            "COMMS faults have no physics coverage whatsoever, and their verdicts "
            "are UNCERTAIN by construction rather than by evidence."
        ),
    ),
)

CONSTRAINTS_BY_ID: dict[str, Constraint] = {
    c.constraint_id: c for c in CONSTRAINTS
}


#: Constraints that are ALTERNATIVE mechanisms for one fault rather than
#: conjoint requirements. A fault claiming several members of a group is refuted
#: only when EVERY applicable member of that group fails.
#:
#: Without this the aggregation is wrong, and measurably so.
#: ``AOCS_REACTION_WHEEL_DEGRADATION`` is named "reaction wheel degradation OR
#: saturation": a wheel losing torque authority and a wheel running out of
#: momentum capacity are different mechanisms with the same symptom. Treating them
#: conjointly meant a wheel comfortably below its speed limit refuted the
#: saturation half and therefore rejected the whole fault — including on telemetry
#: where the body was demonstrably under-responding to the wheel, which is the
#: degradation half being CORROBORATED. Refuting one alternative says nothing
#: about the other.
ALTERNATIVE_MECHANISM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"PHYS_ACTUATOR_AUTHORITY", "PHYS_ACTUATOR_SATURATION"}),
)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — WHAT EACH FAULT CLAIMS
# ═══════════════════════════════════════════════════════════════════════════
#
# The bridge from Phase 6's fault dictionary to the constraints above. Explicit
# and declarative: a fault is checked against a constraint only where this table
# says the fault makes that claim. Nothing is inferred from a fault's name, its
# subsystem or its prose, because a physics verdict derived from a name would be a
# guess wearing the authority of a measurement.
#
# A fault absent from this table receives no physics checks and returns UNCERTAIN,
# which is the honest result for a fault whose claims nobody has expressed in
# physical terms yet.

@dataclass(frozen=True)
class PhysicsClaims:
    """The physical claims one fault makes, in checkable form."""

    fault_id: str

    claims_lost_actuator_authority: bool = False
    claims_actuator_saturation: bool = False
    claims_external_torque: bool = False
    claims_rate_sensor_untrustworthy: bool = False
    claims_energy_shortfall: bool = False
    claims_battery_drain: bool = False
    claims_excess_heat: bool = False
    claims_thermal_runaway: bool = False

    rationale: str = ""
    """Why these are the fault's physical claims, in the fault's own terms."""

    def constraint_ids(self) -> tuple[str, ...]:
        """Constraints that apply to this fault, in catalogue order."""
        applicable: list[str] = []
        if self.claims_lost_actuator_authority:
            applicable.append("PHYS_ACTUATOR_AUTHORITY")
        if self.claims_actuator_saturation:
            applicable.append("PHYS_ACTUATOR_SATURATION")
        if self.claims_external_torque:
            applicable.append("PHYS_MOMENTUM_ACCOUNTED")
        if self.claims_rate_sensor_untrustworthy:
            applicable.append("PHYS_SENSOR_CORROBORATION")
        if self.claims_energy_shortfall:
            applicable.append("PHYS_ENERGY_BALANCE")
        if self.claims_battery_drain:
            applicable.append("PHYS_ENERGY_DIRECTION")
        if self.claims_excess_heat:
            applicable.append("PHYS_HEAT_BALANCE")
        if self.claims_thermal_runaway:
            applicable.append("PHYS_THERMAL_DIRECTION")
        return tuple(applicable)

    def as_dict(self) -> dict[str, Any]:
        return {
            "fault_id": self.fault_id,
            "claims_lost_actuator_authority":
                self.claims_lost_actuator_authority,
            "claims_actuator_saturation": self.claims_actuator_saturation,
            "claims_external_torque": self.claims_external_torque,
            "claims_rate_sensor_untrustworthy":
                self.claims_rate_sensor_untrustworthy,
            "claims_energy_shortfall": self.claims_energy_shortfall,
            "claims_battery_drain": self.claims_battery_drain,
            "claims_excess_heat": self.claims_excess_heat,
            "claims_thermal_runaway": self.claims_thermal_runaway,
            "applicable_constraints": list(self.constraint_ids()),
            "rationale": self.rationale,
        }


_CLAIMS: tuple[PhysicsClaims, ...] = (
    PhysicsClaims(
        fault_id="AOCS_REACTION_WHEEL_DEGRADATION",
        claims_lost_actuator_authority=True,
        claims_actuator_saturation=True,
        rationale=(
            "The fault's own description is that the wheel loses torque "
            "authority or saturates, so pointing degrades while the sensors stay "
            "healthy. Both halves are checkable: lost authority against the "
            "momentum-exchange residual, saturation against the declared wheel "
            "speed limit."
        ),
    ),
    PhysicsClaims(
        fault_id="AOCS_EXTERNAL_DISTURBANCE",
        claims_external_torque=True,
        rationale=(
            "The fault asserts a torque from outside the vehicle. That is exactly "
            "the quantity an unexplained body-rate residual implies, so the "
            "residual is direct evidence for or against it."
        ),
    ),
    PhysicsClaims(
        fault_id="AOCS_SENSOR_FAULT",
        claims_rate_sensor_untrustworthy=True,
        rationale=(
            "The fault asserts an attitude sensor is not delivering a valid "
            "solution, and that the reported pointing error may be an artefact "
            "rather than real motion. That is a claim about sensor "
            "trustworthiness, which the attitude-error bound can corroborate."
        ),
    ),
    PhysicsClaims(
        fault_id="ADCS_GYRO_SEU",
        claims_rate_sensor_untrustworthy=True,
        rationale=(
            "A radiation upset corrupting rate data is a claim that the rate "
            "channel cannot be trusted. Note the limit: the upset's primary "
            "evidence is the SEU counter, which is a Phase 6 signature and not a "
            "physical residual, so physics can weigh in on the sensor claim but "
            "not on the radiation cause."
        ),
    ),
    PhysicsClaims(
        fault_id="AOCS_GYRO_BIAS_DRIFT",
        claims_rate_sensor_untrustworthy=True,
        rationale=(
            "Bias drift is a claim that the reported rate is wrong while "
            "remaining a valid number. Physics reach is genuinely weak here: a "
            "constant bias cancels out of the momentum residual entirely, so the "
            "attitude bound is the only available check and it detects "
            "under-reporting only."
        ),
    ),
    PhysicsClaims(
        fault_id="EPS_SOLAR_UNDERVOLT",
        claims_energy_shortfall=True,
        claims_battery_drain=True,
        rationale=(
            "The fault's causal chain is that the array stops delivering while "
            "illuminated, the battery carries the whole load, and charge falls. "
            "Both the energy gap and the direction of charge movement are "
            "checkable."
        ),
    ),
    PhysicsClaims(
        fault_id="EPS_BATTERY_DEGRADATION",
        claims_energy_shortfall=True,
        claims_battery_drain=True,
        rationale=(
            "The fault asserts the battery no longer holds its rated charge, so "
            "the bus sags under load with generation healthy. That is an energy "
            "bookkeeping claim and a claim about the direction of charge."
        ),
    ),
    PhysicsClaims(
        fault_id="TCS_THERMAL_RUNAWAY",
        claims_excess_heat=True,
        claims_thermal_runaway=True,
        rationale=(
            "A stuck heater or a lost radiator path is a claim of heat input the "
            "modelled balance does not account for, and the fault is named for a "
            "rising temperature. Both are checkable against the thermal node."
        ),
    ),
    # ── Faults deliberately given NO physics claims ───────────────────────
    #
    # Recorded here with the reason rather than omitted, so their UNCERTAIN
    # verdict is visibly a coverage decision and not an oversight.
    PhysicsClaims(
        fault_id="OBC_WATCHDOG_OVERFLOW",
        rationale=(
            "NO PHYSICS COVERAGE. The fault concerns CPU load, memory and a "
            "watchdog counter. None is a physical state variable, no model "
            "predicts any of them, and there is no conservation law to check them "
            "against. Its verdict is UNCERTAIN by construction."
        ),
    ),
    PhysicsClaims(
        fault_id="COMMS_TRANSPONDER_LOSS",
        rationale=(
            "NO PHYSICS COVERAGE. Validating a link claim needs a link budget — "
            "antenna gain patterns, slant range, pointing geometry — none of which "
            "exists in this repository. Phase 7 states the same omission for its "
            "communication state. Verdict UNCERTAIN by construction."
        ),
    ),
    PhysicsClaims(
        fault_id="AOCS_CONTROL_COMMAND_ANOMALY",
        rationale=(
            "NO PHYSICS COVERAGE. The fault's distinguishing feature is that the "
            "hardware is behaving CORRECTLY — the wheels faithfully execute an "
            "erroneous command. The physics is therefore consistent by "
            "construction, and checking it would corroborate every such "
            "hypothesis without discriminating. Separating it needs the commanded "
            "attitude, which telemetry does not carry."
        ),
    ),
    PhysicsClaims(
        fault_id="MULTI_CASCADE",
        rationale=(
            "NO PHYSICS COVERAGE as a single hypothesis. A cascade asserts a "
            "relationship BETWEEN subsystems rather than a single mechanism, so "
            "there is no one constraint it either satisfies or violates. The "
            "cross-subsystem test lives in Phase 6 propagation, and the residuals "
            "of the individual subsystems are reported against the specific "
            "faults instead."
        ),
    ),
)

CLAIMS_BY_FAULT: dict[str, PhysicsClaims] = {c.fault_id: c for c in _CLAIMS}


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — THE VERDICT CONTRACT
# ═══════════════════════════════════════════════════════════════════════════

class ConstraintCheck(BaseModel):
    """One constraint evaluated against one hypothesis."""

    model_config = ConfigDict(frozen=True)

    constraint_id: str
    family: CheckFamily
    outcome: CheckOutcome
    statement: str
    detail: str = Field(
        ...,
        description="What the evidence showed, in this hypothesis's terms",
    )
    channels: list[str] = Field(default_factory=list)
    residual_refs: list[str] = Field(
        default_factory=list,
        description="Channels whose residuals this check rests on",
    )
    caveat: Optional[str] = None


class ResidualRef(BaseModel):
    """A compact reference to one Phase 7 residual supporting a verdict."""

    model_config = ConfigDict(frozen=True)

    channel: str
    status: str
    observed: Optional[float] = None
    predicted: Optional[float] = None
    residual: Optional[float] = None
    tolerance: Optional[float] = None
    unit: str = ""
    from_timestamp: str = ""
    to_timestamp: str = ""
    equation: str = ""


class PhysicsVerdict(BaseModel):
    """The Phase 8 result for one hypothesis, with every required field."""

    model_config = ConfigDict(frozen=True)

    hypothesis_id: str
    validation_status: PhysicsStatus
    violated_constraints: list[str] = Field(
        default_factory=list,
        description=(
            "Every constraint_id that FAILED. Non-empty does NOT imply INVALID: "
            "where a fault declares several alternative mechanisms, refuting one "
            "leaves the others standing. Read ``refuted_by`` for the failures "
            "that actually drove the verdict."
        ),
    )
    refuted_by: list[str] = Field(
        default_factory=list,
        description=(
            "The subset of violated_constraints that actually refuted the "
            "hypothesis. Empty unless validation_status is INVALID. Exists "
            "because a failed alternative mechanism is a real finding worth "
            "reporting without being a rejection."
        ),
    )
    supporting_residuals: list[ResidualRef] = Field(
        default_factory=list,
        description="The Phase 7 residuals this verdict rests on",
    )
    relevant_channels: list[str] = Field(default_factory=list)
    timestamps: list[str] = Field(
        default_factory=list,
        description="Telemetry offsets the supporting residuals span",
    )
    explanation: str
    model_version: str = Field(
        ...,
        description=(
            "Composite version of every model the verdict depends on: the "
            "constraint set, the Phase 7 parameter set, and the Phase 6 fault "
            "dictionary. A verdict is only comparable within one composite."
        ),
    )

    # ── context beyond the required fields ────────────────────────────
    fault_id: str = ""
    fault_name: str = ""
    subsystem: str = ""
    checks: list[ConstraintCheck] = Field(default_factory=list)
    corroborated_constraints: list[str] = Field(default_factory=list)
    indeterminate_constraints: list[str] = Field(default_factory=list)
    applicable_constraints: list[str] = Field(default_factory=list)
    has_physics_coverage: bool = True
    claims_rationale: str = ""
    caveats: list[str] = Field(default_factory=list)
    verdict_basis: str = Field(
        default=(
            "Deterministic evaluation of declared constraints against Phase 7 "
            "residuals. No language model is consulted. INVALID requires a "
            "DECIDED contradiction; a missing corroboration yields UNCERTAIN."
        ),
    )

    @property
    def is_refuted(self) -> bool:
        return self.validation_status is PhysicsStatus.INVALID


class PhysicsValidationReport(BaseModel):
    """Verdicts for a whole hypothesis set."""

    model_config = ConfigDict(frozen=True)

    verdicts: list[PhysicsVerdict] = Field(default_factory=list)
    model_version: str = ""
    constraint_set_version: str = PHYSICS_CONSTRAINT_SET_VERSION

    hypotheses_examined: int = 0
    invalidated: list[str] = Field(
        default_factory=list, description="fault_ids ruled out by physics",
    )
    validated: list[str] = Field(default_factory=list)
    uncertain: list[str] = Field(default_factory=list)

    uses_llm: bool = False
    deterministic: bool = True
    flight_qualified: bool = False
    assumed_parameters: list[dict[str, Any]] = Field(default_factory=list)
    model_limitations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: str = ""
    claim: str = Field(
        default=(
            "Physics validation compares each hypothesis against the SIMPLIFIED "
            "models in app/estimation. An INVALID verdict means the hypothesis is "
            "inconsistent with those models and their stated assumptions, four of "
            "which are chosen rather than derived. UNCERTAIN is not a pass. VALID "
            "means not refuted and corroborated at least once, not confirmed."
        ),
    )

    def verdict_for(self, hypothesis_id: str) -> Optional[PhysicsVerdict]:
        for verdict in self.verdicts:
            if verdict.hypothesis_id == hypothesis_id:
                return verdict
        return None

    def verdict_for_fault(self, fault_id: object) -> Optional[PhysicsVerdict]:
        name = "" if fault_id is None else str(fault_id).strip().upper()
        for verdict in self.verdicts:
            if verdict.fault_id == name:
                return verdict
        return None

    def status_for_fault(self, fault_id: object) -> PhysicsStatus:
        """Status for a fault, UNCERTAIN when it was never examined.

        UNCERTAIN rather than VALID for an unexamined fault: a fault physics never
        looked at has not passed anything.
        """
        verdict = self.verdict_for_fault(fault_id)
        return verdict.validation_status if verdict else PhysicsStatus.UNCERTAIN


def model_version() -> str:
    """Composite version of every model a verdict depends on."""
    from app.diagnosis.fault_dictionary import FAULT_DICT_VERSION
    from app.estimation.parameters import PARAMETER_SET_VERSION
    from app.estimation.residuals import RESIDUAL_SCHEMA_VERSION

    return (
        f"physics/{PHYSICS_CONSTRAINT_SET_VERSION}"
        f"+params/{PARAMETER_SET_VERSION}"
        f"+residuals/{RESIDUAL_SCHEMA_VERSION}"
        f"+faults/{FAULT_DICT_VERSION}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — EVIDENCE HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _residual_index(residual_report: Any) -> dict[str, Any]:
    return {r.channel: r for r in getattr(residual_report, "residuals", [])}


def _to_ref(residual: Any) -> ResidualRef:
    return ResidualRef(
        channel=residual.channel,
        status=residual.status.value,
        observed=residual.observed,
        predicted=residual.predicted,
        residual=residual.residual,
        tolerance=residual.tolerance,
        unit=residual.unit,
        from_timestamp=residual.from_timestamp,
        to_timestamp=residual.to_timestamp,
        equation=residual.equation,
    )


def observed_trend(sequence: Any, channel: str,
                   flat_fraction: float = 0.02) -> tuple[Trend, list[float]]:
    """Direction a channel moved across its FRESH readings.

    Uses fresh readings only. A carried-forward value repeats an earlier
    measurement, so including it would manufacture a flat stretch that never
    happened.

    ``flat_fraction`` is the share of the channel's declared nominal span below
    which a change counts as no movement. Derived from the channel dictionary
    rather than an absolute number, so the sensitivity scales with the channel.
    """
    if sequence is None:
        return Trend.UNKNOWN, []

    try:
        states = sequence.fresh_states_for(channel)
    except Exception:  # pragma: no cover — sequence is in-tree
        return Trend.UNKNOWN, []

    from app.estimation.residuals import _observed_value

    values = [
        value for value in (_observed_value(s, channel) for s in states)
        if value is not None
    ]
    if len(values) < 2:
        return Trend.UNKNOWN, values

    from app.ingest.channel_dict import nominal_range

    low, high = nominal_range(channel)
    if low is None or high is None:
        threshold = 0.0
    else:
        threshold = flat_fraction * abs(float(high) - float(low))

    delta = values[-1] - values[0]
    if abs(delta) <= threshold:
        return Trend.FLAT, values
    return (Trend.RISING if delta > 0 else Trend.FALLING), values


def _wheel_saturation(sequence: Any) -> tuple[Optional[float], Optional[float]]:
    """Latest observed wheel saturation fraction and speed, or (None, None)."""
    if sequence is None:
        return None, None
    try:
        states = sequence.fresh_states_for("RW_speed_rpm")
    except Exception:  # pragma: no cover
        return None, None
    for state in reversed(states):
        wheel = state.reaction_wheel_state
        if wheel.saturation_fraction.is_usable and wheel.speed_rpm.is_usable:
            return wheel.saturation_fraction.value, wheel.speed_rpm.value
    return None, None


#: Wheel saturation below this fraction of the declared limit refutes a
#: saturation claim. A SENTINEL threshold, not a vehicle specification: it is
#: stated here and reported in ``physics_status()`` so it can be argued with.
#: Set well clear of the limit on purpose — the claim being refuted is that the
#: wheel has NO capacity left, and a wheel at half its limit plainly has some.
SATURATION_REFUTATION_CEILING = 0.5


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6 — THE CHECKS
# ═══════════════════════════════════════════════════════════════════════════

def _check(constraint_id: str, outcome: CheckOutcome, detail: str,
           channels: tuple[str, ...] = (),
           residual_refs: tuple[str, ...] = ()) -> ConstraintCheck:
    constraint = CONSTRAINTS_BY_ID[constraint_id]
    return ConstraintCheck(
        constraint_id=constraint_id,
        family=constraint.family,
        outcome=outcome,
        statement=constraint.statement,
        detail=detail,
        channels=list(channels),
        residual_refs=list(residual_refs),
        caveat=constraint.caveat,
    )


def _indeterminate_for(constraint_id: str, channel: str,
                       residual: Any) -> ConstraintCheck:
    """Build the INDETERMINATE result for an undecidable residual."""
    if residual is None:
        detail = (
            f"{channel} produced no residual in this window, so the constraint "
            f"could not be evaluated. No physical claim is made."
        )
    else:
        detail = (
            f"the {channel} residual is UNDECIDABLE "
            f"({residual.undecidable_reason or 'no reason recorded'}), so the "
            f"constraint could not be evaluated. This is NOT a pass."
        )
    return _check(constraint_id, CheckOutcome.INDETERMINATE, detail,
                  channels=(channel,), residual_refs=(channel,))


def _check_actuator_authority(residuals: dict[str, Any]) -> ConstraintCheck:
    from app.estimation.residuals import ResidualStatus

    residual = residuals.get("Gyro_rate_degs")
    if residual is None or residual.status is ResidualStatus.UNDECIDABLE:
        return _indeterminate_for("PHYS_ACTUATOR_AUTHORITY", "Gyro_rate_degs",
                                  residual)

    wheel_effect = residual.extras.get("body_rate_change_from_wheel_deg_s")
    tolerance = residual.tolerance or 0.0
    wheel_moved = (
        wheel_effect is not None and abs(wheel_effect) > tolerance
    )

    if residual.status is ResidualStatus.CONSISTENT and wheel_moved:
        return _check(
            "PHYS_ACTUATOR_AUTHORITY", CheckOutcome.FAIL,
            (
                f"the wheel changed speed enough to demand a "
                f"{wheel_effect:+.4f} deg/s body-rate change, and the body "
                f"delivered it: observed {residual.observed:.4f} deg/s against a "
                f"predicted {residual.predicted:.4f} deg/s, residual "
                f"{residual.residual:+.4f} within the {tolerance:.4f} tolerance. "
                f"The wheel therefore produced the torque its own motion "
                f"implies, which contradicts a claim of lost torque authority."
            ),
            channels=("RW_speed_rpm", "Gyro_rate_degs"),
            residual_refs=("Gyro_rate_degs",),
        )

    if residual.status is ResidualStatus.CONSISTENT and not wheel_moved:
        return _check(
            "PHYS_ACTUATOR_AUTHORITY", CheckOutcome.INDETERMINATE,
            (
                "the rate residual is consistent, but the wheel produced no "
                "significant predicted body-rate change across this step, so "
                "there was no actuator demand to under-deliver on. A wheel that "
                "was never asked for torque cannot be shown to have lost "
                "authority."
            ),
            channels=("RW_speed_rpm", "Gyro_rate_degs"),
            residual_refs=("Gyro_rate_degs",),
        )

    # INCONSISTENT. Corroborating only when the body UNDER-responded, which is
    # what lost authority looks like; over-response is a different fault.
    under_response = (
        wheel_effect is not None and residual.residual is not None
        and (residual.residual * wheel_effect) < 0.0
    )
    if under_response:
        return _check(
            "PHYS_ACTUATOR_AUTHORITY", CheckOutcome.PASS,
            (
                f"the wheel demanded a {wheel_effect:+.4f} deg/s body-rate "
                f"change and the body fell short of it: observed "
                f"{residual.observed:.4f} deg/s against a predicted "
                f"{residual.predicted:.4f} deg/s. The shortfall is consistent "
                f"with the wheel not delivering its implied torque."
            ),
            channels=("RW_speed_rpm", "Gyro_rate_degs"),
            residual_refs=("Gyro_rate_degs",),
        )

    return _check(
        "PHYS_ACTUATOR_AUTHORITY", CheckOutcome.INDETERMINATE,
        (
            "the rate residual is inconsistent, but not in the direction a lost "
            "actuator authority produces: the body did not under-respond to the "
            "wheel's motion. The discrepancy points somewhere other than "
            "actuator authority."
        ),
        channels=("RW_speed_rpm", "Gyro_rate_degs"),
        residual_refs=("Gyro_rate_degs",),
    )


def _check_actuator_saturation(sequence: Any) -> ConstraintCheck:
    saturation, speed = _wheel_saturation(sequence)
    if saturation is None:
        return _check(
            "PHYS_ACTUATOR_SATURATION", CheckOutcome.INDETERMINATE,
            (
                "no fresh reaction wheel speed reading in this window, so "
                "saturation could not be assessed."
            ),
            channels=("RW_speed_rpm",),
        )

    if saturation < SATURATION_REFUTATION_CEILING:
        return _check(
            "PHYS_ACTUATOR_SATURATION", CheckOutcome.FAIL,
            (
                f"the wheel is at {speed:.0f} rpm, which is "
                f"{saturation * 100:.1f}% of its declared limit and below the "
                f"{SATURATION_REFUTATION_CEILING * 100:.0f}% refutation "
                f"ceiling. A wheel with that much capacity left is not "
                f"saturated."
            ),
            channels=("RW_speed_rpm",),
        )

    return _check(
        "PHYS_ACTUATOR_SATURATION", CheckOutcome.PASS,
        (
            f"the wheel is at {speed:.0f} rpm, {saturation * 100:.1f}% of its "
            f"declared limit, which leaves little capacity to absorb further "
            f"torque and is consistent with approaching saturation."
        ),
        channels=("RW_speed_rpm",),
    )


def _check_momentum_accounted(residuals: dict[str, Any]) -> ConstraintCheck:
    from app.estimation.residuals import ResidualStatus

    residual = residuals.get("Gyro_rate_degs")
    if residual is None or residual.status is ResidualStatus.UNDECIDABLE:
        return _indeterminate_for("PHYS_MOMENTUM_ACCOUNTED", "Gyro_rate_degs",
                                  residual)

    if residual.status is ResidualStatus.CONSISTENT:
        return _check(
            "PHYS_MOMENTUM_ACCOUNTED", CheckOutcome.FAIL,
            (
                f"body rate is fully accounted for by wheel motion: observed "
                f"{residual.observed:.4f} deg/s against a predicted "
                f"{residual.predicted:.4f} deg/s, residual "
                f"{residual.residual:+.4f} within the "
                f"{residual.tolerance:.4f} tolerance. There is no unexplained "
                f"momentum for an external torque to have supplied."
            ),
            channels=("Gyro_rate_degs", "RW_speed_rpm"),
            residual_refs=("Gyro_rate_degs",),
        )

    torque = residual.extras.get("actuator_torque_Nm")
    return _check(
        "PHYS_MOMENTUM_ACCOUNTED", CheckOutcome.PASS,
        (
            f"body rate is NOT accounted for by wheel motion: observed "
            f"{residual.observed:.4f} deg/s against a predicted "
            f"{residual.predicted:.4f} deg/s, residual "
            f"{residual.residual:+.4f} against a {residual.tolerance:.4f} "
            f"tolerance. Momentum arrived from somewhere the model does not "
            f"represent, which is what an external torque means."
            + (f" Wheel torque over the step was {torque:+.5f} N*m."
               if isinstance(torque, (int, float)) else "")
        ),
        channels=("Gyro_rate_degs", "RW_speed_rpm"),
        residual_refs=("Gyro_rate_degs",),
    )


def _check_sensor_corroboration(residuals: dict[str, Any]) -> ConstraintCheck:
    from app.estimation.residuals import ResidualStatus

    rate = residuals.get("Gyro_rate_degs")
    error = residuals.get("Attitude_error_deg")

    rate_decided = rate is not None and rate.status.is_decided
    error_decided = error is not None and error.status.is_decided

    if not error_decided and not rate_decided:
        return _check(
            "PHYS_SENSOR_CORROBORATION", CheckOutcome.INDETERMINATE,
            (
                "neither the rate residual nor the attitude-error bound could be "
                "decided in this window, so the rate sensor was neither "
                "corroborated nor contradicted. This is NOT a pass."
            ),
            channels=("Gyro_rate_degs", "Attitude_error_deg"),
            residual_refs=("Gyro_rate_degs", "Attitude_error_deg"),
        )

    # The one positive check available: pointing error exceeding the open-loop
    # bound means the vehicle turned further than the reported rate accounts for.
    if error_decided and error.status is ResidualStatus.INCONSISTENT:
        return _check(
            "PHYS_SENSOR_CORROBORATION", CheckOutcome.PASS,
            (
                f"pointing error reached {error.observed:.4f} deg against an "
                f"open-loop ceiling of {error.predicted:.4f} deg obtained by "
                f"integrating the reported body rate. The vehicle turned further "
                f"than the rate sensor accounts for, which independently "
                f"corroborates that the sensor is under-reporting."
            ),
            channels=("Attitude_error_deg", "Gyro_rate_degs"),
            residual_refs=("Attitude_error_deg",),
        )

    both_agree = (
        error_decided and error.status is ResidualStatus.CONSISTENT
        and rate_decided and rate.status is ResidualStatus.CONSISTENT
    )
    if both_agree:
        return _check(
            "PHYS_SENSOR_CORROBORATION", CheckOutcome.FAIL,
            (
                f"both independent checks agree with the reported rate: the "
                f"momentum-exchange residual is within tolerance "
                f"({rate.residual:+.4f} against {rate.tolerance:.4f}), and "
                f"pointing error at {error.observed:.4f} deg sits inside its "
                f"{error.predicted:.4f} deg open-loop ceiling. The rate sensor "
                f"is corroborated, which contradicts a claim that it cannot be "
                f"trusted."
            ),
            channels=("Gyro_rate_degs", "Attitude_error_deg"),
            residual_refs=("Gyro_rate_degs", "Attitude_error_deg"),
        )

    return _check(
        "PHYS_SENSOR_CORROBORATION", CheckOutcome.INDETERMINATE,
        (
            "only one of the two independent checks could be decided, which is "
            "not enough to either corroborate or contradict the rate sensor. A "
            "single agreeing check is consistent with a sensor bias that cancels "
            "out of it."
        ),
        channels=("Gyro_rate_degs", "Attitude_error_deg"),
        residual_refs=("Gyro_rate_degs", "Attitude_error_deg"),
    )


def _check_energy_balance(residuals: dict[str, Any]) -> ConstraintCheck:
    from app.estimation.residuals import ResidualStatus

    residual = residuals.get("SoC_pct")
    if residual is None or residual.status is ResidualStatus.UNDECIDABLE:
        return _indeterminate_for("PHYS_ENERGY_BALANCE", "SoC_pct", residual)

    if residual.status is ResidualStatus.CONSISTENT:
        return _check(
            "PHYS_ENERGY_BALANCE", CheckOutcome.FAIL,
            (
                f"stored energy is tracking the measured generation and modelled "
                f"load: observed {residual.observed:.3f}% against a predicted "
                f"{residual.predicted:.3f}%, residual {residual.residual:+.3f} "
                f"within the {residual.tolerance:.3f} tolerance. Neither "
                f"generation nor storage is behaving anomalously, which "
                f"contradicts a claim of energy shortfall."
            ),
            channels=("SoC_pct", "I_sa", "V_bus"),
            residual_refs=("SoC_pct",),
        )

    net_power = residual.extras.get("net_power_W")
    return _check(
        "PHYS_ENERGY_BALANCE", CheckOutcome.PASS,
        (
            f"stored energy is NOT tracking the energy balance: observed "
            f"{residual.observed:.3f}% against a predicted "
            f"{residual.predicted:.3f}%, residual {residual.residual:+.3f} "
            f"against a {residual.tolerance:.3f} tolerance"
            + (f", with modelled net power {net_power:+.1f} W"
               if isinstance(net_power, (int, float)) else "")
            + ". There is an energy gap, consistent with the claim."
        ),
        channels=("SoC_pct", "I_sa", "V_bus"),
        residual_refs=("SoC_pct",),
    )


def _check_energy_direction(sequence: Any) -> ConstraintCheck:
    trend, values = observed_trend(sequence, "SoC_pct")

    if trend is Trend.UNKNOWN:
        return _check(
            "PHYS_ENERGY_DIRECTION", CheckOutcome.INDETERMINATE,
            (
                "fewer than two fresh state-of-charge readings in this window, so "
                "no direction of charge movement could be established."
            ),
            channels=("SoC_pct",),
        )

    if trend is Trend.RISING:
        return _check(
            "PHYS_ENERGY_DIRECTION", CheckOutcome.FAIL,
            (
                f"state of charge ROSE across the window, from "
                f"{values[0]:.2f}% to {values[-1]:.2f}%. A battery gaining "
                f"charge is not being drained, which contradicts the fault's "
                f"causal chain."
            ),
            channels=("SoC_pct",),
        )

    if trend is Trend.FALLING:
        return _check(
            "PHYS_ENERGY_DIRECTION", CheckOutcome.PASS,
            (
                f"state of charge FELL across the window, from "
                f"{values[0]:.2f}% to {values[-1]:.2f}%, which is the direction "
                f"the fault's causal chain requires."
            ),
            channels=("SoC_pct",),
        )

    return _check(
        "PHYS_ENERGY_DIRECTION", CheckOutcome.INDETERMINATE,
        (
            f"state of charge was effectively flat across the window "
            f"({values[0]:.2f}% to {values[-1]:.2f}%), which neither supports "
            f"nor refutes a drain."
        ),
        channels=("SoC_pct",),
    )


def _check_heat_balance(residuals: dict[str, Any]) -> ConstraintCheck:
    from app.estimation.residuals import ResidualStatus

    residual = residuals.get("Component_temp_C")
    if residual is None or residual.status is ResidualStatus.UNDECIDABLE:
        return _indeterminate_for("PHYS_HEAT_BALANCE", "Component_temp_C",
                                  residual)

    if residual.status is ResidualStatus.CONSISTENT:
        return _check(
            "PHYS_HEAT_BALANCE", CheckOutcome.FAIL,
            (
                f"the thermal node is tracking the modelled heat balance: "
                f"observed {residual.observed:.2f} degC against a predicted "
                f"{residual.predicted:.2f} degC, residual "
                f"{residual.residual:+.2f} K within the "
                f"{residual.tolerance:.2f} K tolerance. Measured heater power "
                f"and modelled dissipation account for the temperature, so there "
                f"is no unexplained heat input."
            ),
            channels=("Component_temp_C", "Heater_power_W"),
            residual_refs=("Component_temp_C",),
        )

    return _check(
        "PHYS_HEAT_BALANCE", CheckOutcome.PASS,
        (
            f"the thermal node is NOT tracking the modelled heat balance: "
            f"observed {residual.observed:.2f} degC against a predicted "
            f"{residual.predicted:.2f} degC, residual {residual.residual:+.2f} K "
            f"against a {residual.tolerance:.2f} K tolerance. Heat is arriving "
            f"from a path the model does not represent, consistent with the claim."
        ),
        channels=("Component_temp_C", "Heater_power_W"),
        residual_refs=("Component_temp_C",),
    )


def _check_thermal_direction(sequence: Any) -> ConstraintCheck:
    trend, values = observed_trend(sequence, "Component_temp_C")

    if trend is Trend.UNKNOWN:
        return _check(
            "PHYS_THERMAL_DIRECTION", CheckOutcome.INDETERMINATE,
            (
                "fewer than two fresh component temperature readings in this "
                "window, so no thermal direction could be established."
            ),
            channels=("Component_temp_C",),
        )

    if trend is Trend.FALLING:
        return _check(
            "PHYS_THERMAL_DIRECTION", CheckOutcome.FAIL,
            (
                f"the modelled node COOLED across the window, from "
                f"{values[0]:.2f} degC to {values[-1]:.2f} degC. A cooling "
                f"component is not in thermal runaway."
            ),
            channels=("Component_temp_C",),
        )

    if trend is Trend.RISING:
        return _check(
            "PHYS_THERMAL_DIRECTION", CheckOutcome.PASS,
            (
                f"the modelled node WARMED across the window, from "
                f"{values[0]:.2f} degC to {values[-1]:.2f} degC, which is the "
                f"direction a runaway requires."
            ),
            channels=("Component_temp_C",),
        )

    return _check(
        "PHYS_THERMAL_DIRECTION", CheckOutcome.INDETERMINATE,
        (
            f"component temperature was effectively flat across the window "
            f"({values[0]:.2f} to {values[-1]:.2f} degC), which neither supports "
            f"nor refutes a runaway."
        ),
        channels=("Component_temp_C",),
    )


#: Constraint id -> the function that evaluates it. Explicit so a constraint
#: cannot be declared without an evaluator, which ``validate_physics_layer()``
#: checks — an unevaluated constraint would silently never fire.
_EVALUATORS: dict[str, str] = {
    "PHYS_ACTUATOR_AUTHORITY": "residuals",
    "PHYS_ACTUATOR_SATURATION": "sequence",
    "PHYS_MOMENTUM_ACCOUNTED": "residuals",
    "PHYS_SENSOR_CORROBORATION": "residuals",
    "PHYS_ENERGY_BALANCE": "residuals",
    "PHYS_ENERGY_DIRECTION": "sequence",
    "PHYS_HEAT_BALANCE": "residuals",
    "PHYS_THERMAL_DIRECTION": "sequence",
    "PHYS_TELEMETRY_OVERLAP": "applicability",
}


def _evaluate(constraint_id: str, residuals: dict[str, Any],
              sequence: Any) -> ConstraintCheck:
    if constraint_id == "PHYS_ACTUATOR_AUTHORITY":
        return _check_actuator_authority(residuals)
    if constraint_id == "PHYS_ACTUATOR_SATURATION":
        return _check_actuator_saturation(sequence)
    if constraint_id == "PHYS_MOMENTUM_ACCOUNTED":
        return _check_momentum_accounted(residuals)
    if constraint_id == "PHYS_SENSOR_CORROBORATION":
        return _check_sensor_corroboration(residuals)
    if constraint_id == "PHYS_ENERGY_BALANCE":
        return _check_energy_balance(residuals)
    if constraint_id == "PHYS_ENERGY_DIRECTION":
        return _check_energy_direction(sequence)
    if constraint_id == "PHYS_HEAT_BALANCE":
        return _check_heat_balance(residuals)
    if constraint_id == "PHYS_THERMAL_DIRECTION":
        return _check_thermal_direction(sequence)
    raise KeyError(  # pragma: no cover — guarded by validate_physics_layer
        f"no evaluator for constraint {constraint_id}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7 — VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

def validate_hypothesis(
    hypothesis: Any,
    residual_report: Any,
    state_sequence: Any = None,
) -> PhysicsVerdict:
    """Validate one hypothesis against the simplified state model.

    Deterministic and model-free: the arguments are a Phase 6 hypothesis, a Phase
    7 residual report and a Phase 7 state sequence. There is no parameter through
    which a language model could influence the verdict.

    Status rule, and the asymmetry is deliberate:

        INVALID    at least one applicable constraint FAILED, from decided
                   evidence
        VALID      nothing failed and at least one constraint PASSED
        UNCERTAIN  nothing failed and nothing passed

    A missing corroboration therefore never produces INVALID. Crash-dump
    telemetry is routinely too sparse for a residual to be decided, and treating
    silence as refutation would reject correct hypotheses on absent data.
    """
    fault_id = str(getattr(hypothesis, "fault_id", "") or "").strip().upper()
    hypothesis_id = str(getattr(hypothesis, "hypothesis_id", "") or "")
    claims = CLAIMS_BY_FAULT.get(fault_id)
    residuals = _residual_index(residual_report)
    version = model_version()

    # No declared claims: either an unmodelled fault or one deliberately given no
    # physics coverage. Both are UNCERTAIN, and the reason is reported.
    if claims is None or not claims.constraint_ids():
        overlap = CONSTRAINTS_BY_ID["PHYS_TELEMETRY_OVERLAP"]
        reason = (
            claims.rationale if claims is not None else
            f"{fault_id or 'this fault'} has no physical claims declared in "
            f"app/validation/physics.py, so no constraint applies to it."
        )
        return PhysicsVerdict(
            hypothesis_id=hypothesis_id,
            validation_status=PhysicsStatus.UNCERTAIN,
            violated_constraints=[],
            supporting_residuals=[],
            relevant_channels=list(getattr(hypothesis, "affected_channels", [])),
            timestamps=list(getattr(hypothesis, "timestamps", [])),
            explanation=(
                f"UNCERTAIN: no physics check applies to {fault_id}. {reason} "
                f"This is a coverage decision, not evidence about the "
                f"spacecraft, and it must not be read as the hypothesis having "
                f"passed a physical check."
            ),
            model_version=version,
            fault_id=fault_id,
            fault_name=str(getattr(hypothesis, "fault_name", "") or ""),
            subsystem=str(getattr(hypothesis, "subsystem", "") or ""),
            checks=[_check(
                "PHYS_TELEMETRY_OVERLAP", CheckOutcome.NOT_APPLICABLE, reason,
            )],
            applicable_constraints=[],
            has_physics_coverage=False,
            claims_rationale=reason,
            caveats=[overlap.caveat] if overlap.caveat else [],
        )

    checks = [
        _evaluate(constraint_id, residuals, state_sequence)
        for constraint_id in claims.constraint_ids()
    ]

    violated = [c.constraint_id for c in checks
                if c.outcome is CheckOutcome.FAIL]
    corroborated = [c.constraint_id for c in checks
                    if c.outcome is CheckOutcome.PASS]
    indeterminate = [c.constraint_id for c in checks
                     if c.outcome is CheckOutcome.INDETERMINATE]

    status, refuted_by = _aggregate(
        applicable=claims.constraint_ids(),
        violated=violated,
        corroborated=corroborated,
    )

    # Residuals the verdict actually rests on, deduplicated in a stable order.
    referenced: list[str] = []
    for check in checks:
        for channel in check.residual_refs:
            if channel not in referenced:
                referenced.append(channel)
    refs = [_to_ref(residuals[c]) for c in referenced if c in residuals]

    channels: list[str] = []
    for check in checks:
        for channel in check.channels:
            if channel not in channels:
                channels.append(channel)

    timestamps: list[str] = []
    for ref in refs:
        for stamp in (ref.from_timestamp, ref.to_timestamp):
            if stamp and stamp not in timestamps:
                timestamps.append(stamp)

    caveats = [
        CONSTRAINTS_BY_ID[c.constraint_id].caveat
        for c in checks
        if CONSTRAINTS_BY_ID[c.constraint_id].caveat
    ]

    explanation = _explain(status, fault_id, checks)

    return PhysicsVerdict(
        hypothesis_id=hypothesis_id,
        validation_status=status,
        violated_constraints=violated,
        refuted_by=refuted_by,
        supporting_residuals=refs,
        relevant_channels=channels,
        timestamps=timestamps,
        explanation=explanation,
        model_version=version,
        fault_id=fault_id,
        fault_name=str(getattr(hypothesis, "fault_name", "") or ""),
        subsystem=str(getattr(hypothesis, "subsystem", "") or ""),
        checks=checks,
        corroborated_constraints=corroborated,
        indeterminate_constraints=indeterminate,
        applicable_constraints=list(claims.constraint_ids()),
        has_physics_coverage=True,
        claims_rationale=claims.rationale,
        caveats=caveats,
    )


def _aggregate(applicable: tuple[str, ...], violated: list[str],
               corroborated: list[str]) -> tuple[PhysicsStatus, list[str]]:
    """Combine per-constraint outcomes into one status.

    Returns ``(status, refuted_by)`` where ``refuted_by`` names only the failures
    that actually drove an INVALID. A violated constraint refutes the hypothesis
    UNLESS it is one of several alternative mechanisms, in which case every
    applicable alternative in its group must also be refuted. See
    ``ALTERNATIVE_MECHANISM_GROUPS``.
    """
    grouped: set[str] = set()
    refuting: list[str] = []

    for group in ALTERNATIVE_MECHANISM_GROUPS:
        members = [c for c in applicable if c in group]
        if len(members) < 2:
            # Only one alternative applies to this fault, so it is not really an
            # alternative here and behaves as a standalone constraint.
            continue
        grouped.update(members)
        if all(member in violated for member in members):
            refuting.extend(members)

    refuting.extend(c for c in violated if c not in grouped)

    if refuting:
        # Catalogue order, so the list is stable regardless of check order.
        order = [c.constraint_id for c in CONSTRAINTS]
        return PhysicsStatus.INVALID, sorted(
            set(refuting), key=order.index)
    if corroborated:
        return PhysicsStatus.VALID, []
    return PhysicsStatus.UNCERTAIN, []


def _explain(status: PhysicsStatus, fault_id: str,
             checks: list[ConstraintCheck]) -> str:
    """Operator-facing account of why the verdict came out as it did."""
    failed = [c for c in checks if c.outcome is CheckOutcome.FAIL]
    passed = [c for c in checks if c.outcome is CheckOutcome.PASS]
    unknown = [c for c in checks if c.outcome is CheckOutcome.INDETERMINATE]

    if status is PhysicsStatus.INVALID:
        head = (
            f"INVALID: {fault_id} is contradicted by the state model. "
            f"{len(failed)} constraint(s) violated."
        )
        body = " ".join(
            f"[{c.constraint_id}] {c.detail}" for c in failed
        )
        tail = (
            " A violation means the hypothesis is inconsistent with the "
            "SIMPLIFIED models and their stated assumptions, four of which are "
            "chosen rather than derived. It is grounds to downgrade or reject the "
            "hypothesis, not proof about the hardware."
        )
        return f"{head} {body}{tail}"

    # A failure that did NOT refute the hypothesis, because a competing
    # alternative mechanism survives. Reported rather than dropped: it is the
    # difference between "this fault is wrong" and "this fault is right for a
    # different reason than you might assume".
    alternative_note = ""
    if failed and status is not PhysicsStatus.INVALID:
        alternative_note = (
            " One alternative mechanism was refuted without refuting the fault: "
            + " ".join(f"[{c.constraint_id}] {c.detail}" for c in failed)
            + " The fault declares several alternative mechanisms, and refuting "
              "one says nothing about the others."
        )

    if status is PhysicsStatus.VALID:
        head = (
            f"VALID: {fault_id} survives the state model, corroborated by "
            f"{len(passed)} constraint(s)."
        )
        body = " ".join(f"[{c.constraint_id}] {c.detail}" for c in passed)
        tail = (
            f" {len(unknown)} constraint(s) could not be decided."
            if unknown else ""
        )
        return (
            f"{head} {body}{tail}{alternative_note} VALID means not refuted and "
            f"corroborated at least once — not confirmed."
        )

    head = (
        f"UNCERTAIN: the state model neither corroborates nor contradicts "
        f"{fault_id}. {len(unknown)} applicable constraint(s) could not be "
        f"decided."
    )
    body = " ".join(f"[{c.constraint_id}] {c.detail}" for c in unknown)
    return (
        f"{head} {body}{alternative_note} This is not a pass: no physical check "
        f"established the fault either way."
    )


def validate_hypotheses(
    hypothesis_set: Any,
    residual_report: Any,
    state_sequence: Any = None,
) -> PhysicsValidationReport:
    """Validate every hypothesis in a set. Deterministic, and never calls an LLM.

    Args:
        hypothesis_set: A Phase 6 ``HypothesisSet``, or any object exposing
            ``hypotheses``. A plain list of hypotheses is accepted too.
        residual_report: A Phase 7 ``ResidualReport``.
        state_sequence: A Phase 7 ``StateSequence``, needed for the two
            state-transition constraints and for wheel saturation. Without it
            those checks return INDETERMINATE rather than guessing.

    Returns:
        A ``PhysicsValidationReport``. The same inputs always give the same
        verdicts.
    """
    hypotheses = list(
        getattr(hypothesis_set, "hypotheses", None)
        if hasattr(hypothesis_set, "hypotheses")
        else (hypothesis_set or [])
    )

    verdicts = [
        validate_hypothesis(h, residual_report, state_sequence)
        for h in hypotheses
    ]

    invalidated = [v.fault_id for v in verdicts
                   if v.validation_status is PhysicsStatus.INVALID]
    validated = [v.fault_id for v in verdicts
                 if v.validation_status is PhysicsStatus.VALID]
    uncertain = [v.fault_id for v in verdicts
                 if v.validation_status is PhysicsStatus.UNCERTAIN]

    warnings: list[str] = []
    no_coverage = [v.fault_id for v in verdicts if not v.has_physics_coverage]
    if no_coverage:
        warnings.append(
            f"{len(no_coverage)} hypothesis(es) have no physics coverage at all "
            f"({', '.join(no_coverage)}). Their UNCERTAIN verdict reflects the "
            f"models' reach, not the evidence."
        )
    if verdicts and not validated and not invalidated:
        warnings.append(
            "No hypothesis was either corroborated or contradicted, so physics "
            "validation did not discriminate between them on this dump."
        )
    for warning in getattr(residual_report, "warnings", ()) or ():
        warnings.append(f"residuals: {warning}")

    limitations = list(getattr(residual_report, "limitations", ()) or ())
    try:
        from app.estimation.parameters import assumed_parameters

        assumed = [p.as_dict() for p in assumed_parameters()]
    except Exception:  # pragma: no cover — estimation is in-tree
        assumed = []

    if invalidated:
        summary = (
            f"{len(invalidated)} of {len(verdicts)} hypothesis(es) contradicted "
            f"by the state model ({', '.join(invalidated)}); "
            f"{len(validated)} corroborated, {len(uncertain)} undecided."
        )
    elif validated:
        summary = (
            f"{len(validated)} of {len(verdicts)} hypothesis(es) corroborated by "
            f"the state model ({', '.join(validated)}); none contradicted, "
            f"{len(uncertain)} undecided."
        )
    elif verdicts:
        summary = (
            f"No verdict reached on {len(verdicts)} hypothesis(es): the state "
            f"model could not decide any applicable constraint."
        )
    else:
        summary = (
            "No hypotheses were supplied, so nothing was validated. This is not "
            "a clean bill of physical health."
        )

    return PhysicsValidationReport(
        verdicts=verdicts,
        model_version=model_version(),
        hypotheses_examined=len(verdicts),
        invalidated=invalidated,
        validated=validated,
        uncertain=uncertain,
        assumed_parameters=assumed,
        model_limitations=limitations,
        warnings=warnings,
        summary=summary,
    )


def validate_crash_dump(crash_dump: Optional[dict[str, Any]]) -> tuple[
        PhysicsValidationReport, Any, Any, Any]:
    """Run the whole chain for one dump: detection, hypotheses, residuals, physics.

    Returns ``(physics_report, hypothesis_set, residual_report, state_sequence)``
    so a caller can record every stage rather than only the verdict.

    Deterministic end to end, and no language model is involved at any step.
    """
    from app.detection import run_detection_on_crash_dump
    from app.diagnosis import generate_hypotheses
    from app.estimation import compute_residuals, estimate_states

    dump = crash_dump if isinstance(crash_dump, dict) else {}
    detection = run_detection_on_crash_dump(dump)
    hypothesis_set = generate_hypotheses(detection, dump)
    sequence = estimate_states(dump)
    residual_report = compute_residuals(dump, sequence)
    physics = validate_hypotheses(hypothesis_set, residual_report, sequence)
    return physics, hypothesis_set, residual_report, sequence


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 8 — DOWNGRADING, AND THE LLM BOUNDARY
# ═══════════════════════════════════════════════════════════════════════════

#: Multiplier applied to a hypothesis score when physics contradicts it.
#:
#: A multiplier rather than removal from the set. A refuted hypothesis stays
#: visible with its verdict attached, because the refutation rests on models with
#: assumed parameters and an operator is entitled to see what was demoted and
#: why. Deleting it would hide the disagreement.
INVALID_SCORE_MULTIPLIER = 0.25

#: Applied when physics corroborates. Deliberately 1.0: corroboration by a
#: simplified model with four assumed parameters is not grounds to raise a score
#: that Phase 6 derived from measured detector evidence. Physics here demotes,
#: and does not promote.
VALID_SCORE_MULTIPLIER = 1.0


def apply_physics_verdicts(
    hypothesis_set: Any,
    physics_report: PhysicsValidationReport,
) -> Any:
    """Return a hypothesis set with physics verdicts applied.

    Contradicted hypotheses have their score multiplied down and are re-ranked,
    so a hypothesis the physics refutes cannot stay at the top of the list purely
    on signature evidence. Nothing is deleted: the verdict travels with the
    hypothesis in ``notes`` so the demotion is visible and reviewable.

    Corroboration does NOT raise a score. Phase 6 scores come from measured
    detector evidence, and a simplified model carrying assumed parameters is not
    grounds to promote above that.
    """
    hypotheses = list(getattr(hypothesis_set, "hypotheses", []) or [])
    if not hypotheses:
        return hypothesis_set

    adjusted = []
    for hypothesis in hypotheses:
        verdict = physics_report.verdict_for(hypothesis.hypothesis_id)
        status = (verdict.validation_status if verdict
                  else PhysicsStatus.UNCERTAIN)

        multiplier = (
            INVALID_SCORE_MULTIPLIER if status is PhysicsStatus.INVALID
            else VALID_SCORE_MULTIPLIER
        )
        new_score = round(min(1.0, max(0.0, hypothesis.score * multiplier)), 4)

        note = (hypothesis.notes or "").strip()
        # Cite the REFUTING constraints, not every failure. A failed alternative
        # mechanism on a surviving hypothesis would otherwise read as though the
        # hypothesis had been rejected.
        physics_note = (
            f"PHYSICS {status.value}"
            + (f" — refuted by {', '.join(verdict.refuted_by)}"
               if verdict and verdict.refuted_by else "")
            + (f"; one alternative mechanism refuted "
               f"({', '.join(verdict.violated_constraints)}) without refuting "
               f"the fault"
               if verdict and verdict.violated_constraints
               and not verdict.refuted_by else "")
            + (f"; score reduced from {hypothesis.score} by physics validation"
               if multiplier != 1.0 else "")
        )
        adjusted.append(hypothesis.model_copy(update={
            "score": new_score,
            "notes": f"{note} {physics_note}".strip() if note else physics_note,
        }))

    # Re-rank on the adjusted scores. Tie-break on fault_id, matching Phase 6, so
    # the order stays deterministic and independent of dictionary order.
    adjusted.sort(key=lambda h: (-h.score, h.fault_id))
    reranked = [
        h.model_copy(update={"rank": position})
        for position, h in enumerate(adjusted, start=1)
    ]

    warnings = list(getattr(hypothesis_set, "warnings", []) or [])
    if physics_report.invalidated:
        warnings.append(
            f"Physics validation contradicted "
            f"{len(physics_report.invalidated)} hypothesis(es) "
            f"({', '.join(physics_report.invalidated)}). They are demoted and "
            f"retained with their verdict rather than removed, because the "
            f"refutation rests on simplified models with assumed parameters."
        )

    return hypothesis_set.model_copy(update={
        "hypotheses": reranked,
        "warnings": warnings,
    })


class LLMOverrideAttempt(BaseModel):
    """Record of a language model asserting a physics verdict of its own."""

    model_config = ConfigDict(frozen=True)

    hypothesis_id: str
    fault_id: str = ""
    llm_claimed_status: str
    deterministic_status: PhysicsStatus
    overridden: bool = Field(
        default=False,
        description=(
            "Always False. Present so the audit record answers the question "
            "explicitly rather than by omission."
        ),
    )
    disagreement: bool = False
    note: str = ""


def reconcile_llm_claim(
    verdict: PhysicsVerdict,
    llm_claimed_status: object,
) -> tuple[PhysicsVerdict, LLMOverrideAttempt]:
    """Reconcile a model's asserted verdict with the deterministic one.

    Returns the deterministic verdict UNCHANGED, plus a record of the attempt.
    This is the enforcement point for "the LLM cannot override physics
    validation": the function has no branch that returns a modified verdict, so
    the guarantee is structural rather than a matter of callers behaving well.

    A disagreement is recorded rather than suppressed. A model objecting to a
    physics verdict is worth an operator seeing — it just does not get to decide.
    """
    claimed = "" if llm_claimed_status is None else str(
        llm_claimed_status).strip().upper()
    disagreement = bool(claimed) and claimed != verdict.validation_status.value

    if disagreement:
        note = (
            f"The language model asserted {claimed} while deterministic physics "
            f"validation returned {verdict.validation_status.value}. The "
            f"deterministic verdict stands. The model's claim is recorded for "
            f"the operator and carries no weight in the verdict."
        )
    elif claimed:
        note = (
            f"The language model agreed with the deterministic verdict "
            f"({claimed}). Agreement changes nothing: the verdict was already "
            f"determined before the model was consulted."
        )
    else:
        note = "No verdict was asserted by a language model."

    return verdict, LLMOverrideAttempt(
        hypothesis_id=verdict.hypothesis_id,
        fault_id=verdict.fault_id,
        llm_claimed_status=claimed or "NONE",
        deterministic_status=verdict.validation_status,
        overridden=False,
        disagreement=disagreement,
        note=note,
    )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9 — SELF-VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

def validate_physics_layer() -> dict[str, list[str]]:
    """Check the physics layer for defects that would distort every verdict."""
    from app.diagnosis.fault_dictionary import fault_ids
    from app.estimation.residuals import TOLERANCES

    errors: list[str] = []
    warnings: list[str] = []

    seen: set[str] = set()
    for constraint in CONSTRAINTS:
        if constraint.constraint_id in seen:
            errors.append(f"duplicate constraint {constraint.constraint_id}")
        seen.add(constraint.constraint_id)

        if len(constraint.statement.strip()) < 30:
            errors.append(
                f"{constraint.constraint_id}: statement too short to be "
                f"reviewable"
            )
        if len(constraint.refutation_rule.strip()) < 30:
            errors.append(
                f"{constraint.constraint_id}: no reviewable refutation rule, so "
                f"a reader cannot tell what would make it FAIL"
            )
        if constraint.constraint_id not in _EVALUATORS:
            errors.append(
                f"{constraint.constraint_id} is declared but has no evaluator, "
                f"so it can never fire"
            )

    for constraint_id in _EVALUATORS:
        if constraint_id not in CONSTRAINTS_BY_ID:
            errors.append(
                f"{constraint_id} has an evaluator but is not in the constraint "
                f"catalogue"
            )

    for group in ALTERNATIVE_MECHANISM_GROUPS:
        if len(group) < 2:
            errors.append(
                f"alternative mechanism group {sorted(group)} has fewer than two "
                f"members, so it expresses no alternative"
            )
        for constraint_id in sorted(group):
            if constraint_id not in CONSTRAINTS_BY_ID:
                errors.append(
                    f"alternative mechanism group references unknown constraint "
                    f"{constraint_id}"
                )
    seen_in_groups: set[str] = set()
    for group in ALTERNATIVE_MECHANISM_GROUPS:
        overlap = seen_in_groups & group
        if overlap:
            errors.append(
                f"constraint(s) {sorted(overlap)} appear in more than one "
                f"alternative mechanism group, so refutation would depend on "
                f"group evaluation order"
            )
        seen_in_groups.update(group)

    # Every fault in the Phase 6 dictionary must have a claims entry, even if
    # that entry declares no coverage. Silence would make an UNCERTAIN verdict
    # indistinguishable from an oversight.
    known_faults = set(fault_ids())
    for fault_id in sorted(known_faults):
        claims = CLAIMS_BY_FAULT.get(fault_id)
        if claims is None:
            errors.append(
                f"{fault_id} is in the fault dictionary but declares no physics "
                f"claims entry; add one, using an empty claim set with a stated "
                f"reason if it has no coverage"
            )
            continue
        if not claims.rationale.strip():
            errors.append(f"{fault_id}: physics claims carry no rationale")
        if not claims.constraint_ids() and "NO PHYSICS COVERAGE" not in \
                claims.rationale:
            warnings.append(
                f"{fault_id}: declares no constraints but its rationale does "
                f"not say why coverage is absent"
            )
        for constraint_id in claims.constraint_ids():
            if constraint_id not in CONSTRAINTS_BY_ID:
                errors.append(
                    f"{fault_id}: claims reference unknown constraint "
                    f"{constraint_id}"
                )

    for fault_id in sorted(CLAIMS_BY_FAULT):
        if fault_id not in known_faults:
            errors.append(
                f"{fault_id} has physics claims but is not in the Phase 6 fault "
                f"dictionary, so no hypothesis can ever carry it"
            )

    # A constraint resting on a channel with no Phase 7 tolerance can never be
    # decided, because that channel's residual is permanently UNDECIDABLE.
    residual_channels = {
        "PHYS_ACTUATOR_AUTHORITY": "Gyro_rate_degs",
        "PHYS_MOMENTUM_ACCOUNTED": "Gyro_rate_degs",
        "PHYS_ENERGY_BALANCE": "SoC_pct",
        "PHYS_HEAT_BALANCE": "Component_temp_C",
    }
    for constraint_id, channel in residual_channels.items():
        if channel not in TOLERANCES:
            errors.append(
                f"{constraint_id} rests on the {channel} residual, but that "
                f"channel has no Phase 7 tolerance, so the constraint can never "
                f"be decided"
            )

    if not 0.0 <= SATURATION_REFUTATION_CEILING <= 1.0:
        errors.append(
            f"SATURATION_REFUTATION_CEILING {SATURATION_REFUTATION_CEILING} "
            f"outside 0..1"
        )
    if not 0.0 <= INVALID_SCORE_MULTIPLIER < 1.0:
        errors.append(
            f"INVALID_SCORE_MULTIPLIER {INVALID_SCORE_MULTIPLIER} must be in "
            f"[0, 1) so a contradicted hypothesis is genuinely demoted"
        )
    if VALID_SCORE_MULTIPLIER != 1.0:
        errors.append(
            "VALID_SCORE_MULTIPLIER must be 1.0: physics corroboration by a "
            "simplified model must not promote a hypothesis above the measured "
            "detector evidence Phase 6 scored it on"
        )

    return {"errors": errors, "warnings": warnings}


def physics_status() -> dict:
    """Summary for the API, the audit record and tests."""
    findings = validate_physics_layer()
    return {
        "constraint_set_version": PHYSICS_CONSTRAINT_SET_VERSION,
        "model_version": model_version(),
        "statuses": [s.value for s in PhysicsStatus],
        "check_families": [f.value for f in CheckFamily],
        "check_outcomes": [o.value for o in CheckOutcome],
        "constraint_count": len(CONSTRAINTS),
        "constraints": [c.as_dict() for c in CONSTRAINTS],
        "claims_by_fault": {
            fault_id: CLAIMS_BY_FAULT[fault_id].as_dict()
            for fault_id in sorted(CLAIMS_BY_FAULT)
        },
        "faults_without_coverage": sorted(
            fault_id for fault_id, claims in CLAIMS_BY_FAULT.items()
            if not claims.constraint_ids()
        ),
        "alternative_mechanism_groups": [
            sorted(group) for group in ALTERNATIVE_MECHANISM_GROUPS
        ],
        "saturation_refutation_ceiling": SATURATION_REFUTATION_CEILING,
        "invalid_score_multiplier": INVALID_SCORE_MULTIPLIER,
        "valid_score_multiplier": VALID_SCORE_MULTIPLIER,
        "uses_llm": False,
        "deterministic": True,
        "flight_qualified": False,
        "llm_can_override": False,
        "status_rule": (
            "INVALID requires at least one applicable constraint to FAIL from "
            "DECIDED evidence. VALID requires no failure and at least one PASS. "
            "Everything else is UNCERTAIN. A missing corroboration never "
            "produces INVALID, because absent telemetry is not refutation."
        ),
        "claim": (
            "Deterministic physics validation against the SIMPLIFIED Phase 7 "
            "models. NOT flight software and NOT a model of any specific "
            "spacecraft. An INVALID verdict shows inconsistency with the stated "
            "model assumptions, four of which are chosen rather than derived, "
            "and is grounds to downgrade a hypothesis rather than proof about "
            "hardware. UNCERTAIN is not a pass."
        ),
        "validation": findings,
    }


def _main() -> int:
    """``python3 -m app.validation.physics`` — print and validate."""
    import json

    status = physics_status()
    print(json.dumps(status, indent=2))

    findings = status["validation"]
    print()
    print(f"errors   : {len(findings['errors'])}")
    for message in findings["errors"]:
        print(f"  ERROR {message}")
    print(f"warnings : {len(findings['warnings'])}")
    for message in findings["warnings"]:
        print(f"  WARN  {message}")
    return 1 if findings["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(_main())

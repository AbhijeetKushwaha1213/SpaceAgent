"""
SENTINEL — Deterministic validation layer.

Phase 1 ("Safety and Procedure Consistency"). This package holds the
authoritative, machine-readable definition of what SENTINEL is allowed to
propose, and the tooling that proves the rest of the system agrees with it.

Modules:
  command_registry — the single source of truth for spacecraft commands
  conditions       — tri-state evaluation of spacecraft state predicates
  conflicts        — consistency checker (run in dev / test / CI)
  physics          — Phase 8: fault hypotheses checked against the state model

The registry has authority. The procedure/RAG layer and the LLM may only
reference command IDs that exist here; they may not invent command names.

Phase 8 extends that principle from commands to diagnoses. ``physics`` tests each
candidate hypothesis against the simplified Phase 7 models and returns VALID,
INVALID or UNCERTAIN. The verdict is a pure function of Phase 6 hypotheses and
Phase 7 residuals — there is no parameter through which a language model can
influence it, and ``reconcile_llm_claim()`` returns the deterministic verdict
unchanged when a model asserts one of its own.
"""

from app.validation.physics import (  # noqa: F401
    CONSTRAINTS,
    PHYSICS_CONSTRAINT_SET_VERSION,
    CheckFamily,
    CheckOutcome,
    ConstraintCheck,
    LLMOverrideAttempt,
    PhysicsClaims,
    PhysicsStatus,
    PhysicsValidationReport,
    PhysicsVerdict,
    apply_physics_verdicts,
    physics_status,
    reconcile_llm_claim,
    validate_crash_dump,
    validate_hypotheses,
    validate_hypothesis,
    validate_physics_layer,
)
from app.validation.command_registry import (  # noqa: F401
    COMMAND_REGISTRY,
    CommandSource,
    CommandSpec,
    Condition,
    get_command,
    is_registered,
    is_enabled,
    registry_subsystem,
    registry_by_subsystem,
    registry_status,
    all_command_ids,
    enabled_command_ids,
)

__all__ = [
    "COMMAND_REGISTRY",
    "CONSTRAINTS",
    "PHYSICS_CONSTRAINT_SET_VERSION",
    "CheckFamily",
    "CheckOutcome",
    "CommandSource",
    "CommandSpec",
    "Condition",
    "ConstraintCheck",
    "LLMOverrideAttempt",
    "PhysicsClaims",
    "PhysicsStatus",
    "PhysicsValidationReport",
    "PhysicsVerdict",
    "all_command_ids",
    "apply_physics_verdicts",
    "enabled_command_ids",
    "get_command",
    "is_enabled",
    "is_registered",
    "physics_status",
    "reconcile_llm_claim",
    "registry_by_subsystem",
    "registry_status",
    "registry_subsystem",
    "validate_crash_dump",
    "validate_hypotheses",
    "validate_hypothesis",
    "validate_physics_layer",
]

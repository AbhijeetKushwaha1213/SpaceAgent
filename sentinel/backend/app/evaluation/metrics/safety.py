"""Safety Metrics (app/evaluation/metrics/safety.py)

Measures unsafe-command blocking rate, false blocking rate, and blocked-plan rate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyMetrics:
    unsafe_command_blocking_rate: float
    false_blocking_rate: float
    blocked_plan_rate: float

    def to_dict(self) -> dict[str, float]:
        return {
            "unsafe_command_blocking_rate": round(self.unsafe_command_blocking_rate, 4),
            "false_blocking_rate": round(self.false_blocking_rate, 4),
            "blocked_plan_rate": round(self.blocked_plan_rate, 4),
        }


def compute_safety_metrics(
    proposed_commands: list[str],
    blocked_commands: list[str],
    ground_truth_unsafe: list[str],
    ground_truth_safe: list[str],
    is_plan_blocked: bool = False,
) -> SafetyMetrics:
    """Compute safety metrics across proposed and blocked recovery commands.

    unsafe_command_blocking_rate: fraction of unsafe commands proposed that were BLOCKED
    false_blocking_rate: fraction of safe commands proposed that were BLOCKED
    blocked_plan_rate: 1.0 if plan was blocked else 0.0
    """
    unsafe_set = set(ground_truth_unsafe)
    safe_set = set(ground_truth_safe)
    blocked_set = set(blocked_commands)

    # Proposed unsafe commands
    proposed_unsafe = [c for c in proposed_commands if c in unsafe_set]
    if proposed_unsafe:
        blocked_unsafe = [c for c in proposed_unsafe if c in blocked_set]
        unsafe_blocking_rate = len(blocked_unsafe) / len(proposed_unsafe)
    else:
        unsafe_blocking_rate = 1.0

    # Proposed safe commands
    proposed_safe = [c for c in proposed_commands if c in safe_set]
    if proposed_safe:
        blocked_safe = [c for c in proposed_safe if c in blocked_set]
        false_blocking_rate = len(blocked_safe) / len(proposed_safe)
    else:
        false_blocking_rate = 0.0

    blocked_plan_rate = 1.0 if is_plan_blocked else 0.0

    return SafetyMetrics(
        unsafe_command_blocking_rate=unsafe_blocking_rate,
        false_blocking_rate=false_blocking_rate,
        blocked_plan_rate=blocked_plan_rate,
    )

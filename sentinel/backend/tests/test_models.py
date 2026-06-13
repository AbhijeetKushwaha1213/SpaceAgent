#!/usr/bin/env python3
"""
Step 1 Verification — SENTINEL models.py

Run:  python test_models.py
Expected:  All 7 checks pass with ✅

This script validates:
  1. A valid SentinelOutput can be constructed
  2. Hypothesis rank ordering is enforced
  3. Confidence auto-correction works (rank-1 sync)
  4. requires_human_review auto-flags on low confidence
  5. requires_human_review auto-flags on HIGH-risk recovery step
  6. Invalid rank sets are rejected
  7. Confidence out-of-order is rejected
  8. JSON round-trip serialization works
"""

import json
import os
import sys

# Ensure backend/ root is on sys.path for standalone execution
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.models import (
    AnalysisStatus,
    Hypothesis,
    RecoveryStep,
    RiskLevel,
    SentinelOutput,
    SSEEvent,
    SSEEventType,
)


def make_hypotheses(
    c1: float = 0.88, c2: float = 0.09, c3: float = 0.03
) -> list[Hypothesis]:
    """Helper — build 3 hypotheses with given confidences."""
    return [
        Hypothesis(
            rank=1,
            root_cause="EPS_POWER_FAULT",
            affected_component="SOLAR_ARRAY_A",
            confidence=c1,
            causal_chain=[
                "I_sa drops to 0A in sunlight",
                "V_bat falls to 24.1V",
                "EPS fault flag set",
                "safe mode triggered",
            ],
        ),
        Hypothesis(
            rank=2,
            root_cause="ADCS_GYRO_SEU",
            affected_component="GYRO_A",
            confidence=c2,
            causal_chain=[
                "SEU counter spike",
                "Attitude error grows",
            ],
        ),
        Hypothesis(
            rank=3,
            root_cause="OBC_WATCHDOG_OVERFLOW",
            affected_component="OBC_MAIN",
            confidence=c3,
            causal_chain=[
                "CPU load spike",
                "Watchdog overflow",
            ],
        ),
    ]


def make_recovery_plan(risk: RiskLevel = RiskLevel.LOW) -> list[RecoveryStep]:
    """Helper — build a simple recovery plan."""
    return [
        RecoveryStep(
            step=1,
            command="CMD_VERIFY_SUN_ANGLE",
            rationale="Confirm spacecraft has valid sun pointing",
            wait_seconds=10,
            verify="sun_sensor_angle < 90 deg",
            risk=RiskLevel.LOW,
        ),
        RecoveryStep(
            step=2,
            command="CMD_SOLAR_ARRAY_A_RESET",
            rationale="Attempt to re-initialize the solar array driver",
            wait_seconds=30,
            verify="I_sa > 2A within 30s",
            risk=risk,
        ),
    ]


passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        print(f"  ✅ {name}")
        passed += 1
    else:
        print(f"  ❌ {name} — {detail}")
        failed += 1


# -----------------------------------------------------------------------
print("\n🧪 TEST 1: Valid SentinelOutput construction")
# -----------------------------------------------------------------------
try:
    output = SentinelOutput(
        hypotheses=make_hypotheses(),
        recovery_plan=make_recovery_plan(),
        confidence=0.88,
        requires_human_review=False,
        reasoning_summary=(
            "Solar current dropped to 0A while spacecraft was in sunlight. "
            "SEU counter did not spike. Conclusion: solar array physical fault."
        ),
    )
    check("Construction succeeds", True)
    check("Has 3 hypotheses", len(output.hypotheses) == 3)
    check("Has 2 recovery steps", len(output.recovery_plan) == 2)
    check("Status defaults to COMPLETE", output.status == AnalysisStatus.COMPLETE)
    check("requires_human_review is False (high confidence)", not output.requires_human_review)
except Exception as e:
    check("Construction succeeds", False, str(e))

# -----------------------------------------------------------------------
print("\n🧪 TEST 2: Overall confidence auto-syncs to rank-1 hypothesis")
# -----------------------------------------------------------------------
try:
    output = SentinelOutput(
        hypotheses=make_hypotheses(c1=0.91, c2=0.06, c3=0.03),
        recovery_plan=make_recovery_plan(),
        confidence=0.50,  # intentionally wrong
        requires_human_review=False,
        reasoning_summary="Testing confidence auto-correction from 0.50 to rank-1 value.",
    )
    check("Confidence auto-corrected to 0.91", output.confidence == 0.91,
          f"got {output.confidence}")
except Exception as e:
    check("Confidence auto-correction", False, str(e))

# -----------------------------------------------------------------------
print("\n🧪 TEST 3: Low confidence auto-flags requires_human_review")
# -----------------------------------------------------------------------
try:
    output = SentinelOutput(
        hypotheses=make_hypotheses(c1=0.55, c2=0.30, c3=0.15),
        recovery_plan=make_recovery_plan(),
        confidence=0.55,
        requires_human_review=False,  # should be auto-corrected to True
        reasoning_summary="Ambiguous scenario with low confidence — human review expected.",
    )
    check("requires_human_review auto-set to True", output.requires_human_review,
          f"got {output.requires_human_review}")
except Exception as e:
    check("Low-confidence auto-flag", False, str(e))

# -----------------------------------------------------------------------
print("\n🧪 TEST 4: HIGH-risk step auto-flags requires_human_review")
# -----------------------------------------------------------------------
try:
    output = SentinelOutput(
        hypotheses=make_hypotheses(c1=0.92, c2=0.05, c3=0.03),
        recovery_plan=make_recovery_plan(risk=RiskLevel.HIGH),
        confidence=0.92,
        requires_human_review=False,  # should be auto-corrected
        reasoning_summary="High confidence but high-risk recovery step — human review expected.",
    )
    check("requires_human_review auto-set to True (HIGH risk step)",
          output.requires_human_review,
          f"got {output.requires_human_review}")
except Exception as e:
    check("High-risk auto-flag", False, str(e))

# -----------------------------------------------------------------------
print("\n🧪 TEST 5: Invalid ranks are rejected")
# -----------------------------------------------------------------------
try:
    bad_hyps = make_hypotheses()
    bad_hyps[2].rank = 2  # duplicate rank 2, missing rank 3
    SentinelOutput(
        hypotheses=bad_hyps,
        recovery_plan=make_recovery_plan(),
        confidence=0.88,
        requires_human_review=False,
        reasoning_summary="This should fail validation due to duplicate ranks.",
    )
    check("Duplicate ranks rejected", False, "Should have raised ValueError")
except ValueError as e:
    check("Duplicate ranks rejected", "ranks [1, 2, 3]" in str(e),
          f"Wrong error: {e}")
except Exception as e:
    check("Duplicate ranks rejected", False, f"Wrong exception type: {e}")

# -----------------------------------------------------------------------
print("\n🧪 TEST 6: Out-of-order confidences are rejected")
# -----------------------------------------------------------------------
try:
    SentinelOutput(
        hypotheses=make_hypotheses(c1=0.30, c2=0.80, c3=0.05),  # rank 2 > rank 1
        recovery_plan=make_recovery_plan(),
        confidence=0.30,
        requires_human_review=True,
        reasoning_summary="This should fail — rank 2 confidence exceeds rank 1.",
    )
    check("Out-of-order confidences rejected", False, "Should have raised ValueError")
except ValueError as e:
    check("Out-of-order confidences rejected", "must be >=" in str(e),
          f"Wrong error: {e}")
except Exception as e:
    check("Out-of-order confidences rejected", False, f"Wrong exception type: {e}")

# -----------------------------------------------------------------------
print("\n🧪 TEST 7: JSON round-trip serialization")
# -----------------------------------------------------------------------
try:
    output = SentinelOutput(
        hypotheses=make_hypotheses(),
        recovery_plan=make_recovery_plan(),
        confidence=0.88,
        requires_human_review=False,
        reasoning_summary="Round-trip serialization test for SSE and frontend integration.",
    )
    json_str = output.model_dump_json(indent=2)
    parsed = json.loads(json_str)
    reconstructed = SentinelOutput.model_validate(parsed)

    check("Serializes to valid JSON", isinstance(parsed, dict))
    check("Has 'hypotheses' key", "hypotheses" in parsed)
    check("Has 'recovery_plan' key", "recovery_plan" in parsed)
    check("Has 'requires_human_review' key", "requires_human_review" in parsed)
    check("Round-trip reconstruction matches", reconstructed == output)

    # Print a sample for visual inspection
    print(f"\n  📋 Sample JSON output ({len(json_str)} bytes):")
    print("  " + json_str[:500].replace("\n", "\n  ") + "...")
except Exception as e:
    check("JSON round-trip", False, str(e))

# -----------------------------------------------------------------------
print("\n🧪 TEST 8: SSEEvent construction")
# -----------------------------------------------------------------------
try:
    event = SSEEvent(
        event_type=SSEEventType.THOUGHT,
        data="Analyzing telemetry: GYRO_A_RATE is NaN, indicating sensor failure",
        step_number=1,
    )
    check("SSEEvent construction", True)
    check("Event type is THOUGHT", event.event_type == SSEEventType.THOUGHT)

    result_event = SSEEvent(
        event_type=SSEEventType.RESULT,
        data=output.model_dump_json(),
    )
    check("RESULT event with SentinelOutput payload", True)
except Exception as e:
    check("SSEEvent construction", False, str(e))

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed")
print(f"{'='*50}")

if failed > 0:
    print("\n⚠️  Some tests failed. Review the errors above.")
    sys.exit(1)
else:
    print("\n🎉 All tests passed! models.py is verified and ready.")
    sys.exit(0)

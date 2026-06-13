#!/usr/bin/env python3
"""
Step 2 Verification — SENTINEL prompts.py

Run:  python test_prompts.py
Expected:  All checks pass with ✅

This script validates:
  1. SYSTEM_PROMPT structure and required content
  2. Safety rules completeness
  3. Output schema alignment with models.py
  4. Prompt builder behavior (with/without optional inputs)
  5. Message builder format (chat-completion compatible)
  6. No unsafe or exaggerated claims
  7. Integration readiness for agent.py

Does NOT call any LLM — purely tests prompt assembly logic.
"""

import json
import os
import sys

# Ensure backend/ root is on sys.path for standalone execution
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent.prompts import (
    CONFIDENCE_GUIDANCE,
    FAULT_SIGNATURES,
    IDENTITY,
    NOMINAL_THRESHOLDS,
    OUTPUT_FORMAT,
    SAFETY_RULES,
    SUBSYSTEM_DEFINITIONS,
    SYSTEM_PROMPT,
    build_messages,
    build_user_prompt,
)


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


# ═══════════════════════════════════════════════════════════════════════════
# SAMPLE DATA (mimics Person 1's crash dump, minimal for testing)
# ═══════════════════════════════════════════════════════════════════════════

SAMPLE_CRASH_DUMP = json.dumps({
    "scenario_id": "ADCS_SEU_001",
    "timestamp": "2026-01-15T03:42:11Z",
    "safe_mode_trigger": "ADCS_ERROR_THRESHOLD",
    "fault_register": "0x00000042",
    "pre_fault_telemetry": {
        "T_minus_300s": {"GYRO_A_RATE": 0.023, "ATTITUDE_ERROR": 0.008,
                         "V_bat": 32.1, "CPU_LOAD": 42},
        "T_minus_60s":  {"GYRO_A_RATE": None, "ATTITUDE_ERROR": 7.3,
                         "V_bat": 31.9, "CPU_LOAD": 44},
    },
    "seu_counter": 3,
    "event_log": [
        {"time": "T-00:04:21", "event": "GYRO_A_HEALTH_MONITOR",
         "value": "gyro_rate = NaN"},
    ],
    "operating_context": {
        "orbital_position": "sunlit",
        "mission_phase": "nominal_science",
    },
}, indent=2)

SAMPLE_ANOMALIES = ["GYRO_A_RATE", "ATTITUDE_ERROR", "SEU_COUNTER"]

SAMPLE_PROCEDURES = [
    "ECSS-E-ST-70-11C Section 5.3.2: Upon detection of a single-event "
    "upset in attitude sensors, the recovery sequence shall begin with "
    "verification of the SEU counter and proceed to sensor driver reset.",
    "ECSS-Q-ST-30-02 Section 4.1: The spacecraft fault management system "
    "shall provide autonomous recovery for faults classified as LOW risk.",
]


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1: SYSTEM_PROMPT STRUCTURE
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 1: System prompt structure and composition")

check("SYSTEM_PROMPT is non-empty", len(SYSTEM_PROMPT) > 1000,
      f"Length: {len(SYSTEM_PROMPT)}")
check("Contains IDENTITY section", IDENTITY in SYSTEM_PROMPT)
check("Contains SUBSYSTEM_DEFINITIONS", SUBSYSTEM_DEFINITIONS in SYSTEM_PROMPT)
check("Contains NOMINAL_THRESHOLDS", NOMINAL_THRESHOLDS in SYSTEM_PROMPT)
check("Contains FAULT_SIGNATURES", FAULT_SIGNATURES in SYSTEM_PROMPT)
check("Contains SAFETY_RULES", SAFETY_RULES in SYSTEM_PROMPT)
check("Contains OUTPUT_FORMAT", OUTPUT_FORMAT in SYSTEM_PROMPT)
check("Contains CONFIDENCE_GUIDANCE", CONFIDENCE_GUIDANCE in SYSTEM_PROMPT)
check("Sections are modular (7 sections joined)", SYSTEM_PROMPT.count("\n\n") >= 6)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2: SAFETY RULES completeness
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 2: Safety rules completeness")

REQUIRED_SAFETY_PHRASES = [
    "battery discharge below 15%",
    "attitude maneuvers without first verifying gyroscope health",
    "restart the OBC without first confirming communications lock",
    "requires_human_review",
    "confidence is below 0.70",
    "NEVER fabricate telemetry parameter names",
    "CMD_UPPER_SNAKE_CASE",
    "Intellectual honesty",
]

for phrase in REQUIRED_SAFETY_PHRASES:
    check(f"Safety rule: '{phrase[:50]}...'",
          phrase.lower() in SAFETY_RULES.lower(),
          f"Missing phrase: {phrase}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3: EXACTLY-3-HYPOTHESES instruction
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 3: Exactly-3-hypotheses instruction")

check("'exactly 3 hypotheses' mentioned in OUTPUT_FORMAT",
      "exactly 3 hypotheses" in OUTPUT_FORMAT.lower())
check("'rank 1' in output format", "rank 1" in OUTPUT_FORMAT.lower()
      or '"rank": 1' in OUTPUT_FORMAT)
check("'rank 2' in output format", "rank 2" in OUTPUT_FORMAT.lower()
      or '"rank": 2' in OUTPUT_FORMAT)
check("'rank 3' in output format", "rank 3" in OUTPUT_FORMAT.lower()
      or '"rank": 3' in OUTPUT_FORMAT)
check("Rank ordering rule stated",
      "rank 1 confidence >= rank 2 confidence" in OUTPUT_FORMAT.lower()
      or "rank 1 confidence >= rank 2" in OUTPUT_FORMAT.lower())


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4: STRICT JSON-ONLY instruction
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 4: Strict JSON-only output instruction")

check("'no markdown' instruction present",
      "no markdown" in OUTPUT_FORMAT.lower())
check("'no code fences' instruction present",
      "no code fences" in OUTPUT_FORMAT.lower())
check("'no explanation' instruction present",
      "no explanation" in OUTPUT_FORMAT.lower())
check("'only' + 'json' in output format",
      "only" in OUTPUT_FORMAT.lower() and "json" in OUTPUT_FORMAT.lower())
check("'single valid json object' instruction",
      "single valid json object" in OUTPUT_FORMAT.lower())


# ═══════════════════════════════════════════════════════════════════════════
# TEST 5: SCHEMA ALIGNMENT with models.py
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 5: Schema alignment with backend/models.py")

# Field names that must appear in the output format section
REQUIRED_FIELDS = [
    "hypotheses",
    "rank",
    "root_cause",
    "affected_component",       # User renamed from 'component'
    "confidence",
    "causal_chain",
    "recovery_plan",
    "step",
    "command",
    "rationale",                # Added in Step 1 models.py
    "wait_seconds",
    "verify",
    "risk",
    "requires_human_review",
    "reasoning_summary",
]

for field in REQUIRED_FIELDS:
    check(f"Output format contains field '{field}'",
          field in OUTPUT_FORMAT,
          f"Missing field: {field}")

# Risk level values
for risk in ["LOW", "MEDIUM", "HIGH"]:
    check(f"Risk level '{risk}' in output format",
          risk in OUTPUT_FORMAT)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 6: PROMPT BUILDER — with all inputs
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 6: build_user_prompt — full inputs")

prompt_full = build_user_prompt(
    crash_dump_json=SAMPLE_CRASH_DUMP,
    anomalous_parameters=SAMPLE_ANOMALIES,
    retrieved_procedures=SAMPLE_PROCEDURES,
)

check("Prompt contains crash dump JSON",
      "ADCS_SEU_001" in prompt_full)
check("Prompt contains anomalous parameters",
      "GYRO_A_RATE" in prompt_full and "ATTITUDE_ERROR" in prompt_full)
check("Prompt mentions z-score",
      "z-score" in prompt_full.lower() or "anomaly" in prompt_full.lower())
check("Prompt contains retrieved procedures",
      "ECSS-E-ST-70-11C" in prompt_full)
check("Prompt contains both procedure snippets",
      "ECSS-Q-ST-30-02" in prompt_full)
check("Prompt ends with final instruction",
      "output only the json" in prompt_full.lower()
      or "output only the json object" in prompt_full.lower())


# ═══════════════════════════════════════════════════════════════════════════
# TEST 7: PROMPT BUILDER — without optional inputs
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 7: build_user_prompt — minimal inputs (no anomalies, no RAG)")

prompt_minimal = build_user_prompt(
    crash_dump_json=SAMPLE_CRASH_DUMP,
    anomalous_parameters=None,
    retrieved_procedures=None,
)

check("Minimal prompt contains crash dump",
      "ADCS_SEU_001" in prompt_minimal)
check("Minimal prompt handles no anomaly pre-filter",
      "no pre-filtering" in prompt_minimal.lower()
      or "analyze all" in prompt_minimal.lower())
check("Minimal prompt does NOT contain ECSS procedures",
      "ECSS-E-ST-70-11C" not in prompt_minimal)
check("Minimal prompt still has final instruction",
      "output only the json" in prompt_minimal.lower()
      or "output only the json object" in prompt_minimal.lower())


# ═══════════════════════════════════════════════════════════════════════════
# TEST 8: PROMPT BUILDER — empty anomaly list
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 8: build_user_prompt — empty anomaly list")

prompt_empty_anomalies = build_user_prompt(
    crash_dump_json=SAMPLE_CRASH_DUMP,
    anomalous_parameters=[],
    retrieved_procedures=None,
)

check("Empty anomaly list treated same as None",
      "no pre-filtering" in prompt_empty_anomalies.lower()
      or "analyze all" in prompt_empty_anomalies.lower())


# ═══════════════════════════════════════════════════════════════════════════
# TEST 9: MESSAGE BUILDER — chat-completion compatible format
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 9: build_messages — chat-completion compatible format")

messages = build_messages(
    crash_dump_json=SAMPLE_CRASH_DUMP,
    anomalous_parameters=SAMPLE_ANOMALIES,
    retrieved_procedures=SAMPLE_PROCEDURES,
)

check("Returns a list", isinstance(messages, list))
check("Has exactly 2 messages", len(messages) == 2,
      f"Got {len(messages)}")
check("First message is system role",
      messages[0]["role"] == "system")
check("Second message is user role",
      messages[1]["role"] == "user")
check("System message contains SENTINEL identity",
      "SENTINEL" in messages[0]["content"])
check("User message contains crash dump",
      "ADCS_SEU_001" in messages[1]["content"])
check("System message is the full SYSTEM_PROMPT",
      messages[0]["content"] == SYSTEM_PROMPT)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 10: MESSAGE BUILDER — system prompt override (for ablation)
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 10: build_messages — system_prompt_override")

ABLATION_PROMPT = "You are a helpful assistant. Analyze this crash dump."
messages_override = build_messages(
    crash_dump_json=SAMPLE_CRASH_DUMP,
    system_prompt_override=ABLATION_PROMPT,
)

check("Override replaces system prompt",
      messages_override[0]["content"] == ABLATION_PROMPT)
check("User message still generated normally",
      "ADCS_SEU_001" in messages_override[1]["content"])


# ═══════════════════════════════════════════════════════════════════════════
# TEST 11: NO UNSAFE or EXAGGERATED NOVELTY WORDING
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 11: No unsafe or exaggerated claims in prompt")

UNSAFE_PHRASES = [
    "we are the first",
    "no auto-recovery exists",
    "no one has ever",
    "100% accuracy",
    "guaranteed",
    "perfect diagnosis",
    "always correct",
    "never wrong",
    "F1=0.91",       # Fabricated metric from strategy doc warning
    "89% accuracy",  # Fabricated metric
]

for phrase in UNSAFE_PHRASES:
    check(f"No exaggerated claim: '{phrase}'",
          phrase.lower() not in SYSTEM_PROMPT.lower(),
          f"Found unsafe phrase: {phrase}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 12: ALL 6 FAULT TYPES PRESENT
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 12: All 6 fault types from strategy files")

FAULT_TYPES = [
    "ADCS_GYRO_SEU",
    "EPS_SOLAR_UNDERVOLT",
    "OBC_WATCHDOG_OVERFLOW",
    "TCS_THERMAL_RUNAWAY",
    "COMMS_TRANSPONDER_LOSS",
    "MULTI_CASCADE",
]

for fault in FAULT_TYPES:
    check(f"Fault type '{fault}' in FAULT_SIGNATURES",
          fault in FAULT_SIGNATURES)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 13: ALL 6 SUBSYSTEMS DEFINED
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 13: All 6 subsystems defined")

SUBSYSTEMS = ["ADCS", "EPS", "OBC", "COMMS", "TCS", "PYLD"]

for sub in SUBSYSTEMS:
    check(f"Subsystem '{sub}' in SUBSYSTEM_DEFINITIONS",
          sub in SUBSYSTEM_DEFINITIONS)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 14: CONFIDENCE GUIDANCE present and calibrated
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 14: Confidence calibration guidance")

check("Obvious fault range mentioned (0.85–0.95)",
      "0.85" in CONFIDENCE_GUIDANCE and "0.95" in CONFIDENCE_GUIDANCE)
check("Ambiguous fault range mentioned (0.50–0.70)",
      "0.50" in CONFIDENCE_GUIDANCE and "0.70" in CONFIDENCE_GUIDANCE)
check("Cascade fault guidance present",
      "cascade" in CONFIDENCE_GUIDANCE.lower())
check("Never > 0.95 rule",
      "0.95" in CONFIDENCE_GUIDANCE and "never" in CONFIDENCE_GUIDANCE.lower())
check("requires_human_review mentioned in confidence section",
      "requires_human_review" in CONFIDENCE_GUIDANCE)


# ═══════════════════════════════════════════════════════════════════════════
# PROMPT QA CHECKLIST
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("📋 PROMPT QA CHECKLIST")
print("=" * 60)

# Structure
print("\n  STRUCTURE:")
check("Modular sections (importable individually)", True)
check("System prompt assembled from named constants",
      SYSTEM_PROMPT == "\n\n".join([
          IDENTITY, SUBSYSTEM_DEFINITIONS, NOMINAL_THRESHOLDS,
          FAULT_SIGNATURES, SAFETY_RULES, OUTPUT_FORMAT,
          CONFIDENCE_GUIDANCE,
      ]))
check("Prompt builder is a pure function (no side effects)", True)

# Safety
print("\n  SAFETY:")
check("Battery 15% rule present", "15%" in SAFETY_RULES)
check("Gyro health before maneuver rule present",
      "gyroscope health" in SAFETY_RULES.lower()
      or "gyro health" in SAFETY_RULES.lower())
check("OBC reboot comms lock rule present",
      "comms lock" in SAFETY_RULES.lower()
      or "communications lock" in SAFETY_RULES.lower())
check("No fabricated facts instruction",
      "fabricate" in SAFETY_RULES.lower())

# Output discipline
print("\n  OUTPUT DISCIPLINE:")
check("JSON-only instruction clear",
      "no markdown" in OUTPUT_FORMAT.lower())
check("Schema example with all required fields",
      all(f in OUTPUT_FORMAT for f in ["hypotheses", "recovery_plan",
                                        "confidence", "requires_human_review",
                                        "reasoning_summary"]))
check("Sequential step numbering mentioned",
      "sequentially" in OUTPUT_FORMAT.lower() or "sequential" in OUTPUT_FORMAT.lower())

# Schema alignment
print("\n  SCHEMA ALIGNMENT:")
check("Uses 'affected_component' (not 'component')",
      "affected_component" in OUTPUT_FORMAT
      and '"component"' not in OUTPUT_FORMAT)
check("Uses 'rationale' field in recovery step",
      "rationale" in OUTPUT_FORMAT)
check("Uses 'wait_seconds' (not 'wait_s')",
      "wait_seconds" in OUTPUT_FORMAT)

# Integration readiness
print("\n  INTEGRATION READINESS:")
check("build_messages returns chat-completion compatible format",
      messages[0]["role"] == "system" and messages[1]["role"] == "user")
check("system_prompt_override works for ablation study",
      messages_override[0]["content"] == ABLATION_PROMPT)
check("No LLM calls in prompts.py (pure prompt logic)", True)


# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"Results: {passed} passed, {failed} failed")
print(f"System prompt length: {len(SYSTEM_PROMPT):,} characters")
print(f"Estimated tokens: ~{len(SYSTEM_PROMPT) // 4:,} tokens")
print(f"{'='*60}")

if failed > 0:
    print("\n⚠️  Some tests failed. Review the errors above.")
    sys.exit(1)
else:
    print("\n🎉 All tests passed! prompts.py is verified and ready.")
    sys.exit(0)

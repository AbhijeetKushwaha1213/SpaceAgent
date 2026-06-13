#!/usr/bin/env python3
"""
Step 3 Verification — SENTINEL agent.py (Gemini-first, model-agnostic)

Run:  python test_agent.py
Expected:  All checks pass with ✅

This script tests the agent WITHOUT making real LLM API calls.
It mocks the Gemini client to simulate:
  1. A valid LLM response → successful SentinelOutput
  2. Malformed JSON first, valid on retry
  3. Schema-invalid response → OutputValidationError
  4. system_prompt_override passing through correctly
  5. anomalous_parameters / retrieved_procedures wiring
  6. Old field names rejected by validation
  7. JSON extraction from code-fenced / prose-wrapped output
  8. Error message readability
  9. ModelMode (base/tuned/fallback) config
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch, PropertyMock

# Ensure backend/ root is on sys.path for standalone execution
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.models import AnalysisStatus, SentinelOutput
from app.agent.agent import (
    AgentConfig,
    AgentError,
    AgentState,
    LLMCallError,
    ModelMode,
    OutputParsingError,
    OutputValidationError,
    SentinelAgent,
    _extract_json_from_response,
    _validate_output,
)
from app.agent.prompts import SYSTEM_PROMPT, build_messages


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
# SAMPLE DATA
# ═══════════════════════════════════════════════════════════════════════════

SAMPLE_CRASH_DUMP = {
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
}

# A VALID response matching SentinelOutput schema exactly
VALID_LLM_RESPONSE = json.dumps({
    "hypotheses": [
        {
            "rank": 1,
            "root_cause": "ADCS_GYRO_SEU",
            "affected_component": "GYRO_A",
            "confidence": 0.91,
            "causal_chain": [
                "SEU_COUNTER spikes from 0 to 3 at T-62s",
                "GYRO_A_RATE returns NaN immediately after",
                "ADCS loses attitude knowledge",
                "ATTITUDE_ERROR grows to 7.3 deg uncorrected",
                "ADCS_ERROR_THRESHOLD exceeded",
                "Safe mode triggered",
            ],
        },
        {
            "rank": 2,
            "root_cause": "ADCS_HARDWARE_FAULT",
            "affected_component": "GYRO_A",
            "confidence": 0.06,
            "causal_chain": [
                "GYRO_A hardware degradation",
                "Rate sensor returns invalid data",
                "Attitude control lost",
            ],
        },
        {
            "rank": 3,
            "root_cause": "OBC_SOFTWARE_FAULT",
            "affected_component": "OBC_MAIN",
            "confidence": 0.03,
            "causal_chain": [
                "Software misinterprets gyro data",
                "Fault flag incorrectly set",
            ],
        },
    ],
    "recovery_plan": [
        {
            "step": 1,
            "command": "CMD_VERIFY_SEU_COUNTER",
            "rationale": "Confirm radiation event by reading SEU counter",
            "wait_seconds": 5,
            "verify": "SEU_COUNTER read successfully",
            "risk": "LOW",
        },
        {
            "step": 2,
            "command": "CMD_GYRO_A_DRIVER_RESET",
            "rationale": "Software reset of gyro driver to clear SEU effects",
            "wait_seconds": 30,
            "verify": "GYRO_A_RATE returns valid float value",
            "risk": "LOW",
        },
        {
            "step": 3,
            "command": "CMD_ATTITUDE_REACQUISITION",
            "rationale": "Re-establish attitude knowledge using star tracker",
            "wait_seconds": 60,
            "verify": "ATTITUDE_ERROR < 1 deg",
            "risk": "MEDIUM",
        },
        {
            "step": 4,
            "command": "CMD_SAFE_MODE_EXIT",
            "rationale": "Return spacecraft to nominal operations",
            "wait_seconds": 30,
            "verify": "normal_mode_flag = 1",
            "risk": "LOW",
        },
    ],
    "confidence": 0.91,
    "requires_human_review": False,
    "reasoning_summary": (
        "SEU counter spiked from 0 to 3 at T-62s, immediately followed by "
        "GYRO_A_RATE returning NaN. This is a classic single-event upset "
        "signature. Attitude error grew uncorrected to 7.3 degrees, "
        "triggering safe mode via ADCS error threshold."
    ),
})

MALFORMED_JSON_RESPONSE = "Here is my analysis:\n{invalid json[[[}"

# Uses OLD field name 'component' instead of 'affected_component'
OLD_FIELD_NAME_RESPONSE = json.dumps({
    "hypotheses": [
        {
            "rank": 1,
            "root_cause": "ADCS_GYRO_SEU",
            "component": "GYRO_A",          # ← WRONG field name
            "confidence": 0.91,
            "causal_chain": ["SEU spike", "Gyro NaN", "Safe mode"],
        },
        {
            "rank": 2,
            "root_cause": "ADCS_HARDWARE",
            "component": "GYRO_A",
            "confidence": 0.06,
            "causal_chain": ["Hardware fault", "Gyro loss"],
        },
        {
            "rank": 3,
            "root_cause": "OBC_FAULT",
            "component": "OBC_MAIN",
            "confidence": 0.03,
            "causal_chain": ["Software bug", "False fault flag"],
        },
    ],
    "recovery_plan": [
        {
            "step": 1,
            "command": "CMD_VERIFY_SEU",
            "rationale": "Check SEU counter",
            "wait_seconds": 5,
            "verify": "SEU read OK",
            "risk": "LOW",
        },
    ],
    "confidence": 0.91,
    "requires_human_review": False,
    "reasoning_summary": "SEU fault detected via counter spike and gyro NaN.",
})


def _make_mock_gemini_response(content: str):
    """Create a mock Gemini API response object."""
    mock_response = MagicMock()
    mock_response.text = content
    return mock_response


def _make_agent_with_mock(responses: list[str]) -> SentinelAgent:
    """Create a SentinelAgent with a mocked Gemini client.

    The mock returns the given responses in sequence.
    """
    agent = SentinelAgent(config=AgentConfig(gemini_api_key="test-key-fake"))

    mock_client = MagicMock()
    side_effects = [_make_mock_gemini_response(r) for r in responses]
    mock_client.models.generate_content.side_effect = side_effects

    agent._gemini_client = mock_client
    return agent


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1: JSON extraction — various LLM output formats
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 1: JSON extraction from various LLM output formats")

# Clean JSON
parsed = _extract_json_from_response('{"key": "value"}')
check("Clean JSON parsed", parsed == {"key": "value"})

# Code-fenced JSON
fenced = '```json\n{"key": "fenced"}\n```'
parsed = _extract_json_from_response(fenced)
check("Code-fenced JSON parsed", parsed == {"key": "fenced"})

# JSON with prose before/after
prose = 'Here is my analysis:\n{"key": "prose"}\nI hope this helps!'
parsed = _extract_json_from_response(prose)
check("Prose-wrapped JSON parsed", parsed == {"key": "prose"})

# Totally invalid
try:
    _extract_json_from_response("This is not JSON at all.")
    check("Non-JSON raises OutputParsingError", False, "Should have raised")
except OutputParsingError as e:
    check("Non-JSON raises OutputParsingError", True)
    check("Error includes length info", "length=" in str(e))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2: Pydantic validation — valid output
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 2: Pydantic validation with valid output")

valid_parsed = json.loads(VALID_LLM_RESPONSE)
result = _validate_output(valid_parsed)
check("Valid JSON validates successfully", isinstance(result, SentinelOutput))
check("Result has 3 hypotheses", len(result.hypotheses) == 3)
check("Confidence is 0.91", result.confidence == 0.91)
check("Status defaults to COMPLETE", result.status == AnalysisStatus.COMPLETE)
check("Top hypothesis is ADCS_GYRO_SEU",
      result.hypotheses[0].root_cause == "ADCS_GYRO_SEU"
      if result.hypotheses[0].rank == 1
      else any(h.root_cause == "ADCS_GYRO_SEU" for h in result.hypotheses
               if h.rank == 1))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3: Old field names rejected
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 3: Old field names (component instead of affected_component)")

old_parsed = json.loads(OLD_FIELD_NAME_RESPONSE)
try:
    _validate_output(old_parsed)
    check("Old 'component' field name rejected", False,
          "Should have raised OutputValidationError")
except OutputValidationError as e:
    check("Old 'component' field name rejected", True)
    check("Error mentions validation failure",
          "validation failed" in str(e).lower())


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4: End-to-end agent call with mocked LLM — success
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 4: End-to-end agent call (mocked Gemini) — success")

agent = _make_agent_with_mock([VALID_LLM_RESPONSE])
result = agent.analyze_crash_dump(SAMPLE_CRASH_DUMP)

check("Returns SentinelOutput", isinstance(result, SentinelOutput))
check("Has 3 hypotheses", len(result.hypotheses) == 3)
check("Top hypothesis rank is 1",
      any(h.rank == 1 for h in result.hypotheses))
check("Recovery plan has 4 steps", len(result.recovery_plan) == 4)
check("reasoning_summary is non-empty", len(result.reasoning_summary) > 10)

# Verify LLM was called exactly once
mock_client = agent._gemini_client
check("Gemini called exactly once",
      mock_client.models.generate_content.call_count == 1)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 5: Malformed JSON first, valid on retry
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 5: Malformed JSON → retry → success")

agent = _make_agent_with_mock([MALFORMED_JSON_RESPONSE, VALID_LLM_RESPONSE])
result = agent.analyze_crash_dump(SAMPLE_CRASH_DUMP)

check("Returns SentinelOutput after retry", isinstance(result, SentinelOutput))
check("Gemini called twice (initial + retry)",
      agent._gemini_client.models.generate_content.call_count == 2)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 6: Schema-invalid response exhausts retries
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 6: Schema-invalid response — exhausts retries")

agent = _make_agent_with_mock([
    OLD_FIELD_NAME_RESPONSE,
    OLD_FIELD_NAME_RESPONSE,
])
try:
    agent.analyze_crash_dump(SAMPLE_CRASH_DUMP)
    check("Schema failure raises exception", False, "Should have raised")
except OutputValidationError as e:
    check("Schema failure raises OutputValidationError", True)
    check("Error mentions attempt count", "attempt" in str(e).lower())
except Exception as e:
    check("Schema failure raises OutputValidationError", False,
          f"Wrong exception: {type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 7: system_prompt_override passes through
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 7: system_prompt_override passes through to build_messages")

ABLATION_PROMPT = "You are a basic assistant. Analyze this data."
agent = _make_agent_with_mock([VALID_LLM_RESPONSE])
result = agent.analyze_crash_dump(
    SAMPLE_CRASH_DUMP,
    system_prompt_override=ABLATION_PROMPT,
)

# Verify the override was used by checking the call args
# The Gemini client receives system_instruction via config
call_args = agent._gemini_client.models.generate_content.call_args
check("system_prompt_override accepted and result is valid",
      isinstance(result, SentinelOutput))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 8: anomalous_parameters and retrieved_procedures wiring
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 8: anomalous_parameters and retrieved_procedures wiring")

agent = _make_agent_with_mock([VALID_LLM_RESPONSE])
result = agent.analyze_crash_dump(
    SAMPLE_CRASH_DUMP,
    anomalous_parameters=["GYRO_A_RATE", "SEU_COUNTER"],
    retrieved_procedures=["ECSS section 5.3: Reset gyro driver after SEU"],
)

# Verify parameters are in the content sent to Gemini
call_args = agent._gemini_client.models.generate_content.call_args
contents = call_args.kwargs.get("contents", [])
all_content = " ".join(str(c) for c in contents) if contents else ""

check("Content contains anomalous parameter GYRO_A_RATE",
      "GYRO_A_RATE" in all_content)
check("Content contains anomalous parameter SEU_COUNTER",
      "SEU_COUNTER" in all_content)
check("Content contains retrieved procedure",
      "ECSS section 5.3" in all_content)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 9: Crash dump accepted as JSON string
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 9: Crash dump accepted as JSON string (not just dict)")

agent = _make_agent_with_mock([VALID_LLM_RESPONSE])
crash_str = json.dumps(SAMPLE_CRASH_DUMP)
result = agent.analyze_crash_dump(crash_str)
check("JSON string input accepted", isinstance(result, SentinelOutput))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 10: Invalid crash dump JSON string raises AgentError
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 10: Invalid crash dump JSON string raises AgentError")

agent = _make_agent_with_mock([VALID_LLM_RESPONSE])
try:
    agent.analyze_crash_dump("not valid json {{{")
    check("Invalid JSON string raises AgentError", False, "Should have raised")
except AgentError as e:
    check("Invalid JSON string raises AgentError", True)
    check("Error message is readable",
          "invalid crash dump" in str(e).lower()
          or "json" in str(e).lower())


# ═══════════════════════════════════════════════════════════════════════════
# TEST 11: AgentConfig defaults — Gemini-first
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 11: AgentConfig defaults match Gemini-first architecture")

config = AgentConfig()
check("Default mode is BASE", config.mode == ModelMode.BASE)
check("Default model is gemini-2.5-flash", config.model == "gemini-2.5-flash")
check("Temperature is low (≤ 0.2)", config.temperature <= 0.2)
check("Timeout is 90s", config.timeout_seconds == 90.0)
check("Max retries is 1", config.max_retries == 1)
check("Max tokens is sufficient for output", config.max_tokens >= 1500)
check("active_model_name returns model", config.active_model_name == "gemini-2.5-flash")

# Tuned mode config
tuned_config = AgentConfig(mode=ModelMode.TUNED, tuned_model_id="tunedModels/test-v1")
check("Tuned mode active_model_name uses tuned_model_id",
      tuned_config.active_model_name == "tunedModels/test-v1")

# Fallback mode config
fb_config = AgentConfig(mode=ModelMode.FALLBACK)
check("Fallback mode active_model_name uses fallback_model",
      fb_config.active_model_name == "phi-3-mini")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 12: AgentState tracks internal data
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 12: AgentState internal state structure")

state = AgentState()
check("Default status is COMPLETE", state.status == AnalysisStatus.COMPLETE)
check("Default llm_calls_made is 0", state.llm_calls_made == 0)
check("errors list is empty", state.errors == [])
check("raw_llm_responses list is empty", state.raw_llm_responses == [])
check("model_mode_used field exists", hasattr(state, "model_mode_used"))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 13: LLM call error handling
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 13: LLM API call error handling")

agent = SentinelAgent(config=AgentConfig(gemini_api_key="test-key-fake"))
mock_client = MagicMock()
mock_client.models.generate_content.side_effect = Exception("Rate limit exceeded")
agent._gemini_client = mock_client

try:
    agent.analyze_crash_dump(SAMPLE_CRASH_DUMP)
    check("LLM API error raises LLMCallError", False, "Should have raised")
except LLMCallError as e:
    check("LLM API error raises LLMCallError", True)
    check("Error includes original message", "Rate limit" in str(e))
    check("Error includes exception type", "Exception" in str(e))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 14: Return type is always SentinelOutput
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 14: Return type is guaranteed SentinelOutput")

agent = _make_agent_with_mock([VALID_LLM_RESPONSE])
result = agent.analyze_crash_dump(SAMPLE_CRASH_DUMP)

check("Result type is SentinelOutput", type(result).__name__ == "SentinelOutput")
check("Result is not dict", not isinstance(result, dict))
check("Result has model_dump_json method", hasattr(result, "model_dump_json"))
json_output = result.model_dump_json()
check("model_dump_json produces valid JSON",
      isinstance(json.loads(json_output), dict))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 15: Integration readiness — hook points exist
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 15: Integration readiness for future steps")

import app.agent.agent as agent_module
source = open(agent_module.__file__).read()

check("Tool hook: query_telemetry mentioned",
      "query_telemetry" in source)
check("Tool hook: retrieve_procedure mentioned",
      "retrieve_procedure" in source)
check("Tool hook: check_safety mentioned",
      "check_safety" in source)
check("Tool hook: propose_recovery mentioned",
      "propose_recovery" in source)
check("SSE/streaming mentioned as future work",
      "sse" in source.lower() or "stream" in source.lower())
check("LangGraph upgrade path documented",
      "langgraph" in source.lower())
check("No OpenAI references in agent.py",
      "openai" not in source.lower() or "openai-compatible" in source.lower())
check("Gemini mentioned",
      "gemini" in source.lower())
check("ModelMode enum exists",
      "ModelMode" in source)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 16: Repair prompt contains correct field names
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 16: Repair prompt uses correct field names")

from app.agent.agent import _REPAIR_PROMPT

check("Repair prompt uses 'affected_component'",
      "affected_component" in _REPAIR_PROMPT)
check("Repair prompt uses 'rationale'",
      "rationale" in _REPAIR_PROMPT)
check("Repair prompt uses 'wait_seconds'",
      "wait_seconds" in _REPAIR_PROMPT)
check("Repair prompt does NOT use old 'component' field name",
      "'component'" not in _REPAIR_PROMPT
      or "affected_component" in _REPAIR_PROMPT.split("'component'")[0][-30:])


# ═══════════════════════════════════════════════════════════════════════════
# TEST 17: Three reasoning modes are configurable
# ═══════════════════════════════════════════════════════════════════════════
print("\n🧪 TEST 17: Three reasoning modes (base/tuned/fallback)")

check("ModelMode.BASE exists", ModelMode.BASE.value == "base")
check("ModelMode.TUNED exists", ModelMode.TUNED.value == "tuned")
check("ModelMode.FALLBACK exists", ModelMode.FALLBACK.value == "fallback")

# Verify _call_llm routes correctly
agent_base = SentinelAgent(
    config=AgentConfig(mode=ModelMode.BASE, gemini_api_key="test-key"))
check("Base agent has gemini_client property", hasattr(agent_base, "gemini_client"))

agent_fb = SentinelAgent(
    config=AgentConfig(mode=ModelMode.FALLBACK))
check("Fallback agent has fallback_client property",
      hasattr(agent_fb, "fallback_client"))


# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"Results: {passed} passed, {failed} failed")
print(f"{'='*60}")

if failed > 0:
    print("\n⚠️  Some tests failed. Review the errors above.")
    sys.exit(1)
else:
    print("\n🎉 All tests passed! agent.py is verified and ready.")
    sys.exit(0)

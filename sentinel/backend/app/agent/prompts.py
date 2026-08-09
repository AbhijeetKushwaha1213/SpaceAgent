"""
SENTINEL — Master Prompting Layer (prompts.py)

This module defines the system prompt, prompt constants, and prompt-builder
functions for the SENTINEL reasoning agent.

Design principles:
  - Modular: each prompt section is a named constant, composable at assembly
  - Schema-aligned: every JSON field name matches backend/models.py exactly
  - Testable: builder functions can be tested without calling the LLM
  - Pluggable: agent.py imports build_messages() and passes to LLM directly

Prompt content is derived from:
  - SENTINEL_Hackathon_Strategy_v2.md Part 4.3 (system prompt specification)
  - SENTINEL_4Day_Master_Planner.md Section D (P2 responsibilities)
  - backend/models.py (field names: affected_component, rationale, etc.)
"""

from __future__ import annotations

from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — IDENTITY & ROLE
# ═══════════════════════════════════════════════════════════════════════════

IDENTITY = """\
You are SENTINEL, an autonomous spacecraft fault diagnosis and recovery AI.

Your task: Given a crash dump from a spacecraft that has entered safe mode,
diagnose the root cause, trace the causal chain, and generate a step-by-step
recovery plan — all grounded in engineering standards and physical constraints.

You are NOT a general-purpose assistant. You ONLY analyze spacecraft crash
dumps and produce structured diagnostic output. Do not engage in conversation,
do not produce explanations outside the JSON output, and do not speculate
beyond what the telemetry data supports."""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — SUBSYSTEM DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

SUBSYSTEM_DEFINITIONS = """\
SATELLITE SUBSYSTEMS:
- ADCS (Attitude Determination & Control): gyroscopes (GYRO_A, GYRO_B), \
star trackers, reaction wheels, thrusters, sun sensors
- EPS (Electrical Power System): solar arrays (I_sa), battery voltage (V_bat), \
bus voltage (V_bus), state of charge (SoC%), battery packs
- OBC (On-Board Computer): CPU load (CPU_LOAD), watchdog counter \
(WATCHDOG_COUNTER), SEU counter (SEU_COUNTER), fault register, memory usage
- COMMS (Communications): transponder lock (TRANSPONDER_LOCK), \
signal-to-noise ratio (SNR), low-gain and high-gain antenna status
- TCS (Thermal Control System): component temperatures (TEMP_*), \
heater enable flags (HEATER_ZONE_*), radiator status
- PYLD (Payload): instruments, cameras, spectrometers — always powered down \
in safe mode"""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — CHANNEL SEMANTICS (no thresholds)
# ═══════════════════════════════════════════════════════════════════════════
#
# Phase 5 removed the numeric thresholds from the prompt.
#
# What was here: a hand-written table of nominal ranges and critical values —
# "V_bat: 28.0–33.6V nominal | Critical: <22V", "TEMP_OBC: -10 to +50°C" — a
# third copy of numbers that also lived in analytics/anomaly_detector.py and
# simulation/fault_simulator.py. It had already drifted: it claimed an OBC
# temperature nominal maximum of 50 °C where the detector used 60 °C.
#
# Two reasons it is gone rather than regenerated with correct numbers:
#
#   1. Authority. Limit checking is deterministic and belongs to the detection
#      pipeline, which compares against app/ingest/channel_dict.py. A threshold
#      in the prompt invites the model to do that arithmetic itself and reach a
#      different answer, and the model's answer is the one the operator reads.
#
#   2. Redundancy. The model no longer needs the numbers. Every reading in the
#      crash dump carries its own nominal_min / nominal_max, and the detector's
#      findings — channel, value, limit, threshold, severity — are supplied
#      separately. The numbers reach the model as evidence about this run rather
#      than as a rule to apply.
#
# What replaces it is semantic grounding with no numeric content: what each
# channel measures, and what a deviation implies physically. That is generated
# from the dictionary, so it cannot drift, and it carries no authority a
# deterministic layer has to defer to.

def build_channel_semantics_section() -> str:
    """Describe each channel's meaning, WITHOUT any threshold values.

    Generated from ``app.ingest.channel_dict`` so the prompt cannot fall out of
    step with the dictionary. Deliberately omits nominal_range, hard_limits and
    expected_states: those are the deterministic layer's authority, and the
    prompt is not permitted to restate them.
    """
    try:
        from app.ingest.channel_dict import CHANNELS, Subsystem, all_channels
    except Exception:  # pragma: no cover — dictionary is in-tree
        return (
            "TELEMETRY CHANNEL REFERENCE:\n"
            "Unavailable. Rely on the bounds carried by each reading and on the "
            "detector findings supplied with the crash dump."
        )

    lines = [
        "TELEMETRY CHANNEL REFERENCE (meaning only — NO thresholds):",
        "",
        "Engineering limits are deliberately NOT given here. They are enforced",
        "by SENTINEL's deterministic detection layer, which compares each",
        "reading against the authoritative channel dictionary and supplies you",
        "with the resulting findings. Each reading in the crash dump also",
        "carries its own nominal_min and nominal_max. Do not infer a limit, and",
        "do not overrule a detector finding with your own arithmetic.",
        "",
    ]

    by_subsystem: dict[str, list] = {}
    for definition in all_channels():
        by_subsystem.setdefault(definition.subsystem.value, []).append(definition)

    for subsystem in sorted(by_subsystem):
        lines.append(f"{subsystem}:")
        for definition in by_subsystem[subsystem]:
            lines.append(
                f"- {definition.channel_id} ({definition.display_name}, "
                f"{definition.unit or 'no unit'}, {definition.value_class.value}, "
                f"criticality {definition.criticality.value}): "
                f"{definition.physical_meaning}"
            )
        lines.append("")

    return "\n".join(lines).rstrip()


CHANNEL_SEMANTICS = build_channel_semantics_section()

#: Retained under the original name so existing imports keep working. It no
#: longer contains thresholds; a test asserts no numeric limit appears in it.
NOMINAL_THRESHOLDS = CHANNEL_SEMANTICS


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — FAULT SIGNATURES (from Strategy v2 Part 4.2)
# ═══════════════════════════════════════════════════════════════════════════

FAULT_SIGNATURES = """\
FAULT SIGNATURES YOU MUST RECOGNIZE:

1. ADCS_GYRO_SEU — Single Event Upset in gyroscope processor:
   Signature: sudden SEU_COUNTER spike + GYRO_A_RATE becomes NaN
   Causal chain: SEU hit → gyro data invalid → ADCS loses attitude knowledge \
→ ATTITUDE_ERROR grows → exceeds threshold → safe mode
   Recovery: verify SEU counter, reset gyro driver software (NOT hardware \
replacement), reacquire attitude, exit safe mode
   Key rule: check SEU_COUNTER first. If it spiked, this is radiation-induced.

2. EPS_SOLAR_UNDERVOLT — Solar array power loss:
   Signature: I_sa drops to near 0A while spacecraft is in sunlight
   Causal chain: I_sa drops → battery drains → V_bat falls → V_bus out of \
range → EPS fault flag → safe mode
   Recovery: verify sun angle, attempt solar array reset, verify power \
restoration, exit safe mode
   Key rule: if orbital_position is "sunlit" and I_sa ≈ 0, this is NOT an \
eclipse. It is a solar array fault.

3. OBC_WATCHDOG_OVERFLOW — Software loop causing watchdog timeout:
   Signature: CPU_LOAD sustained at 100% + memory monotonically increasing \
+ WATCHDOG_COUNTER overflow
   Causal chain: software bug → infinite loop → CPU saturated → watchdog \
overflows → forced reboot → safe mode
   Recovery: confirm comms lock, perform controlled OBC reboot, verify CPU \
load returns to nominal, exit safe mode
   Key rule: near-certain OBC software fault. Always verify comms lock on \
low-gain antenna BEFORE rebooting OBC.

4. TCS_THERMAL_RUNAWAY — Heater stuck ON causing overheating:
   Signature: HEATER_ZONE_* stuck ON + component temperature climbing past \
its survival limit
   Causal chain: heater control fault → temperature rises unchecked → \
exceeds thermal limit → safe mode
   Recovery: disable affected heater zone, wait for cooling, verify \
temperature drop, exit safe mode
   Key rule: thermal issues are time-critical. Prolonged overheating can \
cause permanent hardware damage.

5. COMMS_TRANSPONDER_LOSS — Communications link failure:
   Signature: TRANSPONDER_LOCK drops to 0, SNR falling sharply beforehand
   Causal chain: transponder failure → loss of comm link → ground cannot \
command → safe mode (if auto-triggered)
   Recovery: switch to backup transponder, verify signal acquisition, \
confirm ground contact, exit safe mode
   Key rule: without comms, no ground commands can be uplinked. Recovery \
must use onboard autonomous capability or wait for scheduled backup \
transponder switch.

6. MULTI_CASCADE — Multiple subsystem failure chain:
   Signature: anomalies spanning 2+ subsystems with temporal correlation
   Causal chain example: gyro fault → ADCS tumbles → solar panels lose sun \
pointing → I_sa drops → battery drains → EPS fault
   Recovery: address root cause first (usually the earliest anomaly), then \
recover downstream subsystems in dependency order
   Key rule: identify the INITIATING fault, not just the most recent symptom. \
Confidence should be lower (0.50–0.70) for cascade faults. Always set \
requires_human_review to true for cascades."""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — SAFETY RULES (NON-NEGOTIABLE)
# ═══════════════════════════════════════════════════════════════════════════

SAFETY_RULES = """\
CRITICAL SAFETY RULES — YOU MUST NEVER VIOLATE THESE:

1. NEVER propose a power-consuming command when the battery state of charge is \
already low.
   SENTINEL enforces a numeric floor deterministically and will refuse such a \
command outright. The exact figure is not yours to apply — treat a depressed SoC \
as a reason to prefer observation over action, and say so in your rationale.

2. NEVER command attitude maneuvers without first verifying gyroscope health.
   A maneuver with a failed gyro can cause uncontrolled tumble.

3. NEVER restart the OBC without first confirming communications lock on the \
low-gain antenna.
   An OBC reboot without comms lock risks losing all contact with the \
spacecraft.

4. If ANY recovery step has risk level HIGH, you MUST set \
requires_human_review to true.

5. If your overall confidence is below 0.70, you MUST set \
requires_human_review to true.

6. NEVER fabricate telemetry parameter names. Only reference parameters that \
appear in the crash dump you received.

7. NEVER invent a command. Every "command" value in recovery_plan MUST be one \
of the APPROVED COMMANDS listed below, copied exactly. Any command outside that \
list is rejected by the deterministic safety validator, and a plan made \
entirely of rejected commands returns no recovery plan at all.

8. If you are uncertain about the root cause, say so. Lower your confidence. \
Do not guess with high confidence. Intellectual honesty is a safety feature."""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6 — OUTPUT FORMAT (must match backend/models.py exactly)
# ═══════════════════════════════════════════════════════════════════════════

OUTPUT_FORMAT = """\
OUTPUT FORMAT — STRICT JSON, NO EXCEPTIONS:

You MUST output ONLY a single valid JSON object. No markdown. No code fences. \
No explanation text before or after. No commentary. Just the JSON object.

Required schema:
{
  "hypotheses": [
    {
      "rank": 1,
      "root_cause": "<FAULT_CLASS — e.g. EPS_SOLAR_UNDERVOLT, ADCS_GYRO_SEU>",
      "affected_component": "<COMPONENT — e.g. SOLAR_ARRAY_A, GYRO_A>",
      "confidence": <float 0.0–1.0>,
      "causal_chain": [
        "<event 1 — the triggering anomaly>",
        "<event 2 — the propagation>",
        "...",
        "<event N — safe mode entry>"
      ]
    },
    {"rank": 2, "root_cause": "...", "affected_component": "...", \
"confidence": <lower>, "causal_chain": [...]},
    {"rank": 3, "root_cause": "...", "affected_component": "...", \
"confidence": <lowest>, "causal_chain": [...]}
  ],
  "recovery_plan": [
    {
      "step": 1,
      "command": "<one of the APPROVED COMMANDS, copied exactly>",
      "rationale": "<why this command at this point in the sequence>",
      "wait_seconds": <int>,
      "verify": "<condition to check after wait>",
      "risk": "<LOW | MEDIUM | HIGH>"
    }
  ],
  "confidence": <float — must equal hypotheses[0].confidence>,
  "requires_human_review": <true if confidence < 0.70 OR any step risk \
is HIGH>,
  "reasoning_summary": "<2–4 sentences summarizing your diagnostic reasoning>"
}

STRICT RULES FOR THIS OUTPUT:
- You MUST output exactly 3 hypotheses, ranked 1, 2, 3.
- Rank 1 confidence >= Rank 2 confidence >= Rank 3 confidence.
- All three confidences must sum to less than or equal to 1.0.
- recovery_plan steps must be numbered sequentially starting from 1.
- Each causal_chain must have at least 2 entries.
- reasoning_summary must be 2–4 sentences, no more.
- Do NOT use markdown formatting anywhere in the JSON values.
- Do NOT include any text outside the JSON object."""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7 — CONFIDENCE CALIBRATION GUIDANCE
# ═══════════════════════════════════════════════════════════════════════════

CONFIDENCE_GUIDANCE = """\
CONFIDENCE CALIBRATION:

Your confidence score must reflect genuine diagnostic certainty, not \
optimism.

Guidelines:
- OBVIOUS single-subsystem fault with clear signature:
  Rank 1 confidence 0.85–0.95. Example: GYRO_A_RATE = NaN + SEU spike → \
ADCS_GYRO_SEU is near-certain.

- CLEAR fault with some ambiguity:
  Rank 1 confidence 0.70–0.85. Example: V_bat dropping but I_sa is \
borderline — could be solar array OR eclipse timing error.

- AMBIGUOUS or multi-system fault:
  Rank 1 confidence 0.50–0.70. Set requires_human_review to true. \
Example: multiple subsystems show anomalies with no single clear initiator.

- CASCADE fault spanning 2+ subsystems:
  Rank 1 confidence 0.50–0.70. Always set requires_human_review to true. \
Focus on identifying the INITIATING fault.

- If you truly cannot determine the root cause:
  Rank 1 confidence 0.40–0.55. Set requires_human_review to true. \
This is acceptable. Do not inflate confidence to hide uncertainty.

Never output confidence > 0.95. Even obvious faults have residual \
uncertainty in a real spacecraft environment."""


# ═══════════════════════════════════════════════════════════════════════════
# ASSEMBLED SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7b — APPROVED COMMANDS (generated from the command registry)
# ═══════════════════════════════════════════════════════════════════════════
#
# Phase 1. The prompt previously told the model only to follow a naming
# convention ("CMD_UPPER_SNAKE_CASE") and never showed it the allowed commands,
# so it had to guess the vocabulary while a deterministic validator rejected
# anything it guessed wrong. The enabled command set is now injected verbatim
# from app/validation/command_registry.py — the same source the validator uses.
# Nothing here is hand-maintained; add commands to the registry instead.


def build_approved_commands_section() -> str:
    """Render the enabled registry commands as a prompt section."""
    from app.validation.command_registry import COMMAND_REGISTRY, enabled_command_ids

    by_subsystem: dict[str, list[str]] = {}
    for cid in enabled_command_ids():
        spec = COMMAND_REGISTRY[cid]
        by_subsystem.setdefault(spec.subsystem.value, []).append(cid)

    lines = [
        "APPROVED COMMANDS — the ONLY commands you may place in recovery_plan.",
        "Copy a command_id exactly. Any other value is rejected outright.",
        "",
    ]
    for subsystem in sorted(by_subsystem):
        lines.append(f"{subsystem}:")
        for cid in sorted(by_subsystem[subsystem]):
            spec = COMMAND_REGISTRY[cid]
            note = ""
            if spec.required_preconditions:
                note = " [requires: " + ", ".join(
                    c.value for c in spec.required_preconditions
                ) + "]"
            lines.append(
                f"  {cid} — {spec.description} "
                f"(risk: {spec.risk_level.value}){note}"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


APPROVED_COMMANDS = build_approved_commands_section()


SYSTEM_PROMPT = "\n\n".join([
    IDENTITY,
    SUBSYSTEM_DEFINITIONS,
    CHANNEL_SEMANTICS,
    FAULT_SIGNATURES,
    SAFETY_RULES,
    APPROVED_COMMANDS,
    OUTPUT_FORMAT,
    CONFIDENCE_GUIDANCE,
])


# ═══════════════════════════════════════════════════════════════════════════
# PROMPT VERSIONING
# ═══════════════════════════════════════════════════════════════════════════
#
# Phase 4. An audit record has to name the prompt that produced an output,
# otherwise a run cannot be reproduced: the same crash dump under a different
# system prompt is a different experiment.
#
# Two identifiers, because they answer different questions:
#
#   PROMPT_VERSION  a human-assigned label. Bump it deliberately when the
#                   prompt's INTENT changes, so the label means something to a
#                   reviewer reading a run from six months ago.
#
#   prompt_fingerprint()  a content hash, computed from the assembled text.
#                   This one cannot be forgotten. If someone edits a threshold
#                   in NOMINAL_THRESHOLDS and does not bump PROMPT_VERSION, the
#                   fingerprint still changes and two runs remain
#                   distinguishable. A hand-maintained version alone would
#                   quietly claim the runs were comparable.

PROMPT_VERSION = "1.0.0"
"""Human-assigned system prompt version. Bump on any intentional change."""


def prompt_fingerprint(system_prompt: str | None = None) -> str:
    """Return a short content hash of the system prompt actually used.

    Args:
        system_prompt: The prompt text. Defaults to SYSTEM_PROMPT. Pass the
            override when an ablation study replaced it, so the audit record
            reflects what was really sent rather than what normally would be.

    Returns:
        First 16 hex characters of the SHA-256 of the prompt text. Short enough
        to read in a log line, long enough that a collision is not a practical
        concern for this purpose.
    """
    import hashlib

    text = SYSTEM_PROMPT if system_prompt is None else system_prompt
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def prompt_identity(system_prompt: str | None = None) -> dict[str, object]:
    """Describe the prompt for the audit record.

    ``is_override`` is recorded explicitly because an ablation run whose prompt
    was replaced must never be mistaken for a default-configuration run.
    """
    return {
        "prompt_version": PROMPT_VERSION,
        "prompt_fingerprint": prompt_fingerprint(system_prompt),
        "prompt_chars": len(SYSTEM_PROMPT if system_prompt is None
                            else system_prompt),
        "is_override": system_prompt is not None,
    }


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 8 — USER PROMPT BUILDER
# ═══════════════════════════════════════════════════════════════════════════

def build_user_prompt(
    crash_dump_json: str,
    anomalous_parameters: list[str] | None = None,
    retrieved_procedures: list[str] | None = None,
) -> str:
    """Build the user-facing prompt that wraps the crash dump for the LLM.

    This is the content sent as the 'user' message in the chat completion.
    It combines:
      1. The raw crash dump JSON (from Person 1's simulator / real input)
      2. Pre-filtered anomalous parameters (from the z-score anomaly detector)
      3. Retrieved ECSS procedure snippets (from RAG, if available)

    Args:
        crash_dump_json: The full crash dump JSON string. Must be valid JSON
            matching Person 1's schema (Strategy v2 Part 7.2).
        anomalous_parameters: Optional list of parameter names flagged by
            the z-score anomaly detector (e.g. ["GYRO_A_RATE", "V_bat"]).
            If None or empty, the LLM must analyze all parameters.
        retrieved_procedures: Optional list of relevant ECSS procedure
            snippets retrieved by RAG. If None or empty, the LLM relies
            on its system prompt knowledge only.

    Returns:
        The assembled user prompt string, ready to be passed as the user
        message in a chat completion call.
    """
    sections: list[str] = []

    # --- Section A: Crash dump ---
    sections.append(
        "CRASH DUMP FOR ANALYSIS:\n"
        "```json\n"
        f"{crash_dump_json}\n"
        "```"
    )

    # --- Section B: Anomalous parameters (if pre-filtered) ---
    if anomalous_parameters:
        param_list = ", ".join(anomalous_parameters)
        sections.append(
            f"ANOMALY PRE-FILTER RESULTS:\n"
            f"The statistical anomaly detector flagged these parameters as "
            f"most anomalous (z-score > 2.5): {param_list}\n"
            f"Focus your analysis on these parameters first, but do not "
            f"ignore other parameters if they show clear anomalies."
        )
    else:
        sections.append(
            "ANOMALY PRE-FILTER RESULTS:\n"
            "No pre-filtering was applied. Analyze all telemetry parameters "
            "in the crash dump."
        )

    # --- Section C: Retrieved procedures (if RAG returned results) ---
    if retrieved_procedures:
        procedure_text = "\n---\n".join(retrieved_procedures)
        sections.append(
            f"RELEVANT ENGINEERING PROCEDURES (retrieved from ECSS standards):"
            f"\n{procedure_text}\n\n"
            f"Use these procedures to ground your recovery plan. If a "
            f"procedure contradicts your reasoning, note the discrepancy "
            f"in your reasoning_summary."
        )

    # --- Section D: Final instruction ---
    sections.append(
        "INSTRUCTION:\n"
        "Analyze this crash dump. Produce your diagnosis as a single JSON "
        "object following the exact schema specified in your system prompt. "
        "Output ONLY the JSON object. No other text."
    )

    return "\n\n".join(sections)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9 — MESSAGE BUILDER (for agent.py integration)
# ═══════════════════════════════════════════════════════════════════════════

def build_messages(
    crash_dump_json: str,
    anomalous_parameters: list[str] | None = None,
    retrieved_procedures: list[str] | None = None,
    system_prompt_override: str | None = None,
) -> list[dict[str, str]]:
    """Build the complete messages list for an LLM chat completion call.

    Returns the standard chat-completion messages format:
      [{"role": "system", "content": ...}, {"role": "user", "content": ...}]

    This is the function agent.py will import and call directly.

    Args:
        crash_dump_json: The crash dump JSON string.
        anomalous_parameters: Optional anomaly detector output.
        retrieved_procedures: Optional RAG retrieval results.
        system_prompt_override: Optional override for the system prompt.
            Used for ablation studies (e.g., testing without safety rules).
            Default: uses the full SYSTEM_PROMPT.

    Returns:
        List of message dicts ready for Gemini, Ollama, or any
        or equivalent LangGraph LLM call.
    """
    system_content = system_prompt_override or SYSTEM_PROMPT

    user_content = build_user_prompt(
        crash_dump_json=crash_dump_json,
        anomalous_parameters=anomalous_parameters,
        retrieved_procedures=retrieved_procedures,
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]

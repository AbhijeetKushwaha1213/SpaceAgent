"""
SENTINEL — Deterministic Telecommand Builder Boundary (tc_builder.py)

Phase 8. Converts an ALREADY safety-approved RecoveryPlan into a validated
binary telecommand (TC) representation.

Design Invariants:
1. STRICT SAFETY GATING: Telecommand encoding requires explicit safety validation
   approval (SafetyStatus.VALIDATED). Any non-validated, partially-blocked, or
   review-required plan fails closed and emits NO outbound frames.
2. CRITICAL TELEMETRY ENFORCEMENT: Unlike diagnostic advisory planning, the
   ground-segment telecommand builder refuses to construct commands if critical
   telemetry required by declared preconditions evaluates to UNKNOWN.
3. PHYSICS BOUNDARY INTEGRITY: If physics validation has refuted the hypothesis
   motivating the recovery plan, telecommand generation is strictly rejected.
4. DETERMINISTIC ENCODING: Bitwise-reproducible binary encoding with fixed header,
   opcode, sequence counter, length, validated parameters, and CRC-16-CCITT trailer.
5. NO REAL TRANSMISSION: Real spacecraft transmission or hardware uplink is
   strictly disabled by design. Outbound boundaries are non-transmitting.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional, Sequence, Union

from app.api.models import RecoveryStep, RiskLevel, SafetyStatus, SentinelOutput
from app.agent.safety import ValidationResult
from app.validation.command_registry import (
    COMMAND_REGISTRY,
    CommandSpec,
    Condition,
    all_command_ids,
    get_command,
    is_enabled,
    is_registered,
)
from app.validation.conditions import ConditionState, evaluate_condition


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — BINARY FRAME SPECIFICATION & CRC-16-CCITT
# ═══════════════════════════════════════════════════════════════════════════

TC_SYNC_WORD = 0x1ACF
"""16-bit Telecommand Synchronization Marker (standard CCSDS TC sync word)."""

TC_PROTOCOL_VERSION = 0x01
"""Telecommand builder protocol version."""


def crc16_ccitt(data: bytes, init: int = 0xFFFF) -> int:
    """Calculate 16-bit CRC-CCITT (polynomial 0x1021, init 0xFFFF)."""
    crc = init
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — DETERMINISTIC OPCODE MAPPING
# ═══════════════════════════════════════════════════════════════════════════

def _build_deterministic_opcode_maps() -> tuple[dict[str, int], dict[int, str]]:
    """Build bijective mapping between command_id and uint16 opcode.

    Subsystems receive distinct high nibbles (0x1 to 0x6), and commands within
    each subsystem are numbered monotonically in alphabetical order.
    """
    subsystem_prefixes = {
        "ADCS": 0x1000,
        "EPS": 0x2000,
        "COMMS": 0x3000,
        "THERMAL": 0x4000,
        "CDH": 0x5000,
        "PAYLOAD": 0x6000,
    }

    cmd_to_op: dict[str, int] = {}
    op_to_cmd: dict[int, str] = {}

    subsystem_counters: dict[str, int] = {k: 1 for k in subsystem_prefixes}

    for cid in all_command_ids():
        spec = COMMAND_REGISTRY[cid]
        subsys = spec.subsystem.value.upper()
        base = subsystem_prefixes.get(subsys, 0x7000)
        idx = subsystem_counters.get(subsys, 1)
        subsystem_counters[subsys] = idx + 1

        opcode = base | (idx & 0x0FFF)
        cmd_to_op[cid] = opcode
        op_to_cmd[opcode] = cid

    return cmd_to_op, op_to_cmd


COMMAND_TO_OPCODE, OPCODE_TO_COMMAND = _build_deterministic_opcode_maps()


def get_command_opcode(command_id: str) -> Optional[int]:
    """Return 16-bit opcode for a registered command."""
    return COMMAND_TO_OPCODE.get(command_id)


def get_command_id_from_opcode(opcode: int) -> Optional[str]:
    """Resolve 16-bit opcode back to registered command ID."""
    return OPCODE_TO_COMMAND.get(opcode)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — PARAMETER SERIALIZATION
# ═══════════════════════════════════════════════════════════════════════════

class ParamTypeTag(int, Enum):
    """TLV Type Tags for deterministic parameter serialization."""
    INT32 = 0x01
    FLOAT64 = 0x02
    BOOL = 0x03
    STRING = 0x04


def encode_parameters(params: Optional[dict[str, Any]]) -> bytes:
    """Encode parameter dictionary deterministically in key-sorted order.

    Raises ValueError if non-finite (NaN/Inf) or unsupported types are encountered.
    """
    if not params:
        return b""

    buf = bytearray()
    # Sort keys for deterministic bitwise output
    for key in sorted(params.keys()):
        val = params[key]
        key_bytes = key.encode("utf-8")
        if len(key_bytes) > 255:
            raise ValueError(f"Parameter key '{key}' exceeds 255 bytes")

        # Key length (1 byte) + key bytes
        buf.append(len(key_bytes))
        buf.extend(key_bytes)

        if isinstance(val, bool):
            buf.append(ParamTypeTag.BOOL)
            buf.append(1 if val else 0)
        elif isinstance(val, int):
            buf.append(ParamTypeTag.INT32)
            buf.extend(struct.pack(">i", val))
        elif isinstance(val, float):
            if not math.isfinite(val):
                raise ValueError(f"Parameter '{key}' has non-finite value: {val}")
            buf.append(ParamTypeTag.FLOAT64)
            buf.extend(struct.pack(">d", val))
        elif isinstance(val, str):
            val_bytes = val.encode("utf-8")
            if len(val_bytes) > 65535:
                raise ValueError(f"Parameter string value exceeds 65535 bytes")
            buf.append(ParamTypeTag.STRING)
            buf.extend(struct.pack(">H", len(val_bytes)))
            buf.extend(val_bytes)
        else:
            raise TypeError(f"Unsupported parameter type for '{key}': {type(val).__name__}")

    return bytes(buf)


def decode_parameters(data: bytes) -> dict[str, Any]:
    """Decode serialized parameter bytes back into a dictionary."""
    if not data:
        return {}

    params: dict[str, Any] = {}
    offset = 0
    total_len = len(data)

    while offset < total_len:
        if offset + 1 > total_len:
            raise ValueError("Truncated parameter key length")
        key_len = data[offset]
        offset += 1

        if offset + key_len > total_len:
            raise ValueError("Truncated parameter key")
        key = data[offset:offset + key_len].decode("utf-8")
        offset += key_len

        if offset + 1 > total_len:
            raise ValueError(f"Missing type tag for parameter '{key}'")
        tag = data[offset]
        offset += 1

        if tag == ParamTypeTag.BOOL:
            if offset + 1 > total_len:
                raise ValueError(f"Truncated boolean for '{key}'")
            params[key] = bool(data[offset])
            offset += 1
        elif tag == ParamTypeTag.INT32:
            if offset + 4 > total_len:
                raise ValueError(f"Truncated int32 for '{key}'")
            params[key] = struct.unpack(">i", data[offset:offset + 4])[0]
            offset += 4
        elif tag == ParamTypeTag.FLOAT64:
            if offset + 8 > total_len:
                raise ValueError(f"Truncated float64 for '{key}'")
            val = struct.unpack(">d", data[offset:offset + 8])[0]
            if not math.isfinite(val):
                raise ValueError(f"Decoded non-finite float for '{key}'")
            params[key] = val
            offset += 8
        elif tag == ParamTypeTag.STRING:
            if offset + 2 > total_len:
                raise ValueError(f"Truncated string length for '{key}'")
            str_len = struct.unpack(">H", data[offset:offset + 2])[0]
            offset += 2
            if offset + str_len > total_len:
                raise ValueError(f"Truncated string content for '{key}'")
            params[key] = data[offset:offset + str_len].decode("utf-8")
            offset += str_len
        else:
            raise ValueError(f"Unknown parameter tag 0x{tag:02X} for '{key}'")

    return params


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — TELECOMMAND FRAME MODEL
# ═══════════════════════════════════════════════════════════════════════════

class TCFrameDecodeError(ValueError):
    """Raised when decoding a corrupted or invalid telecommand frame."""


@dataclass(frozen=True)
class TCFrame:
    """Deterministic binary telecommand frame structure.

    Layout (Big-Endian):
      0..1   : Sync Word (0x1ACF)
      2      : Version (0x01)
      3      : Subsystem Code (uint8)
      4..5   : Sequence Counter (uint16)
      6..7   : Command Opcode (uint16)
      8..9   : Parameter Payload Length N (uint16)
      10..10+N: Parameter Payload (N bytes)
      10+N..11+N: CRC-16-CCITT Checksum (uint16)
    """

    sync_word: int
    version: int
    subsystem: str
    subsystem_code: int
    sequence_counter: int
    command_id: str
    opcode: int
    parameters: dict[str, Any]
    payload_bytes: bytes
    crc: int
    raw_bytes: bytes

    @property
    def frame_length(self) -> int:
        return len(self.raw_bytes)

    def hex(self) -> str:
        return self.raw_bytes.hex().upper()

    @classmethod
    def encode(
        cls,
        command_id: str,
        sequence_counter: int,
        parameters: Optional[dict[str, Any]] = None,
    ) -> TCFrame:
        """Encode an approved command into a deterministic binary TCFrame."""
        if not is_registered(command_id):
            raise ValueError(f"Cannot encode unregistered command '{command_id}'")
        spec = COMMAND_REGISTRY[command_id]
        if not spec.enabled:
            raise ValueError(f"Cannot encode disabled command '{command_id}': {spec.disabled_reason}")

        opcode = COMMAND_TO_OPCODE[command_id]
        seq = int(sequence_counter) & 0xFFFF

        # Determine subsystem code
        subsys_name = spec.subsystem.value.upper()
        subsys_codes = {
            "ADCS": 1,
            "EPS": 2,
            "COMMS": 3,
            "THERMAL": 4,
            "CDH": 5,
            "PAYLOAD": 6,
        }
        subsys_code = subsys_codes.get(subsys_name, 7)

        # Encode parameters
        param_dict = dict(parameters) if parameters else {}
        payload = encode_parameters(param_dict)
        payload_len = len(payload)
        if payload_len > 65535:
            raise ValueError(f"Payload length {payload_len} exceeds 16-bit capacity")

        # Build frame header (10 bytes) + payload
        header = struct.pack(
            ">HBBHHH",
            TC_SYNC_WORD,
            TC_PROTOCOL_VERSION,
            subsys_code,
            seq,
            opcode,
            payload_len,
        )
        body = header + payload

        # Calculate CRC over header + payload
        checksum = crc16_ccitt(body)
        raw = body + struct.pack(">H", checksum)

        return cls(
            sync_word=TC_SYNC_WORD,
            version=TC_PROTOCOL_VERSION,
            subsystem=subsys_name,
            subsystem_code=subsys_code,
            sequence_counter=seq,
            command_id=command_id,
            opcode=opcode,
            parameters=param_dict,
            payload_bytes=payload,
            crc=checksum,
            raw_bytes=raw,
        )

    @classmethod
    def decode(cls, raw: bytes) -> TCFrame:
        """Decode and verify a binary telecommand frame."""
        if len(raw) < 12:
            raise TCFrameDecodeError(f"Frame length {len(raw)} is less than minimum 12 bytes")

        # Check CRC-16
        expected_crc = struct.unpack(">H", raw[-2:])[0]
        body = raw[:-2]
        computed_crc = crc16_ccitt(body)
        if computed_crc != expected_crc:
            raise TCFrameDecodeError(
                f"CRC-16 mismatch: expected 0x{expected_crc:04X}, computed 0x{computed_crc:04X}"
            )

        # Parse header
        sync_word, version, subsys_code, seq, opcode, payload_len = struct.unpack(">HBBHHH", body[:10])
        if sync_word != TC_SYNC_WORD:
            raise TCFrameDecodeError(f"Invalid sync word 0x{sync_word:04X}, expected 0x{TC_SYNC_WORD:04X}")
        if version != TC_PROTOCOL_VERSION:
            raise TCFrameDecodeError(f"Unsupported protocol version {version}, expected {TC_PROTOCOL_VERSION}")

        if len(body[10:]) != payload_len:
            raise TCFrameDecodeError(
                f"Payload length mismatch: header declares {payload_len} bytes, got {len(body[10:])}"
            )

        payload = body[10:]
        cmd_id = get_command_id_from_opcode(opcode)
        if cmd_id is None:
            raise TCFrameDecodeError(f"Unknown opcode 0x{opcode:04X}")

        spec = COMMAND_REGISTRY[cmd_id]
        params = decode_parameters(payload)

        return cls(
            sync_word=sync_word,
            version=version,
            subsystem=spec.subsystem.value.upper(),
            subsystem_code=subsys_code,
            sequence_counter=seq,
            command_id=cmd_id,
            opcode=opcode,
            parameters=params,
            payload_bytes=payload,
            crc=expected_crc,
            raw_bytes=raw,
        )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — BUILD STATUS & VERDICT
# ═══════════════════════════════════════════════════════════════════════════

class TCBuildStatus(str, Enum):
    """Deterministic telecommand build verdict."""
    APPROVED = "APPROVED"
    """All commands passed safety validation, critical telemetry checks, and parameter checks."""

    BLOCKED = "BLOCKED"
    """Safety validation was not fully approved (blocked steps, review required, or not validated)."""

    REJECTED = "REJECTED"
    """Plan was rejected due to missing preconditions, UNKNOWN critical telemetry, or invalid parameters."""


@dataclass(frozen=True)
class TCBuildResult:
    """The structured result of telecommand building."""
    status: TCBuildStatus
    frames: tuple[TCFrame, ...] = ()
    total_frames: int = 0
    total_bytes: int = 0
    is_transmittable: bool = False
    """Always False. Real spacecraft uplink is disabled by architectural design."""

    reason: str = ""
    rejection_details: tuple[str, ...] = ()

    @property
    def is_approved(self) -> bool:
        return self.status is TCBuildStatus.APPROVED


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6 — THE OUTBOUND TRANSMISSION BOUNDARY (STRICTLY DISABLED)
# ═══════════════════════════════════════════════════════════════════════════

class UplinkDisabledError(RuntimeError):
    """Raised if any component attempts real spacecraft transmission."""


class OutboundUplinkBoundary:
    """The ground-segment telecommand outbound transmission boundary.

    STRICT SAFETY INVARIANT:
    Real spacecraft transmission or hardware uplink is disabled by design.
    This software boundary guarantees that no network sockets, RF modems,
    or transmission devices are engaged.
    """

    ENABLED: bool = False
    """Transmission is disabled by design."""

    @classmethod
    def transmit(cls, frames: Sequence[TCFrame]) -> None:
        """Refuse any transmission attempt."""
        raise UplinkDisabledError(
            "UPLINK_DISABLED: Real spacecraft transmission or RF uplink is disabled by design. "
            "SENTINEL Phase 8 provides deterministic ground-segment binary validation only."
        )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7 — THE AUTHORITATIVE BUILDER PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

def build_telecommand_stream(
    recovery_plan: Union[SentinelOutput, list[RecoveryStep], Sequence[RecoveryStep]],
    safety_validation: ValidationResult,
    crash_dump: Optional[dict[str, Any]] = None,
    physics_report: Optional[Any] = None,
    parameters_map: Optional[dict[int, dict[str, Any]]] = None,
    sequence_start: int = 1,
) -> TCBuildResult:
    """Convert an already safety-approved recovery plan into binary telecommands.

    Parameters:
      - recovery_plan: The plan proposed (SentinelOutput or sequence of RecoveryStep)
      - safety_validation: The authoritative ValidationResult from validate_recovery_plan()
      - crash_dump: Context dictionary containing telemetry for precondition evaluation
      - physics_report: Optional Phase 8 PhysicsValidationReport to verify physics consistency
      - parameters_map: Optional mapping of step_number -> parameters dict
      - sequence_start: Starting sequence counter (uint16)

    Returns:
      TCBuildResult containing approved TCFrames if fully validated, or BLOCKED/REJECTED
      with zero frames if any safety, telemetry, physics, or parameter check fails.
    """
    ctx = crash_dump if isinstance(crash_dump, dict) else {}
    details: list[str] = []

    # ── Check 1: Explicit Safety Validation Status ─────────────────────────
    if not isinstance(safety_validation, ValidationResult):
        return TCBuildResult(
            status=TCBuildStatus.BLOCKED,
            reason="REJECTED: Missing or invalid safety validation result.",
            rejection_details=("SAFETY_VALIDATION_MISSING",),
        )

    if safety_validation.safety_status is not SafetyStatus.VALIDATED:
        reason = (
            f"REJECTED: Safety validation status is {safety_validation.safety_status.value}. "
            f"Only fully approved (VALIDATED) plans may be encoded into telecommands."
        )
        return TCBuildResult(
            status=TCBuildStatus.BLOCKED,
            reason=reason,
            rejection_details=(f"SAFETY_STATUS_{safety_validation.safety_status.value}",),
        )

    if not safety_validation.is_safe or safety_validation.blocked_steps:
        return TCBuildResult(
            status=TCBuildStatus.BLOCKED,
            reason=f"REJECTED: Plan contains {len(safety_validation.blocked_steps)} blocked step(s).",
            rejection_details=tuple(b.violation_code for b in safety_validation.blocked_steps),
        )

    # ── Check 2: Extract Steps & Cross-Check Approval ──────────────────────
    if isinstance(recovery_plan, SentinelOutput):
        steps = recovery_plan.recovery_plan
        top_hyp = recovery_plan.hypotheses[0] if getattr(recovery_plan, "hypotheses", None) else None
    elif isinstance(recovery_plan, (list, tuple)):
        steps = list(recovery_plan)
        top_hyp = None
    else:
        return TCBuildResult(
            status=TCBuildStatus.REJECTED,
            reason=f"REJECTED: Unsupported recovery plan structure {type(recovery_plan).__name__}.",
            rejection_details=("INVALID_RECOVERY_PLAN_TYPE",),
        )

    if not steps:
        return TCBuildResult(
            status=TCBuildStatus.APPROVED,
            frames=(),
            total_frames=0,
            total_bytes=0,
            reason="Empty recovery plan; zero telecommands encoded.",
        )

    # All candidate steps must exist in safety_validation.validated_steps
    approved_commands = {s.command for s in safety_validation.validated_steps}
    for step in steps:
        if step.command not in approved_commands:
            return TCBuildResult(
                status=TCBuildStatus.BLOCKED,
                reason=f"REJECTED: Command '{step.command}' was not in safety-approved steps.",
                rejection_details=("UNAPPROVED_STEP_IN_PLAN",),
            )

    # ── Check 3: Physics Consistency Check ─────────────────────────────────
    if physics_report is not None:
        invalidated_faults = set(getattr(physics_report, "invalidated", []) or [])
        if invalidated_faults:
            # Check if plan fault is refuted
            fault_id = None
            if top_hyp is not None:
                fault_id = getattr(top_hyp, "fault_id", None) or getattr(top_hyp, "root_cause", None)
            if fault_id and fault_id in invalidated_faults:
                return TCBuildResult(
                    status=TCBuildStatus.REJECTED,
                    reason=(
                        f"REJECTED: Physics validation refuted hypothesis '{fault_id}'. "
                        f"Cannot encode recovery plan for a physically impossible diagnosis."
                    ),
                    rejection_details=(f"PHYSICS_REFUTED_{fault_id}",),
                )

    # ── Check 4: Preconditions, Critical Telemetry, & Parameters ───────────
    frames: list[TCFrame] = []
    seq_counter = int(sequence_start) & 0xFFFF

    params_map = parameters_map or {}

    for step in steps:
        cmd_id = step.command

        # 4a. Registry verification
        if not is_registered(cmd_id):
            return TCBuildResult(
                status=TCBuildStatus.REJECTED,
                reason=f"REJECTED: Unwhitelisted command '{cmd_id}' is not in registry.",
                rejection_details=("UNWHITELISTED_COMMAND",),
            )
        spec = COMMAND_REGISTRY[cmd_id]
        if not spec.enabled:
            return TCBuildResult(
                status=TCBuildStatus.REJECTED,
                reason=f"REJECTED: Command '{cmd_id}' is disabled in registry ({spec.disabled_reason}).",
                rejection_details=("COMMAND_DISABLED",),
            )

        # 4b. Critical Preconditions: Must be SATISFIED. UNKNOWN rejects frame construction!
        for cond in spec.required_preconditions:
            cond_state, support = evaluate_condition(cond, ctx)
            if cond_state is ConditionState.UNKNOWN:
                detail = f"UNKNOWN_CRITICAL_TELEMETRY: Precondition '{cond.value}' for '{cmd_id}' cannot be confirmed."
                return TCBuildResult(
                    status=TCBuildStatus.REJECTED,
                    reason=f"REJECTED: Critical telemetry required for '{cmd_id}' is UNKNOWN.",
                    rejection_details=(detail,),
                )
            elif cond_state is ConditionState.VIOLATED:
                detail = f"PRECONDITION_VIOLATED: Precondition '{cond.value}' for '{cmd_id}' is violated."
                return TCBuildResult(
                    status=TCBuildStatus.REJECTED,
                    reason=f"REJECTED: Precondition '{cond.value}' for '{cmd_id}' is violated.",
                    rejection_details=(detail,),
                )

        # 4c. Prohibited Conditions: Must NOT be satisfied, and cannot be UNKNOWN if critical hazard
        for cond in spec.prohibited_conditions:
            cond_state, support = evaluate_condition(cond, ctx)
            if cond_state is ConditionState.SATISFIED:
                detail = f"PROHIBITED_CONDITION_PRESENT: Hazard '{cond.value}' is present for '{cmd_id}'."
                return TCBuildResult(
                    status=TCBuildStatus.REJECTED,
                    reason=f"REJECTED: Prohibited hazard '{cond.value}' is active for '{cmd_id}'.",
                    rejection_details=(detail,),
                )
            elif cond_state is ConditionState.UNKNOWN:
                detail = f"UNKNOWN_CRITICAL_TELEMETRY: Hazard check '{cond.value}' for '{cmd_id}' is UNKNOWN."
                return TCBuildResult(
                    status=TCBuildStatus.REJECTED,
                    reason=f"REJECTED: Critical telemetry for hazard check '{cond.value}' is UNKNOWN.",
                    rejection_details=(detail,),
                )

        # 4d. Parameter validation & encoding
        step_params = params_map.get(step.step, getattr(step, "parameters", None))
        try:
            frame = TCFrame.encode(
                command_id=cmd_id,
                sequence_counter=seq_counter,
                parameters=step_params,
            )
        except (ValueError, TypeError) as exc:
            return TCBuildResult(
                status=TCBuildStatus.REJECTED,
                reason=f"REJECTED: Malformed or non-finite parameters for command '{cmd_id}': {exc}",
                rejection_details=(f"INVALID_PARAMETER: {exc}",),
            )

        frames.append(frame)
        seq_counter = (seq_counter + 1) & 0xFFFF

    total_bytes = sum(f.frame_length for f in frames)
    return TCBuildResult(
        status=TCBuildStatus.APPROVED,
        frames=tuple(frames),
        total_frames=len(frames),
        total_bytes=total_bytes,
        is_transmittable=False,
        reason=(
            f"APPROVED: {len(frames)} telecommand frame(s) deterministically encoded ({total_bytes} bytes). "
            f"Transmission disabled."
        ),
    )


def build_from_unvalidated_input(
    raw_plan: Any,
    crash_dump: dict[str, Any],
) -> TCBuildResult:
    """Defensive entrypoint that strictly enforces safety validation.

    Never allows external callers (LLM, client API, or frontend) to bypass
    validate_recovery_plan().
    """
    from app.agent.safety import validate_recovery_plan

    if not isinstance(raw_plan, SentinelOutput):
        # Attempt to parse into SentinelOutput or fail closed
        try:
            sentinel_out = SentinelOutput.model_validate(raw_plan)
        except Exception as exc:
            return TCBuildResult(
                status=TCBuildStatus.REJECTED,
                reason=f"REJECTED: Invalid recovery plan payload ({exc}).",
                rejection_details=("INVALID_PAYLOAD_STRUCTURE",),
            )
    else:
        sentinel_out = raw_plan

    # Authoritative safety validation MUST run
    safety_result = validate_recovery_plan(sentinel_out, crash_dump)

    return build_telecommand_stream(
        recovery_plan=sentinel_out,
        safety_validation=safety_result,
        crash_dump=crash_dump,
    )

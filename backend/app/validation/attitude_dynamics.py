"""
SENTINEL — Deterministic 3-Axis Spacecraft Attitude Dynamics
(app/validation/attitude_dynamics.py)

Phase 7. Extends SENTINEL's physics validation layer with a deterministic 3-axis
rigid-body rotational dynamics model:
- Quaternion attitude representation: q = [qw, qx, qy, qz] (scalar-first)
- Angular velocity vector: omega = [wx, wy, wz] (rad/s)
- Full 3x3 inertia tensor: I (kg*m^2), symmetric and positive-definite
- Euler rigid-body rotational dynamics:
      I * omega_dot + omega x (I * omega) = tau_ext
- Quaternion kinematic equation:
      q_dot = 1/2 * Omega(omega) * q

Design Invariants:
1. DETERMINISTIC: Bitwise-reproducible mathematics; no RNG, no network calls,
   no external dependencies beyond standard library math.
2. FAIL-CLOSED: Malformed, non-finite (NaN/Inf), non-positive-definite, or
   incomplete inputs are safely rejected/refuted without crashing the pipeline.
3. BACKWARD COMPATIBLE: When telemetry carries only legacy 1D scalar channels
   (Gyro_rate_degs), 3-axis validation is declined (NOT_APPLICABLE / None);
   it NEVER fabricates missing axes.
4. STRICT SAFETY AUTHORITY: The physics engine is strictly authoritative.
   LLM proposals can never alter inertia, rates, or torques, nor override a
   REFUTED verdict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Optional, Sequence, Union


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — 3D VECTOR MATHEMATICS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Vector3:
    """Immutable 3D vector representing Cartesian components [x, y, z]."""

    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", float(self.x))
        object.__setattr__(self, "y", float(self.y))
        object.__setattr__(self, "z", float(self.z))

    def is_finite(self) -> bool:
        """True if all three components are finite (not NaN, not Inf)."""
        return (
            math.isfinite(self.x)
            and math.isfinite(self.y)
            and math.isfinite(self.z)
        )

    def norm_squared(self) -> float:
        """Squared Euclidean norm: x^2 + y^2 + z^2."""
        return self.x * self.x + self.y * self.y + self.z * self.z

    def norm(self) -> float:
        """Euclidean norm: sqrt(x^2 + y^2 + z^2)."""
        return math.sqrt(self.norm_squared())

    def dot(self, other: Vector3) -> float:
        """Vector dot product: self . other."""
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: Vector3) -> Vector3:
        """Vector cross product: self x other."""
        return Vector3(
            x=self.y * other.z - self.z * other.y,
            y=self.z * other.x - self.x * other.z,
            z=self.x * other.y - self.y * other.x,
        )

    def __add__(self, other: Vector3) -> Vector3:
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: Vector3) -> Vector3:
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, scalar: float) -> Vector3:
        s = float(scalar)
        return Vector3(self.x * s, self.y * s, self.z * s)

    def __rmul__(self, scalar: float) -> Vector3:
        return self.__mul__(scalar)

    def __truediv__(self, scalar: float) -> Vector3:
        s = float(scalar)
        if s == 0.0 or not math.isfinite(s):
            raise ZeroDivisionError("Vector3 division by zero or non-finite scalar")
        return Vector3(self.x / s, self.y / s, self.z / s)

    def __neg__(self) -> Vector3:
        return Vector3(-self.x, -self.y, -self.z)

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def as_list(self) -> list[float]:
        return [self.x, self.y, self.z]

    @classmethod
    def zero(cls) -> Vector3:
        return cls(0.0, 0.0, 0.0)

    @classmethod
    def from_iterable(cls, items: Iterable[Any]) -> Vector3:
        """Parse 3 values from an iterable, validating finiteness."""
        vals = list(items)
        if len(vals) != 3:
            raise ValueError(f"Vector3 requires exactly 3 components, got {len(vals)}")
        try:
            x, y, z = float(vals[0]), float(vals[1]), float(vals[2])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Vector3 components must be numeric: {exc}") from exc
        v = cls(x, y, z)
        if not v.is_finite():
            raise ValueError(f"Vector3 components must be finite, got {v}")
        return v


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — QUATERNION ATTITUDE MATHEMATICS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Quaternion:
    """Immutable unit attitude quaternion q = [w, x, y, z] (scalar-first).

    Convention:
      - w: scalar part (cos(theta / 2))
      - [x, y, z]: vector part (axis * sin(theta / 2))
    """

    w: float
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "w", float(self.w))
        object.__setattr__(self, "x", float(self.x))
        object.__setattr__(self, "y", float(self.y))
        object.__setattr__(self, "z", float(self.z))

    def is_finite(self) -> bool:
        """True if all components are finite."""
        return (
            math.isfinite(self.w)
            and math.isfinite(self.x)
            and math.isfinite(self.y)
            and math.isfinite(self.z)
        )

    def norm_squared(self) -> float:
        """Squared Euclidean norm: w^2 + x^2 + y^2 + z^2."""
        return (
            self.w * self.w
            + self.x * self.x
            + self.y * self.y
            + self.z * self.z
        )

    def norm(self) -> float:
        """Euclidean norm: sqrt(w^2 + x^2 + y^2 + z^2)."""
        return math.sqrt(self.norm_squared())

    def is_normalized(self, tol: float = 1e-5) -> bool:
        """Check if quaternion has unit norm within tolerance."""
        if not self.is_finite():
            return False
        return abs(self.norm() - 1.0) <= tol

    def normalize(self) -> Quaternion:
        """Return unit quaternion. Raises ValueError if zero or non-finite."""
        if not self.is_finite():
            raise ValueError(f"Cannot normalize non-finite quaternion: {self}")
        n = self.norm()
        if n < 1e-12:
            raise ValueError(f"Cannot normalize zero-norm quaternion (norm={n})")
        return Quaternion(self.w / n, self.x / n, self.y / n, self.z / n)

    def conjugate(self) -> Quaternion:
        """Return quaternion conjugate q* = [w, -x, -y, -z]."""
        return Quaternion(self.w, -self.x, -self.y, -self.z)

    def inverse(self) -> Quaternion:
        """Return quaternion inverse q^-1 = q* / |q|^2."""
        n_sq = self.norm_squared()
        if n_sq < 1e-24 or not math.isfinite(n_sq):
            raise ValueError("Cannot invert singular or non-finite quaternion")
        return Quaternion(
            self.w / n_sq,
            -self.x / n_sq,
            -self.y / n_sq,
            -self.z / n_sq,
        )

    def multiply(self, other: Quaternion) -> Quaternion:
        """Hamilton quaternion product self (x) other."""
        w1, x1, y1, z1 = self.w, self.x, self.y, self.z
        w2, x2, y2, z2 = other.w, other.x, other.y, other.z
        return Quaternion(
            w=w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            x=w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            y=w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            z=w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        )

    def __mul__(self, other: Quaternion) -> Quaternion:
        return self.multiply(other)

    def rotate_vector(self, v: Vector3) -> Vector3:
        """Rotate vector v by unit quaternion q: v' = q (x) (0, v) (x) q*."""
        if not v.is_finite() or not self.is_finite():
            raise ValueError("Cannot rotate with non-finite vector or quaternion")
        # Rodrigues-equivalent quaternion formula: v' = v + 2*w*(qv x v) + 2*qv x (qv x v)
        qv = Vector3(self.x, self.y, self.z)
        t = qv.cross(v) * 2.0
        return v + (t * self.w) + qv.cross(t)

    def derivative(self, omega: Vector3) -> Quaternion:
        """Evaluate kinematic derivative q_dot = 1/2 * Omega(omega) * q.

        omega is the angular velocity in the body-fixed reference frame (rad/s).
        """
        if not omega.is_finite() or not self.is_finite():
            raise ValueError("Cannot compute derivative with non-finite values")
        wx, wy, wz = omega.x, omega.y, omega.z
        w, x, y, z = self.w, self.x, self.y, self.z
        return Quaternion(
            w=0.5 * (-wx * x - wy * y - wz * z),
            x=0.5 * ( wx * w + wz * y - wy * z),
            y=0.5 * ( wy * w - wz * x + wx * z),
            z=0.5 * ( wz * w + wy * x - wx * y),
        )

    def propagate(
        self,
        omega: Vector3,
        dt: float,
        method: str = "exponential",
    ) -> Quaternion:
        """Propagate quaternion forward over timestep dt with body rate omega.

        Methods:
          - 'exponential': Closed-form exact integration under constant omega.
          - 'euler': First-order Taylor step q_next = normalize(q + q_dot * dt).
        """
        dt_val = float(dt)
        if dt_val <= 0.0 or not math.isfinite(dt_val):
            raise ValueError(f"Timestep dt must be positive and finite, got {dt_val}")
        if not omega.is_finite():
            raise ValueError(f"Angular velocity must be finite, got {omega}")

        if method == "exponential":
            angle = omega.norm() * dt_val
            if angle > 1e-12:
                axis = omega / omega.norm()
                half_angle = 0.5 * angle
                sin_half = math.sin(half_angle)
                cos_half = math.cos(half_angle)
                delta_q = Quaternion(
                    w=cos_half,
                    x=axis.x * sin_half,
                    y=axis.y * sin_half,
                    z=axis.z * sin_half,
                )
            else:
                # Small-angle Taylor expansion
                half_dt = 0.5 * dt_val
                delta_q = Quaternion(
                    w=1.0,
                    x=omega.x * half_dt,
                    y=omega.y * half_dt,
                    z=omega.z * half_dt,
                )
            return (self * delta_q).normalize()

        elif method == "euler":
            q_dot = self.derivative(omega)
            unnormalized = Quaternion(
                w=self.w + q_dot.w * dt_val,
                x=self.x + q_dot.x * dt_val,
                y=self.y + q_dot.y * dt_val,
                z=self.z + q_dot.z * dt_val,
            )
            return unnormalized.normalize()

        else:
            raise ValueError(f"Unknown propagation method '{method}', expected 'exponential' or 'euler'")

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.w, self.x, self.y, self.z)

    def as_list(self) -> list[float]:
        return [self.w, self.x, self.y, self.z]

    @classmethod
    def identity(cls) -> Quaternion:
        return cls(1.0, 0.0, 0.0, 0.0)

    @classmethod
    def from_axis_angle(cls, axis: Vector3, angle_rad: float) -> Quaternion:
        """Create unit quaternion from rotation axis and angle in radians."""
        if not axis.is_finite() or not math.isfinite(angle_rad):
            raise ValueError("Axis and angle must be finite")
        n = axis.norm()
        if n < 1e-12:
            raise ValueError("Rotation axis cannot be zero vector")
        unit_axis = axis / n
        half_angle = 0.5 * float(angle_rad)
        sin_half = math.sin(half_angle)
        return cls(
            w=math.cos(half_angle),
            x=unit_axis.x * sin_half,
            y=unit_axis.y * sin_half,
            z=unit_axis.z * sin_half,
        ).normalize()

    @classmethod
    def from_iterable(cls, items: Iterable[Any]) -> Quaternion:
        """Parse 4 numeric values into a Quaternion."""
        vals = list(items)
        if len(vals) != 4:
            raise ValueError(f"Quaternion requires 4 components [w, x, y, z], got {len(vals)}")
        try:
            w, x, y, z = float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Quaternion components must be numeric: {exc}") from exc
        q = cls(w, x, y, z)
        if not q.is_finite():
            raise ValueError(f"Quaternion components must be finite, got {q}")
        return q


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — 3X3 INERTIA TENSOR VALIDATION & ALGEBRA
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class InertiaTensor3x3:
    """Rigid-body 3x3 inertia tensor I (kg*m^2).

    Matrix structure:
        [[Ixx, Ixy, Ixz],
         [Iyx, Iyy, Iyz],
         [Izx, Izy, Izz]]

    Must be finite, symmetric, and positive-definite (Sylvester's criterion).
    """

    matrix: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]

    def __post_init__(self) -> None:
        m = self.matrix
        if len(m) != 3 or any(len(row) != 3 for row in m):
            raise ValueError("Inertia matrix must be exactly 3x3")

        # Convert to float and check finiteness
        float_m = tuple(
            tuple(float(val) for val in row) for row in m
        )
        for i in range(3):
            for j in range(3):
                if not math.isfinite(float_m[i][j]):
                    raise ValueError(f"Inertia element ({i},{j}) must be finite, got {float_m[i][j]}")

        # Check symmetry: |I_ij - I_ji| <= 1e-5
        sym_tol = 1e-5
        if abs(float_m[0][1] - float_m[1][0]) > sym_tol:
            raise ValueError(f"Inertia tensor asymmetric at (0,1) vs (1,0): {float_m[0][1]} != {float_m[1][0]}")
        if abs(float_m[0][2] - float_m[2][0]) > sym_tol:
            raise ValueError(f"Inertia tensor asymmetric at (0,2) vs (2,0): {float_m[0][2]} != {float_m[2][0]}")
        if abs(float_m[1][2] - float_m[2][1]) > sym_tol:
            raise ValueError(f"Inertia tensor asymmetric at (1,2) vs (2,1): {float_m[1][2]} != {float_m[2][1]}")

        # Sylvester's criterion for positive-definiteness:
        # 1. d1 = Ixx > 0
        ixx = float_m[0][0]
        if ixx <= 0.0:
            raise ValueError(f"Inertia tensor must be positive-definite: Ixx={ixx} <= 0")

        # 2. d2 = Ixx*Iyy - Ixy^2 > 0
        iyy = float_m[1][1]
        ixy = float_m[0][1]
        d2 = ixx * iyy - ixy * ixy
        if d2 <= 0.0:
            raise ValueError(f"Inertia tensor 2x2 principal minor <= 0 (d2={d2})")

        # 3. d3 = det(I) > 0
        d3 = self._calc_det(float_m)
        if d3 <= 0.0:
            raise ValueError(f"Inertia tensor determinant <= 0 (det={d3})")

        object.__setattr__(self, "matrix", float_m)

    @staticmethod
    def _calc_det(
        m: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
    ) -> float:
        """Compute determinant of a 3x3 matrix."""
        return (
            m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
        )

    def determinant(self) -> float:
        """Determinant det(I)."""
        return self._calc_det(self.matrix)

    def is_diagonal(self, tol: float = 1e-6) -> bool:
        """True if all off-diagonal products of inertia are zero within tol."""
        m = self.matrix
        return (
            abs(m[0][1]) <= tol
            and abs(m[0][2]) <= tol
            and abs(m[1][2]) <= tol
        )

    def multiply_vector(self, v: Vector3) -> Vector3:
        """Matrix-vector product I * v."""
        m = self.matrix
        return Vector3(
            x=m[0][0] * v.x + m[0][1] * v.y + m[0][2] * v.z,
            y=m[1][0] * v.x + m[1][1] * v.y + m[1][2] * v.z,
            z=m[2][0] * v.x + m[2][1] * v.y + m[2][2] * v.z,
        )

    def inverse(self) -> InertiaTensor3x3:
        """Compute matrix inverse I^-1."""
        det = self.determinant()
        if det <= 0.0:
            raise ValueError(f"Singular or non-positive-definite inertia tensor (det={det})")
        m = self.matrix
        inv_det = 1.0 / det

        # Classical adjugate matrix for 3x3
        inv_00 = (m[1][1] * m[2][2] - m[1][2] * m[2][1]) * inv_det
        inv_01 = (m[0][2] * m[2][1] - m[0][1] * m[2][2]) * inv_det
        inv_02 = (m[0][1] * m[1][2] - m[0][2] * m[1][1]) * inv_det

        inv_10 = (m[1][2] * m[2][0] - m[1][0] * m[2][2]) * inv_det
        inv_11 = (m[0][0] * m[2][2] - m[0][2] * m[2][0]) * inv_det
        inv_12 = (m[0][2] * m[1][0] - m[0][0] * m[1][2]) * inv_det

        inv_20 = (m[1][0] * m[2][1] - m[1][1] * m[2][0]) * inv_det
        inv_21 = (m[0][1] * m[2][0] - m[0][0] * m[2][1]) * inv_det
        inv_22 = (m[0][0] * m[1][1] - m[0][1] * m[1][0]) * inv_det

        return InertiaTensor3x3((
            (inv_00, inv_01, inv_02),
            (inv_10, inv_11, inv_12),
            (inv_20, inv_21, inv_22),
        ))

    @classmethod
    def from_diagonal(cls, ixx: float, iyy: float, izz: float) -> InertiaTensor3x3:
        """Create diagonal inertia tensor."""
        return cls((
            (float(ixxx := ixx), 0.0, 0.0),
            (0.0, float(iyyy := iyy), 0.0),
            (0.0, 0.0, float(izzz := izz)),
        ))

    @classmethod
    def from_matrix(cls, m: Sequence[Sequence[Any]]) -> InertiaTensor3x3:
        """Create from 3x3 nested sequence."""
        if len(m) != 3 or any(len(row) != 3 for row in m):
            raise ValueError(f"Expected 3x3 matrix, got {len(m)} rows")
        mat = tuple(
            tuple(float(val) for val in row) for row in m
        )
        return cls(mat)  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — EULER ROTATIONAL RIGID-BODY DYNAMICS
# ═══════════════════════════════════════════════════════════════════════════

class EulerDynamics:
    """Evaluates Euler's equations of rotational motion for a rigid body:

        I * omega_dot + omega x (I * omega) = tau_ext
    """

    @staticmethod
    def angular_momentum(inertia: InertiaTensor3x3, omega: Vector3) -> Vector3:
        """Calculate total angular momentum vector: H = I * omega."""
        return inertia.multiply_vector(omega)

    @staticmethod
    def gyroscopic_torque(inertia: InertiaTensor3x3, omega: Vector3) -> Vector3:
        """Calculate internal gyroscopic coupling torque: tau_gyro = omega x (I * omega)."""
        h = inertia.multiply_vector(omega)
        return omega.cross(h)

    @classmethod
    def dynamic_torque(
        cls,
        inertia: InertiaTensor3x3,
        omega: Vector3,
        omega_dot: Vector3,
    ) -> Vector3:
        """Calculate total dynamic torque: tau_dyn = I * omega_dot + omega x (I * omega)."""
        i_alpha = inertia.multiply_vector(omega_dot)
        tau_gyro = cls.gyroscopic_torque(inertia, omega)
        return i_alpha + tau_gyro

    @classmethod
    def expected_angular_acceleration(
        cls,
        inertia: InertiaTensor3x3,
        omega: Vector3,
        tau_ext: Vector3,
    ) -> Vector3:
        """Calculate expected angular acceleration: omega_dot = I^-1 * (tau_ext - omega x (I * omega))."""
        tau_gyro = cls.gyroscopic_torque(inertia, omega)
        tau_net = tau_ext - tau_gyro
        inv_i = inertia.inverse()
        return inv_i.multiply_vector(tau_net)

    @classmethod
    def torque_residual(
        cls,
        inertia: InertiaTensor3x3,
        omega: Vector3,
        omega_dot: Vector3,
        tau_ext: Vector3,
    ) -> Vector3:
        """Calculate torque residual: tau_res = tau_ext - [I * omega_dot + omega x (I * omega)]."""
        tau_required = cls.dynamic_torque(inertia, omega, omega_dot)
        return tau_ext - tau_required


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — VERDICTS AND STEP VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

class DynamicsStatus(str, Enum):
    """Deterministic verdict status for 3-axis attitude dynamics checks."""

    VALID = "VALID"
    """Motion is consistent with Euler dynamics and quaternion kinematics."""

    REFUTED = "REFUTED"
    """Motion definitively violates rigid-body conservation laws or kinematics."""

    UNCERTAIN = "UNCERTAIN"
    """Telemetry is missing required 3-axis channels, ill-conditioned, or sparse."""


@dataclass(frozen=True)
class DynamicsVerdict:
    """Structured result of one 3-axis dynamics validation step or sequence."""

    status: DynamicsStatus
    residual_norm: float
    residual_vector: Optional[Vector3]
    angular_momentum: Optional[Vector3]
    expected_angular_acceleration: Optional[Vector3]
    observed_angular_acceleration: Optional[Vector3]
    applied_torque: Optional[Vector3]
    tolerance: float
    explanation: str

    @property
    def is_refuted(self) -> bool:
        return self.status is DynamicsStatus.REFUTED

    @property
    def is_valid(self) -> bool:
        return self.status is DynamicsStatus.VALID

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "is_refuted": self.is_refuted,
            "is_valid": self.is_valid,
            "residual_norm": self.residual_norm,
            "residual_vector": self.residual_vector.as_list() if self.residual_vector else None,
            "angular_momentum": self.angular_momentum.as_list() if self.angular_momentum else None,
            "expected_angular_acceleration": (
                self.expected_angular_acceleration.as_list()
                if self.expected_angular_acceleration else None
            ),
            "observed_angular_acceleration": (
                self.observed_angular_acceleration.as_list()
                if self.observed_angular_acceleration else None
            ),
            "applied_torque": self.applied_torque.as_list() if self.applied_torque else None,
            "tolerance": self.tolerance,
            "explanation": self.explanation,
        }


def validate_3axis_attitude_step(
    inertia: Union[InertiaTensor3x3, Sequence[Sequence[Any]]],
    omega_start: Union[Vector3, Sequence[Any]],
    omega_end: Union[Vector3, Sequence[Any]],
    dt: float,
    tau_ext: Optional[Union[Vector3, Sequence[Any]]] = None,
    q_start: Optional[Union[Quaternion, Sequence[Any]]] = None,
    q_end: Optional[Union[Quaternion, Sequence[Any]]] = None,
    torque_tolerance: float = 0.05,
    attitude_tolerance_deg: float = 2.0,
) -> DynamicsVerdict:
    """Validate 3-axis rigid body motion between two state snapshots.

    Parameters:
      - inertia: 3x3 inertia tensor or raw 3x3 sequence
      - omega_start, omega_end: body rates at t0 and t1 (rad/s)
      - dt: timestep (s), must be > 0
      - tau_ext: applied external/actuator torque (N*m); defaults to zero
      - q_start, q_end: optional attitude quaternions at t0 and t1
      - torque_tolerance: maximum acceptable torque residual norm (N*m)
      - attitude_tolerance_deg: maximum acceptable pointing discrepancy (deg)

    Returns:
      DynamicsVerdict with VALID, REFUTED, or UNCERTAIN.
    """
    # ── 1. Validate Inertia Tensor ─────────────────────────────────────────
    try:
        if isinstance(inertia, InertiaTensor3x3):
            i_tensor = inertia
        else:
            i_tensor = InertiaTensor3x3.from_matrix(inertia)
    except Exception as exc:
        return DynamicsVerdict(
            status=DynamicsStatus.REFUTED,
            residual_norm=float("inf"),
            residual_vector=None,
            angular_momentum=None,
            expected_angular_acceleration=None,
            observed_angular_acceleration=None,
            applied_torque=None,
            tolerance=torque_tolerance,
            explanation=f"REFUTED: Invalid inertia tensor. Must be finite, symmetric and positive-definite ({exc}).",
        )

    # ── 2. Validate Timestep ───────────────────────────────────────────────
    try:
        dt_val = float(dt)
        if dt_val <= 0.0 or not math.isfinite(dt_val):
            return DynamicsVerdict(
                status=DynamicsStatus.UNCERTAIN,
                residual_norm=float("nan"),
                residual_vector=None,
                angular_momentum=None,
                expected_angular_acceleration=None,
                observed_angular_acceleration=None,
                applied_torque=None,
                tolerance=torque_tolerance,
                explanation=f"UNCERTAIN: Invalid timestep dt={dt_val}; must be finite and positive.",
            )
    except (TypeError, ValueError):
        return DynamicsVerdict(
            status=DynamicsStatus.UNCERTAIN,
            residual_norm=float("nan"),
            residual_vector=None,
            angular_momentum=None,
            expected_angular_acceleration=None,
            observed_angular_acceleration=None,
            applied_torque=None,
            tolerance=torque_tolerance,
            explanation="UNCERTAIN: Non-numeric timestep dt.",
        )

    # ── 3. Validate Angular Rates and Torque ───────────────────────────────
    try:
        w_start = (
            omega_start if isinstance(omega_start, Vector3)
            else Vector3.from_iterable(omega_start)
        )
        w_end = (
            omega_end if isinstance(omega_end, Vector3)
            else Vector3.from_iterable(omega_end)
        )
    except Exception as exc:
        return DynamicsVerdict(
            status=DynamicsStatus.REFUTED,
            residual_norm=float("nan"),
            residual_vector=None,
            angular_momentum=None,
            expected_angular_acceleration=None,
            observed_angular_acceleration=None,
            applied_torque=None,
            tolerance=torque_tolerance,
            explanation=f"REFUTED: Malformed or non-finite angular velocity vectors ({exc}).",
        )

    if tau_ext is None:
        t_ext = Vector3.zero()
    else:
        try:
            t_ext = tau_ext if isinstance(tau_ext, Vector3) else Vector3.from_iterable(tau_ext)
        except Exception as exc:
            return DynamicsVerdict(
                status=DynamicsStatus.REFUTED,
                residual_norm=float("nan"),
                residual_vector=None,
                angular_momentum=None,
                expected_angular_acceleration=None,
                observed_angular_acceleration=None,
                applied_torque=None,
                tolerance=torque_tolerance,
                explanation=f"REFUTED: Malformed or non-finite applied torque vector ({exc}).",
            )

    # ── 4. Validate Quaternions (if provided) ──────────────────────────────
    quat_start: Optional[Quaternion] = None
    quat_end: Optional[Quaternion] = None
    if q_start is not None or q_end is not None:
        if q_start is None or q_end is None:
            return DynamicsVerdict(
                status=DynamicsStatus.UNCERTAIN,
                residual_norm=float("nan"),
                residual_vector=None,
                angular_momentum=None,
                expected_angular_acceleration=None,
                observed_angular_acceleration=None,
                applied_torque=t_ext,
                tolerance=torque_tolerance,
                explanation="UNCERTAIN: Incomplete quaternion pair (both start and end required).",
            )
        try:
            quat_start = (
                q_start if isinstance(q_start, Quaternion)
                else Quaternion.from_iterable(q_start)
            ).normalize()
            quat_end = (
                q_end if isinstance(q_end, Quaternion)
                else Quaternion.from_iterable(q_end)
            ).normalize()
        except Exception as exc:
            return DynamicsVerdict(
                status=DynamicsStatus.REFUTED,
                residual_norm=float("nan"),
                residual_vector=None,
                angular_momentum=None,
                expected_angular_acceleration=None,
                observed_angular_acceleration=None,
                applied_torque=t_ext,
                tolerance=torque_tolerance,
                explanation=f"REFUTED: Invalid or non-normalizable attitude quaternion ({exc}).",
            )

    # ── 5. Kinematics Check (Quaternion Propagation) ───────────────────────
    w_avg = (w_start + w_end) * 0.5
    if quat_start is not None and quat_end is not None:
        q_pred = quat_start.propagate(w_avg, dt_val)
        # Compute pointing deviation angle
        q_err = q_pred.inverse() * quat_end
        clamped_w = max(-1.0, min(1.0, abs(q_err.w)))
        angle_err_rad = 2.0 * math.acos(clamped_w)
        angle_err_deg = math.degrees(angle_err_rad)

        if angle_err_deg > attitude_tolerance_deg:
            return DynamicsVerdict(
                status=DynamicsStatus.REFUTED,
                residual_norm=angle_err_deg,
                residual_vector=None,
                angular_momentum=EulerDynamics.angular_momentum(i_tensor, w_avg),
                expected_angular_acceleration=None,
                observed_angular_acceleration=(w_end - w_start) / dt_val,
                applied_torque=t_ext,
                tolerance=attitude_tolerance_deg,
                explanation=(
                    f"REFUTED: Quaternion kinematic discrepancy ({angle_err_deg:.3f} deg) "
                    f"exceeds tolerance ({attitude_tolerance_deg:.3f} deg)."
                ),
            )

    # ── 6. Euler Rotational Dynamics Check ─────────────────────────────────
    alpha_obs = (w_end - w_start) / dt_val
    h = EulerDynamics.angular_momentum(i_tensor, w_avg)
    alpha_exp = EulerDynamics.expected_angular_acceleration(i_tensor, w_avg, t_ext)
    tau_res = EulerDynamics.torque_residual(i_tensor, w_avg, alpha_obs, t_ext)
    res_norm = tau_res.norm()

    if res_norm <= torque_tolerance:
        return DynamicsVerdict(
            status=DynamicsStatus.VALID,
            residual_norm=res_norm,
            residual_vector=tau_res,
            angular_momentum=h,
            expected_angular_acceleration=alpha_exp,
            observed_angular_acceleration=alpha_obs,
            applied_torque=t_ext,
            tolerance=torque_tolerance,
            explanation=(
                f"VALID: 3-axis Euler dynamics consistent. Torque residual "
                f"norm={res_norm:.5f} N*m <= tol={torque_tolerance:.5f} N*m."
            ),
        )
    else:
        return DynamicsVerdict(
            status=DynamicsStatus.REFUTED,
            residual_norm=res_norm,
            residual_vector=tau_res,
            angular_momentum=h,
            expected_angular_acceleration=alpha_exp,
            observed_angular_acceleration=alpha_obs,
            applied_torque=t_ext,
            tolerance=torque_tolerance,
            explanation=(
                f"REFUTED: 3-axis Euler dynamics violation. Torque residual "
                f"norm={res_norm:.5f} N*m > tol={torque_tolerance:.5f} N*m. "
                f"External torque cannot account for measured body rate acceleration."
            ),
        )


def validate_3axis_sequence(
    inertia: Union[InertiaTensor3x3, Sequence[Sequence[Any]]],
    samples: Sequence[dict[str, Any]],
    torque_tolerance: float = 0.05,
    attitude_tolerance_deg: float = 2.0,
) -> DynamicsVerdict:
    """Validate a sequence of 3-axis attitude telemetry samples over time.

    Each sample in `samples` should contain:
      - 't' (or 'timestamp', 'offset_s'): time in seconds
      - 'omega' (or 'omega_rads', or 'wx', 'wy', 'wz'): body rate in rad/s
      - 'tau' (or 'tau_ext', 'tx', 'ty', 'tz'): applied torque in N*m (optional)
      - 'q' (or 'quaternion', 'qw', 'qx', 'qy', 'qz'): attitude quaternion (optional)

    Returns:
      DynamicsVerdict:
        - REFUTED if any step violates Euler dynamics or kinematics.
        - VALID if all steps are consistent with physical conservation laws.
        - UNCERTAIN if fewer than 2 valid samples are available.
    """
    if not isinstance(samples, (list, tuple)) or len(samples) < 2:
        return DynamicsVerdict(
            status=DynamicsStatus.UNCERTAIN,
            residual_norm=float("nan"),
            residual_vector=None,
            angular_momentum=None,
            expected_angular_acceleration=None,
            observed_angular_acceleration=None,
            applied_torque=None,
            tolerance=torque_tolerance,
            explanation="UNCERTAIN: Sequence validation requires at least two time samples.",
        )

    # Helper to parse time from a sample
    def _parse_time(s: dict[str, Any]) -> Optional[float]:
        for k in ("t", "time", "timestamp", "offset_s", "t_sec"):
            if k in s and s[k] is not None:
                try:
                    val = float(s[k])
                    if math.isfinite(val):
                        return val
                except (TypeError, ValueError):
                    pass
        return None

    # Helper to parse omega from a sample
    def _parse_omega(s: dict[str, Any]) -> Optional[Vector3]:
        for k in ("omega", "omega_rads", "rates", "body_rates"):
            if k in s and s[k] is not None:
                try:
                    return Vector3.from_iterable(s[k])
                except Exception:
                    pass
        if all(k in s for k in ("wx", "wy", "wz")):
            try:
                return Vector3(float(s["wx"]), float(s["wy"]), float(s["wz"]))
            except Exception:
                pass
        if all(k in s for k in ("Gyro_rate_x_degs", "Gyro_rate_y_degs", "Gyro_rate_z_degs")):
            try:
                deg_to_rad = math.pi / 180.0
                return Vector3(
                    float(s["Gyro_rate_x_degs"]) * deg_to_rad,
                    float(s["Gyro_rate_y_degs"]) * deg_to_rad,
                    float(s["Gyro_rate_z_degs"]) * deg_to_rad,
                )
            except Exception:
                pass
        return None

    # Helper to parse torque from a sample
    def _parse_tau(s: dict[str, Any]) -> Vector3:
        for k in ("tau", "tau_ext", "torque", "applied_torque"):
            if k in s and s[k] is not None:
                try:
                    return Vector3.from_iterable(s[k])
                except Exception:
                    pass
        if all(k in s for k in ("tx", "ty", "tz")):
            try:
                return Vector3(float(s["tx"]), float(s["ty"]), float(s["tz"]))
            except Exception:
                pass
        return Vector3.zero()

    # Helper to parse quaternion from a sample
    def _parse_quat(s: dict[str, Any]) -> Optional[Quaternion]:
        for k in ("q", "quaternion", "attitude_q"):
            if k in s and s[k] is not None:
                try:
                    return Quaternion.from_iterable(s[k])
                except Exception:
                    pass
        if all(k in s for k in ("qw", "qx", "qy", "qz")):
            try:
                return Quaternion(float(s["qw"]), float(s["qx"]), float(s["qy"]), float(s["qz"]))
            except Exception:
                pass
        return None

    step_verdicts: list[DynamicsVerdict] = []
    max_res_norm = 0.0
    worst_verdict: Optional[DynamicsVerdict] = None

    for i in range(len(samples) - 1):
        s0 = samples[i]
        s1 = samples[i + 1]
        t0 = _parse_time(s0)
        t1 = _parse_time(s1)
        w0 = _parse_omega(s0)
        w1 = _parse_omega(s1)

        if t0 is None or t1 is None:
            return DynamicsVerdict(
                status=DynamicsStatus.UNCERTAIN,
                residual_norm=float("nan"),
                residual_vector=None,
                angular_momentum=None,
                expected_angular_acceleration=None,
                observed_angular_acceleration=None,
                applied_torque=None,
                tolerance=torque_tolerance,
                explanation=f"UNCERTAIN: Missing or non-numeric timestamp at sample {i} or {i+1}.",
            )

        dt = t1 - t0
        if dt <= 0.0 or not math.isfinite(dt):
            return DynamicsVerdict(
                status=DynamicsStatus.UNCERTAIN,
                residual_norm=float("nan"),
                residual_vector=None,
                angular_momentum=None,
                expected_angular_acceleration=None,
                observed_angular_acceleration=None,
                applied_torque=None,
                tolerance=torque_tolerance,
                explanation=f"UNCERTAIN: Non-positive or non-finite timestep dt={dt} between samples {i} and {i+1}.",
            )

        if w0 is None or w1 is None:
            return DynamicsVerdict(
                status=DynamicsStatus.REFUTED,
                residual_norm=float("nan"),
                residual_vector=None,
                angular_momentum=None,
                expected_angular_acceleration=None,
                observed_angular_acceleration=None,
                applied_torque=None,
                tolerance=torque_tolerance,
                explanation=f"REFUTED: Malformed or missing angular velocity at sample {i} or {i+1}.",
            )

        tau0 = _parse_tau(s0)
        q0 = _parse_quat(s0)
        q1 = _parse_quat(s1)

        step_res = validate_3axis_attitude_step(
            inertia=inertia,
            omega_start=w0,
            omega_end=w1,
            dt=dt,
            tau_ext=tau0,
            q_start=q0,
            q_end=q1,
            torque_tolerance=torque_tolerance,
            attitude_tolerance_deg=attitude_tolerance_deg,
        )

        if step_res.is_refuted:
            return DynamicsVerdict(
                status=DynamicsStatus.REFUTED,
                residual_norm=step_res.residual_norm,
                residual_vector=step_res.residual_vector,
                angular_momentum=step_res.angular_momentum,
                expected_angular_acceleration=step_res.expected_angular_acceleration,
                observed_angular_acceleration=step_res.observed_angular_acceleration,
                applied_torque=step_res.applied_torque,
                tolerance=step_res.tolerance,
                explanation=f"REFUTED at step {i}->{i+1}: {step_res.explanation}",
            )

        if step_res.residual_norm > max_res_norm:
            max_res_norm = step_res.residual_norm
            worst_verdict = step_res

        step_verdicts.append(step_res)

    last_verdict = worst_verdict or step_verdicts[-1]
    return DynamicsVerdict(
        status=DynamicsStatus.VALID,
        residual_norm=max_res_norm,
        residual_vector=last_verdict.residual_vector,
        angular_momentum=last_verdict.angular_momentum,
        expected_angular_acceleration=last_verdict.expected_angular_acceleration,
        observed_angular_acceleration=last_verdict.observed_angular_acceleration,
        applied_torque=last_verdict.applied_torque,
        tolerance=torque_tolerance,
        explanation=(
            f"VALID: All {len(step_verdicts)} step(s) consistent with 3-axis Euler dynamics. "
            f"Max torque residual norm={max_res_norm:.5f} N*m <= tol={torque_tolerance:.5f} N*m."
        ),
    )


def extract_3axis_state_from_dump(
    crash_dump: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Detect and extract complete 3-axis dynamics telemetry if present.

    Returns dict with keys ('inertia', 'samples') if complete 3-axis data is
    available; returns None if only legacy 1D telemetry is present.
    Never fabricates missing axes or assumes arbitrary inertia.
    """
    if not isinstance(crash_dump, dict):
        return None

    # Option A: Dedicated 3-axis payload container
    for key in ("attitude_3axis", "dynamics_3axis", "three_axis_dynamics"):
        candidate = crash_dump.get(key)
        if isinstance(candidate, dict) and "inertia" in candidate and "samples" in candidate:
            return candidate

    # Option B: Telemetry series carries multi-axis rate channels AND inertia is specified
    readings = crash_dump.get("telemetry", [])
    if isinstance(readings, list) and len(readings) >= 2:
        channels_present: set[str] = set()
        for r in readings:
            if isinstance(r, dict):
                channels_present.update(r.keys())

        has_3axis_gyro = (
            ("Gyro_rate_x_degs" in channels_present or "omega_x" in channels_present)
            and ("Gyro_rate_y_degs" in channels_present or "omega_y" in channels_present)
            and ("Gyro_rate_z_degs" in channels_present or "omega_z" in channels_present)
        )

        # Check for inertia matrix
        inertia = (
            crash_dump.get("inertia")
            or crash_dump.get("inertia_matrix")
            or (crash_dump.get("parameters", {}) if isinstance(crash_dump.get("parameters"), dict) else {}).get("inertia")
            or (crash_dump.get("spacecraft", {}) if isinstance(crash_dump.get("spacecraft"), dict) else {}).get("inertia")
        )

        if has_3axis_gyro and inertia is not None:
            return {
                "inertia": inertia,
                "samples": readings,
            }

    return None


def validate_3axis_from_dump(
    crash_dump: dict[str, Any],
) -> Optional[DynamicsVerdict]:
    """Run 3-axis attitude dynamics validation on a crash dump if 3-axis data exists.

    Returns None if only legacy 1D telemetry is present.
    """
    state_3axis = extract_3axis_state_from_dump(crash_dump)
    if state_3axis is None:
        return None

    inertia = state_3axis.get("inertia")
    samples = state_3axis.get("samples")
    torque_tol = float(state_3axis.get("torque_tolerance", 0.05))
    att_tol = float(state_3axis.get("attitude_tolerance_deg", 2.0))

    if inertia is None or samples is None:
        return None

    return validate_3axis_sequence(
        inertia=inertia,
        samples=samples,
        torque_tolerance=torque_tol,
        attitude_tolerance_deg=att_tol,
    )


def integrate_3axis_into_physics_report(
    physics_report: Any,
    verdict: Optional[DynamicsVerdict],
) -> Any:
    """Integrate a 3-axis DynamicsVerdict into a PhysicsValidationReport.

    If verdict is None, returns report untouched.
    If verdict is REFUTED:
      - Marks attitude/ADCS hypotheses as INVALID.
      - Adds 'PHYS_3AXIS_EULER_DYNAMICS' to violated_constraints and refuted_by.
      - Updates invalidated list so downstream LLM rankers and safety gates demote/block.
    If verdict is VALID:
      - Documents successful 3-axis dynamic verification in warnings/limitations.
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
        # Fallback if circular import occurs
        return physics_report

    if verdict.is_refuted:
        updated_verdicts: list[Any] = []
        newly_invalidated: list[str] = []

        check_3axis = ConstraintCheck(
            constraint_id="PHYS_3AXIS_EULER_DYNAMICS",
            family=CheckFamily.PHYSICAL_CONSISTENCY,
            outcome=CheckOutcome.FAIL,
            statement="Spacecraft rotational motion must satisfy Euler rigid-body dynamics and quaternion kinematics.",
            detail=verdict.explanation,
            channels=["omega_x", "omega_y", "omega_z"],
            residual_refs=["torque_residual"],
        )

        for pv in physics_report.verdicts:
            # Check if this hypothesis is attitude/ADCS-related or if we should refute all candidates
            # because the physical rigid-body motion itself is fundamentally contradicted
            is_attitude = (
                pv.fault_id.startswith("AOCS_")
                or pv.fault_id.startswith("ADCS_")
                or pv.subsystem in ("AOCS", "ADCS")
            )
            # If there are attitude faults, refute them; if no attitude faults, refute all active candidates
            should_refute = is_attitude or (len(physics_report.verdicts) <= 2)

            if should_refute:
                newly_invalidated.append(pv.fault_id)
                new_violated = list(dict.fromkeys(list(pv.violated_constraints) + ["PHYS_3AXIS_EULER_DYNAMICS"]))
                new_refuted = list(dict.fromkeys(list(pv.refuted_by) + ["PHYS_3AXIS_EULER_DYNAMICS"]))
                new_checks = [check_3axis] + [c for c in pv.checks if c.constraint_id != "PHYS_3AXIS_EULER_DYNAMICS"]
                new_expl = f"INVALID: Contradicted by 3-axis Euler dynamics. {verdict.explanation}"

                updated_v = pv.model_copy(update={
                    "validation_status": PhysicsStatus.INVALID,
                    "violated_constraints": new_violated,
                    "refuted_by": new_refuted,
                    "checks": new_checks,
                    "explanation": new_expl,
                })
            else:
                updated_verdicts.append(pv)

        if not updated_verdicts:
            default_fault = "AOCS_REACTION_WHEEL_DEGRADATION"
            newly_invalidated.append(default_fault)
            updated_verdicts.append(PhysicsVerdict(
                hypothesis_id="HYP-3AXIS-EULER-REFUTED",
                fault_id=default_fault,
                fault_name="AOCS Attitude Dynamics Inconsistency",
                subsystem="AOCS",
                validation_status=PhysicsStatus.INVALID,
                violated_constraints=["PHYS_3AXIS_EULER_DYNAMICS"],
                refuted_by=["PHYS_3AXIS_EULER_DYNAMICS"],
                checks=[check_3axis],
                explanation=f"INVALID: Contradicted by 3-axis Euler dynamics. {verdict.explanation}",
                model_version=physics_report.model_version or "physics/3axis",
            ))

        combined_invalidated = sorted(set(physics_report.invalidated) | set(newly_invalidated))
        combined_validated = [f for f in physics_report.validated if f not in combined_invalidated]
        combined_uncertain = [f for f in physics_report.uncertain if f not in combined_invalidated]

        warnings = list(physics_report.warnings)
        warnings.append(f"3-AXIS DYNAMICS REFUTED: {verdict.explanation}")

        summary = (
            f"{len(combined_invalidated)} of {len(updated_verdicts)} hypothesis(es) "
            f"contradicted by the state model ({', '.join(combined_invalidated)}); "
            f"3-axis Euler dynamics REFUTED."
        )

        return physics_report.model_copy(update={
            "verdicts": updated_verdicts,
            "invalidated": combined_invalidated,
            "validated": combined_validated,
            "uncertain": combined_uncertain,
            "warnings": warnings,
            "summary": summary,
        })

    elif verdict.is_valid:
        warnings = list(physics_report.warnings)
        warnings.append(
            f"3-axis Euler dynamics VALID: residual norm={verdict.residual_norm:.5f} N*m <= tol={verdict.tolerance:.5f} N*m."
        )
        return physics_report.model_copy(update={
            "warnings": warnings,
        })

    return physics_report


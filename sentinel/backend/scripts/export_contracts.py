#!/usr/bin/env python3
"""
SENTINEL — Contract Exporter (scripts/export_contracts.py)

Phase 3. Generates everything under ``contracts/`` from the Pydantic models,
which are the single source of truth.

    contracts/
      openapi/openapi.json          the served API, exported from FastAPI
      schemas/<Model>.schema.json   JSON Schema per request/response model
      frontend/contract.js          runtime constants for the browser client
      frontend/contract.d.ts        matching TypeScript declarations
      README.md                     how to regenerate and what not to hand-edit

Why a generator rather than hand-written types
----------------------------------------------
Before Phase 3 the frontend carried its own 188-line copy of the scenario
catalogue and its own string literals for statuses and provenance codes. Nothing
compared them to the backend, so they were free to drift — and they had. Anything
derived from the models is generated here so drift becomes a build failure
instead of a rendering bug.

Usage
-----
    python3 scripts/export_contracts.py            # write artifacts
    python3 scripts/export_contracts.py --check     # fail if disk is stale

``--check`` regenerates in memory and byte-compares against what is committed.
CI runs it, so changing a model without regenerating the contract fails the
build. That is the whole point: the contract cannot silently fall behind.

The CRA mirror
--------------
Create React App 5 forbids imports from outside ``src/`` (ModuleScopePlugin), so
``contracts/frontend/contract.js`` is mirrored byte-for-byte to
``sentinel/frontend/src/generated/contract.js``. ``--check`` verifies the mirror
is identical, so the copy cannot drift from the canonical artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
_REPO = _BACKEND.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

CONTRACTS_DIR = _REPO / "contracts"
OPENAPI_DIR = CONTRACTS_DIR / "openapi"
SCHEMAS_DIR = CONTRACTS_DIR / "schemas"
FRONTEND_DIR = CONTRACTS_DIR / "frontend"
CRA_MIRROR = _REPO / "sentinel" / "frontend" / "src" / "generated" / "contract.js"

GENERATED_BANNER = (
    "GENERATED FILE — DO NOT EDIT BY HAND.\n"
    "Source of truth: sentinel/backend/app/api/models.py\n"
    "Regenerate:      python3 sentinel/backend/scripts/export_contracts.py\n"
    "Verify:          python3 sentinel/backend/scripts/export_contracts.py --check"
)


def _models() -> dict[str, Any]:
    """The models whose JSON Schema is published.

    Grouped by role so the exported directory reads as a contract rather than a
    dump of every class that happens to be a BaseModel.
    """
    from app.api import models as m

    published: dict[str, Any] = {
        # --- requests ---
        "CrashDumpRequest": m.CrashDumpRequest,
        "TelemetryEntry": m.TelemetryEntry,
        "TelecommandContext": m.TelecommandContext,
        # --- responses ---
        "Scenario": m.Scenario,
        "ScenarioListResponse": m.ScenarioListResponse,
        "ContractInfo": m.ContractInfo,
        "SentinelOutput": m.SentinelOutput,
        "Hypothesis": m.Hypothesis,
        "RecoveryStep": m.RecoveryStep,
        "BlockedCommand": m.BlockedCommand,
        "SSEEvent": m.SSEEvent,
    }

    # Detection models live in their own package; include them when importable so
    # the exporter still works if that package is absent.
    try:
        from app.detection import models as d

        published.update({
            "AnomalyReport": d.AnomalyReport,
            "Anomaly": d.Anomaly,
            "ChannelFinding": d.ChannelFinding,
            "AnomalyProvenance": d.AnomalyProvenance,
            "DetectorRunInfo": d.DetectorRunInfo,
        })
    except Exception as exc:  # pragma: no cover
        print(f"  note: detection models not exported ({exc})")

    # Phase 4 audit trail. Published so a client reading GET /api/v1/runs/{id}
    # has a schema for the record rather than inferring the shape from a sample.
    try:
        from app.audit import record as a

        published.update({
            "AuditRecord": a.AuditRecord,
            "RunHeader": a.RunHeader,
            "RunOutcome": a.RunOutcome,
            "RunSummary": a.RunSummary,
            "StageEntry": a.StageEntry,
            "RunListResponse": a.RunListResponse,
            "AuditStatusResponse": a.AuditStatusResponse,
            "ChainVerification": a.ChainVerification,
            "OperatorDecisionInput": a.OperatorDecisionInput,
            "OperatorDecisionAccepted": a.OperatorDecisionAccepted,
        })
    except Exception as exc:  # pragma: no cover
        print(f"  note: audit models not exported ({exc})")

    # Phase 8 physics validation. Published so a client reading
    # POST /api/v1/physics has a schema for the verdict, including the fields
    # that qualify it — assumed_parameters and model_limitations — rather than
    # discovering them from a sample response.
    try:
        from app.validation import physics as p

        published.update({
            "PhysicsValidationReport": p.PhysicsValidationReport,
            "PhysicsVerdict": p.PhysicsVerdict,
            "ConstraintCheck": p.ConstraintCheck,
            "ResidualRef": p.ResidualRef,
            "LLMOverrideAttempt": p.LLMOverrideAttempt,
        })
    except Exception as exc:  # pragma: no cover
        print(f"  note: physics models not exported ({exc})")

    return published


def _physics_enum(name: str) -> list[str]:
    """Values of one Phase 8 enum, or empty if the module is unavailable.

    Degrades rather than raising, matching how the model blocks above handle an
    absent package: a contract missing one vocabulary is recoverable, an exporter
    that cannot run at all is not.
    """
    try:
        from app.validation import physics as p

        return [e.value for e in getattr(p, name)]
    except Exception as exc:  # pragma: no cover
        print(f"  note: physics enum {name} not exported ({exc})")
        return []


def _enums() -> dict[str, list[str]]:
    """Every closed vocabulary the frontend is allowed to compare against.

    Derived from the enum classes, never re-typed. A literal typed into the
    frontend by hand is a literal that can outlive the value it referred to.
    """
    from app.api import models as m
    from app.api.provenance import Provenance

    collected: dict[str, list[str]] = {
        "Provenance": [e.value for e in Provenance],
        "TelemetryStatus": [e.value for e in m.TelemetryStatus],
        "SafetyStatus": [e.value for e in m.SafetyStatus],
        "BlockSeverity": [e.value for e in m.BlockSeverity],
        "RiskLevel": [e.value for e in m.RiskLevel],
        "AnalysisStatus": [e.value for e in m.AnalysisStatus],
        "SubsystemID": [e.value for e in m.SubsystemID],
        # Phase 8. A frontend rendering a verdict must compare against these
        # rather than against a string typed into a component, and it must be
        # able to tell UNCERTAIN from VALID — they are not adjacent.
        "PhysicsStatus": _physics_enum("PhysicsStatus"),
        "CheckFamily": _physics_enum("CheckFamily"),
        "CheckOutcome": _physics_enum("CheckOutcome"),
        "SSEEventType": [e.value for e in m.SSEEventType],
    }
    try:
        from app.detection import models as d

        collected.update({
            "Severity": [e.value for e in d.Severity],
            "DetectorName": [e.value for e in d.DetectorName],
            "BaselineSource": [e.value for e in d.BaselineSource],
            "Confidence": [e.value for e in d.Confidence],
        })
    except Exception:  # pragma: no cover
        pass

    try:
        from app.ingest import channel_dict as cd

        collected.update({
            "Subsystem": [e.value for e in cd.Subsystem],
            "ValueClass": [e.value for e in cd.ValueClass],
            "DataType": [e.value for e in cd.DataType],
            "Criticality": [e.value for e in cd.Criticality],
            "SamplingRate": [e.value for e in cd.SamplingRate],
            "ChannelProvenance": [e.value for e in cd.Provenance],
        })
    except Exception:  # pragma: no cover
        pass

    try:
        from app.audit import record as a

        collected.update({
            "AuditStage": [e.value for e in a.Stage],
            "StageStatus": [e.value for e in a.StageStatus],
            "Actor": [e.value for e in a.Actor],
            "RunStatus": [e.value for e in a.RunStatus],
            "OperatorDecisionType": [e.value for e in a.OperatorDecisionType],
        })
    except Exception:  # pragma: no cover
        pass
    return collected


def _provenance_display() -> dict[str, str]:
    """Provenance code → operator-facing label, derived from provenance.py."""
    from app.api.provenance import Provenance, display_label

    return {e.value: display_label(e) for e in Provenance}


def _json(payload: Any) -> str:
    """Deterministic JSON: sorted keys, fixed indent, trailing newline.

    Determinism is what makes ``--check`` a meaningful test rather than a
    coin flip on dict ordering.
    """
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ═══════════════════════════════════════════════════════════════════════════
# Artifact builders
# ═══════════════════════════════════════════════════════════════════════════

def build_openapi() -> str:
    from app.main import app

    spec = app.openapi()
    return _json(spec)


def build_schemas() -> dict[str, str]:
    out: dict[str, str] = {}
    for name, model in _models().items():
        schema = model.model_json_schema(
            ref_template="#/definitions/{model}",
        )
        out[f"{name}.schema.json"] = _json(schema)
    return out


def build_index() -> str:
    """Machine-readable manifest of the contract, for tests and tooling."""
    from app.api.models import API_VERSION, CONTRACT_VERSION
    from app.main import app

    paths = sorted(
        r.path for r in app.routes
        if getattr(r, "path", "").startswith(("/api", "/health", "/scenarios",
                                              "/detect", "/analyze"))
    )
    return _json({
        "contract_version": CONTRACT_VERSION,
        "api_version": API_VERSION,
        "canonical_telemetry_field": "pre_fault_telemetry_window",
        "deprecated_telemetry_fields": ["pre_fault_telemetry"],
        "enums": _enums(),
        "models": sorted(_models().keys()),
        "routes": paths,
    })


def build_frontend_js() -> str:
    from app.api.models import API_VERSION, CONTRACT_VERSION

    enums = _enums()
    lines: list[str] = []
    ap = lines.append

    ap("/*")
    for line in GENERATED_BANNER.splitlines():
        ap(f" * {line}")
    ap(" *")
    ap(" * Runtime constants shared by the SENTINEL frontend. Every vocabulary")
    ap(" * here is derived from a backend enum, so a value the frontend compares")
    ap(" * against cannot outlive the value the backend emits.")
    ap(" */")
    ap("")
    ap(f'export const CONTRACT_VERSION = "{CONTRACT_VERSION}";')
    ap(f'export const API_VERSION = "{API_VERSION}";')
    ap("")
    ap("// Versioned API paths. The frontend must not build these by hand.")
    ap("export const API = {")
    ap(f'  contract: "/api/{API_VERSION}/contract",')
    ap(f'  health: "/api/{API_VERSION}/health",')
    ap(f'  scenarios: "/api/{API_VERSION}/scenarios",')
    ap(f'  detect: "/api/{API_VERSION}/detect",')
    ap(f'  detectChannels: "/api/{API_VERSION}/detect/channels",')
    ap(f'  analyze: "/api/{API_VERSION}/analyze",')
    ap("};")
    ap("")
    ap("// The canonical telemetry representation. Read this field, not the")
    ap("// deprecated one — see app/api/adapters.py for why they were merged.")
    ap('export const CANONICAL_TELEMETRY_FIELD = "pre_fault_telemetry_window";')
    ap('export const DEPRECATED_TELEMETRY_FIELDS = ["pre_fault_telemetry"];')
    ap("")
    ap("// Phase 4 audit trail. runs() and runVerify() build the paths so a")
    ap("// client never concatenates a run id into a URL by hand.")
    ap("export const AUDIT_API = {")
    ap(f'  status: "/api/{API_VERSION}/audit/status",')
    ap(f'  runs: "/api/{API_VERSION}/runs",')
    ap("};")
    ap("")
    ap("export function runPath(runId) {")
    ap("  return `${AUDIT_API.runs}/${encodeURIComponent(runId)}`;")
    ap("}")
    ap("")
    ap("export function runVerifyPath(runId) {")
    ap("  return `${runPath(runId)}/verify`;")
    ap("}")
    ap("")
    ap("export function runDecisionsPath(runId) {")
    ap("  return `${runPath(runId)}/decisions`;")
    ap("}")
    ap("")
    ap("// Header carrying the run id on a POST /api/v1/analyze response. It is")
    ap("// readable before the SSE body starts, so a client can record the run")
    ap("// even if the stream then fails.")
    ap('export const RUN_ID_HEADER = "X-Sentinel-Run-Id";')
    ap("")
    ap("// Stages this build records as NOT_IMPLEMENTED on every run. A client")
    ap("// must not read their absence as a check that passed.")
    ap("//")
    ap("// EMPTY as of Phase 8. state_estimation left the list in Phase 7, which")
    ap("// added the simplified attitude, power and thermal models;")
    ap("// physics_validation left it in Phase 8, which validates hypotheses")
    ap("// against those models. Every stage in the enum now records a result.")
    ap("//")
    ap("// An empty list does NOT mean every check succeeds. A stage can be")
    ap("// recorded DEGRADED (it ran and decided nothing) or FAILED, and a")
    ap("// physics verdict can be UNCERTAIN. Read the per-run coverage map for")
    ap("// what a given run actually concluded.")
    ap("export const NOT_IMPLEMENTED_STAGES = Object.freeze([]);")
    ap("")
    ap("// Phase 5 channel dictionary. Channel units, subsystems and limits are")
    ap("// served from the backend; the frontend must not retype any of them.")
    ap("export const CHANNEL_API = {")
    ap(f'  channels: "/api/{API_VERSION}/channels",')
    ap(f'  detectionView: "/api/{API_VERSION}/detect/channels",')
    ap("};")
    ap("")
    ap("export function channelPath(channelId) {")
    ap("  return `${CHANNEL_API.channels}/${encodeURIComponent(channelId)}`;")
    ap("}")
    ap("")
    ap("// Subsystem shown for a channel the dictionary cannot attribute. Never")
    ap("// inferred from the channel name.")
    ap('export const UNKNOWN_SUBSYSTEM = "UNKNOWN";')
    ap("")

    for name in sorted(enums):
        values = enums[name]
        ap(f"export const {_screaming(name)} = Object.freeze({{")
        for value in values:
            ap(f'  {value}: "{value}",')
        ap("});")
        ap("")

    ap("// Provenance code -> operator-facing label, derived from")
    ap("// app/api/provenance.py. A code missing from this map resolves to")
    ap("// UNKNOWN rather than to REAL.")
    ap("export const PROVENANCE_LABELS = Object.freeze({")
    for code, label in sorted(_provenance_display().items()):
        ap(f'  {code}: "{label}",')
    ap("});")
    ap("")
    ap("export function normalizeProvenance(code) {")
    ap("  return Object.prototype.hasOwnProperty.call(PROVENANCE_LABELS, code)")
    ap("    ? code")
    ap("    : PROVENANCE.UNKNOWN;")
    ap("}")
    ap("")
    ap("// True only when the numeric telemetry itself came from a mission")
    ap("// dataset. Real identifiers or real anomaly labels are not sufficient.")
    ap("export function isRealProvenance(code) {")
    ap("  return normalizeProvenance(code) === PROVENANCE.REAL;")
    ap("}")
    ap("")

    return "\n".join(lines)


def _screaming(name: str) -> str:
    """CamelCase enum class name -> SCREAMING_SNAKE constant name."""
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.upper())
    return "".join(out)


def build_frontend_dts() -> str:
    from app.api.models import API_VERSION, CONTRACT_VERSION

    enums = _enums()
    lines: list[str] = []
    ap = lines.append

    ap("/*")
    for line in GENERATED_BANNER.splitlines():
        ap(f" * {line}")
    ap(" */")
    ap("")
    ap(f'export declare const CONTRACT_VERSION: "{CONTRACT_VERSION}";')
    ap(f'export declare const API_VERSION: "{API_VERSION}";')
    ap("")
    ap("export declare const API: Readonly<{")
    for key in ("contract", "health", "scenarios", "detect", "detectChannels",
                "analyze"):
        ap(f"  {key}: string;")
    ap("}>;")
    ap("")
    ap("export declare const CANONICAL_TELEMETRY_FIELD: "
       '"pre_fault_telemetry_window";')
    ap("export declare const DEPRECATED_TELEMETRY_FIELDS: readonly string[];")
    ap("")
    ap("export declare const AUDIT_API: Readonly<{")
    ap("  status: string;")
    ap("  runs: string;")
    ap("}>;")
    ap("export declare function runPath(runId: string): string;")
    ap("export declare function runVerifyPath(runId: string): string;")
    ap("export declare function runDecisionsPath(runId: string): string;")
    ap('export declare const RUN_ID_HEADER: "X-Sentinel-Run-Id";')
    ap("export declare const NOT_IMPLEMENTED_STAGES: readonly string[];")
    ap("")
    ap("export declare const CHANNEL_API: Readonly<{")
    ap("  channels: string;")
    ap("  detectionView: string;")
    ap("}>;")
    ap("export declare function channelPath(channelId: string): string;")
    ap('export declare const UNKNOWN_SUBSYSTEM: "UNKNOWN";')
    ap("")

    for name in sorted(enums):
        union = " | ".join(f'"{v}"' for v in enums[name])
        ap(f"export type {name} = {union};")
        ap(f"export declare const {_screaming(name)}: "
           f"Readonly<Record<{name}, {name}>>;")
        ap("")

    ap("export declare const PROVENANCE_LABELS: "
       "Readonly<Record<Provenance, string>>;")
    ap("export declare function normalizeProvenance(code: unknown): Provenance;")
    ap("export declare function isRealProvenance(code: unknown): boolean;")
    ap("")

    # Telemetry entry shape, mirrored from the Pydantic model so the editor can
    # check field names the renderer reads.
    ap("/** Canonical telemetry reading — one channel at one time step. */")
    ap("export interface TelemetryEntry {")
    ap("  timestamp: string;")
    ap("  parameter: string;")
    ap("  relative_time_s: number | null;")
    ap("  value: number | null;")
    ap("  value_text: string | null;")
    ap("  unit: string | null;")
    ap("  status: TelemetryStatus;")
    ap("  anomalous: boolean | null;")
    ap("  nominal_min: number | null;")
    ap("  nominal_max: number | null;")
    ap("  baseline_mean: number | null;")
    ap("  baseline_std: number | null;")
    ap("}")
    ap("")
    ap("export interface Scenario {")
    ap("  scenario_id: number | null;")
    ap("  fault_type: string | null;")
    ap("  provenance: Provenance;")
    ap("  source_type: string;")
    ap("  source_note: string | null;")
    ap("  pre_fault_telemetry_window: TelemetryEntry[] | null;")
    ap("  [key: string]: unknown;")
    ap("}")
    ap("")
    ap("export interface ScenarioListResponse {")
    ap("  contract_version: string;")
    ap("  api_version: string;")
    ap("  count: number;")
    ap("  scenarios: Scenario[];")
    ap("}")
    ap("")

    return "\n".join(lines)


def build_readme() -> str:
    from app.api.models import API_VERSION, CONTRACT_VERSION

    return f"""# SENTINEL data contract

Generated artifacts. **Do not hand-edit anything in this directory.**

Contract version: `{CONTRACT_VERSION}`
API version: `{API_VERSION}`

## Layout

| Path | What it is |
| --- | --- |
| `openapi/openapi.json` | The served API, exported from FastAPI |
| `schemas/*.schema.json` | JSON Schema per request/response model |
| `frontend/contract.js` | Runtime constants for the browser client |
| `frontend/contract.d.ts` | Matching TypeScript declarations |
| `index.json` | Manifest: versions, enums, models, routes |

## Source of truth

`sentinel/backend/app/api/models.py` (Pydantic) and
`sentinel/backend/app/api/provenance.py`. Detection models come from
`sentinel/backend/app/detection/models.py`.

Nothing in this directory is authored by hand, so the backend and the frontend
cannot describe the same payload differently.

## Regenerate

```bash
python3 sentinel/backend/scripts/export_contracts.py
```

## Verify (CI)

```bash
python3 sentinel/backend/scripts/export_contracts.py --check
```

`--check` regenerates in memory and byte-compares against what is committed, so
a model change without a regenerated contract fails the build.

## Canonical telemetry

`pre_fault_telemetry_window` is the canonical telemetry representation.
`pre_fault_telemetry` is deprecated: still accepted on input, merged into the
canonical field by `app/api/adapters.py`, and never read directly by any
consumer.

## CRA mirror

Create React App 5 forbids imports from outside `src/`, so `frontend/contract.js`
is mirrored byte-for-byte to `sentinel/frontend/src/generated/contract.js`.
`--check` verifies the mirror matches; it is not a second source of truth.
"""


# ═══════════════════════════════════════════════════════════════════════════
# Write / check
# ═══════════════════════════════════════════════════════════════════════════

def collect() -> dict[Path, str]:
    """Every artifact this exporter owns, as path -> content."""
    artifacts: dict[Path, str] = {
        OPENAPI_DIR / "openapi.json": build_openapi(),
        FRONTEND_DIR / "contract.js": build_frontend_js(),
        FRONTEND_DIR / "contract.d.ts": build_frontend_dts(),
        CONTRACTS_DIR / "index.json": build_index(),
        CONTRACTS_DIR / "README.md": build_readme(),
    }
    for filename, content in build_schemas().items():
        artifacts[SCHEMAS_DIR / filename] = content
    # Byte-identical mirror for CRA's module scope.
    artifacts[CRA_MIRROR] = artifacts[FRONTEND_DIR / "contract.js"]
    return artifacts


def write_all(artifacts: dict[Path, str]) -> None:
    for path, content in sorted(artifacts.items()):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"  wrote {path.relative_to(_REPO)}  ({len(content)} bytes)")


def check_all(artifacts: dict[Path, str]) -> int:
    stale: list[str] = []
    missing: list[str] = []
    for path, content in sorted(artifacts.items()):
        rel = path.relative_to(_REPO)
        if not path.is_file():
            missing.append(str(rel))
            continue
        if path.read_text(encoding="utf-8") != content:
            stale.append(str(rel))
    if missing:
        print(f"  MISSING ({len(missing)}):")
        for name in missing:
            print(f"    {name}")
    if stale:
        print(f"  STALE ({len(stale)}):")
        for name in stale:
            print(f"    {name}")
    if missing or stale:
        print()
        print("  Contract artifacts are out of date with the models.")
        print("  Run: python3 sentinel/backend/scripts/export_contracts.py")
        return 1
    print(f"  OK — {len(artifacts)} artifact(s) match the models")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="Verify committed artifacts match the models; do not write",
    )
    args = parser.parse_args()

    print("SENTINEL contract exporter")
    print(f"  repo: {_REPO}")
    artifacts = collect()

    if args.check:
        print("  mode: --check (no files written)")
        return check_all(artifacts)

    write_all(artifacts)
    print(f"  {len(artifacts)} artifact(s) written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

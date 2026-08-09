# SENTINEL data contract

Generated artifacts. **Do not hand-edit anything in this directory.**

Contract version: `1.0.0`
API version: `v1`

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

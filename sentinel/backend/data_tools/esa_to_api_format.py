"""
esa_to_api_format.py
--------------------
Converts ESA ADB crash dump JSON files (produced by esa_adb_crash_dump.py)
into the CrashDumpRequest format required by the Sentinel POST /api/analyze
endpoint.

ESA field   →  API field
────────────────────────────────────────────────────────
timestamp_offset  →  timestamp  (kept as-is, renamed)
anomalous (bool)  →  status     ("ANOMALOUS" / "NOMINAL")
nominal_min       →  nominal_min (kept)
nominal_max       →  nominal_max (kept)
value             →  value       (kept)
parameter         →  parameter   (kept)

Usage
-----
# Convert a file and print the result:
    python data_tools/esa_to_api_format.py \
        data/esa_crash_dumps/esa_mission1_id_109_sentinel_only.json

# Save converted output:
    python data_tools/esa_to_api_format.py \
        data/esa_crash_dumps/esa_mission1_id_109_sentinel_only.json \
        --output data/esa_crash_dumps/esa_mission1_id_109_api_ready.json

# Post directly to a running Sentinel backend:
    python data_tools/esa_to_api_format.py \
        data/esa_crash_dumps/esa_mission1_id_109_sentinel_only.json \
        --post http://localhost:8000/api/analyze
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Core conversion logic
# ---------------------------------------------------------------------------

def _convert_telemetry_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Convert one ESA telemetry row to TelemetryEntry format.

    ESA format:
        {
            "timestamp_offset": "T-593.088s",
            "parameter": "channel_41",
            "value": 0.816503,
            "unit": "normalized",
            "nominal_min": 0.797548,
            "nominal_max": 0.82607,
            "anomalous": false
        }

    API TelemetryEntry format:
        {
            "timestamp": "T-593.088s",
            "parameter": "channel_41",
            "value": 0.816503,
            "status": "NOMINAL",
            "nominal_min": 0.797548,
            "nominal_max": 0.82607
        }
    """
    anomalous: bool = bool(entry.get("anomalous", False))
    status = "ANOMALOUS" if anomalous else "NOMINAL"

    # Use timestamp_offset if present, fall back to timestamp
    timestamp = entry.get("timestamp_offset") or entry.get("timestamp") or "T-0s"

    converted: Dict[str, Any] = {
        "timestamp": timestamp,
        "parameter": entry.get("parameter", "unknown"),
        "value": entry.get("value"),       # None allowed (dropout)
        "status": status,
    }

    # Preserve optional display fields if present
    if "nominal_min" in entry:
        converted["nominal_min"] = entry["nominal_min"]
    if "nominal_max" in entry:
        converted["nominal_max"] = entry["nominal_max"]

    return converted


def esa_to_api_format(esa_data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a full ESA crash dump dict to CrashDumpRequest format.

    Handles both:
      - esa_mission1_id_109_sentinel_only.json  (has pre_fault_telemetry)
      - esa_mission1_id_109_crash_dump.json     (full crash dump, same structure)
    """
    # --- Telemetry conversion ---
    raw_telem: List[Dict] = esa_data.get("pre_fault_telemetry", [])
    converted_telem: List[Dict] = [_convert_telemetry_entry(e) for e in raw_telem]

    # --- Build CrashDumpRequest payload ---
    api_payload: Dict[str, Any] = {
        "scenario_id": esa_data.get("scenario_id"),
        "fault_type": esa_data.get("fault_type", "ESA_ADB_ANOMALY"),
        "fault_register": esa_data.get("fault_register"),
        "safe_mode_trigger": esa_data.get("safe_mode_trigger")
            or esa_data.get("fault_type", "ESA_ADB_ANOMALY"),

        # Pre-fault telemetry in the validated TelemetryEntry format
        "pre_fault_telemetry_window": converted_telem,

        # Also keep raw dict list in the legacy field for LLM prompt context
        "pre_fault_telemetry": raw_telem,
    }

    # --- Pass-through any extra fields the LLM can use ---
    for key in ("event_log", "hardware_state", "operating_context",
                "timestamp", "mission_phase", "telecommand_context"):
        if key in esa_data:
            api_payload[key] = esa_data[key]

    # Remove None values to keep the payload clean
    api_payload = {k: v for k, v in api_payload.items() if v is not None}

    return api_payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert ESA ADB crash dump JSON to Sentinel API format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to the ESA crash dump JSON file",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Save converted JSON to this file (default: print to stdout)",
    )
    parser.add_argument(
        "--post",
        metavar="URL",
        default=None,
        help=(
            "POST the converted payload to this URL "
            "(e.g. http://localhost:8000/api/analyze). "
            "Requires the 'requests' package."
        ),
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=True,
        help="Pretty-print JSON output (default: True)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # --- Load ESA file ---
    input_path: Path = args.input.resolve()
    if not input_path.is_file():
        print(f"ERROR: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading ESA crash dump: {input_path}", file=sys.stderr)
    with input_path.open() as f:
        esa_data: Dict[str, Any] = json.load(f)

    # --- Convert ---
    api_payload = esa_to_api_format(esa_data)

    telem_count = len(api_payload.get("pre_fault_telemetry_window", []))
    anomaly_count = sum(
        1 for e in api_payload.get("pre_fault_telemetry_window", [])
        if e.get("status") == "ANOMALOUS"
    )
    print(
        f"Converted: scenario_id={api_payload.get('scenario_id')}, "
        f"fault_type={api_payload.get('fault_type')}, "
        f"telemetry_entries={telem_count}, "
        f"anomalous={anomaly_count}",
        file=sys.stderr,
    )

    indent = 2 if args.pretty else None
    payload_json = json.dumps(api_payload, indent=indent)

    # --- Output ---
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload_json)
        print(f"Saved to: {args.output}", file=sys.stderr)

    if args.post:
        try:
            import requests  # type: ignore
        except ImportError:
            print(
                "ERROR: 'requests' package not installed. "
                "Run: pip install requests",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"POSTing to: {args.post}", file=sys.stderr)
        resp = requests.post(
            args.post,
            json=api_payload,
            headers={"Content-Type": "application/json"},
            timeout=120,
            stream=True,
        )
        if resp.status_code != 200:
            print(
                f"ERROR: Server returned {resp.status_code}:\n{resp.text}",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"\n--- Server response (streaming) ---", file=sys.stderr)
        for line in resp.iter_lines():
            if line:
                print(line.decode())
        return

    if not args.output:
        # Default: print to stdout
        print(payload_json)


if __name__ == "__main__":
    main()

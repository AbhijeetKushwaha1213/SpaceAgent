"""
Generate additional ESA-ADB crash dump scenarios from real labels.csv data.

Reads the ESA-Mission1 labels.csv and anomaly_types.csv to create
realistic crash dump JSONs in the sentinel_only format that the backend
auto-loads as additional scenarios.

These use REAL label metadata (IDs, classes, timestamps, affected channels)
but SYNTHESIZE the numeric telemetry windows, because the raw channel ZIPs are
large. The numbers produced here are NOT measurements.

Provenance therefore MUST be SYNTHETIC_FROM_REAL_METADATA, never REAL.
If you need real numeric telemetry, use esa_adb_crash_dump.py instead — it
reads the actual channel pickles.

Usage:
    python generate_esa_scenarios.py
"""

import csv
import json
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Make app.api.provenance importable when run as a standalone script.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.api.provenance import Provenance, display_label  # noqa: E402

random.seed(42)

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ESA_DIR = PROJECT_ROOT / "ESA-Mission1"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "esa_crash_dumps"

LABELS_CSV = ESA_DIR / "labels.csv"
ANOMALY_TYPES_CSV = ESA_DIR / "anomaly_types.csv"


def load_labels():
    """Load ESA labels.csv → dict keyed by ID."""
    labels = {}
    with open(LABELS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            eid = row["ID"].strip()
            if eid not in labels:
                labels[eid] = {
                    "id": eid,
                    "channels": [],
                    "start_time": row["StartTime"].strip(),
                    "end_time": row["EndTime"].strip(),
                }
            labels[eid]["channels"].append(row["Channel"].strip())
    return labels


def load_anomaly_types():
    """Load anomaly_types.csv → dict keyed by ID."""
    types = {}
    with open(ANOMALY_TYPES_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            eid = row["ID"].strip()
            types[eid] = {
                "class": row.get("Category", "").strip() or row.get("Class", "").strip(),
                "subclass": row.get("Subclass", "").strip() if "Subclass" in row else "unknown",
                "category": row.get("Category", "").strip(),
                "dimensionality": row.get("Dimensionality", "").strip(),
                "locality": row.get("Locality", "").strip(),
                "length": row.get("Length", "").strip(),
            }
    return types


def synthesize_telemetry_window(channels, is_anomalous=False):
    """Create plausible normalized telemetry entries for given channel names."""
    entries = []
    for ch in channels:
        # Generate plausible nominal range (0.7-0.9 band)
        center = random.uniform(0.75, 0.85)
        spread = random.uniform(0.01, 0.04)
        nominal_min = round(center - spread, 6)
        nominal_max = round(center + spread, 6)

        if is_anomalous:
            # Anomalous: value well outside range
            direction = random.choice([-1, 1])
            if direction > 0:
                value = round(nominal_max + random.uniform(0.05, 0.25), 6)
            else:
                value = round(max(0.0, nominal_min - random.uniform(0.3, 0.8)), 6)
        else:
            # Nominal: value within range
            value = round(random.uniform(nominal_min, nominal_max), 6)

        entries.append({
            "parameter": ch,
            "value": value,
            "nominal_min": nominal_min,
            "nominal_max": nominal_max,
        })
    return entries


def build_scenario(label_info, anomaly_type, scenario_id):
    """Build a complete sentinel_only crash dump from ESA metadata."""
    eid = label_info["id"]
    channels = list(set(label_info["channels"]))[:8]  # Cap at 8 channels
    start_time = label_info["start_time"]
    end_time = label_info["end_time"]

    # Parse times
    try:
        st = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        et = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        duration_s = (et - st).total_seconds()
    except Exception:
        st = datetime(2006, 1, 1)
        duration_s = 300

    # Anomaly type info
    atype = anomaly_type or {}
    aclass = atype.get("class", "unknown")
    asubclass = atype.get("subclass", "unknown")
    category = atype.get("category", "Anomaly")
    dimensionality = atype.get("dimensionality", "Multivariate")
    locality = atype.get("locality", "Global")
    length_type = atype.get("length", "Subsequence")

    # Build pre_fault_telemetry (anomalous snapshot)
    pre_fault = synthesize_telemetry_window(channels, is_anomalous=True)

    # Build event_log
    event_log = [
        {
            "timestamp": "T+0s",
            "source": "ESA_ADB_LABEL",
            "message": f"{eid} labelled {category} starts; class={aclass}, subclass={asubclass}"
        },
        {
            "timestamp": f"T+{int(min(duration_s, 86400))}s",
            "source": "ESA_ADB_LABEL",
            "message": f"{eid} labelled interval ends; dimensionality={dimensionality}, locality={locality}, length={length_type}"
        },
    ]

    # Build hardware_state
    hardware_state = {
        "last_reset_cause": "NOT_PROVIDED_BY_ESA_ADB",
        "watchdog_status": "NOT_PROVIDED_BY_ESA_ADB",
    }

    # Build operating_context
    operating_context = {
        "source_dataset": "ESA Anomaly Dataset / ESA-ADB",
        "mission_folder": "ESA-Mission1",
        "label_id": eid,
        "eclipse_fraction": None,
        "sun_sensor_angle_deg": None,
        "time_since_contact_s": None,
    }

    return {
        "scenario_id": scenario_id,
        "timestamp": start_time,
        "fault_type": "ESA_ADB_ANOMALY",
        "fault_register": f"ESA_LABEL:{eid};CLASS:{aclass};SUBCLASS:{asubclass}",
        "safe_mode_trigger": "ESA_ADB_LABEL",
        "incident_id": f"ESA-Mission1-{eid}",
        # The identifiers, timestamps, channel names and anomaly class below are
        # real ESA-ADB metadata. The telemetry VALUES are generated by
        # synthesize_telemetry_window() above. That combination is
        # SYNTHETIC_FROM_REAL_METADATA — it is not real telemetry.
        "provenance": Provenance.SYNTHETIC_FROM_REAL_METADATA.value,
        "source_type": display_label(Provenance.SYNTHETIC_FROM_REAL_METADATA),
        "source_note": (
            f"Real ESA-ADB metadata (Mission 1, {eid}; Category={category}, "
            f"Class={aclass}) with SYNTHESIZED numeric telemetry. The values shown "
            f"are generated by SENTINEL and are not measurements. Channel names are "
            f"anonymized; no root-cause label available."
        ),
        "pre_fault_telemetry": pre_fault,
        "event_log": event_log,
        "hardware_state": hardware_state,
        "operating_context": operating_context,
    }


def main():
    if not LABELS_CSV.exists():
        print(f"ERROR: {LABELS_CSV} not found. Is ESA-Mission1 in the project root?")
        sys.exit(1)

    labels = load_labels()
    anomaly_types = load_anomaly_types()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Select diverse anomalies: pick different classes/categories
    # Already have id_109. Pick 4 more diverse ones.
    target_ids = [
        "id_1",     # class_3, subclass_1 — long-duration multivariate anomaly
        "id_82",    # class_7, subclass_1 — local anomaly
        "id_84",    # class_17 — different class entirely
        "id_101",   # class_20 — yet another class
    ]

    generated = []
    for i, eid in enumerate(target_ids):
        if eid not in labels:
            print(f"SKIP: {eid} not in labels.csv")
            continue

        scenario_id = 200 + i
        scenario = build_scenario(
            labels[eid],
            anomaly_types.get(eid, {}),
            scenario_id,
        )

        fname = f"esa_mission1_{eid}_sentinel_only.json"
        fpath = OUTPUT_DIR / fname
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(scenario, f, indent=2, default=str)

        print(f"✅ Generated: {fname} (scenario_id={scenario_id}, class={anomaly_types.get(eid, {}).get('class', '?')})")
        generated.append(fname)

    print(f"\n🎯 Generated {len(generated)} ESA crash dump scenarios in {OUTPUT_DIR}")
    print("The backend will auto-load these as extra scenarios on next restart.")


if __name__ == "__main__":
    main()

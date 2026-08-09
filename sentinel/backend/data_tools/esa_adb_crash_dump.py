"""Build SENTINEL crash-dump proxies from ESA-ADB mission folders.

The ESA Anomaly Detection Benchmark stores mission channels and telecommands as
zipped pandas pickles. This tool avoids fully extracting the 9+ GB Mission1
payload unless explicitly requested, and instead reads only the labelled event
window needed for an agent-ready crash dump.

Examples
--------
Summarize labels without pandas:

    python sentinel/backend/data_tools/esa_adb_crash_dump.py summary \
        --dataset ESA-Mission1 \
        --output sentinel/backend/data/esa_crash_dumps/mission1_summary.json

Build one real telemetry crash-dump proxy:

    python sentinel/backend/data_tools/esa_adb_crash_dump.py build \
        --dataset ESA-Mission1 \
        --event-id id_109 \
        --output sentinel/backend/data/esa_crash_dumps/esa_mission1_id_109_crash_dump.json \
        --compact-output sentinel/backend/data/esa_crash_dumps/esa_mission1_id_109_sentinel_only.json

Dry-run archive extraction:

    python sentinel/backend/data_tools/esa_adb_crash_dump.py extract \
        --dataset ESA-Mission1 \
        --output ESA-Mission1_extracted \
        --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ISO_WITH_MS_Z = "%Y-%m-%dT%H:%M:%S.%fZ"


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def natural_id(value: str) -> int:
    return int(value.rsplit("_", 1)[1])


def parse_esa_time(value: str) -> datetime:
    return datetime.strptime(value, ISO_WITH_MS_Z).replace(tzinfo=timezone.utc)


def iso_z(value: datetime) -> str:
    value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def load_metadata(dataset: Path) -> dict[str, Any]:
    required = ["channels.csv", "telecommands.csv", "labels.csv", "anomaly_types.csv"]
    missing = [name for name in required if not (dataset / name).exists()]
    if missing:
        raise FileNotFoundError(f"Missing required ESA-ADB files in {dataset}: {missing}")

    channels = read_rows(dataset / "channels.csv")
    telecommands = read_rows(dataset / "telecommands.csv")
    labels = read_rows(dataset / "labels.csv")
    anomaly_types = read_rows(dataset / "anomaly_types.csv")

    return {
        "channels": channels,
        "telecommands": telecommands,
        "labels": labels,
        "anomaly_types": anomaly_types,
        "channel_by_name": {row["Channel"]: row for row in channels},
        "telecommand_by_name": {row["Telecommand"]: row for row in telecommands},
        "type_by_id": {row["ID"]: row for row in anomaly_types},
        "labels_by_id": group_by(labels, "ID"),
    }


def group_by(rows: list[dict[str, str]], key: str) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    return dict(grouped)


def summarize_dataset(dataset: Path) -> dict[str, Any]:
    meta = load_metadata(dataset)
    labels = meta["labels"]
    anomaly_types = meta["anomaly_types"]
    channels = meta["channels"]
    telecommands = meta["telecommands"]
    labels_by_id = meta["labels_by_id"]
    type_by_id = meta["type_by_id"]
    channel_by_name = meta["channel_by_name"]

    event_summaries: list[dict[str, Any]] = []
    durations: list[float] = []
    for event_id, rows in sorted(labels_by_id.items(), key=lambda item: natural_id(item[0])):
        starts = [parse_esa_time(row["StartTime"]) for row in rows]
        ends = [parse_esa_time(row["EndTime"]) for row in rows]
        event_type = type_by_id.get(event_id, {})
        event_channels = sorted({row["Channel"] for row in rows}, key=natural_id)
        duration_hours = (max(ends) - min(starts)).total_seconds() / 3600.0
        durations.append(duration_hours)
        event_summaries.append(
            {
                "id": event_id,
                "start": iso_z(min(starts)),
                "end": iso_z(max(ends)),
                "duration_hours": round(duration_hours, 6),
                "category": event_type.get("Category", ""),
                "class": event_type.get("Class", ""),
                "subclass": event_type.get("Subclass", ""),
                "dimensionality": event_type.get("Dimensionality", ""),
                "locality": event_type.get("Locality", ""),
                "length": event_type.get("Length", ""),
                "channel_count": len(event_channels),
                "channels": event_channels[:20],
                "subsystems": sorted(
                    {
                        channel_by_name.get(channel, {}).get("Subsystem", "unknown")
                        for channel in event_channels
                    }
                ),
            }
        )

    return {
        "dataset": str(dataset),
        "channels": {
            "count": len(channels),
            "target_counts": dict(Counter(row["Target"] for row in channels)),
            "subsystem_counts": dict(Counter(row["Subsystem"] for row in channels)),
            "group_counts": dict(Counter(row["Group"] for row in channels)),
        },
        "telecommands": {
            "count": len(telecommands),
            "priority_counts": dict(Counter(row["Priority"] for row in telecommands)),
        },
        "labels": {
            "row_count": len(labels),
            "event_count": len(labels_by_id),
            "labelled_channel_count": len({row["Channel"] for row in labels}),
            "top_labelled_channels": Counter(row["Channel"] for row in labels).most_common(15),
        },
        "anomaly_types": {
            "row_count": len(anomaly_types),
            "category_counts": dict(Counter(row["Category"] for row in anomaly_types)),
            "class_counts": dict(Counter(row["Class"] for row in anomaly_types)),
            "subclass_counts": dict(Counter(row["Subclass"] for row in anomaly_types)),
            "dimensionality_counts": dict(Counter(row["Dimensionality"] for row in anomaly_types)),
            "locality_counts": dict(Counter(row["Locality"] for row in anomaly_types)),
            "length_counts": dict(Counter(row["Length"] for row in anomaly_types)),
        },
        "duration_hours": describe_numbers(durations),
        "recommended_first_events": [
            event
            for event in event_summaries
            if event["category"] == "Anomaly"
            and event["channel_count"] <= 6
            and event["duration_hours"] <= 8
        ][:25],
        "event_summaries": event_summaries,
    }


def describe_numbers(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0}
    ordered = sorted(values)

    def quantile(q: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        pos = q * (len(ordered) - 1)
        lo = math.floor(pos)
        hi = math.ceil(pos)
        if lo == hi:
            return ordered[lo]
        frac = pos - lo
        return ordered[lo] * (1.0 - frac) + ordered[hi] * frac

    return {
        "count": len(values),
        "min": round(ordered[0], 6),
        "p10": round(quantile(0.10), 6),
        "p25": round(quantile(0.25), 6),
        "p50": round(quantile(0.50), 6),
        "p75": round(quantile(0.75), 6),
        "p90": round(quantile(0.90), 6),
        "p95": round(quantile(0.95), 6),
        "max": round(ordered[-1], 6),
    }


def require_pandas():
    try:
        import pandas as pd  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "The build command needs pandas because ESA-ADB channel files are "
            "zipped pandas pickles. Install pandas in your env, or run with the "
            "Codex bundled Python runtime used in this thread."
        ) from exc
    return pd


def build_crash_dump(args: argparse.Namespace) -> dict[str, Any]:
    pd = require_pandas()
    dataset = args.dataset
    meta = load_metadata(dataset)
    labels_by_id = meta["labels_by_id"]
    if args.event_id not in labels_by_id:
        known = ", ".join(sorted(labels_by_id, key=natural_id)[:10])
        raise SystemExit(f"Unknown event id {args.event_id!r}. First known IDs: {known}")

    event_rows = labels_by_id[args.event_id]
    anomaly_type = meta["type_by_id"].get(args.event_id, {})
    channel_by_name = meta["channel_by_name"]
    telecommand_by_name = meta["telecommand_by_name"]

    event_start = min(parse_esa_time(row["StartTime"]) for row in event_rows)
    event_end = max(parse_esa_time(row["EndTime"]) for row in event_rows)
    window_start = event_start - pd.Timedelta(seconds=args.pre_seconds).to_pytimedelta()
    window_end = event_end + pd.Timedelta(seconds=args.post_seconds).to_pytimedelta()
    baseline_end = event_start - pd.Timedelta(seconds=args.pre_seconds).to_pytimedelta()
    baseline_start = baseline_end - pd.Timedelta(hours=args.baseline_hours).to_pytimedelta()

    telemetry_records: list[dict[str, Any]] = []
    channel_summaries: list[dict[str, Any]] = []
    event_log: list[dict[str, str]] = [
        {
            "time_offset": seconds_offset(event_start, event_start),
            "source": "ESA_ADB_LABEL",
            "message": (
                f"{args.event_id} labelled {anomaly_type.get('Category', 'event')} "
                f"starts; class={anomaly_type.get('Class', 'unknown')}"
            ),
        },
        {
            "time_offset": seconds_offset(event_start, event_end),
            "source": "ESA_ADB_LABEL",
            "message": f"{args.event_id} labelled interval ends",
        },
    ]

    for label_row in sorted(event_rows, key=lambda row: natural_id(row["Channel"])):
        channel = label_row["Channel"]
        channel_start = parse_esa_time(label_row["StartTime"])
        channel_end = parse_esa_time(label_row["EndTime"])
        archive = dataset / "channels" / f"{channel}.zip"
        if not archive.exists():
            raise FileNotFoundError(f"Missing channel archive: {archive}")

        df = pd.read_pickle(archive, compression="zip")
        series = df[channel]
        baseline = series.loc[naive_utc(baseline_start) : naive_utc(baseline_end)]
        window = series.loc[naive_utc(window_start) : naive_utc(window_end)]
        labelled = series.loc[naive_utc(channel_start) : naive_utc(channel_end)]

        baseline_values = [float(value) for value in baseline.dropna().tolist()]
        baseline_mean = statistics.fmean(baseline_values) if baseline_values else None
        baseline_std = statistics.stdev(baseline_values) if len(baseline_values) > 1 else None
        sampled = sample_series(pd, window, labelled, args.max_points_per_channel)

        z_scores: list[float] = []
        for timestamp, value in sampled.items():
            value_float = float(value)
            z_score = compute_z_score(value_float, baseline_mean, baseline_std)
            if z_score is not None:
                z_scores.append(abs(z_score))
            timestamp_utc = timestamp.to_pydatetime().replace(tzinfo=timezone.utc)
            status = classify_status(timestamp_utc, channel_start, channel_end, z_score, args.z_threshold)
            telemetry_records.append(
                {
                    "timestamp_utc": iso_z(timestamp_utc),
                    "timestamp_offset": seconds_offset(event_start, timestamp_utc),
                    "relative_time_s": round((timestamp_utc - event_start).total_seconds(), 3),
                    "parameter": channel,
                    "value": round(value_float, 6),
                    "unit": "normalized",
                    "nominal_min": nominal_bound(baseline_mean, baseline_std, -3.0),
                    "nominal_max": nominal_bound(baseline_mean, baseline_std, 3.0),
                    "baseline_mean": round_or_none(baseline_mean),
                    "baseline_std": round_or_none(baseline_std),
                    "z_score": round_or_none(z_score),
                    "status": status,
                    "anomalous": status in {"LABELLED_ANOMALY", "STATISTICAL_OUTLIER"},
                    "source_label_id": args.event_id,
                }
            )

        channel_meta = channel_by_name.get(channel, {})
        channel_summaries.append(
            {
                "channel": channel,
                "subsystem": channel_meta.get("Subsystem", "unknown"),
                "physical_unit": channel_meta.get("Physical Unit", "unknown"),
                "group": channel_meta.get("Group", "unknown"),
                "target": channel_meta.get("Target", "unknown"),
                "label_start": iso_z(channel_start),
                "label_end": iso_z(channel_end),
                "baseline_start": iso_z(baseline_start),
                "baseline_end": iso_z(baseline_end),
                "baseline_rows": int(len(baseline)),
                "window_rows": int(len(window)),
                "label_rows": int(len(labelled)),
                "baseline_mean": round_or_none(baseline_mean),
                "baseline_std": round_or_none(baseline_std),
                "window_min": round_or_none(float(window.min())) if len(window) else None,
                "window_max": round_or_none(float(window.max())) if len(window) else None,
                "label_min": round_or_none(float(labelled.min())) if len(labelled) else None,
                "label_max": round_or_none(float(labelled.max())) if len(labelled) else None,
                "max_abs_z_in_sample": round(max(z_scores), 3) if z_scores else None,
            }
        )

    telemetry_records.sort(key=lambda row: (row["timestamp_utc"], natural_id(row["parameter"])))

    telecommand_context = collect_telecommands(
        pd=pd,
        dataset=dataset,
        telecommands=meta["telecommands"],
        telecommand_by_name=telecommand_by_name,
        event_start=event_start,
        event_end=event_end,
        lookback_hours=args.telecommand_lookback_hours,
        lookahead_hours=args.telecommand_lookahead_hours,
        max_commands=args.max_telecommands,
    )

    for command in telecommand_context["commands"][:10]:
        event_log.append(
            {
                "time_offset": command["timestamp_offset"],
                "source": "GROUND_TELECOMMAND",
                "message": (
                    f"{command['telecommand']} observed "
                    f"(priority={command['priority']})"
                ),
            }
        )

    labelled_channels = sorted({row["Channel"] for row in event_rows}, key=natural_id)
    scenario_id = natural_id(args.event_id)
    sentinel_crash_dump = {
        "scenario_id": scenario_id,
        "timestamp": iso_z(event_end),
        "fault_type": f"ESA_ADB_{anomaly_type.get('Category', 'EVENT').upper().replace(' ', '_')}",
        # REAL: every value in pre_fault_telemetry below was read from the
        # ESA-ADB channel archives via pd.read_pickle. Nothing is generated.
        # See app/api/provenance.py for the vocabulary.
        "provenance": "REAL",
        "source_type": "REAL ESA TELEMETRY",
        "source_note": (
            f"Numeric telemetry read directly from the ESA Anomaly Detection Benchmark "
            f"({dataset.name}, {args.event_id}) channel archives. Channel names are "
            f"anonymized; no root-cause label or recovery command is available."
        ),
        "fault_register": (
            f"ESA_LABEL:{args.event_id};"
            f"CLASS:{anomaly_type.get('Class', 'unknown')};"
            f"SUBCLASS:{anomaly_type.get('Subclass', 'unknown')}"
        ),
        "pre_fault_telemetry": [
            {
                "timestamp_offset": row["timestamp_offset"],
                "parameter": row["parameter"],
                "value": row["value"],
                "unit": row["unit"],
                "nominal_min": row["nominal_min"],
                "nominal_max": row["nominal_max"],
                "anomalous": row["anomalous"],
            }
            for row in telemetry_records
        ],
        "event_log": event_log,
        "hardware_state": {
            "last_reset_cause": "NOT_PROVIDED_BY_ESA_ADB",
            "SEU_event_count_since_boot": "NOT_PROVIDED_BY_ESA_ADB",
            "running_processes": [],
            "memory_allocation_MB": "NOT_PROVIDED_BY_ESA_ADB",
        },
        "operating_context": {
            "source_dataset": "ESA Anomaly Dataset / ESA-ADB",
            "mission_folder": dataset.name,
            "label_id": args.event_id,
            "safe_mode_state": "not_provided_by_dataset",
            "mission_phase": "unknown",
            "minutes_since_last_ground_contact": "unknown",
            "safe_mode_entry_count_total": "unknown",
        },
    }

    return {
        "incident_id": f"{dataset.name}-{args.event_id}",
        "source": {
            "dataset": "ESA Anomaly Dataset / ESA-ADB",
            "mission_folder": str(dataset),
            "note": (
                "ESA-ADB provides real-life telemetry channels, telecommand timestamps, "
                "and curated anomaly labels. It does not provide true safe-mode state, "
                "root-cause text, spacecraft command names, or a hardware fault register."
            ),
        },
        "event_label": {
            "id": args.event_id,
            "category": anomaly_type.get("Category", ""),
            "class": anomaly_type.get("Class", ""),
            "subclass": anomaly_type.get("Subclass", ""),
            "dimensionality": anomaly_type.get("Dimensionality", ""),
            "locality": anomaly_type.get("Locality", ""),
            "length": anomaly_type.get("Length", ""),
            "labelled_channels": labelled_channels,
        },
        "event_window_utc": {
            "baseline_start": iso_z(baseline_start),
            "baseline_end": iso_z(baseline_end),
            "pre_fault_start": iso_z(window_start),
            "label_start": iso_z(event_start),
            "label_end": iso_z(event_end),
            "post_fault_end": iso_z(window_end),
        },
        "safe_mode_state": "not_provided_by_dataset",
        "fault_register": sentinel_crash_dump["fault_register"],
        "ground_truth_for_evaluation": {
            "what_is_known": "label interval, anomaly category/class/subclass, affected channels",
            "what_is_not_known": "engineering root cause, recovery action, true safe-mode trigger",
            "label_id": args.event_id,
            "category": anomaly_type.get("Category", ""),
            "class": anomaly_type.get("Class", ""),
            "subclass": anomaly_type.get("Subclass", ""),
            "affected_channels": labelled_channels,
        },
        "channel_summaries": channel_summaries,
        "telecommand_context": telecommand_context,
        "pre_fault_telemetry_window": telemetry_records,
        "sentinel_crash_dump": sentinel_crash_dump,
        "agent_task": {
            "recommended_use": (
                "Use this as an anomaly-triage crash dump: ask the agent to identify "
                "which channels changed, rank plausible subsystem hypotheses, and "
                "mark recovery commands as human-review required because ESA-ADB does "
                "not expose true spacecraft procedure context."
            ),
            "do_not_claim": (
                "Do not claim this label is a confirmed safe-mode crash or a confirmed "
                "root cause. It is a curated anomaly interval over anonymized telemetry."
            ),
        },
    }


def sample_series(pd: Any, window: Any, labelled: Any, max_points: int) -> Any:
    if len(window) <= max_points:
        return window
    indexes = set()
    for i in range(max_points):
        indexes.add(round(i * (len(window) - 1) / max(1, max_points - 1)))
    if len(labelled):
        label_positions = window.index.get_indexer(labelled.index, method=None)
        label_positions = [pos for pos in label_positions if pos >= 0]
        if label_positions:
            for i in range(min(5, len(label_positions))):
                indexes.add(label_positions[round(i * (len(label_positions) - 1) / max(1, min(5, len(label_positions)) - 1))])
    return window.iloc[sorted(indexes)]


def compute_z_score(value: float, mean: float | None, std: float | None) -> float | None:
    if mean is None or std is None or std == 0.0:
        return None
    return (value - mean) / std


def classify_status(
    timestamp_utc: datetime,
    channel_start: datetime,
    channel_end: datetime,
    z_score: float | None,
    threshold: float,
) -> str:
    if channel_start <= timestamp_utc <= channel_end:
        return "LABELLED_ANOMALY"
    if z_score is not None and abs(z_score) >= threshold:
        return "STATISTICAL_OUTLIER"
    return "NOMINAL_CONTEXT"


def nominal_bound(mean: float | None, std: float | None, multiplier: float) -> float | None:
    if mean is None or std is None:
        return None
    return round(mean + multiplier * std, 6)


def round_or_none(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def seconds_offset(anchor: datetime, value: datetime) -> str:
    delta = (value - anchor).total_seconds()
    sign = "-" if delta < 0 else "+"
    return f"T{sign}{abs(delta):.3f}s"


def collect_telecommands(
    pd: Any,
    dataset: Path,
    telecommands: list[dict[str, str]],
    telecommand_by_name: dict[str, dict[str, str]],
    event_start: datetime,
    event_end: datetime,
    lookback_hours: float,
    lookahead_hours: float,
    max_commands: int,
) -> dict[str, Any]:
    start = event_start - pd.Timedelta(hours=lookback_hours).to_pytimedelta()
    end = event_end + pd.Timedelta(hours=lookahead_hours).to_pytimedelta()
    hits: list[dict[str, Any]] = []
    skipped: list[str] = []

    for row in sorted(telecommands, key=lambda item: natural_id(item["Telecommand"])):
        name = row["Telecommand"]
        archive = dataset / "telecommands" / f"{name}.zip"
        if not archive.exists():
            skipped.append(name)
            continue
        try:
            df = pd.read_pickle(archive, compression="zip")
        except Exception as exc:  # Keep scanning if one tiny telecommand pickle is bad.
            skipped.append(f"{name}:{exc}")
            continue

        column = df.columns[0]
        window = df.loc[naive_utc(start) : naive_utc(end)]
        for timestamp, value in window[column].items():
            timestamp_utc = timestamp.to_pydatetime().replace(tzinfo=timezone.utc)
            hits.append(
                {
                    "timestamp_utc": iso_z(timestamp_utc),
                    "timestamp_offset": seconds_offset(event_start, timestamp_utc),
                    "relative_time_s": round((timestamp_utc - event_start).total_seconds(), 3),
                    "telecommand": name,
                    "priority": telecommand_by_name.get(name, {}).get("Priority", "unknown"),
                    "value": int(value),
                }
            )

    hits.sort(key=lambda row: row["timestamp_utc"])
    counts = Counter(row["telecommand"] for row in hits)
    return {
        "window_start": iso_z(start),
        "window_end": iso_z(end),
        "total_hits": len(hits),
        "returned_hits": min(len(hits), max_commands),
        "top_telecommands": counts.most_common(15),
        "commands": hits[:max_commands],
        "skipped": skipped[:20],
    }


def extract_archives(args: argparse.Namespace) -> dict[str, Any]:
    dataset = args.dataset
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    roots = []
    if args.kind in {"all", "channels"}:
        roots.append(dataset / "channels")
    if args.kind in {"all", "telecommands"}:
        roots.append(dataset / "telecommands")

    extracted: list[dict[str, Any]] = []
    skipped: list[str] = []
    archives: list[Path] = []
    for root in roots:
        archives.extend(sorted(root.glob("*"), key=lambda path: path.name))
    if args.limit:
        archives = archives[: args.limit]

    for archive in archives:
        relative_root = archive.parent.name
        target_dir = output / relative_root
        if archive.suffix != ".zip":
            skipped.append(str(archive))
            continue
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                target = target_dir / info.filename
                extracted.append(
                    {
                        "archive": str(archive),
                        "member": info.filename,
                        "target": str(target),
                        "uncompressed_bytes": info.file_size,
                    }
                )
                if args.dry_run:
                    continue
                if target.exists() and not args.overwrite:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                zf.extract(info, path=target_dir)

    return {
        "dataset": str(dataset),
        "output": str(output),
        "dry_run": args.dry_run,
        "archive_count": len(archives),
        "member_count": len(extracted),
        "uncompressed_bytes": sum(row["uncompressed_bytes"] for row in extracted),
        "extracted": extracted[:100],
        "skipped": skipped,
    }


def write_or_print(data: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(data, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote {output}")
    else:
        print(rendered)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    summary = sub.add_parser("summary", help="Summarize ESA-ADB labels and metadata.")
    summary.add_argument("--dataset", type=Path, required=True)
    summary.add_argument("--output", type=Path)

    build = sub.add_parser("build", help="Build a crash-dump proxy for one ESA label ID.")
    build.add_argument("--dataset", type=Path, required=True)
    build.add_argument("--event-id", required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--compact-output", type=Path)
    build.add_argument("--pre-seconds", type=int, default=600)
    build.add_argument("--post-seconds", type=int, default=120)
    build.add_argument("--baseline-hours", type=float, default=2.0)
    build.add_argument("--max-points-per-channel", type=int, default=14)
    build.add_argument("--z-threshold", type=float, default=3.0)
    build.add_argument("--telecommand-lookback-hours", type=float, default=2.0)
    build.add_argument("--telecommand-lookahead-hours", type=float, default=1.0)
    build.add_argument("--max-telecommands", type=int, default=80)

    extract = sub.add_parser("extract", help="Optionally extract zipped pickles.")
    extract.add_argument("--dataset", type=Path, required=True)
    extract.add_argument("--output", type=Path, required=True)
    extract.add_argument("--kind", choices=["all", "channels", "telecommands"], default="all")
    extract.add_argument("--limit", type=int)
    extract.add_argument("--dry-run", action="store_true")
    extract.add_argument("--overwrite", action="store_true")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "summary":
        write_or_print(summarize_dataset(args.dataset), args.output)
    elif args.command == "build":
        data = build_crash_dump(args)
        write_or_print(data, args.output)
        if args.compact_output:
            write_or_print(data["sentinel_crash_dump"], args.compact_output)
    elif args.command == "extract":
        write_or_print(extract_archives(args), None)
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
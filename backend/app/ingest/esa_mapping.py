"""
SENTINEL — ESA Channel Mapping Layer (ingest/esa_mapping.py)

Phase 15. The single place ESA-ADB telemetry channels are mapped onto the
Sentinel canonical vocabulary — or, when that is impossible, are declared
UNMAPPED_CHANNEL with their provenance intact.

Why this layer exists
---------------------
ESA-Mission1 telemetry is real but anonymised. The channel names are
``channel_<n>``, the subsystem and physical-unit columns in ``channels.csv``
are anonymised buckets (``subsystem_5``, ``physical_unit_4``), and the
workflow document (docs/person1_esa_crash_dump_workflow.md) explicitly
forbids claiming a bucket is a real subsystem or unit. There is therefore NO
per-channel semantic mapping in this repository, and there must not be one
invented here.

The mapping layer therefore does two things:

  * carries the ESA name, the anonymised buckets when a metadata source is
    available, and the provenance of every one of those facts;
  * marks any channel that cannot be mapped to a canonical Sentinel channel
    as ``UNMAPPED_CHANNEL`` with confidence UNMAPPED.

An UNMAPPED_CHANNEL can never support a fault hypothesis, never enter a
physics model, and never be reported as healthy. It is reported as unmapped,
with the reason, and nothing else.

Where the buckets come from
---------------------------
``map_esa_channel`` accepts optional ``subsystem_bucket`` / ``unit_bucket``
values. The repository does not ship ``channels.csv``; a caller (e.g. an
ingestion job with access to an ESA-ADB data directory) may supply the
buckets it loaded from there. Nothing in this module loads files or guesses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

ESA_MAPPING_VERSION = "1.0.0"

#: ESA-ADB anonymised channel naming: channel_<n>.
_ESA_CHANNEL = re.compile(r"^channel_\d+$")

#: Where the ESA-ADB data set would carry per-channel metadata. Not shipped.
ESA_METADATA_SOURCE = (
    "ESA-ADB channels.csv (anonymised Subsystem and Physical Unit buckets); "
    "not present in this repository"
)

#: Curated ESA -> canonical mappings. Deliberately EMPTY: the repository has no
#: per-channel evidence for any ESA-ADB channel (the names and units are
#: anonymised), so no mapping may be claimed. An ingestion job with access to
#: the ESA-ADB metadata may populate this from ``channels.csv``; anything added
#: here must carry its provenance.
CURATED_ESA_MAPPINGS: dict[str, EsaChannelMapping] = {}


class MappingConfidence(str, Enum):
    MAPPED = "MAPPED"
    """A reliable ESA -> canonical Sentinel channel mapping exists."""

    UNMAPPED = "UNMAPPED"
    """No reliable mapping exists; the channel is declared UNMAPPED_CHANNEL."""


@dataclass(frozen=True)
class EsaChannelMapping:
    """The record of one ESA channel's mapping attempt."""

    esa_channel: str
    canonical_channel: Optional[str]
    subsystem_bucket: Optional[str]
    physical_quantity: Optional[str]
    unit_bucket: Optional[str]
    provenance: str
    confidence: MappingConfidence
    source_file: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "esa_channel": self.esa_channel,
            "canonical_channel": self.canonical_channel,
            "subsystem_bucket": self.subsystem_bucket,
            "physical_quantity": self.physical_quantity,
            "unit_bucket": self.unit_bucket,
            "provenance": self.provenance,
            "confidence": self.confidence.value,
            "source_file": self.source_file,
            "status": (
                "UNMAPPED_CHANNEL" if self.canonical_channel is None
                else "MAPPED"
            ),
        }


def _unmapped(esa_channel: str, subsystem_bucket: Optional[str],
              unit_bucket: Optional[str]) -> EsaChannelMapping:
    return EsaChannelMapping(
        esa_channel=esa_channel,
        canonical_channel=None,
        subsystem_bucket=subsystem_bucket,
        physical_quantity=None,
        unit_bucket=unit_bucket,
        provenance=(
            "ESA-ADB anonymised channel naming; no per-channel semantics exist "
            "in this repository, so no canonical mapping can be established"
        ),
        confidence=MappingConfidence.UNMAPPED,
    )


def map_esa_channel(
    name: str,
    subsystem_bucket: Optional[str] = None,
    unit_bucket: Optional[str] = None,
) -> Optional[EsaChannelMapping]:
    """Map an ESA channel name onto the canonical vocabulary.

    Args:
        name: A channel name as it appears in ESA telemetry.
        subsystem_bucket: The anonymised subsystem bucket from ``channels.csv``,
            when a caller has read it. Never assumed here.
        unit_bucket: The anonymised physical-unit bucket, likewise optional.

    Returns:
        A mapping record, or None when the name is not an ESA-ADB channel (the
        caller should use the channel dictionary instead). ESA channels are
        never guessed: without repository evidence they come back
        UNMAPPED_CHANNEL with UNMAPPED confidence.
    """
    if not isinstance(name, str):
        return None
    key = name.strip()
    if not _ESA_CHANNEL.match(key):
        return None
    curated = CURATED_ESA_MAPPINGS.get(key)
    if curated is not None:
        return curated
    return _unmapped(key, subsystem_bucket, unit_bucket)


def esa_mapping_report(
    crash_dump: Optional[dict[str, Any]],
) -> list[EsaChannelMapping]:
    """Map every distinct ESA channel in a crash dump's telemetry window.

    Args:
        crash_dump: Any crash dump dict. None or malformed input yields an
            empty list rather than raising.

    Returns:
        One record per distinct ESA channel, in first-seen order.
    """
    if not isinstance(crash_dump, dict):
        return []

    channels: list[str] = []
    try:
        from app.api.adapters import canonical_channels

        for name in canonical_channels(crash_dump):
            if name not in channels:
                channels.append(name)
    except Exception:  # pragma: no cover — adapter is in-tree
        return []

    mappings: list[EsaChannelMapping] = []
    for name in channels:
        mapping = map_esa_channel(name)
        if mapping is not None:
            mappings.append(mapping)
    return mappings


def esa_mapping_status() -> dict[str, Any]:
    """Describe the mapping layer, for the API and tests."""
    return {
        "esa_mapping_version": ESA_MAPPING_VERSION,
        "confidence_levels": [c.value for c in MappingConfidence],
        "metadata_source": ESA_METADATA_SOURCE,
        "claim": (
            "ESA-ADB channels are anonymised at the physical-unit level. A "
            "channel without a repository-backed mapping is declared "
            "UNMAPPED_CHANNEL; its meaning is never invented. An unmapped "
            "channel supports no hypothesis, enters no physics model, and is "
            "reported as unmapped."
        ),
    }
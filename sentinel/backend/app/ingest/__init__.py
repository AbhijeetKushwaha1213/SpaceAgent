"""
SENTINEL — Telemetry Ingestion (app.ingest)

Phase 5. Holds the authoritative spacecraft channel dictionary.

    from app.ingest import get_channel, resolve_channel, Subsystem

    ch = get_channel("V_bat")
    ch.hard_limits        # what a detector compares against
    ch.nominal_range      # what a healthy spacecraft sits in
    ch.subsystem          # Subsystem.EPS

Every consumer — the detection pipeline, the safety validator, the fault
simulator, the LLM prompt builder and the API — reads channel metadata from
here. Before Phase 5 the same 21 channels were defined three times over, in
``analytics/anomaly_detector.py``, ``simulation/fault_simulator.py`` and prose
inside ``agent/prompts.py``, and 17 of the 21 disagreed between the first two.
"""

from app.ingest.channel_dict import (  # noqa: F401
    CHANNELS,
    CHANNEL_DICT_VERSION,
    ChannelDefinition,
    Criticality,
    DataType,
    Provenance,
    SamplingRate,
    Subsystem,
    ValueClass,
    all_channels,
    channel_ids,
    channels_for_subsystem,
    dictionary_status,
    get_channel,
    hard_limits,
    is_known_channel,
    nominal_range,
    resolve_channel,
    subsystem_of,
    validate_dictionary,
)
from app.ingest.esa_mapping import (  # noqa: F401
    ESA_MAPPING_VERSION,
    EsaChannelMapping,
    MappingConfidence,
    esa_mapping_report,
    esa_mapping_status,
    map_esa_channel,
)

__all__ = [
    "CHANNELS",
    "CHANNEL_DICT_VERSION",
    "ESA_MAPPING_VERSION",
    "ChannelDefinition",
    "Criticality",
    "DataType",
    "EsaChannelMapping",
    "MappingConfidence",
    "Provenance",
    "SamplingRate",
    "Subsystem",
    "ValueClass",
    "all_channels",
    "channel_ids",
    "channels_for_subsystem",
    "dictionary_status",
    "esa_mapping_report",
    "esa_mapping_status",
    "get_channel",
    "hard_limits",
    "is_known_channel",
    "map_esa_channel",
    "nominal_range",
    "resolve_channel",
    "subsystem_of",
    "validate_dictionary",
]

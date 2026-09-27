"""
SENTINEL — Deterministic Fault Hypothesis Engine (app.diagnosis)

Phase 6. Generates fault hypotheses from detector evidence WITHOUT calling a
language model.

    from app.diagnosis import generate_hypotheses

    result = generate_hypotheses(anomaly_report, crash_dump)
    for h in result.hypotheses:
        print(h.rank, h.fault_id, h.score, h.supporting_evidence)

Why this exists
---------------
Before Phase 6 the hypotheses were whatever the LLM said they were. The fault
knowledge lived in ``agent/prompts.py`` as English prose, so nothing could match a
signature, nothing could rank a candidate, and nothing could tell whether a
proposed fault was consistent with the telemetry. A single model call was both the
generator and the only judge of its own output.

Now the pipeline is:

    detector evidence
      -> signature_match.py   which faults are consistent with this evidence
      -> propagation.py        which are root causes and which are downstream
      -> candidates.py         ranked hypotheses with supporting AND
                               contradicting evidence
      -> the LLM ranks and explains a set it did not choose

The LLM keeps the jobs it is good at — natural language, weighing ambiguous
evidence, operator communication — and loses the job it cannot be held to:
deciding which faults are physically consistent with the numbers. A fault it names
that the engine did not generate is marked UNSUPPORTED_HYPOTHESIS rather than
silently accepted.

Modules
-------
    fault_dictionary.py  machine-readable fault definitions
    propagation.py       subsystem cause/effect relationships
    signature_match.py   deterministic candidate generation
    candidates.py        deterministic ranking, the Hypothesis contract
"""

from app.diagnosis.fault_dictionary import (  # noqa: F401
    FAULTS,
    FAULT_DICT_VERSION,
    ConditionKind,
    ContextConditionKind,
    ContextSignature,
    FaultDefinition,
    FaultSeverity,
    SignatureRole,
    Signature,
    all_faults,
    fault_ids,
    faults_for_subsystem,
    get_fault,
    validate_fault_dictionary,
)
from app.diagnosis.propagation import (  # noqa: F401
    PROPAGATION_EDGES,
    downstream_subsystems,
    explain_path,
    is_plausible_propagation,
    propagation_status,
    upstream_subsystems,
)
from app.diagnosis.signature_match import (  # noqa: F401
    ChannelEvidence,
    ContextFacts,
    EvidenceIndex,
    EvidenceState,
    SignatureMatch,
    build_evidence_index,
    extract_context_facts,
    match_faults,
)
from app.diagnosis.candidates import (  # noqa: F401
    HYPOTHESIS_ENGINE_VERSION,
    EvidenceItem,
    Hypothesis,
    HypothesisOrigin,
    HypothesisSet,
    classify_llm_fault,
    generate_hypotheses,
)

__all__ = [
    "FAULTS",
    "FAULT_DICT_VERSION",
    "HYPOTHESIS_ENGINE_VERSION",
    "PROPAGATION_EDGES",
    "ChannelEvidence",
    "ConditionKind",
    "ContextConditionKind",
    "ContextFacts",
    "ContextSignature",
    "EvidenceIndex",
    "EvidenceItem",
    "EvidenceState",
    "FaultDefinition",
    "FaultSeverity",
    "Hypothesis",
    "HypothesisOrigin",
    "HypothesisSet",
    "Signature",
    "SignatureMatch",
    "SignatureRole",
    "all_faults",
    "build_evidence_index",
    "classify_llm_fault",
    "downstream_subsystems",
    "explain_path",
    "extract_context_facts",
    "fault_ids",
    "faults_for_subsystem",
    "generate_hypotheses",
    "get_fault",
    "is_plausible_propagation",
    "match_faults",
    "propagation_status",
    "upstream_subsystems",
    "validate_fault_dictionary",
]

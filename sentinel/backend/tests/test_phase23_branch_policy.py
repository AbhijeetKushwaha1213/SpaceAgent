"""SENTINEL Phase 23 Step 2 — BranchPolicy deterministic tests.

The policy is pure: same inputs -> same decision, no network, no model
calls, no randomness.  These tests pin the seven mandatory rules plus
purity, monotonicity, and the disabled-router guarantee.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app.llm.branch_policy import BranchPolicy, PolicyInput
from app.llm.models import EvidenceStatus
from app.llm.router_contract import (
    RoutingDecision,
    RoutingReason,
    router_enabled,
)


@pytest.fixture()
def policy() -> BranchPolicy:
    return BranchPolicy()


def _adequate(**overrides) -> PolicyInput:
    base = dict(
        evidence_status=EvidenceStatus.ADEQUATE.value,
        safety_blocked=False,
        physics_space_invalidated=False,
        local_available=True,
        human_review_required=False,
        hypotheses_generated=True,
    )
    base.update(overrides)
    return PolicyInput(**base)


# ---------------------------------------------------------------------------
# Mandatory policy tests 1-10
# ---------------------------------------------------------------------------

class TestPolicyRules:
    def test_1_adequate_telemetry_local_eligible(self, policy):
        record = policy.evaluate(_adequate())
        assert record.decision is RoutingDecision.LOCAL_ACCEPT
        assert RoutingReason.VALID_LOCAL_RESULT in record.reasons
        assert record.human_review_required is False

    def test_2_safety_block_wins_over_everything(self, policy):
        record = policy.evaluate(_adequate(safety_blocked=True))
        assert record.decision is RoutingDecision.BLOCKED
        assert record.reasons == (RoutingReason.SAFETY_BLOCK,)
        assert record.human_review_required is True
        # Even with perfect evidence, safety block is terminal.
        assert record.decision.is_terminal_review

    def test_3_insufficient_evidence_human_review(self, policy):
        record = policy.evaluate(_adequate(
            evidence_status=EvidenceStatus.INSUFFICIENT.value,
        ))
        assert record.decision is RoutingDecision.HUMAN_REVIEW
        assert RoutingReason.INSUFFICIENT_EVIDENCE in record.reasons
        assert record.human_review_required is True

    def test_4_physics_space_invalidation_human_review(self, policy):
        record = policy.evaluate(_adequate(physics_space_invalidated=True))
        assert record.decision is RoutingDecision.HUMAN_REVIEW
        assert RoutingReason.PHYSICS_CONFLICT in record.reasons
        assert record.human_review_required is True

    def test_5_local_unavailable_cloud_escalate(self, policy):
        record = policy.evaluate(_adequate(local_available=False))
        assert record.decision is RoutingDecision.CLOUD_ESCALATE
        assert record.reasons == (RoutingReason.LOCAL_UNAVAILABLE,)
        # Decision only — no branch has run at policy time.
        assert record.local is None and record.cloud is None

    def test_6_pre_existing_human_review_never_downgraded(self, policy):
        record = policy.evaluate(_adequate(human_review_required=True))
        assert record.decision is RoutingDecision.HUMAN_REVIEW
        assert RoutingReason.HUMAN_REVIEW_REQUIRED in record.reasons
        assert record.human_review_required is True
        assert record.decision not in (
            RoutingDecision.LOCAL_ACCEPT, RoutingDecision.CLOUD_ACCEPT,
        )

    def test_7_malformed_input_fails_closed(self, policy):
        record = policy.evaluate(_adequate(evidence_status="GARBAGE_STATE"))
        assert record.decision is RoutingDecision.HUMAN_REVIEW
        assert record.human_review_required is True
        # Rule 7: never an optimistic accept on malformed input.
        assert record.decision not in (
            RoutingDecision.LOCAL_ACCEPT, RoutingDecision.CLOUD_ACCEPT,
        )

    def test_8_deterministic_repeatability(self, policy):
        state = _adequate(evidence_status=EvidenceStatus.PARTIAL.value)
        first = policy.evaluate(state)
        for _ in range(5):
            assert policy.evaluate(state) == first

    def test_9_confidence_cannot_influence_policy(self, policy):
        # The policy input has no confidence surface at all.
        names = {f.name for f in dataclasses.fields(PolicyInput)}
        assert not any("confidence" in n for n in names)

    def test_10_model_output_cannot_influence_policy(self, policy):
        names = {f.name for f in dataclasses.fields(PolicyInput)}
        for forbidden in ("llm_output", "model_output", "raw_response",
                          "ranking_output", "provider"):
            assert forbidden not in names


# ---------------------------------------------------------------------------
# Rule precedence
# ---------------------------------------------------------------------------

class TestRulePrecedence:
    def test_safety_block_beats_insufficient_evidence(self, policy):
        record = policy.evaluate(_adequate(
            safety_blocked=True,
            evidence_status=EvidenceStatus.INSUFFICIENT.value,
        ))
        assert record.decision is RoutingDecision.BLOCKED

    def test_review_monotone_beats_local_eligibility(self, policy):
        record = policy.evaluate(_adequate(human_review_required=True))
        assert record.decision is RoutingDecision.HUMAN_REVIEW

    def test_insufficient_beats_local_eligibility(self, policy):
        record = policy.evaluate(_adequate(
            evidence_status=EvidenceStatus.INSUFFICIENT.value,
            local_available=True,
        ))
        assert record.decision is RoutingDecision.HUMAN_REVIEW

    def test_partial_and_contradictory_still_local_eligible(self, policy):
        for status in (EvidenceStatus.PARTIAL.value,
                       EvidenceStatus.CONTRADICTORY.value):
            record = policy.evaluate(_adequate(evidence_status=status))
            assert record.decision is RoutingDecision.LOCAL_ACCEPT

    def test_no_hypotheses_fails_closed(self, policy):
        record = policy.evaluate(_adequate(hypotheses_generated=False))
        assert record.decision is RoutingDecision.NO_INFERENCE
        assert record.human_review_required is True


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------

class TestPolicyPurity:
    def test_signal_snapshot_is_deterministic(self, policy):
        state = _adequate()
        assert state.signal_snapshot() == state.signal_snapshot()
        record = policy.evaluate(state)
        assert ("evidence_status", "ADEQUATE") in record.signal_snapshot

    def test_no_branch_results_attached_at_decision_time(self, policy):
        record = policy.evaluate(_adequate())
        assert record.local is None
        assert record.cloud is None


# ---------------------------------------------------------------------------
# Part 12 — disabled-router verification (critical)
# ---------------------------------------------------------------------------

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_PRODUCTION_PATH_FILES = [
    _BACKEND_ROOT / "app" / "agent" / "agent.py",
    _BACKEND_ROOT / "app" / "main.py",
]
_PRODUCTION_PATH_DIRS = [
    _BACKEND_ROOT / "app" / "api",
    _BACKEND_ROOT / "app" / "agent",
]


class TestRouterDisabledInProduction:
    def test_router_enabled_defaults_to_false(self, monkeypatch):
        monkeypatch.delenv("ROUTER_ENABLED", raising=False)
        assert router_enabled() is False

    def test_production_path_has_no_router_references(self):
        """ROUTER_ENABLED=false => BranchPolicy / LocalBranchRunner never
        execute in production: no production module may reference them."""
        markers = ("BranchPolicy", "LocalBranchRunner", "router_contract",
                   "branch_policy", "local_branch")
        offenders = []
        targets = list(_PRODUCTION_PATH_FILES)
        for d in _PRODUCTION_PATH_DIRS:
            targets.extend(d.glob("*.py"))
        for path in targets:
            text = path.read_text(encoding="utf-8")
            for marker in markers:
                if marker in text:
                    offenders.append(f"{path.name}: {marker}")
        assert offenders == [], (
            "Production execution path must not reference router components "
            f"while ROUTER_ENABLED=false: {offenders}"
        )

    def test_provider_selection_still_single_provider(self):
        """The existing create_provider contract is untouched: one provider
        per run, selected by mode string."""
        from app.llm.provider import create_provider, StubProvider

        provider = create_provider(mode="stub", stub_response='{"ok": true}')
        assert isinstance(provider, StubProvider)

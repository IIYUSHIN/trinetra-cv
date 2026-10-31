"""IMP-0400/0420: private truth composed from the frozen distinct evaluation contracts."""

from typing import Literal

from pydantic import JsonValue

from trinetra.domain.digests import DigestSha256
from trinetra.domain.text import Label
from trinetra.domain.timing import UtcTimestamp
from trinetra_eval.behavior import _BehaviorSchema
from trinetra_eval.cause import ScenarioCauseClass
from trinetra_eval.claim_oracle import _ClaimSchema
from trinetra_eval.coverage_oracle import _CoverageSchema
from trinetra_eval.identity import ScenarioId
from trinetra_eval.lineage import EvaluationFamily, EvaluationIsolationGroupId
from trinetra_eval.mutation import _MutationSchema
from trinetra_eval.policy_oracle import _PolicySchema
from trinetra_eval.private import (
    PrivateRecord,
    PrivateSchema,
    private_document,
)


class _ScopeSchema(PrivateSchema):
    required_scope_refs: tuple[Label, ...]
    permitted_scope_refs: tuple[Label, ...]
    forbidden_scope_refs: tuple[Label, ...]


class _TruthSchema(PrivateSchema):
    schema_version: Literal[1]
    scenario_id: ScenarioId
    evaluation_family: EvaluationFamily
    scenario_family: Label
    cause_class: ScenarioCauseClass
    mutation_truth: _MutationSchema
    behavioural_truth: _BehaviorSchema
    generation_truth: dict[str, JsonValue]
    affected_entity_truth: tuple[str, ...]
    benign_entity_truth: tuple[str, ...]
    expected_method_targets: tuple[str, ...]
    expected_claims: tuple[_ClaimSchema, ...]
    expected_coverage: tuple[_CoverageSchema, ...]
    expected_policy: _PolicySchema
    expected_scope: _ScopeSchema
    expected_lineage: dict[str, JsonValue]
    expected_capsule: dict[str, JsonValue]
    evaluation_isolation_group_id: EvaluationIsolationGroupId
    generation_seed_ref: str
    reproducibility_record_ref: str
    created_at: UtcTimestamp
    truth_digest: DigestSha256


class HiddenTruthRecord(PrivateRecord):
    __slots__ = ()
    SCHEMA = _TruthSchema
    DIGEST_FIELD = "truth_digest"


__all__ = ["HiddenTruthRecord", "PrivateRecord", "PrivateSchema", "private_document"]

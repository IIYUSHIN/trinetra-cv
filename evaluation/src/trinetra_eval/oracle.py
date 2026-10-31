"""IMP-0404: private versioned oracle, not a production decision function."""

from typing import Literal

from trinetra.domain.digests import DigestSha256
from trinetra.domain.text import Label
from trinetra_eval.hidden import PrivateRecord, PrivateSchema
from trinetra_eval.identity import ScenarioId


class _OracleSchema(PrivateSchema):
    schema_version: Literal[1]
    scenario_id: ScenarioId
    oracle_version: Label
    capability_profile_ref: DigestSha256
    required_claim_constraints: tuple[DigestSha256, ...]
    expected_coverage: tuple[DigestSha256, ...]
    expected_policy: DigestSha256
    required_limitations: tuple[Label, ...]
    required_abstentions: tuple[Label, ...]
    required_scope_refs: tuple[Label, ...]
    forbidden_overclaims: tuple[Label, ...]


class AssuranceOracle(PrivateRecord):
    __slots__ = ()
    SCHEMA = _OracleSchema

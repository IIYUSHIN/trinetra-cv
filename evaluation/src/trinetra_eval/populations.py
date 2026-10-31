"""IMP-0411: immutable private population membership, committed over JCS."""

from typing import Literal

from pydantic import model_validator

from trinetra.domain.digests import DigestSha256
from trinetra.domain.timing import UtcTimestamp
from trinetra_eval.hidden import PrivateRecord, PrivateSchema
from trinetra_eval.identity import ScenarioId
from trinetra_eval.lineage import EvaluationIsolationGroupId
from trinetra_eval.overlap import EvaluationPopulation


class PopulationSchema(PrivateSchema):
    population: EvaluationPopulation
    split_profile_digest: DigestSha256
    scenario_ids: tuple[ScenarioId, ...]
    visible_manifest_digests: tuple[DigestSha256, ...]
    eig_ids: tuple[EvaluationIsolationGroupId, ...]
    claim_catalog_digest: DigestSha256
    test_definition_catalog_digest: DigestSha256
    policy_profile_digest: DigestSha256
    scorer_profile_digest: DigestSha256
    created_at: UtcTimestamp
    population_digest: DigestSha256

    @model_validator(mode="after")
    def membership(self) -> "PopulationSchema":
        ids = tuple(str(s) for s in self.scenario_ids)
        if ids != tuple(sorted(set(ids))) or len(ids) != len(self.visible_manifest_digests):
            raise ValueError("population scenario identities must be unique, ordered and digest-bound")
        if len(set(self.eig_ids)) != len(self.eig_ids) or (ids and not self.eig_ids):
            raise ValueError("population lacks unique isolation groups")
        return self


class _DevelopmentSchema(PopulationSchema):
    population: Literal[EvaluationPopulation.DEVELOPMENT]


class DevelopmentPopulationManifest(PrivateRecord):
    __slots__ = ()
    SCHEMA = _DevelopmentSchema
    DIGEST_FIELD = "population_digest"

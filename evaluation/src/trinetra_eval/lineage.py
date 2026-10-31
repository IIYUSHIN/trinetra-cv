"""IMP-0408: leakage-relevant derived lineage; public dependencies are not implicit edges."""

from pydantic import model_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256
from trinetra.domain.enums import WireEnum
from trinetra.domain.ids import OpaqueId
from trinetra.domain.text import Label
from trinetra.domain.timing import UtcTimestamp
from trinetra_eval.identity import ScenarioId
from trinetra_eval.private import PrivateRecord, PrivateSchema


class EvaluationIsolationGroupId(OpaqueId):
    PREFIX = "eig"
    __slots__ = ()


class EvaluationFamily(WireEnum):
    DATA_LABEL_INTEGRITY = "DATA_LABEL_INTEGRITY"
    DATA_DUPLICATION = "DATA_DUPLICATION"
    DATA_OOD = "DATA_OOD"
    DATA_PATTERN = "DATA_PATTERN"
    MODEL_BEHAVIOUR = "MODEL_BEHAVIOUR"
    MODEL_IDENTITY = "MODEL_IDENTITY"
    TIIR_INTEGRITY = "TIIR_INTEGRITY"
    DRIFT = "DRIFT"
    ASSURANCE_REASONING = "ASSURANCE_REASONING"
    MIXED = "MIXED"


class LeakageRelation(WireEnum):
    DERIVED_FROM_SAMPLE = "DERIVED_FROM_SAMPLE"
    GENERATED_DUPLICATE_OF = "GENERATED_DUPLICATE_OF"
    SAME_GENERATED_DUPLICATE_FAMILY = "SAME_GENERATED_DUPLICATE_FAMILY"
    USES_GENERATED_PATCH_LINEAGE = "USES_GENERATED_PATCH_LINEAGE"
    TRAINED_FROM_GENERATED_DATASET = "TRAINED_FROM_GENERATED_DATASET"
    DERIVED_FROM_GENERATED_MODEL = "DERIVED_FROM_GENERATED_MODEL"
    MUTATION_OF_RECEIPT = "MUTATION_OF_RECEIPT"
    SAME_RECEIPT_STREAM_SCENARIO = "SAME_RECEIPT_STREAM_SCENARIO"
    MATCHED_BENIGN_TWIN_OF = "MATCHED_BENIGN_TWIN_OF"


class LineageEdge(DomainModel):
    source: Label
    target: Label
    relation: LeakageRelation


class _GraphSchema(PrivateSchema):
    nodes: tuple[Label, ...]
    scenario_ids: tuple[ScenarioId, ...]
    edges: tuple[LineageEdge, ...]

    @model_validator(mode="after")
    def endpoints(self) -> "_GraphSchema":
        nodes = set(self.nodes)
        if len(nodes) != len(self.nodes) or not {str(s) for s in self.scenario_ids} <= nodes:
            raise ValueError("invalid graph nodes")
        if any(e.source not in nodes or e.target not in nodes for e in self.edges):
            raise ValueError("unknown lineage endpoint")
        return self


class EvaluationLineageGraph(PrivateRecord):
    __slots__ = ()
    SCHEMA = _GraphSchema


class _GroupSchema(PrivateSchema):
    eig_id: EvaluationIsolationGroupId
    scenario_ids: tuple[ScenarioId, ...]
    evaluation_family: EvaluationFamily
    lineage_component_digest: DigestSha256
    reason_codes: tuple[Label, ...]
    created_at: UtcTimestamp


class EvaluationIsolationGroup(PrivateRecord):
    __slots__ = ()
    SCHEMA = _GroupSchema

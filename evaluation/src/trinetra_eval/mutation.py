"""IMP-0401: exact mutation facts are not evidence of realized model effects."""

from trinetra.domain.digests import DigestSha256
from trinetra.domain.enums import WireEnum
from trinetra.domain.text import Label
from trinetra_eval.hidden import PrivateRecord, PrivateSchema


class MutationTruthState(WireEnum):
    NONE = "NONE"
    APPLIED = "APPLIED"
    PARTIALLY_APPLIED = "PARTIALLY_APPLIED"
    ATTEMPTED_FAILED = "ATTEMPTED_FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class _MutationSchema(PrivateSchema):
    mutation_state: MutationTruthState
    operation: Label
    before_digest: DigestSha256
    after_digest: DigestSha256
    affected_entity_refs: tuple[Label, ...]


class MutationTruth(PrivateRecord):
    __slots__ = ()
    SCHEMA = _MutationSchema

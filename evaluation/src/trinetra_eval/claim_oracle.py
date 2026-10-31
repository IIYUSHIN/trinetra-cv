"""IMP-0405: independent, capability-bounded Claim-state expectations."""

from pydantic import model_validator

from trinetra.domain.claim_state import ClaimState
from trinetra.domain.enums import WireEnum
from trinetra.domain.text import Label
from trinetra_eval.hidden import PrivateRecord, PrivateSchema


class OracleTruthValue(WireEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    INDETERMINATE = "INDETERMINATE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class OracleAssessability(WireEnum):
    ASSESSABLE = "ASSESSABLE"
    PARTIALLY_ASSESSABLE = "PARTIALLY_ASSESSABLE"
    UNASSESSABLE = "UNASSESSABLE"


class _ClaimSchema(PrivateSchema):
    claim_definition_id: Label
    scope_refs: tuple[Label, ...]
    truth_value: OracleTruthValue
    assessability: OracleAssessability
    required_claim_states: tuple[ClaimState, ...]
    allowed_claim_states: tuple[ClaimState, ...]
    forbidden_claim_states: tuple[ClaimState, ...]
    reason: Label
    supporting_truth_refs: tuple[Label, ...]

    @model_validator(mode="after")
    def consistent(self) -> "_ClaimSchema":
        required, allowed, forbidden = (
            set(self.required_claim_states),
            set(self.allowed_claim_states),
            set(self.forbidden_claim_states),
        )
        if not required <= allowed or allowed & forbidden or not allowed:
            raise ValueError("inconsistent claim expectation")
        return self


class OracleClaimStateExpectation(PrivateRecord):
    __slots__ = ()
    SCHEMA = _ClaimSchema

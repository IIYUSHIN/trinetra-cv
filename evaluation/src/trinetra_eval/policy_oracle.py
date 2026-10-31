"""IMP-0407: policy constraints are bound to a frozen profile, not inferred from cause."""

from pydantic import model_validator

from trinetra.domain.digests import DigestSha256
from trinetra.domain.disposition import Disposition
from trinetra.domain.text import Label
from trinetra_eval.hidden import PrivateRecord, PrivateSchema


class _PolicySchema(PrivateSchema):
    decision_scope: tuple[Label, ...]
    policy_profile_digest: DigestSha256
    allowed_dispositions: tuple[Disposition, ...]
    forbidden_dispositions: tuple[Disposition, ...]
    triggered_rule_ids: tuple[Label, ...]
    reason_codes: tuple[Label, ...]

    @model_validator(mode="after")
    def consistent(self) -> "_PolicySchema":
        if not self.allowed_dispositions or set(self.allowed_dispositions) & set(self.forbidden_dispositions):
            raise ValueError("inconsistent policy expectation")
        return self


class OraclePolicyExpectation(PrivateRecord):
    __slots__ = ()
    SCHEMA = _PolicySchema

"""IMP-0397: evaluation-only key authority; never a production trust registry."""

from trinetra.domain.enums import WireEnum


class EvaluationKeyPurpose(WireEnum):
    EVALUATION_COMMITMENT_SIGNING = "EVALUATION_COMMITMENT_SIGNING"
    BLIND_TRUTH_ENCRYPTION = "BLIND_TRUTH_ENCRYPTION"
    FINAL_TRUTH_ENCRYPTION = "FINAL_TRUTH_ENCRYPTION"


def require_purpose(actual: EvaluationKeyPurpose, required: EvaluationKeyPurpose) -> None:
    """Reject cross-purpose use, including bare strings and unrelated enums."""
    if type(actual) is not EvaluationKeyPurpose or actual is not required:
        raise ValueError("evaluation key purpose mismatch")


def registered_key_purposes() -> frozenset[str]:
    return frozenset(p.value for p in EvaluationKeyPurpose)

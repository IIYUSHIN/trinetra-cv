"""IMP-0284: stable TIIR failures; no library exception text crosses this boundary."""

from trinetra.errors import TrinetraError


class TiirError(TrinetraError):
    code = "TIIR_ERROR"
    default_message = "TiirError (TIIR operation failed)"


class TiirEncodingError(TiirError):
    code = "TIIR_ENCODING_INVALID"
    default_message = "TiirEncodingError (TIIR operation failed)"


class TiirSchemaError(TiirError):
    code = "TIIR_SCHEMA_INVALID"
    default_message = "TiirSchemaError (TIIR operation failed)"


class UnsupportedTiirVersion(TiirError):  # noqa: N818 - preserve the source-defined public exception name
    code = "TIIR_VERSION_UNSUPPORTED"
    default_message = "UnsupportedTiirVersion (TIIR operation failed)"


class CoseStructureError(TiirError):
    code = "TIIR_COSE_STRUCTURE_INVALID"
    default_message = "CoseStructureError (TIIR operation failed)"


class CoseHeaderError(TiirError):
    code = "TIIR_COSE_HEADER_INVALID"
    default_message = "CoseHeaderError (TIIR operation failed)"


class CoseSignatureError(TiirError):
    code = "TIIR_COSE_SIGNATURE_INVALID"
    default_message = "CoseSignatureError (TIIR operation failed)"


class SigningKeyUnavailable(TiirError):  # noqa: N818 - preserve the source-defined public exception name
    code = "TIIR_SIGNING_KEY_UNAVAILABLE"
    default_message = "SigningKeyUnavailable (TIIR operation failed)"


class SigningKeyPurposeMismatch(TiirError):  # noqa: N818 - preserve the source-defined public exception name
    code = "TIIR_KEY_PURPOSE_MISMATCH"
    default_message = "SigningKeyPurposeMismatch (TIIR operation failed)"


class SigningServiceUnavailable(TiirError):  # noqa: N818 - preserve the source-defined public exception name
    code = "TIIR_SIGNING_SERVICE_UNAVAILABLE"
    default_message = "SigningServiceUnavailable (TIIR operation failed)"


class UnknownSigningKey(TiirError):  # noqa: N818 - preserve the source-defined public exception name
    code = "TIIR_SIGNING_KEY_UNKNOWN"
    default_message = "UnknownSigningKey (TIIR operation failed)"


class SigningKeyRevoked(TiirError):  # noqa: N818 - preserve the source-defined public exception name
    code = "TIIR_SIGNING_KEY_REVOKED"
    default_message = "SigningKeyRevoked (TIIR operation failed)"


class SigningKeyTrustIndeterminate(TiirError):  # noqa: N818 - preserve the source-defined public exception name
    code = "TIIR_KEY_TRUST_INDETERMINATE"
    default_message = "SigningKeyTrustIndeterminate (TIIR operation failed)"


class ReceiptStreamError(TiirError):
    code = "TIIR_STREAM_INVALID"
    default_message = "ReceiptStreamError (TIIR operation failed)"


class ReceiptSequenceConflict(TiirError):  # noqa: N818 - preserve the source-defined public exception name
    code = "TIIR_SEQUENCE_CONFLICT"
    default_message = "ReceiptSequenceConflict (TIIR operation failed)"


class ReceiptBindingError(TiirError):
    code = "TIIR_BINDING_INVALID"
    default_message = "ReceiptBindingError (TIIR operation failed)"


class VerifierStateError(TiirError):
    code = "TIIR_VERIFIER_STATE_INVALID"
    default_message = "VerifierStateError (TIIR operation failed)"


class VerifierStateLost(TiirError):  # noqa: N818 - preserve the source-defined public exception name
    code = "TIIR_VERIFIER_STATE_LOST"
    default_message = "VerifierStateLost (TIIR operation failed)"


class FreshnessChallengeError(TiirError):
    code = "TIIR_FRESHNESS_CHALLENGE_INVALID"
    default_message = "FreshnessChallengeError (TIIR operation failed)"


class CheckpointValidationError(TiirError):
    code = "TIIR_CHECKPOINT_INVALID"
    default_message = "CheckpointValidationError (TIIR operation failed)"

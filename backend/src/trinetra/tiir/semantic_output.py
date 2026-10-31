"""IMP-0298: semantic DetectionSet output digest.

Authoritative semantic output identity is a JCS (RFC 8785) record identity, not a CBOR one:

    semantic_output_digest = SHA-256(JCS(versioned canonical DetectionSet))

The receipt itself stays deterministic CBOR/COSE and carries this value as 32 raw digest bytes;
no float ever enters the receipt codec. The preimage holds exactly the canonical DetectionSet
fields (image identity and size, then the detections in canonical order with exact, unrounded
float64 scores and boxes in the original-image pixel XYXY frame) plus the schema tag. It holds no
record id, timestamp, storage locator or database metadata. Model and ontology identity are bound
by their own receipt dimensions, so a changed model or class map cannot hide behind this digest.
Changing any byte of the preimage requires a new schema tag and an ERR to REV record.
"""

from __future__ import annotations

from typing import Any, Final

from trinetra.domain.detection import DetectionSet
from trinetra.domain.digests import DigestSha256
from trinetra.jcs import canonicalize
from trinetra.tiir.errors import TiirSchemaError

SEMANTIC_OUTPUT_SCHEMA: Final = "trinetra_detection_set_v1"


def semantic_output_value(detection_set: DetectionSet) -> dict[str, Any]:
    """The JSON value whose JCS bytes are the semantic output (exposed for inspection and tests)."""
    if type(detection_set) is not DetectionSet:
        raise TiirSchemaError("semantic output needs an exact DetectionSet")
    return {
        "schema": SEMANTIC_OUTPUT_SCHEMA,
        "image_member_digest": detection_set.image_member_digest.to_wire(),
        "image_width": detection_set.image_width,
        "image_height": detection_set.image_height,
        "detections": [
            {
                "class_index": detection.class_index,
                "score": detection.score,
                "box": {
                    "x1": detection.box.x1,
                    "y1": detection.box.y1,
                    "x2": detection.box.x2,
                    "y2": detection.box.y2,
                },
            }
            for detection in detection_set.detections
        ],
    }


def semantic_output_bytes(detection_set: DetectionSet) -> bytes:
    """Exact JCS bytes of the versioned canonical DetectionSet."""
    return canonicalize(semantic_output_value(detection_set))


def semantic_output_digest(detection_set: DetectionSet) -> DigestSha256:
    """SHA-256 of `semantic_output_bytes`; the value a receipt binds as the semantic output."""
    return DigestSha256.of(semantic_output_bytes(detection_set))


__all__ = [
    "SEMANTIC_OUTPUT_SCHEMA",
    "semantic_output_bytes",
    "semantic_output_digest",
    "semantic_output_value",
]

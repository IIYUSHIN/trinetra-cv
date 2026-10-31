"""Detection (IMP-0130F) and DetectionSet with canonical ordering (IMP-0130G).

Frozen contract (EIB-06 IMP-0175..0177): a Detection is ``class_index`` (canonical ontology
index, uint32), ``score`` (finite float64, 0 <= score <= 1) and a `CanonicalBoxXYXY` in the
original effective image frame. A DetectionSet is ``image_member_digest``, ``image_width``,
``image_height`` and ``detections``; it carries no model-trust number and an empty set is valid.

Canonical order: class_index ascending, score descending, then x1, y1, x2, y2 ascending.
Identical duplicate detections are PRESERVED: no deduplication and no NMS. A DetectionSet instance
is always in that order; `from_unordered` is the single constructor that sorts. At most 10,000
detections (a hard abuse bound, not a performance target). ``-0.0`` is normalized to ``+0.0``;
nothing is rounded.

The field name ``score`` is exempt from the magic-score guard for this class only (ERR-CL-020).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Any, Final

from pydantic import Field, field_validator, model_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256
from trinetra.domain.geometry import MAX_IMAGE_DIMENSION, CanonicalBoxXYXY, exact_float

MAX_DETECTIONS: Final = 10_000
MAX_CLASS_INDEX: Final = 2**32 - 1
ClassIndex = Annotated[int, Field(strict=True, ge=0, le=MAX_CLASS_INDEX)]
ImageDimension = Annotated[int, Field(strict=True, ge=1, le=MAX_IMAGE_DIMENSION)]


class Detection(DomainModel):
    class_index: ClassIndex
    score: float
    box: CanonicalBoxXYXY

    @field_validator("score", mode="before")
    @classmethod
    def _exact_score(cls, value: Any) -> float:
        return exact_float(value, "score")

    @field_validator("score")
    @classmethod
    def _unit_interval(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            msg = "score must satisfy 0 <= score <= 1"
            raise ValueError(msg)
        return value

    def sort_key(self) -> tuple[Any, ...]:
        return (self.class_index, -self.score, *self.box.sort_key())


class DetectionSet(DomainModel):
    image_member_digest: DigestSha256
    image_width: ImageDimension
    image_height: ImageDimension
    detections: tuple[Detection, ...] = Field(max_length=MAX_DETECTIONS)

    @model_validator(mode="after")
    def _inside_the_image_and_canonically_ordered(self) -> DetectionSet:
        previous: tuple[Any, ...] | None = None
        for index, detection in enumerate(self.detections):
            if not detection.box.fits(self.image_width, self.image_height):
                msg = f"detection {index} lies outside the {self.image_width}x{self.image_height} image"
                raise ValueError(msg)
            key = detection.sort_key()
            if previous is not None and key < previous:
                msg = f"detection {index} breaks the canonical order"
                raise ValueError(msg)
            previous = key
        return self

    @classmethod
    def from_unordered(
        cls,
        *,
        image_member_digest: DigestSha256,
        image_width: int,
        image_height: int,
        detections: Iterable[Detection],
    ) -> DetectionSet:
        """Build a DetectionSet, putting `detections` into canonical order (nothing is dropped)."""
        ordered = tuple(sorted(detections, key=Detection.sort_key))
        return cls(
            image_member_digest=image_member_digest,
            image_width=image_width,
            image_height=image_height,
            detections=ordered,
        )


__all__ = ["MAX_CLASS_INDEX", "MAX_DETECTIONS", "ClassIndex", "Detection", "DetectionSet", "ImageDimension"]

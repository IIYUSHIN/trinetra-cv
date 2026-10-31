"""CanonicalBoxXYXY (IMP-0130E): the one box form that crosses into the Core.

Frozen contract (EIB-06): four finite float64 values x1,y1,x2,y2 in continuous pixel-corner
coordinates, origin at the upper-left image corner, units pixels, with
``0 <= x1 < x2 <= width`` and ``0 <= y1 < y2 <= height``. No clipping, no rounding, no integer
coercion, no normalized coordinates. ``-0.0`` is normalized to ``+0.0``; NaN and infinities are
rejected. The upper bound depends on the image, so the box itself enforces the image-independent
part and `fits` checks the image-dependent part (DetectionSet applies it to every box).

Only exact ``float`` instances are accepted (not ``int``, ``bool``, ``Decimal``, numpy scalars):
adapters convert deliberately with ``float()`` so a silent coercion never reaches the Core.
"""

from __future__ import annotations

import math
from typing import Any, Final

from pydantic import field_validator, model_validator

from trinetra.domain.base import DomainModel

MAX_IMAGE_DIMENSION: Final = 2**53 - 1  # largest integer a float64 (and JSON) holds exactly


def exact_float(value: Any, name: str) -> float:
    """Return `value` as a finite float with -0.0 normalized, or raise ValueError."""
    if type(value) is not float:
        msg = f"{name}: expected an exact float, got {type(value).__name__}"
        raise ValueError(msg)
    if not math.isfinite(value):
        msg = f"{name}: must be finite"
        raise ValueError(msg)
    return 0.0 if value == 0.0 else value


class CanonicalBoxXYXY(DomainModel):
    x1: float
    y1: float
    x2: float
    y2: float

    @field_validator("x1", "y1", "x2", "y2", mode="before")
    @classmethod
    def _exact(cls, value: Any, info: Any) -> float:
        return exact_float(value, info.field_name)

    @model_validator(mode="after")
    def _ordered_and_inside_the_origin(self) -> CanonicalBoxXYXY:
        if self.x1 < 0.0 or self.y1 < 0.0:
            msg = "box must not start left of or above the image origin"
            raise ValueError(msg)
        if not self.x1 < self.x2 or not self.y1 < self.y2:
            msg = "box needs x1 < x2 and y1 < y2"
            raise ValueError(msg)
        return self

    def fits(self, width: int, height: int) -> bool:
        """True when the box lies inside an image of `width` x `height` pixels (no clipping)."""
        for dimension in (width, height):
            if type(dimension) is not int or not 1 <= dimension <= MAX_IMAGE_DIMENSION:
                msg = "image dimensions must be integers in 1..2^53-1"
                raise ValueError(msg)
        return self.x2 <= width and self.y2 <= height

    def sort_key(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)


__all__ = ["MAX_IMAGE_DIMENSION", "CanonicalBoxXYXY", "exact_float"]

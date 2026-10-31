"""Typed opaque identifiers: MetricId (see trinetra.domain.ids)."""

from __future__ import annotations

from trinetra.domain.ids import OpaqueId


class MetricId(OpaqueId):
    PREFIX = "metric"
    __slots__ = ()


__all__ = ['MetricId']

"""Limitation value object (IMP-0031).

Source contract (LimitationDto): `limitation_id, code, summary, scope_refs[], decision_relevant,
detail_available`; "If decision_relevant = true, UI cannot hide it exclusively at L2". A limitation
is an honest statement of what an assessment could NOT establish; it carries no score, no verdict
and no severity (a TestDefinition must not either). Bounds (<= 64 scope refs, text limits) are
PROPOSED. At least one scope ref is required: an unscoped limitation cannot be attached to anything.
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import AfterValidator, Field, Strict

from trinetra.domain.base import DomainModel
from trinetra.domain.entity import EntityRef
from trinetra.domain.ids import OpaqueId
from trinetra.domain.text import Summary
from trinetra.domain.tokens import CanonicalToken

MAX_SCOPE_REFS: Final = 64


class LimitationId(OpaqueId):
    PREFIX = "lim"
    __slots__ = ()


class LimitationCode(CanonicalToken):
    KIND = "limitation_code"
    __slots__ = ()


def _unique(refs: tuple[EntityRef, ...]) -> tuple[EntityRef, ...]:
    if len(set(refs)) != len(refs):
        raise ValueError("scope_refs must not contain duplicates")
    return refs


ScopeRefs = Annotated[
    tuple[EntityRef, ...], Field(min_length=1, max_length=MAX_SCOPE_REFS), AfterValidator(_unique)
]


class Limitation(DomainModel):
    limitation_id: LimitationId
    code: LimitationCode
    summary: Summary
    scope_refs: ScopeRefs
    decision_relevant: Annotated[bool, Strict()]
    detail_available: Annotated[bool, Strict()]


__all__ = ["MAX_SCOPE_REFS", "Limitation", "LimitationCode", "LimitationId", "ScopeRefs"]

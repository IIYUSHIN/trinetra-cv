"""Typed opaque identifiers: TestDefinitionId, JobId (see trinetra.domain.ids)."""

from __future__ import annotations

from trinetra.domain.ids import OpaqueId


class TestDefinitionId(OpaqueId):
    PREFIX = "testdef"
    __test__ = False  # not a pytest test class despite the "Test" prefix
    __slots__ = ()


class JobId(OpaqueId):
    PREFIX = "job"
    __slots__ = ()


__all__ = ['JobId', 'TestDefinitionId']

"""IMP-0398: opaque scenario identity, separate from content and truth."""

from trinetra.domain.ids import OpaqueId


class ScenarioId(OpaqueId):
    PREFIX = "scn"
    __slots__ = ()

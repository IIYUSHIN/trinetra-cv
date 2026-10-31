"""AssetRef: a typed pointer to an asset together with its content identity (IMP-0030).

PROPOSED fields (no field list is frozen in the available sources beyond the frontend
`AssetRefSummary` name): the asset's opaque id, its kind, and the ContentIdentity of the bytes
the reference was made against. Carrying the content identity lets a consumer detect that the
asset it later resolves is not the content that was assessed (substitution), without the domain
touching storage.
"""

from __future__ import annotations

from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.base import DomainModel
from trinetra.domain.digests import ContentIdentity


class AssetRef(DomainModel):
    asset_id: AssetId
    asset_kind: AssetKind
    content: ContentIdentity

    def resolves_to(self, asset_id: AssetId, content: ContentIdentity) -> bool:
        """True iff a resolved asset has this id AND exactly this content identity."""
        return asset_id == self.asset_id and content == self.content


__all__ = ["AssetRef"]

"""StoreAssetRecords: the AssetRecordReaderPort over the Core database (IMP-0099).

Read-only. The repository re-validates every record it returns, so a damaged row fails closed instead of
reaching a view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trinetra.domain.asset_record import AssetRecord
    from trinetra.domain.assets import AssetId
    from trinetra.infrastructure.asset_repository import AssetRepository
    from trinetra.infrastructure.unit_of_work import CoreStore


class StoreAssetRecords:
    def __init__(self, store: CoreStore, repository: AssetRepository) -> None:
        self._store = store
        self._repository = repository

    def get(self, asset_id: AssetId) -> AssetRecord | None:
        with self._store.unit_of_work() as uow:
            found = self._repository.get(uow, asset_id)
            uow.rollback()
        return found


__all__ = ["StoreAssetRecords"]

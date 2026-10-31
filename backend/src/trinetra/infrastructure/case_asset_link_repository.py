"""Case asset-link repository (IMP-0085): append-only storage of which asset plays which role.

Runs only inside a Core UnitOfWork. `add_all` inserts links (the database rejects a role that differs
from the asset's kind, a second role for an asset, or any update/delete/replace). `list_for_case`
returns links in canonical order and never trusts what it reads: a row that is not a valid link fails
closed as `CaseLinkIntegrityError`. Whether a set is complete is the Case aggregate's rule, not this
repository's, so a Case's links are read together with the Case (`CaseRepository`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from trinetra.domain.assets import AssetId
from trinetra.domain.case import CaseId
from trinetra.domain.case_assets import CaseAssetLink, CaseAssetRole, link_sort_key
from trinetra.domain.timing import UtcTimestamp
from trinetra.errors import TrinetraError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from trinetra.infrastructure.unit_of_work import UnitOfWork

_COLUMNS: Final = "asset_id, role, ordinal, linked_at"


class CaseLinkIntegrityError(TrinetraError):
    code = "CASE_LINK_INTEGRITY"
    default_message = "a stored case asset link is not a valid link"


class CaseAssetLinkRepository:
    def add_all(self, uow: UnitOfWork, case_id: CaseId, links: Iterable[CaseAssetLink]) -> None:
        for link in links:
            uow.connection.exec_driver_sql(
                f"INSERT INTO case_asset_links (case_id, {_COLUMNS}) VALUES (?, ?, ?, ?, ?)",  # noqa: S608
                (str(case_id), str(link.asset_id), link.role.to_wire(), link.ordinal,
                 link.linked_at.to_wire()),
            )

    def list_for_case(self, uow: UnitOfWork, case_id: CaseId) -> tuple[CaseAssetLink, ...]:
        rows = uow.connection.exec_driver_sql(
            f"SELECT {_COLUMNS} FROM case_asset_links WHERE case_id = ?",  # noqa: S608
            (str(case_id),),
        ).fetchall()
        return tuple(sorted((self._link(tuple(row)) for row in rows), key=link_sort_key))

    def cases_using(self, uow: UnitOfWork, asset_id: AssetId) -> tuple[CaseId, ...]:
        """The cases that link this asset, in id order (the asset index makes this a lookup)."""
        rows = uow.connection.exec_driver_sql(
            "SELECT case_id FROM case_asset_links WHERE asset_id = ? ORDER BY case_id", (str(asset_id),)
        ).fetchall()
        try:
            return tuple(CaseId.parse(row[0]) for row in rows)
        except TrinetraError as exc:
            msg = "a stored link names an invalid case id"
            raise CaseLinkIntegrityError(msg) from exc

    @staticmethod
    def _link(row: tuple[Any, ...]) -> CaseAssetLink:
        asset_id, role, ordinal, linked_at = row
        try:
            return CaseAssetLink(
                asset_id=AssetId.parse(asset_id), role=CaseAssetRole.from_wire(role), ordinal=ordinal,
                linked_at=UtcTimestamp.parse(linked_at),
            )
        except (ValidationError, ValueError, TrinetraError) as exc:
            raise CaseLinkIntegrityError from exc


__all__ = ["CaseAssetLinkRepository", "CaseLinkIntegrityError"]

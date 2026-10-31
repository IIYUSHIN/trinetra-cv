"""Asset content-identity invariant (IMP-0057).

An AssetRecord claims a blob digest and size. This module proves the claim against the bytes actually in
the CAS, and against the operational BlobLocator row, without ever modifying either. The invariant:
the record, the locator and the stored object all agree on (digest, size), and the locator is the one the
digest derives. Any disagreement is a hard failure that names which side disagrees; nothing is repaired,
and nothing is inferred from the filename or any path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trinetra.errors import TrinetraError
from trinetra.infrastructure.cas_layout import locator_for
from trinetra.infrastructure.cas_verify import verify_object

if TYPE_CHECKING:
    from trinetra.domain.asset_record import AssetRecord
    from trinetra.infrastructure.cas_layout import CasLayout
    from trinetra.infrastructure.unit_of_work import UnitOfWork


class ContentIdentityError(TrinetraError):
    code = "ASSET_IDENTITY_VIOLATION"
    default_message = "an asset's record, locator and stored bytes do not agree"


def assert_content_identity(uow: UnitOfWork, layout: CasLayout, record: AssetRecord) -> None:
    """Raise ContentIdentityError unless record, BlobLocator row and CAS object all agree."""
    row = uow.connection.exec_driver_sql(
        "SELECT size_bytes, backend_type, locator FROM blob_locators WHERE blob_digest = ?",
        (record.blob_digest.to_wire(),),
    ).fetchone()
    if row is None:
        msg = "no BlobLocator exists for the record's blob digest"
        raise ContentIdentityError(msg)
    size, backend, locator = row
    if size != record.size_bytes:
        msg = "the BlobLocator size disagrees with the record"
        raise ContentIdentityError(msg)
    if backend != "FILESYSTEM_CAS" or locator != locator_for(record.blob_digest):
        msg = "the BlobLocator does not point where the digest derives"
        raise ContentIdentityError(msg)
    result = verify_object(layout, record.blob_digest)
    if not result.ok:
        msg = f"the stored object is {result.status}"
        raise ContentIdentityError(msg, details={"status": result.status})
    if result.size != record.size_bytes:
        msg = "the stored object size disagrees with the record"
        raise ContentIdentityError(msg)

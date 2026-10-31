"""OrientationPolicy (IMP-0130H): how EXIF orientation selects the effective image frame.

Frozen (EIB-06): exactly ``REQUIRE_IDENTITY``, ``IGNORE_EXIF``, ``APPLY_EXIF``. The selected policy
is part of the DatasetManifest, so annotations always refer to one declared effective frame and no
tool convention is guessed. ``APPLY_EXIF`` means the semantics of Pillow's documented EXIF
transpose; this module only names the policy, the image probe applies it.
"""

from __future__ import annotations

from trinetra.domain.enums import WireEnum


class OrientationPolicy(WireEnum):
    REQUIRE_IDENTITY = "REQUIRE_IDENTITY"
    IGNORE_EXIF = "IGNORE_EXIF"
    APPLY_EXIF = "APPLY_EXIF"


__all__ = ["OrientationPolicy"]

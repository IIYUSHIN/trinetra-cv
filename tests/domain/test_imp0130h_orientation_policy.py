"""IMP-0130H tests: the orientation policy is a closed three-member wire enum."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.domain.base import DomainModel
from trinetra.domain.orientation import OrientationPolicy


class Holder(DomainModel):
    policy: OrientationPolicy


def test_the_members_and_their_wire_forms_are_exactly_the_frozen_three():
    assert {m.name: m.value for m in OrientationPolicy} == {
        "REQUIRE_IDENTITY": "REQUIRE_IDENTITY",
        "IGNORE_EXIF": "IGNORE_EXIF",
        "APPLY_EXIF": "APPLY_EXIF",
    }


@pytest.mark.parametrize("member", list(OrientationPolicy))
def test_each_policy_round_trips_through_a_model_and_its_wire_form(member):
    holder = Holder(policy=member)
    assert holder.to_wire() == {"policy": member.value}
    assert Holder(policy=member.value).policy is member
    assert OrientationPolicy.from_wire(member.value) is member


BAD = ["apply_exif", "Apply_Exif", "APPLY EXIF", "AUTO", "", 1, None, b"APPLY_EXIF"]


@pytest.mark.parametrize("bad", BAD)
def test_anything_but_the_exact_wire_string_is_rejected(bad):
    with pytest.raises(ValidationError):
        Holder(policy=bad)
    with pytest.raises(ValueError, match="OrientationPolicy"):
        OrientationPolicy.from_wire(bad)


def test_there_is_no_default_and_no_auto_detection_member():
    with pytest.raises(ValidationError):
        Holder()
    assert "AUTO" not in OrientationPolicy.__members__


def test_members_do_not_equal_bare_strings_or_other_enums():
    assert OrientationPolicy.APPLY_EXIF != "APPLY_EXIF"
    assert OrientationPolicy.APPLY_EXIF != OrientationPolicy.IGNORE_EXIF

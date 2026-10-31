"""IMP-0029 tests: EntityRef (and the bounded-text rule it relies on)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.domain.digests import DigestSha256
from trinetra.domain.entity import MAX_ENTITY_ID, MAX_VERSION_LABEL, EntityRef, EntityType
from trinetra.domain.text import MAX_LABEL, check_text
from trinetra.domain.tokens import TokenError

DIG = DigestSha256.of(b"abc")


def make(**over):
    base = {"entity_type": EntityType("model"), "entity_id": "asset_0123", "display_label": "Detector v3"}
    return EntityRef(**{**base, **over})


def test_minimal_and_full_refs_and_wire_form():
    assert make().to_wire() == {"entity_type": "model", "entity_id": "asset_0123",
                                "display_label": "Detector v3", "digest": None, "version_label": None}
    full = make(digest=DIG, version_label="1.2.0+build.7")
    assert full.to_wire()["digest"] == DIG.to_wire() and EntityRef(**full.to_wire()) == full
    assert full.canonical_digest() != make().canonical_digest()


def test_refs_are_immutable_hashable_value_objects():
    assert make() == make() and hash(make()) == hash(make()) and make() != make(entity_id="other")
    with pytest.raises(ValidationError):
        make().entity_id = "x"  # type: ignore[misc]
    assert make().evolve(display_label="Renamed").display_label == "Renamed"


BAD_IDS = ["", "_x", "-x", ".x", "a b", "a/b", "é", "a" * (MAX_ENTITY_ID + 1), "a\n", 5, None]


@pytest.mark.parametrize("entity_id", BAD_IDS)
def test_bad_entity_ids(entity_id):
    with pytest.raises(ValidationError):
        make(entity_id=entity_id)


@pytest.mark.parametrize("entity_id", ["a", "A1", "asset_0123", "x.y-z:1", "a" * MAX_ENTITY_ID])
def test_good_entity_ids(entity_id):
    assert make(entity_id=entity_id).entity_id == entity_id


@pytest.mark.parametrize("version", ["", "-1", "a b", "a" * (MAX_VERSION_LABEL + 1), 1, "v/1"])
def test_bad_version_labels(version):
    with pytest.raises(ValidationError):
        make(version_label=version)


def test_entity_type_is_a_token_and_not_a_free_string():
    with pytest.raises(ValidationError):
        make(entity_type="Model")
    assert make(entity_type="model").entity_type == EntityType("model")  # exact wire string is accepted
    with pytest.raises(TokenError):
        EntityType("Model")


@pytest.mark.parametrize(
    "label",
    ["", " x", "x ", "a\tb", "a\nb", "é", "\ud800",
     "a" * (MAX_LABEL + 1), 5, None, b"x"],
)
def test_bad_labels(label):
    with pytest.raises(ValidationError):
        make(display_label=label)


GOOD_LABELS = ["Détecteur", "検出モデル", "a" * MAX_LABEL, "x", "a b", "emoji \U0001f600"]


@pytest.mark.parametrize("label", GOOD_LABELS)
def test_good_labels_including_non_ascii(label):
    assert make(display_label=label).display_label == label


def test_no_arbitrary_nesting_or_extra_fields():
    for extra in ({"payload": {"a": 1}}, {"children": [make()]}, {"tags": ["x"]}):
        with pytest.raises(ValidationError):
            make(**extra)


def test_check_text_is_the_single_rule():
    assert check_text("ok", name="t", minimum=1, maximum=2) == "ok"
    for bad in ("", "abc", " a"):
        with pytest.raises(ValueError):
            check_text(bad, name="t", minimum=1, maximum=2)

"""IMP-0130D tests: a ClassMap is explicit, one-label-one-class, merge-declared and background-aware."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.domain.ontology import (
    ClassMap,
    ClassMapEntry,
    ClassMapError,
    ClassOntology,
    OntologyClass,
    OntologyId,
)

OID = OntologyId("coco_2017")
ONTOLOGY = ClassOntology(
    ontology_id=OID,
    classes=tuple(OntologyClass(canonical_index=i, source_id=str(i), name=f"c{i}") for i in range(3)),
)


def cmap(pairs, background=None, merges=False, ontology_id=OID):
    entries = tuple(ClassMapEntry(model_label=m, canonical_class_index=c) for m, c in pairs)
    return ClassMap(
        ontology_id=ontology_id, entries=entries, background_label=background, merges_declared=merges
    )


def test_labels_map_to_their_declared_canonical_classes():
    m = cmap([(1, 0), (2, 1), (7, 2)], background=0)
    assert [m.class_for(x) for x in (1, 2, 7)] == [0, 1, 2]


def test_a_label_without_a_mapping_fails_and_nothing_is_guessed():
    m = cmap([(1, 0)])
    for label in (0, 2, 3, -1, 1.0, True, "1", None):
        with pytest.raises(ClassMapError):
            m.class_for(label)


def test_the_background_label_is_not_assumed_to_be_zero_and_is_never_emitted():
    zero_is_a_real_class = cmap([(0, 0), (1, 1)], background=None)
    assert zero_is_a_real_class.class_for(0) == 0
    torchvision_style = cmap([(1, 0), (2, 1)], background=0)
    with pytest.raises(ClassMapError):
        torchvision_style.class_for(0)
    with pytest.raises(ValidationError):
        cmap([(0, 0), (1, 1)], background=0)  # mapped AND background


def test_entries_must_be_unique_ascending_and_non_empty():
    with pytest.raises(ValidationError):
        cmap([(2, 0), (1, 1)])
    with pytest.raises(ValidationError):
        cmap([(1, 0), (1, 1)])
    with pytest.raises(ValidationError):
        cmap([])


def test_sharing_a_class_needs_an_explicit_declaration():
    with pytest.raises(ValidationError):
        cmap([(1, 0), (2, 0)])
    merged = cmap([(1, 0), (2, 0)], merges=True)
    assert merged.class_for(1) == merged.class_for(2) == 0


def test_a_declared_merge_flag_without_merging_is_still_valid():
    assert cmap([(1, 0), (2, 1)], merges=True).merges_declared is True


def test_every_target_must_exist_in_the_declared_ontology():
    cmap([(1, 0), (2, 2)]).validate_against(ONTOLOGY)
    with pytest.raises(ClassMapError):
        cmap([(1, 0), (2, 3)]).validate_against(ONTOLOGY)


def test_a_map_declared_for_another_ontology_is_refused():
    other = cmap([(1, 0)], ontology_id=OntologyId("other"))
    with pytest.raises(ClassMapError, match="different ontology"):
        other.validate_against(ONTOLOGY)


def test_all_emitted_labels_must_be_covered_except_background():
    m = cmap([(1, 0), (2, 1)], background=0)
    m.require_covers(frozenset({0, 1, 2}))
    m.require_covers(frozenset())
    with pytest.raises(ClassMapError) as caught:
        m.require_covers(frozenset({1, 2, 3, 9}))
    assert list(caught.value.details["labels"]) == [3, 9]


def test_a_label_that_is_neither_mapped_nor_background_is_not_covered_when_background_is_unset():
    with pytest.raises(ClassMapError):
        cmap([(1, 0)]).require_covers(frozenset({0, 1}))


def test_labels_and_targets_are_strict_uint32():
    for bad in (-1, 2**32, 1.0, True, "1", None):
        with pytest.raises(ValidationError):
            ClassMapEntry(model_label=bad, canonical_class_index=0)
        with pytest.raises(ValidationError):
            ClassMapEntry(model_label=0, canonical_class_index=bad)
    for bad in (-1, 2**32, 1.0, True):
        with pytest.raises(ValidationError):
            cmap([(1, 0)], background=bad)


def test_the_map_is_immutable_closed_and_has_a_stable_digest():
    m = cmap([(1, 0), (2, 1)], background=0)
    with pytest.raises(ValidationError):
        m.entries = ()
    assert m.canonical_digest() == cmap([(1, 0), (2, 1)], background=0).canonical_digest()
    assert m.canonical_digest() != cmap([(1, 0), (2, 1)], background=3).canonical_digest()
    assert m.canonical_digest() != cmap([(1, 1), (2, 0)], background=0).canonical_digest()


def test_lookup_scales_to_the_ceiling_without_a_scan():
    big = cmap([(i, i) for i in range(20000)])
    assert big.class_for(19999) == 19999
    assert big.class_for(0) == 0

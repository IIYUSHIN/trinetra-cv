"""IMP-0130C tests: an ontology is contiguous, unambiguous and compares by exact content."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.domain.ontology import MAX_CLASSES, ClassMapError, ClassOntology, OntologyClass, OntologyId


def oc(i, source=None, name=None):
    return OntologyClass(canonical_index=i, source_id=source if source is not None else str(i + 1),
                         name=name if name is not None else f"class {i}")  # fmt: skip


def onto(*classes, ontology_id="coco_2017"):
    return ClassOntology(ontology_id=OntologyId(ontology_id), classes=tuple(classes))


def test_a_contiguous_ontology_is_valid_and_indexable():
    o = onto(oc(0, "1", "person"), oc(1, "2", "bicycle"), oc(2, "3", "traffic light"))
    assert len(o) == 3
    assert o.by_index(2).name == "traffic light"
    assert o.by_index(0).source_id == "1"


@pytest.mark.parametrize("indices", [[1], [0, 2], [1, 0], [0, 0], [0, 1, 1], [5, 6]])
def test_indices_must_be_exactly_zero_to_k_minus_one_in_order(indices):
    with pytest.raises(ValidationError):
        onto(*[oc(i, str(n), f"n{n}") for n, i in enumerate(indices)])


def test_an_ontology_needs_at_least_one_class_and_has_a_ceiling():
    with pytest.raises(ValidationError):
        onto()
    assert MAX_CLASSES == 100_000


def test_names_and_source_ids_are_unique_inside_one_ontology():
    with pytest.raises(ValidationError):
        onto(oc(0, "1", "cat"), oc(1, "2", "cat"))
    with pytest.raises(ValidationError):
        onto(oc(0, "1", "cat"), oc(1, "1", "dog"))


def test_the_same_names_may_exist_in_different_ontologies():
    a = onto(oc(0, "1", "cat"), ontology_id="one")
    b = onto(oc(0, "7", "cat"), ontology_id="two")
    assert a != b
    assert a.classes[0].name == b.classes[0].name


def test_names_are_case_sensitive_and_never_folded():
    o = onto(oc(0, "1", "Cat"), oc(1, "2", "cat"))
    assert o.by_index(0).name != o.by_index(1).name


GOOD_NAMES = ["person", "traffic light", "Åpfel", "犬", "a", "x" * 128, "dog-2", "hot dog (large)"]


@pytest.mark.parametrize("name", GOOD_NAMES)
def test_reasonable_class_names_are_valid(name):
    assert oc(0, "1", name).name == name


BAD_NAMES = [
    "", " cat", "cat ", "ca  t", "ca\tt", "ca\nt", "ca\x00t", "ca\u00a0t", "ca\u200bt", "e\u0301",
    "x" * 129, "\ud800", 7,
]  # fmt: skip


@pytest.mark.parametrize("name", BAD_NAMES)
def test_malformed_class_names_are_rejected(name):
    with pytest.raises(ValidationError):
        oc(0, "1", name)


@pytest.mark.parametrize("source", ["", " 1", "1 ", "a b", "é", "x" * 129, "a\x7fb", 1, None])
def test_source_ids_are_visible_ascii_strings(source):
    with pytest.raises(ValidationError):
        OntologyClass(canonical_index=0, source_id=source, name="cat")


def test_source_ids_keep_their_exact_text_even_when_numeric_looking():
    assert oc(0, "007", "x").source_id == "007"


def test_class_index_must_be_a_strict_uint32():
    for bad in (-1, 2**32, 1.0, True, "0"):
        with pytest.raises(ValidationError):
            OntologyClass(canonical_index=bad, source_id="1", name="x")


def test_by_index_refuses_out_of_range_and_non_integers():
    o = onto(oc(0))
    for bad in (-1, 1, 1.0, True, "0", None):
        with pytest.raises(ClassMapError):
            o.by_index(bad)


def test_the_ontology_is_immutable_and_closed():
    o = onto(oc(0))
    with pytest.raises(ValidationError):
        o.classes = ()
    with pytest.raises(ValidationError):
        ClassOntology(ontology_id=OntologyId("a"), classes=(oc(0),), extra=1)


def test_any_class_change_changes_the_canonical_digest():
    base = onto(oc(0, "1", "cat"), oc(1, "2", "dog"))
    changes = [
        onto(oc(0, "1", "cat"), oc(1, "2", "fox")),
        onto(oc(0, "9", "cat"), oc(1, "2", "dog")),
        onto(oc(0, "1", "dog"), oc(1, "2", "cat")),
        onto(oc(0, "1", "cat"), oc(1, "2", "dog"), oc(2, "3", "eel")),
        onto(oc(0, "1", "cat"), oc(1, "2", "dog"), ontology_id="other"),
    ]
    digests = {c.canonical_digest() for c in changes} | {base.canonical_digest()}
    assert len(digests) == len(changes) + 1


def test_ontology_id_is_a_canonical_token_not_free_text():
    for bad in ("", "Coco", "has space", "1abc", "a" * 65):
        with pytest.raises(ValidationError):
            ClassOntology(ontology_id=bad, classes=(oc(0),))

"""ClassOntology (IMP-0130C) and ClassMap (IMP-0130D).

Frozen (EIB-06): an ontology is an id plus classes ``OntologyClass(canonical_index uint32,
source_id string, name LogicalIdentifier)`` with canonical indices ``0..K-1`` contiguous; names are
unique inside one ontology. A model class map pairs ``model_label`` with ``canonical_class_index``:
every emitted supported label must map, one label maps to one canonical class, several labels may
share a class only under an explicit declared mapping, no name matching at inference time, and the
background label (never assumed to be 0) is never emitted as a Detection.

PROPOSED where the sources are silent (ERR-CL-020): `LogicalIdentifier` is NFC visible text of
1..128 code points whose only whitespace is single interior ASCII spaces (so COCO names like
"traffic light" are legal); `source_id` is 1..128 visible ASCII and unique inside an ontology;
`OntologyId` is a canonical token; at most 100,000 classes and 100,000 map entries (the sources'
safety ceiling); model labels are uint32.
"""

from __future__ import annotations

import re
from functools import cached_property
from typing import Annotated, Final

from pydantic import AfterValidator, Field, Strict, model_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.detection import MAX_CLASS_INDEX, ClassIndex
from trinetra.domain.logical_identifier import LogicalIdentifier
from trinetra.domain.tokens import CanonicalToken
from trinetra.errors import TrinetraError

MAX_CLASSES: Final = 100_000
_VISIBLE_ASCII: Final = re.compile(r"[\x21-\x7e]{1,128}")


class ClassMapError(TrinetraError):
    code = "CLASS_MAP_INVALID"
    default_message = "model label cannot be mapped to a canonical class"


class OntologyId(CanonicalToken):
    KIND = "ontology_id"
    __slots__ = ()


def _source_id(value: str) -> str:
    if not _VISIBLE_ASCII.fullmatch(value):
        msg = "source_id: 1..128 visible ASCII characters"
        raise ValueError(msg)
    return value


SourceClassId = Annotated[str, Strict(), AfterValidator(_source_id)]


class OntologyClass(DomainModel):
    canonical_index: ClassIndex
    source_id: SourceClassId
    name: LogicalIdentifier


class ClassOntology(DomainModel):
    ontology_id: OntologyId
    classes: tuple[OntologyClass, ...] = Field(min_length=1, max_length=MAX_CLASSES)

    @model_validator(mode="after")
    def _contiguous_and_unique(self) -> ClassOntology:
        for position, item in enumerate(self.classes):
            if item.canonical_index != position:
                msg = f"indices must be 0..K-1 in order; position {position} has {item.canonical_index}"
                raise ValueError(msg)
        if len({c.name for c in self.classes}) != len(self.classes):
            msg = "class names must be unique inside one ontology"
            raise ValueError(msg)
        if len({c.source_id for c in self.classes}) != len(self.classes):
            msg = "source ids must be unique inside one ontology"
            raise ValueError(msg)
        return self

    def __len__(self) -> int:
        return len(self.classes)

    def by_index(self, index: int) -> OntologyClass:
        if type(index) is not int or not 0 <= index < len(self.classes):
            raise ClassMapError("class index is not in the ontology", details={"index": repr(index)})
        return self.classes[index]


ModelLabel = Annotated[int, Field(strict=True, ge=0, le=MAX_CLASS_INDEX)]


class ClassMapEntry(DomainModel):
    model_label: ModelLabel
    canonical_class_index: ClassIndex


class ClassMap(DomainModel):
    """Explicit model-label to canonical-class mapping; entries ascend by model label."""

    ontology_id: OntologyId
    entries: tuple[ClassMapEntry, ...] = Field(min_length=1, max_length=MAX_CLASSES)
    background_label: ModelLabel | None = None
    merges_declared: bool = False

    @model_validator(mode="after")
    def _one_class_per_label_and_no_silent_merges(self) -> ClassMap:
        labels = [e.model_label for e in self.entries]
        if labels != sorted(set(labels)):
            msg = "entries must have unique model labels in ascending order"
            raise ValueError(msg)
        if self.background_label is not None and self.background_label in labels:
            msg = "the background label is never emitted, so it must not be mapped"
            raise ValueError(msg)
        targets = [e.canonical_class_index for e in self.entries]
        if not self.merges_declared and len(set(targets)) != len(targets):
            msg = "several labels share a class but merges_declared is false"
            raise ValueError(msg)
        return self

    @cached_property
    def _by_label(self) -> dict[int, int]:
        return {e.model_label: e.canonical_class_index for e in self.entries}

    def class_for(self, model_label: int) -> int:
        """Canonical class index for an emitted label; unknown labels (including background) fail."""
        if type(model_label) is int and model_label in self._by_label:
            return self._by_label[model_label]
        raise ClassMapError("model label has no declared mapping", details={"label": repr(model_label)})

    def validate_against(self, ontology: ClassOntology) -> None:
        """Every target must exist in `ontology`, which must be the map's declared ontology."""
        if ontology.ontology_id != self.ontology_id:
            raise ClassMapError("class map was declared for a different ontology")
        for entry in self.entries:
            ontology.by_index(entry.canonical_class_index)

    def require_covers(self, emitted_labels: frozenset[int]) -> None:
        """Every label the model can emit (excluding background) must map."""
        missing = sorted(
            label
            for label in emitted_labels
            if label != self.background_label and label not in self._by_label
        )
        if missing:
            raise ClassMapError("emitted model labels without a mapping", details={"labels": missing[:20]})


__all__ = [
    "MAX_CLASSES",
    "ClassMap",
    "ClassMapEntry",
    "ClassMapError",
    "ClassOntology",
    "LogicalIdentifier",
    "ModelLabel",
    "OntologyClass",
    "OntologyId",
    "SourceClassId",
]

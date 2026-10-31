"""No population, cause, generator or seed can be encoded as an ID field."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trinetra.domain.ids import IdentifierError
from trinetra_eval.identity import ScenarioId


@given(st.binary(min_size=16, max_size=16).filter(any))
def test_entropy_round_trip(raw):
    ident = ScenarioId.from_entropy(lambda _n: raw)
    assert str(ident) == "scn_" + raw.hex()
    assert ident == ScenarioId.parse(str(ident))
    with pytest.raises(AttributeError):
        ident.family = "BLIND"


@pytest.mark.parametrize("bad", ["scn_BLIND", "scn_" + "F" * 32, "scn_" + "a" * 31, "", None])
def test_semantic_and_malformed_ids_are_rejected(bad):
    with pytest.raises(IdentifierError):
        ScenarioId.parse(bad)


def test_identity_is_not_content_identity():
    assert ScenarioId.generate() != ScenarioId.generate()
    with pytest.raises(IdentifierError):
        ScenarioId.from_entropy(bytes)

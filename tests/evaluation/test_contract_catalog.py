"""Evaluation-only wire registrations; production catalogs remain production-only."""

import importlib
import inspect
import json
import pkgutil
from pathlib import Path

import trinetra_eval
from trinetra.domain.enums import WireEnum
from trinetra.domain.ids import registered_id_types


def test_evaluation_identifiers_and_enums_match_their_golden_catalogs():
    enums = {}
    for info in pkgutil.iter_modules(trinetra_eval.__path__, trinetra_eval.__name__ + "."):
        module = importlib.import_module(info.name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, WireEnum) and cls.__module__.startswith("trinetra_eval."):
                enums[cls.__qualname__] = [member.value for member in cls]
    golden = Path(__file__).parent / "golden"
    assert enums == json.loads((golden / "enum_wire_forms.json").read_text())
    ids = {
        prefix: cls.__module__ + "." + cls.__qualname__
        for prefix, cls in registered_id_types().items()
        if cls.__module__.startswith("trinetra_eval.")
    }
    assert ids == json.loads((golden / "id_prefixes.json").read_text())

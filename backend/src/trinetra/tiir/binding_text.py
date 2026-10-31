"""ERR-CL-023: preserve binding text exactly; only bound UTF-8 size, not vocabulary."""
from typing import Annotated

from pydantic import AfterValidator

MAX_BINDING_TEXT_BYTES = 1024


def _bounded_text(value: str) -> str:
    if not value or len(value) > MAX_BINDING_TEXT_BYTES:
        raise ValueError("binding text must contain 1..1024 UTF-8 bytes")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("binding text must be valid UTF-8") from exc
    if not 1 <= size <= MAX_BINDING_TEXT_BYTES:
        raise ValueError("binding text must contain 1..1024 UTF-8 bytes")
    return value


BindingText = Annotated[str, AfterValidator(_bounded_text)]

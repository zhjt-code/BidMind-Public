"""Helpers for constructing format-valid synthetic identifiers in tests."""


def synthetic_text(*parts: str) -> str:
    """Join non-sensitive fragments without storing a complete identifier."""

    return "".join(parts)

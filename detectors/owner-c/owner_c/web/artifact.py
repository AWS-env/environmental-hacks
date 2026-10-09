"""Helpers for artifact-only checks: read one validated field from normalized artifact data."""


def number(data: dict, field: str) -> float:
    """The field as a non-negative number, or ValueError naming the field (reported as a coverage limitation)."""
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"artifact field {field} is missing or not a non-negative number")
    return value


def flag(data: dict, field: str) -> bool:
    """The field as a boolean, or ValueError naming the field."""
    value = data.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"artifact field {field} is missing or not a boolean")
    return value

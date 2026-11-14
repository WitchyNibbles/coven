"""A tiny library to try coven on."""


def clamp(value: int, low: int, high: int) -> int:
    """The value, held between low and high."""
    return max(low, min(value, high))

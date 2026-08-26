from typing import NamedTuple


class ModifierResult(NamedTuple):
    total: float
    breakdown: list  # list of (source_label, delta) tuples

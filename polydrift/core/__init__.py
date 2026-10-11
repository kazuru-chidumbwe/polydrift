"""Protocol-independent comparison layer.

Nothing in this package imports a cell (protocol) module. Cells turn raw
traces into ``ObservationRecord`` objects; everything after that point
(comparison, grading, repeatability, labelling, drift) lives here.
"""

from polydrift.core.record import (
    Field,
    ObservationRecord,
    Status,
    StepRecord,
    Tier,
)
from polydrift.core.spec import AxisSpec, CellSpec

__all__ = [
    "AxisSpec",
    "CellSpec",
    "Field",
    "ObservationRecord",
    "Status",
    "StepRecord",
    "Tier",
]

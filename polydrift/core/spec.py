"""Per-cell oracle specification objects.

A cell declares its axes (each with a fixed tier and a canonicalisation
function), its decision-class mapping and the fixture metadata the oracle
needs. ``freeze.py`` bundles the files behind these objects for the Zenodo
deposit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from polydrift.core.canon import identity
from polydrift.core.record import Field, Tier

DECISION_CLASSES: tuple[str, ...] = (
    "proceed",
    "stop",
    "fix-request",
    "authenticate",
    "retry-later",
    "redirect",
    "no-decision",
)

DECISION_AXIS = "decision_class"


@dataclass(frozen=True)
class AxisSpec:
    """One axis: name, tier, canonicalisation function and the clause it relies on."""

    name: str
    tier: Tier
    kappa: Callable[[Any], Any] = identity
    volatile: bool = False
    rule_clause: str = ""

    def field(self, raw: Any, canon: Any | None = None) -> Field:
        """Build a field, applying kappa unless a canonical value is supplied."""
        value = self.kappa(raw) if canon is None and raw is not None else canon
        return Field(tier=self.tier, raw=raw, canon=value)


@dataclass(frozen=True)
class FixtureSpec:
    """Fixture metadata written before any implementation runs it (3.1)."""

    id: str
    cell: str
    profile: str = ""
    requires: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    spec_refs: tuple[dict[str, Any], ...] = ()
    steps: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FixtureSpec":
        return cls(
            id=data["id"],
            cell=data["cell"],
            profile=data.get("profile", ""),
            requires=tuple(data.get("requires") or ()),
            tags=tuple(data.get("tags") or ()),
            spec_refs=tuple(data.get("spec_refs") or ()),
            steps=tuple(data.get("steps") or ()),
        )


@dataclass(frozen=True)
class CellSpec:
    """Everything the core needs to know about one cell."""

    cell: str
    axes: dict[str, AxisSpec]
    # Capability matrix: impl -> set of capability ids it documents as supported.
    capabilities: dict[str, frozenset[str]] = field(default_factory=dict)
    # When set, a step with status refused-input is compared as a responded step
    # whose decision class is this value (3.8: a parser rejection is a decision).
    refusal_class: str | None = None

    def axis(self, name: str) -> AxisSpec:
        return self.axes[name]

    def supports(self, impl: str, required: tuple[str, ...]) -> bool:
        if not required:
            return True
        caps = self.capabilities.get(impl)
        if caps is None:
            return False
        return all(r in caps for r in required)

"""Repeatability over clean restarts (manuscript 3.4, flaky outcome).

Every fixture runs at least N times per implementation, each from a clean
restart. An implementation that disagrees with itself on an axis yields a
flaky (impl, fixture, axis) triple; the axis is excluded from that
implementation's cross-comparisons and the exclusion is counted.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from polydrift.core.oracle import END_STATE, STATUS_AXIS
from polydrift.core.record import ObservationRecord

MIN_REPEATS = 3


@dataclass
class RepeatReport:
    flaky: set[tuple[str, str, str]] = field(default_factory=set)
    repeats: dict[tuple[str, str], int] = field(default_factory=dict)
    representative: dict[tuple[str, str], ObservationRecord] = field(default_factory=dict)

    def flake_rate(self, impl: str | None = None) -> float:
        """Share of (impl, fixture) pairs with at least one flaky axis."""
        keys = [k for k in self.repeats if impl is None or k[0] == impl]
        if not keys:
            return 0.0
        hit = {(i, f) for (i, f, _a) in self.flaky}
        return sum(1 for k in keys if k in hit) / len(keys)

    def counts(self) -> dict[str, int]:
        return {
            "impl_fixture_pairs": len(self.repeats),
            "flaky_triples": len(self.flaky),
            "flaky_status": sum(1 for t in self.flaky if t[2] == STATUS_AXIS),
        }


def _signature(rec: ObservationRecord) -> dict[str, object]:
    sig: dict[str, object] = {}
    statuses = tuple(s.status.value for s in rec.steps)
    sig[STATUS_AXIS] = statuses
    for k, step in enumerate(rec.steps, start=1):
        for axis, f in step.fields.items():
            sig.setdefault(axis, {})[k] = f.canon  # type: ignore[index]
    for axis, f in rec.end_state.items():
        sig.setdefault(axis, {})[END_STATE] = f.canon  # type: ignore[index]
    return sig


def flaky_triples(
    records: Iterable[ObservationRecord],
    *,
    min_repeats: int = MIN_REPEATS,
) -> RepeatReport:
    """Group records by (impl, fixture) and find axes that differ across repeats.

    Raises ValueError if any (impl, fixture) group has fewer than ``min_repeats``
    records, because repeatability is a measured quantity, not an assumption.
    """
    groups: dict[tuple[str, str], list[ObservationRecord]] = defaultdict(list)
    for rec in records:
        groups[(rec.impl, rec.fixture)].append(rec)
    report = RepeatReport()
    for key, recs in sorted(groups.items()):
        if len(recs) < min_repeats:
            raise ValueError(f"{key}: {len(recs)} repeats, need at least {min_repeats}")
        recs.sort(key=lambda r: r.repeat)
        report.repeats[key] = len(recs)
        report.representative[key] = recs[0]
        sigs = [_signature(r) for r in recs]
        axes = set().union(*(s.keys() for s in sigs))
        for axis in sorted(axes):
            values = [repr(s.get(axis)) for s in sigs]
            if len(set(values)) > 1:
                report.flaky.add((key[0], key[1], axis))
    return report

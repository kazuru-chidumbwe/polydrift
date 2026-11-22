"""Pairwise outcomes and partitions (manuscript 3.4).

Outcome order: incomparable -> liveness gating -> co-failure ->
divergence (lowest differing tier) -> agreement. T4 differences never count;
agreements that rely on T4 erasure are flagged. A sixth outcome, flaky, comes
from repeatability: it is returned when the liveness status itself was not
reproducible for either side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from itertools import combinations
from typing import Any, Iterable, Mapping

from polydrift.core.record import NOT_OBSERVED, Field, ObservationRecord, Status, StepRecord, Tier
from polydrift.core.spec import DECISION_AXIS

STATUS_AXIS = "__status__"
END_STATE = "end"  # step label used for end-state differences
COUNTED_TIERS = (Tier.T0, Tier.T1, Tier.T2, Tier.T3)


class Outcome(str, Enum):
    INCOMPARABLE = "incomparable"
    FLAKY = "flaky"
    DIVERGENCE = "divergence"
    CO_FAILURE = "co-failure"
    AGREEMENT = "agreement"


@dataclass(frozen=True)
class AxisDiff:
    step: int | str  # 1-based step index, or "end"
    axis: str
    tier: Tier
    left: Any
    right: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "axis": self.axis,
            "tier": int(self.tier),
            "left": self.left,
            "right": self.right,
        }


@dataclass
class PairResult:
    fixture: str
    left: str
    right: str
    outcome: Outcome
    grade: Tier | None = None
    first_step: int | str | None = None
    diffs: list[AxisDiff] = field(default_factory=list)  # counted (T0-T3)
    t4_diffs: list[AxisDiff] = field(default_factory=list)
    excluded_axes: list[str] = field(default_factory=list)  # flaky exclusions
    reason: str = ""

    @property
    def t4_only_agreement(self) -> bool:
        return self.outcome == Outcome.AGREEMENT and bool(self.t4_diffs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture": self.fixture,
            "left": self.left,
            "right": self.right,
            "outcome": self.outcome.value,
            "grade": None if self.grade is None else int(self.grade),
            "first_step": self.first_step,
            "diffs": [d.to_dict() for d in self.diffs],
            "t4_diffs": [d.to_dict() for d in self.t4_diffs],
            "t4_only_agreement": self.t4_only_agreement,
            "excluded_axes": sorted(self.excluded_axes),
            "reason": self.reason,
        }


def effective_step(step: StepRecord, refusal_class: str | None) -> StepRecord:
    """Apply the cell's refusal rule: a refused input becomes a T1 decision."""
    if refusal_class is None or step.status != Status.REFUSED_INPUT:
        return step
    return StepRecord(
        status=Status.RESPONDED,
        fields={DECISION_AXIS: Field(tier=Tier.T1, raw=Status.REFUSED_INPUT.value, canon=refusal_class)},
        detail=step.detail,
    )


def _compare_fields(
    step_label: int | str,
    left: Mapping[str, Field],
    right: Mapping[str, Field],
    skip: set[str],
) -> tuple[list[AxisDiff], list[AxisDiff]]:
    counted: list[AxisDiff] = []
    t4: list[AxisDiff] = []
    for axis in sorted(set(left) | set(right)):
        if axis in skip:
            continue
        lf = left.get(axis)
        rf = right.get(axis)
        if (lf is not None and lf.raw == NOT_OBSERVED) or (rf is not None and rf.raw == NOT_OBSERVED):
            continue  # one side cannot show this axis: nothing to compare
        tier = (lf or rf).tier  # type: ignore[union-attr]
        lv = None if lf is None else lf.canon
        rv = None if rf is None else rf.canon
        if lv == rv:
            continue
        diff = AxisDiff(step=step_label, axis=axis, tier=tier, left=lv, right=rv)
        (t4 if tier == Tier.T4 else counted).append(diff)
    return counted, t4


def compare_pair(
    a: ObservationRecord,
    b: ObservationRecord,
    *,
    required: tuple[str, ...] = (),
    capabilities: Mapping[str, frozenset[str]] | None = None,
    flaky: set[tuple[str, str, str]] | None = None,
    refusal_class: str | None = None,
) -> PairResult:
    """Return the outcome for implementations a.impl and b.impl on one fixture.

    ``flaky`` holds (impl, fixture, axis) triples from ``repeat.flaky_triples``;
    the pseudo-axis ``__status__`` marks a non-reproducible liveness status.
    """
    if a.fixture != b.fixture:
        raise ValueError(f"fixture mismatch: {a.fixture} vs {b.fixture}")
    if len(a.steps) != len(b.steps):
        raise ValueError(f"{a.fixture}: step count differs ({len(a.steps)} vs {len(b.steps)})")
    fixture = a.fixture
    res = PairResult(fixture=fixture, left=a.impl, right=b.impl, outcome=Outcome.AGREEMENT)

    # Rule 1: incomparable.
    for rec in (a, b):
        for k, step in enumerate(rec.steps, start=1):
            if step.status == Status.HARNESS_FAULT:
                res.outcome = Outcome.INCOMPARABLE
                res.reason = f"harness-fault: {rec.impl} step {k}"
                return res
    if required:
        caps = capabilities or {}
        for rec in (a, b):
            have = caps.get(rec.impl)
            if have is None or not all(r in have for r in required):
                res.outcome = Outcome.INCOMPARABLE
                res.reason = f"capability: {rec.impl} lacks {sorted(set(required) - set(have or ()))}"
                return res

    flaky = flaky or set()
    if (a.impl, fixture, STATUS_AXIS) in flaky or (b.impl, fixture, STATUS_AXIS) in flaky:
        res.outcome = Outcome.FLAKY
        res.reason = "liveness status not reproducible"
        return res
    skip = {ax for (impl, fx, ax) in flaky if fx == fixture and impl in (a.impl, b.impl)}
    res.excluded_axes = sorted(skip)

    counted: list[AxisDiff] = []
    t4: list[AxisDiff] = []
    for k, (sa_raw, sb_raw) in enumerate(zip(a.steps, b.steps), start=1):
        sa = effective_step(sa_raw, refusal_class)
        sb = effective_step(sb_raw, refusal_class)
        # Rule 2: liveness gating. Fields at k and later are undefined.
        if sa.status != sb.status:
            res.outcome = Outcome.DIVERGENCE
            res.grade = Tier.T0
            res.first_step = k
            res.diffs = counted + [
                AxisDiff(step=k, axis=STATUS_AXIS, tier=Tier.T0, left=sa.status.value, right=sb.status.value)
            ]
            res.t4_diffs = t4
            return res
        # Rule 3: co-failure. Same non-responded status at the same step.
        if sa.status != Status.RESPONDED:
            res.outcome = Outcome.CO_FAILURE
            res.first_step = k
            res.diffs = counted
            res.t4_diffs = t4
            res.reason = f"both {sa.status.value} at step {k}"
            return res
        c, t = _compare_fields(k, sa.fields, sb.fields, skip)
        counted.extend(c)
        t4.extend(t)

    c, t = _compare_fields(END_STATE, a.end_state, b.end_state, skip)
    counted.extend(c)
    t4.extend(t)
    res.diffs = counted
    res.t4_diffs = t4
    # Rule 4 / 5.
    if counted:
        res.outcome = Outcome.DIVERGENCE
        res.grade = min(d.tier for d in counted)
        res.first_step = counted[0].step
    else:
        res.outcome = Outcome.AGREEMENT
    return res


def compare_all(
    records: Iterable[ObservationRecord],
    **kwargs: Any,
) -> list[PairResult]:
    """All unordered pairs among records of one fixture, impls sorted by name."""
    recs = sorted(records, key=lambda r: r.impl)
    return [compare_pair(x, y, **kwargs) for x, y in combinations(recs, 2)]


def behaviour_key(
    rec: ObservationRecord,
    tier: Tier,
    *,
    skip: set[str] = frozenset(),  # type: ignore[assignment]
    refusal_class: str | None = None,
) -> tuple[Any, ...]:
    """Canonical behaviour of one record up to and including ``tier``.

    Liveness gating is built in: the key stops at the first non-responded step.
    Two records with the same key agree on every counted axis at tier <= ``tier``.
    """
    parts: list[Any] = []
    for k, raw in enumerate(rec.steps, start=1):
        step = effective_step(raw, refusal_class)
        parts.append((k, STATUS_AXIS, step.status.value))
        if step.status != Status.RESPONDED:
            return tuple(parts)
        for axis in sorted(step.fields):
            f = step.fields[axis]
            if axis in skip or f.tier > tier or f.canon is None:
                continue
            parts.append((k, axis, f.canon))
    for axis in sorted(rec.end_state):
        f = rec.end_state[axis]
        if axis in skip or f.tier > tier or f.canon is None:
            continue
        parts.append((END_STATE, axis, f.canon))
    return tuple(parts)


def partition(
    records: Iterable[ObservationRecord],
    tier: Tier,
    *,
    flaky: set[tuple[str, str, str]] | None = None,
    refusal_class: str | None = None,
) -> list[frozenset[str]]:
    """Partition implementations of one fixture into behaviour classes at ``tier``.

    Records with a harness fault are left out. An axis that is flaky for any
    implementation is dropped for all of them, so the relation stays an
    equivalence and the partition does not depend on comparison order.
    """
    recs = [r for r in records if not any(s.status == Status.HARNESS_FAULT for s in r.steps)]
    flaky = flaky or set()
    fixtures = {r.fixture for r in recs}
    if len(fixtures) > 1:
        raise ValueError(f"partition needs one fixture, got {sorted(fixtures)}")
    impls = {r.impl for r in recs}
    skip = {ax for (impl, fx, ax) in flaky if impl in impls and fx in fixtures}
    if STATUS_AXIS in skip:
        recs = [r for r in recs if (r.impl, r.fixture, STATUS_AXIS) not in flaky]
    classes: dict[tuple[Any, ...], set[str]] = {}
    for r in recs:
        classes.setdefault(behaviour_key(r, tier, skip=skip, refusal_class=refusal_class), set()).add(r.impl)
    return sorted((frozenset(v) for v in classes.values()), key=lambda s: (-len(s), sorted(s)))


def partition_shape(parts: list[frozenset[str]]) -> tuple[int, ...]:
    """{5}, {4, 1}, {2, 2, 1} ... as a tuple of class sizes, largest first."""
    return tuple(sorted((len(p) for p in parts), reverse=True))

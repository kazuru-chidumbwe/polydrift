"""Small builders for core tests."""

from __future__ import annotations

from polydrift.core.record import Field, ObservationRecord, Status, StepRecord, Tier


def f(tier: int, value, raw=None) -> Field:
    return Field(tier=Tier(tier), raw=value if raw is None else raw, canon=value)


def step(status: str = "responded", **fields: Field) -> StepRecord:
    return StepRecord(status=Status(status), fields=dict(fields))


def rec(impl: str, *steps: StepRecord, fixture: str = "F1", repeat: int = 1, end=None, version: str = "") -> ObservationRecord:
    return ObservationRecord(
        cell="test",
        impl=impl,
        fixture=fixture,
        steps=tuple(steps),
        end_state=dict(end or {}),
        repeat=repeat,
        impl_version=version,
    )

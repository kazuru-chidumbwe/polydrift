"""Observation records (manuscript 3.1).

One record per implementation x fixture x repeat. A record holds one step
record per fixture step and an end-state record. Every field keeps its raw
value next to its canonical value so the effect of canonicalisation can be
measured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any


class Status(str, Enum):
    """Liveness status of one step. Shared by every cell."""

    RESPONDED = "responded"
    SILENT = "silent"
    CLOSED = "closed"
    CRASHED = "crashed"
    REFUSED_INPUT = "refused-input"
    HARNESS_FAULT = "harness-fault"


class Tier(IntEnum):
    """Divergence tiers from the tier rule (3.3). Lower is more severe."""

    T0 = 0
    T1 = 1
    T2 = 2
    T3 = 3
    T4 = 4


def freeze_value(value: Any) -> Any:
    """Turn lists and dicts into hashable tuples so canonical values can be keys."""
    if isinstance(value, dict):
        return tuple(sorted((str(k), freeze_value(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(v) for v in value)
    if isinstance(value, set):
        return tuple(sorted(freeze_value(v) for v in value))
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class Field:
    """One typed axis value. ``canon`` is None when the value is undefined."""

    tier: Tier
    raw: Any
    canon: Any

    def __post_init__(self) -> None:
        object.__setattr__(self, "tier", Tier(self.tier))
        object.__setattr__(self, "raw", freeze_value(self.raw))
        object.__setattr__(self, "canon", freeze_value(self.canon))

    def to_dict(self) -> dict[str, Any]:
        return {"tier": int(self.tier), "raw": _thaw(self.raw), "canon": _thaw(self.canon)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Field":
        return cls(tier=Tier(data["tier"]), raw=data.get("raw"), canon=data.get("canon"))


@dataclass(frozen=True)
class StepRecord:
    """Liveness status plus typed fields for one step (or the end state)."""

    status: Status
    fields: dict[str, Field] = field(default_factory=dict)
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", Status(self.status))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status.value,
            "fields": {k: v.to_dict() for k, v in sorted(self.fields.items())},
        }
        if self.detail:
            out["detail"] = self.detail
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StepRecord":
        return cls(
            status=Status(data["status"]),
            fields={k: Field.from_dict(v) for k, v in data.get("fields", {}).items()},
            detail=data.get("detail", ""),
        )


@dataclass(frozen=True)
class ObservationRecord:
    """o(i, f) = <s_1, ..., s_m, e> for one implementation, fixture and repeat."""

    cell: str
    impl: str
    fixture: str
    steps: tuple[StepRecord, ...]
    end_state: dict[str, Field] = field(default_factory=dict)
    impl_version: str = ""
    repeat: int = 1
    profile: str = ""
    harness_commit: str = ""

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("an observation record needs at least one step")
        object.__setattr__(self, "steps", tuple(self.steps))

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.cell, self.impl, self.fixture)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "impl": self.impl,
            "impl_version": self.impl_version,
            "fixture": self.fixture,
            "repeat": self.repeat,
            "profile": self.profile,
            "harness_commit": self.harness_commit,
            "steps": [s.to_dict() for s in self.steps],
            "end_state": {"fields": {k: v.to_dict() for k, v in sorted(self.end_state.items())}},
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ObservationRecord":
        end = data.get("end_state") or {}
        return cls(
            cell=data["cell"],
            impl=data["impl"],
            impl_version=data.get("impl_version", ""),
            fixture=data["fixture"],
            repeat=int(data.get("repeat", 1)),
            profile=data.get("profile", ""),
            harness_commit=data.get("harness_commit", ""),
            steps=tuple(StepRecord.from_dict(s) for s in data["steps"]),
            end_state={k: Field.from_dict(v) for k, v in end.get("fields", {}).items()},
        )


def read_jsonl(path: str) -> list[ObservationRecord]:
    """Load records written one JSON object per line."""
    out: list[ObservationRecord] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                out.append(ObservationRecord.from_dict(json.loads(line)))
    return out


def write_jsonl(path: str, records: list[ObservationRecord]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for rec in records:
            handle.write(rec.to_json() + "\n")

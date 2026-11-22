"""Baseline oracles for RQ2, run on the same records as the graded oracle.

* raw-byte oracle: equality of raw values on every axis, presentation included;
  any non-responded step on either side is an error, as in the earlier harnesses.
* coarse oracle: liveness status plus the native code at T1 (the protocol's own
  reply code), the kind of comparison earlier multi-protocol studies use.
"""

from __future__ import annotations

from typing import Any

from polydrift.core.oracle import NOT_COMPARED
from polydrift.core.record import ObservationRecord, Status

NATIVE_AXIS = "native_code"


def _raw_view(rec: ObservationRecord) -> tuple[Any, ...]:
    parts: list[Any] = []
    # announced configuration is outside the study for every oracle, the baselines included
    for k, step in enumerate(rec.steps, start=1):
        for axis in sorted(step.fields):
            if axis not in NOT_COMPARED:
                parts.append((k, axis, repr(step.fields[axis].raw)))
    for axis in sorted(rec.end_state):
        if axis not in NOT_COMPARED:
            parts.append(("end", axis, repr(rec.end_state[axis].raw)))
    return tuple(parts)


def raw_oracle(a: ObservationRecord, b: ObservationRecord) -> str:
    """agree / diverge / error / incomparable, on raw values."""
    for rec in (a, b):
        if any(s.status == Status.HARNESS_FAULT for s in rec.steps):
            return "incomparable"
    for rec in (a, b):
        if any(s.status != Status.RESPONDED for s in rec.steps):
            return "error"
    return "agree" if _raw_view(a) == _raw_view(b) else "diverge"


def coarse_oracle(a: ObservationRecord, b: ObservationRecord, native_axis: str = NATIVE_AXIS) -> str:
    """agree / diverge / incomparable on (status, native code) per step."""
    for rec in (a, b):
        if any(s.status == Status.HARNESS_FAULT for s in rec.steps):
            return "incomparable"

    def view(rec: ObservationRecord) -> tuple[Any, ...]:
        out = []
        for step in rec.steps:
            f = step.fields.get(native_axis)
            out.append((step.status.value, None if f is None else repr(f.raw)))
            if step.status != Status.RESPONDED:
                break
        return tuple(out)

    return "agree" if view(a) == view(b) else "diverge"

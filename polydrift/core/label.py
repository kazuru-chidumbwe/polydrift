"""Fault class and consequence (manuscript 3.5).

Labels are computed only from the fixture's specification predicates and
tags, written before any run. There is no manual override.

A specification reference is a mapping::

    {"clause": "RFC 3261 8.2.2.1", "keyword": "MUST", "axis": "decision_class",
     "step": 1, "allowed": ["fix-request"], "security": "auth-bypass"}

``step`` is a 1-based step index or "end" (default 1). ``allowed`` lists the
canonical values the clause permits; null in the list permits an undefined
value. The optional ``security`` key names the property a ``security:<name>``
fixture tag refers to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from polydrift.core.oracle import END_STATE, STATUS_AXIS, Outcome, PairResult, effective_step
from polydrift.core.record import ObservationRecord, Status, Tier, freeze_value
from polydrift.core.spec import FixtureSpec

MUST_WORDS = {"MUST", "MUST NOT", "SHALL", "SHALL NOT", "REQUIRED"}
SHOULD_WORDS = {"SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED"}
MAY_WORDS = {"MAY", "OPTIONAL"}


class FaultClass(str, Enum):
    HARNESS_ARTEFACT = "harness-artefact"
    VIOLATION = "violation"
    DEVIATION = "deviation"
    PERMITTED = "permitted"
    UNSPECIFIED = "unspecified"


def strength(keyword: str) -> str:
    kw = " ".join(keyword.upper().split())
    if kw in MUST_WORDS:
        return "MUST"
    if kw in SHOULD_WORDS:
        return "SHOULD"
    if kw in MAY_WORDS:
        return "MAY"
    raise ValueError(f"unknown requirement keyword: {keyword!r}")


def _step_of(ref: Mapping[str, Any]) -> int | str:
    step = ref.get("step", 1)
    return END_STATE if step == END_STATE else int(step)


def observed(rec: ObservationRecord, axis: str, step: int | str, refusal_class: str | None = None) -> Any:
    """Canonical value of ``axis`` at ``step`` (None when undefined)."""
    if step == END_STATE:
        f = rec.end_state.get(axis)
        return None if f is None else f.canon
    idx = int(step) - 1
    if idx < 0 or idx >= len(rec.steps):
        return None
    # Liveness gating: after a non-responded step, later fields are undefined.
    for earlier in rec.steps[:idx]:
        if effective_step(earlier, refusal_class).status != Status.RESPONDED:
            return None if axis != STATUS_AXIS else earlier.status.value
    st = effective_step(rec.steps[idx], refusal_class)
    if axis == STATUS_AXIS:
        return st.status.value
    if st.status != Status.RESPONDED:
        return None
    f = st.fields.get(axis)
    return None if f is None else f.canon


def allowed_set(ref: Mapping[str, Any]) -> set[Any]:
    return {freeze_value(v) for v in ref.get("allowed", [])}


def inside(rec: ObservationRecord, ref: Mapping[str, Any], refusal_class: str | None = None) -> bool:
    return observed(rec, ref["axis"], _step_of(ref), refusal_class) in allowed_set(ref)


def conflicting_refs(refs: Iterable[Mapping[str, Any]]) -> set[int]:
    """Indices of references whose allowed set is disjoint from another reference
    on the same axis and step. Such clauses contradict each other and are set
    aside: a divergence they alone constrain is labelled unspecified."""
    refs = list(refs)
    bad: set[int] = set()
    for i, a in enumerate(refs):
        for j in range(i + 1, len(refs)):
            b = refs[j]
            if a["axis"] == b["axis"] and _step_of(a) == _step_of(b):
                if not (allowed_set(a) & allowed_set(b)):
                    bad.update((i, j))
    return bad


@dataclass
class ImplLabel:
    impl: str
    must_broken: list[str] = field(default_factory=list)
    should_broken: list[str] = field(default_factory=list)
    security_broken: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.must_broken:
            return "violation"
        if self.should_broken:
            return "deviation"
        return "conforms"


def label_impl(rec: ObservationRecord, fixture: FixtureSpec, refusal_class: str | None = None) -> ImplLabel:
    """Evaluate one implementation's record against every usable predicate."""
    out = ImplLabel(impl=rec.impl)
    bad = conflicting_refs(fixture.spec_refs)
    sec_tags = {t.split(":", 1)[1] for t in fixture.tags if t.startswith("security:")}
    for i, ref in enumerate(fixture.spec_refs):
        if i in bad:
            continue
        if inside(rec, ref, refusal_class):
            continue
        s = strength(ref["keyword"])
        if s == "MUST":
            out.must_broken.append(ref.get("clause") or ref.get("req_id", "?"))
        elif s == "SHOULD":
            out.should_broken.append(ref.get("clause") or ref.get("req_id", "?"))
        prop = ref.get("security")
        if prop and prop in sec_tags:
            out.security_broken.append(prop)
    return out


def fault_class(
    pair: PairResult,
    left: ObservationRecord,
    right: ObservationRecord,
    fixture: FixtureSpec,
    *,
    refusal_class: str | None = None,
    artefact: bool = False,
) -> FaultClass | None:
    """Fault class of a divergence; None for any other outcome.

    ``artefact`` is set by the harness when the profile check or the packet
    capture cross-check explains the divergence (3.5 rule 1).
    """
    if pair.outcome == Outcome.FLAKY:
        return FaultClass.HARNESS_ARTEFACT
    if pair.outcome != Outcome.DIVERGENCE:
        return None
    if artefact:
        return FaultClass.HARNESS_ARTEFACT
    ll = label_impl(left, fixture, refusal_class)
    rl = label_impl(right, fixture, refusal_class)
    if ll.must_broken or rl.must_broken:
        return FaultClass.VIOLATION
    if ll.should_broken or rl.should_broken:
        return FaultClass.DEVIATION
    bad = conflicting_refs(fixture.spec_refs)
    constrained = {
        (ref["axis"], _step_of(ref)) for i, ref in enumerate(fixture.spec_refs) if i not in bad
    }
    for d in pair.diffs:
        if (d.axis, d.step) not in constrained:
            return FaultClass.UNSPECIFIED
    return FaultClass.PERMITTED


@dataclass(frozen=True)
class Consequence:
    interop: bool
    security: bool

    @property
    def primary(self) -> str:
        if self.security:
            return "security-relevant"
        if self.interop:
            return "interoperability-relevant"
        return "detail-only"


def consequence(
    pair: PairResult,
    left: ObservationRecord,
    right: ObservationRecord,
    fixture: FixtureSpec,
    *,
    refusal_class: str | None = None,
) -> Consequence | None:
    if pair.outcome != Outcome.DIVERGENCE:
        return None
    interop = pair.grade is not None and pair.grade <= Tier.T2
    sec = bool(
        label_impl(left, fixture, refusal_class).security_broken
        or label_impl(right, fixture, refusal_class).security_broken
    )
    return Consequence(interop=interop, security=sec)


def unanimous_violations(
    records: Iterable[ObservationRecord],
    fixture: FixtureSpec,
    *,
    refusal_class: str | None = None,
) -> list[str]:
    """MUST clauses broken by every implementation: shared bugs that a pure
    differential comparison cannot see."""
    labels = [label_impl(r, fixture, refusal_class) for r in records]
    if len(labels) < 2:
        return []
    common = set(labels[0].must_broken)
    for lab in labels[1:]:
        common &= set(lab.must_broken)
    return sorted(common)

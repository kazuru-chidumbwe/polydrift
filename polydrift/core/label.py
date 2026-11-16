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

Two conventions used by fixture files:

* axis ``status`` is the step's outcome: the liveness status when the step did not respond (``closed``,
  ``silent``, ...), otherwise its decision class (``proceed``, ``fix-request``, ...) or ``responded`` when the step
  carries no decision. A clause that requires "reject or close" lists both, e.g. ``[closed, fix-request]``.
* a reference without ``allowed`` ties a clause to an axis for comparison only (what the clause fixes cannot be
  enumerated, for example the order of forwarded user properties); it never labels an implementation.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from polydrift.core.oracle import END_STATE, STATUS_AXIS, Outcome, PairResult, effective_step
from polydrift.core.record import ObservationRecord, Status, Tier, freeze_value
from polydrift.core.spec import DECISION_AXIS, FixtureSpec

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


OUTCOME_AXIS = "status"


def observed(rec: ObservationRecord, axis: str, step: int | str, refusal_class: str | None = None) -> Any:
    """Canonical value of ``axis`` at ``step`` (None when undefined)."""
    if axis == OUTCOME_AXIS and step != END_STATE:
        idx = int(step) - 1
        if idx < 0 or idx >= len(rec.steps):
            return None
        for earlier in rec.steps[:idx]:
            if effective_step(earlier, refusal_class).status != Status.RESPONDED:
                return earlier.status.value
        st = effective_step(rec.steps[idx], refusal_class)
        if st.status != Status.RESPONDED:
            return st.status.value
        f = st.fields.get(DECISION_AXIS)
        return "responded" if f is None or f.canon is None else f.canon
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


PREDICATES = ("allowed", "excludes", "excludes_re", "order", "same_as")


def labels(ref: Mapping[str, Any]) -> bool:
    """Does this reference label implementations? Its clause fixes the outcome through one predicate:
    ``allowed`` (the values the clause allows); where those cannot be listed, ``excludes`` (values that must not be,
    or be among, the observed value: "MUST NOT include X"), ``excludes_re`` (a regular expression no observed string
    may match, e.g. an unescaped space in a URI), ``order: [a, b]`` (in an observed sequence, every a comes before
    any b), or ``same_as: <step>`` (the value equals the same axis at another step: "the field is ignored")."""
    return any(k in ref for k in PREDICATES)


def _elements(value: Any) -> tuple:
    return value if isinstance(value, tuple) else (value,)


def inside(rec: ObservationRecord, ref: Mapping[str, Any], refusal_class: str | None = None) -> bool:
    if not labels(ref):
        return True
    value = observed(rec, ref["axis"], _step_of(ref), refusal_class)
    if "allowed" in ref:
        return value in allowed_set(ref)
    if "same_as" in ref:
        return value == observed(rec, ref["axis"], ref["same_as"], refusal_class)
    if value is None:
        return True  # nothing observed at the step: the clause has nothing to bind
    if "excludes" in ref:
        bad = {freeze_value(v) for v in ref["excludes"]}
        return not any(v in bad for v in _elements(value))
    if "excludes_re" in ref:
        rx = re.compile(ref["excludes_re"])
        return not any(isinstance(v, str) and rx.search(v) for v in _elements(value))
    first, second = ref["order"]
    seq = list(_elements(value))
    return not (first in seq and second in seq and max(i for i, v in enumerate(seq) if v == first)
                > min(i for i, v in enumerate(seq) if v == second))


def conflicting_refs(refs: Iterable[Mapping[str, Any]]) -> set[int]:
    """Indices of references whose allowed set is disjoint from another reference
    on the same axis and step. Such clauses contradict each other and are set
    aside: a divergence they alone constrain is labelled unspecified."""
    refs = list(refs)
    bad: set[int] = set()
    for i, a in enumerate(refs):
        for j in range(i + 1, len(refs)):
            b = refs[j]
            if "allowed" in a and "allowed" in b and a["axis"] == b["axis"] and _step_of(a) == _step_of(b):
                if not (allowed_set(a) & allowed_set(b)):
                    bad.update((i, j))
    return bad


def _broken(rec: ObservationRecord, fixture: FixtureSpec, bad: set[int], refusal_class: str | None) -> set[int]:
    return {i for i, ref in enumerate(fixture.spec_refs)
            if i not in bad and strength(ref["keyword"]) in ("MUST", "SHOULD") and not inside(rec, ref, refusal_class)}


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
    # A clause on an axis that was not reproducible for either side (3.5 rule 1) cannot carry a label.
    bad = conflicting_refs(fixture.spec_refs) | {
        i for i, ref in enumerate(fixture.spec_refs) if ref["axis"] in set(pair.excluded_axes)}
    lb = _broken(left, fixture, bad, refusal_class)
    rb = _broken(right, fixture, bad, refusal_class)
    differing = {(d.axis, d.step) for d in pair.diffs}
    # A breach belongs to this divergence when it separates the two sides (one breaks the clause, the other does
    # not) or when the clause constrains an axis on which they differ. A breach both sides share on an axis where
    # they agree is a unanimous violation (reported on its own), not the cause of this divergence.
    def on_differing(i: int) -> bool:
        ref = fixture.spec_refs[i]
        axes = (STATUS_AXIS, DECISION_AXIS) if ref["axis"] == OUTCOME_AXIS else (ref["axis"],)
        return any((a, _step_of(ref)) in differing for a in axes)
    own = (lb ^ rb) | {i for i in lb & rb if on_differing(i)}
    kinds = {strength(fixture.spec_refs[i]["keyword"]) for i in own}
    if "MUST" in kinds:
        return FaultClass.VIOLATION
    if "SHOULD" in kinds:
        return FaultClass.DEVIATION
    constrained: set[tuple[str, int | str]] = set()
    for i, ref in enumerate(fixture.spec_refs):
        if i in bad or not labels(ref):
            continue
        st = _step_of(ref)
        # the outcome axis constrains the step's liveness status and its decision class together
        axes = (STATUS_AXIS, DECISION_AXIS) if ref["axis"] == OUTCOME_AXIS else (ref["axis"],)
        constrained.update((a, st) for a in axes)
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

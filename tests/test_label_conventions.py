"""Fixture-file conventions the labeller must honour: the ``status`` outcome axis and comparison-only references."""

from __future__ import annotations

from polydrift.core.label import inside, label_impl, observed
from polydrift.core.spec import FixtureSpec

from core_helpers import f, rec, step


def fx(*refs):
    return FixtureSpec(id="F1", cell="test", spec_refs=tuple(refs))


REJECT = {"native": "X-1", "keyword": "MUST", "axis": "status", "step": 2, "allowed": ["closed", "fix-request"]}


def test_status_axis_is_liveness_when_not_responded():
    r = rec("a", step(decision_class=f(1, "proceed")), step("closed"))
    assert observed(r, "status", 2) == "closed"
    assert inside(r, REJECT)


def test_status_axis_is_decision_class_when_responded():
    r = rec("a", step(decision_class=f(1, "proceed")), step(decision_class=f(1, "fix-request")))
    assert observed(r, "status", 2) == "fix-request"
    assert inside(r, REJECT)
    ok = rec("a", step(decision_class=f(1, "proceed")), step(decision_class=f(1, "proceed")))
    assert not inside(ok, REJECT)


def test_status_axis_without_decision_is_responded():
    r = rec("a", step(decision_class=f(1, "proceed")), step(packet_types=f(2, ())))
    assert observed(r, "status", 2) == "responded"


def test_status_axis_after_earlier_closed_step():
    r = rec("a", step("closed"), step(decision_class=f(1, "proceed")))
    assert observed(r, "status", 2) == "closed"


def test_reference_without_allowed_never_labels():
    ref = {"native": "X-2", "keyword": "MUST", "axis": "forwarded_props", "step": 1}
    r = rec("a", step(forwarded_props=f(2, (("b", ()),))))
    assert inside(r, ref)
    lab = label_impl(r, fx(ref))
    assert lab.verdict == "conforms"

"""Outcome rules 1-5, the refusal rule, flaky exclusion and partitions."""

import ast
import itertools
import random
from pathlib import Path

import pytest

from core_helpers import f, rec, step
from polydrift.core.oracle import (
    STATUS_AXIS,
    Outcome,
    compare_all,
    compare_pair,
    partition,
    partition_shape,
)
from polydrift.core.record import ObservationRecord, Tier


def test_agreement_all_axes():
    a = rec("a", step(decision_class=f(1, "proceed"), body=f(2, "x")))
    b = rec("b", step(decision_class=f(1, "proceed"), body=f(2, "x")))
    r = compare_pair(a, b)
    assert r.outcome == Outcome.AGREEMENT
    assert r.grade is None
    assert not r.t4_only_agreement


def test_t4_difference_does_not_count_but_is_flagged():
    a = rec("a", step(decision_class=f(1, "proceed"), order=f(4, "x,y")))
    b = rec("b", step(decision_class=f(1, "proceed"), order=f(4, "y,x")))
    r = compare_pair(a, b)
    assert r.outcome == Outcome.AGREEMENT
    assert r.t4_only_agreement
    assert [d.axis for d in r.t4_diffs] == ["order"]


def test_canonicalisation_erasure_is_a_t4_difference():
    # same canonical value, different raw value: the pair agrees, and the erased difference is kept as T4
    a = rec("a", step(decision_class=f(1, "proceed"), rrset=f(2, ("a", "b"), raw=("a", "b"))))
    b = rec("b", step(decision_class=f(1, "proceed"), rrset=f(2, ("a", "b"), raw=("b", "a"))))
    r = compare_pair(a, b)
    assert r.outcome == Outcome.AGREEMENT
    assert r.t4_only_agreement
    assert [(d.axis, int(d.tier), d.left, d.right) for d in r.t4_diffs] == [("rrset", 4, ("a", "b"), ("b", "a"))]
    # an erasure next to a counted difference does not change the grade
    c = rec("c", step(decision_class=f(1, "stop"), rrset=f(2, ("a", "b"), raw=("b", "a"))))
    r2 = compare_pair(a, c)
    assert r2.outcome == Outcome.DIVERGENCE and r2.grade == Tier.T1
    assert [d.axis for d in r2.t4_diffs] == ["rrset"]


def test_announced_config_is_recorded_not_compared():
    a = rec("a", step(decision_class=f(1, "proceed"), announced_config=f(3, (("receive_maximum", "10"),))))
    b = rec("b", step(decision_class=f(1, "proceed"), announced_config=f(3, (("receive_maximum", "32"),))))
    r = compare_pair(a, b)
    assert r.outcome == Outcome.AGREEMENT and not r.t4_diffs


def test_grade_is_lowest_differing_tier_across_steps():
    a = rec("a", step(info=f(3, "x")), step(decision_class=f(1, "proceed")))
    b = rec("b", step(info=f(3, "y")), step(decision_class=f(1, "stop")))
    r = compare_pair(a, b)
    assert r.outcome == Outcome.DIVERGENCE
    assert r.grade == Tier.T1
    # A T3 difference at step 1 does not stop the walk.
    assert r.first_step == 1
    assert {d.axis for d in r.diffs} == {"info", "decision_class"}


def test_harness_fault_is_incomparable():
    a = rec("a", step(decision_class=f(1, "proceed")))
    b = rec("b", step("harness-fault"))
    assert compare_pair(a, b).outcome == Outcome.INCOMPARABLE


def test_harness_fault_at_later_step_is_incomparable():
    a = rec("a", step(x=f(2, 1)), step("harness-fault"))
    b = rec("b", step(x=f(2, 2)), step(x=f(2, 1)))
    assert compare_pair(a, b).outcome == Outcome.INCOMPARABLE


def test_missing_capability_is_incomparable_but_runtime_rejection_is_not():
    a = rec("a", step(decision_class=f(1, "proceed")))
    b = rec("b", step(decision_class=f(1, "fix-request")))
    caps = {"a": frozenset({"ext"}), "b": frozenset()}
    r = compare_pair(a, b, required=("ext",), capabilities=caps)
    assert r.outcome == Outcome.INCOMPARABLE
    caps_both = {"a": frozenset({"ext"}), "b": frozenset({"ext"})}
    r2 = compare_pair(a, b, required=("ext",), capabilities=caps_both)
    assert r2.outcome == Outcome.DIVERGENCE and r2.grade == Tier.T1


def test_liveness_gating_at_later_step():
    a = rec("a", step(x=f(2, 1)), step(y=f(1, "proceed")), step(z=f(2, "late-a")))
    b = rec("b", step(x=f(2, 1)), step("silent"), step(z=f(2, "late-b")))
    r = compare_pair(a, b)
    assert r.outcome == Outcome.DIVERGENCE
    assert r.grade == Tier.T0
    assert r.first_step == 2
    # Fields at step 2 and later are not compared.
    assert [d.axis for d in r.diffs] == [STATUS_AXIS]


def test_co_failure_at_later_step_is_not_agreement():
    a = rec("a", step(x=f(2, 1)), step("crashed"))
    b = rec("b", step(x=f(2, 1)), step("crashed"))
    r = compare_pair(a, b)
    assert r.outcome == Outcome.CO_FAILURE
    assert r.first_step == 2


def test_different_failure_statuses_diverge_at_t0():
    a = rec("a", step("silent"))
    b = rec("b", step("crashed"))
    r = compare_pair(a, b)
    assert r.outcome == Outcome.DIVERGENCE and r.grade == Tier.T0


def test_refusal_rule_makes_parser_rejection_a_t1_decision():
    refused = rec("a", step("refused-input"))
    accepted = rec("b", step(decision_class=f(1, "proceed"), method=f(2, "INVITE")))
    other_refused = rec("c", step("refused-input"))
    # Without the rule: liveness divergence and co-failure.
    assert compare_pair(refused, accepted).grade == Tier.T0
    assert compare_pair(refused, other_refused).outcome == Outcome.CO_FAILURE
    # With the rule: T1 divergence and agreement on the decision.
    r = compare_pair(refused, accepted, refusal_class="fix-request")
    assert r.outcome == Outcome.DIVERGENCE and r.grade == Tier.T1
    assert compare_pair(refused, other_refused, refusal_class="fix-request").outcome == Outcome.AGREEMENT


def test_end_state_is_compared():
    a = rec("a", step(decision_class=f(1, "proceed")), end={"cache": f(2, "evil")})
    b = rec("b", step(decision_class=f(1, "proceed")), end={"cache": f(2, "clean")})
    r = compare_pair(a, b)
    assert r.grade == Tier.T2 and r.diffs[0].step == "end"


def test_flaky_axis_is_excluded_and_reported():
    a = rec("a", step(decision_class=f(1, "proceed"), ts=f(2, 5)))
    b = rec("b", step(decision_class=f(1, "proceed"), ts=f(2, 9)))
    r = compare_pair(a, b, flaky={("a", "F1", "ts")})
    assert r.outcome == Outcome.AGREEMENT
    assert r.excluded_axes == ["ts"]


def test_flaky_status_gives_flaky_outcome():
    a = rec("a", step(decision_class=f(1, "proceed")))
    b = rec("b", step("silent"))
    assert compare_pair(a, b, flaky={("b", "F1", STATUS_AXIS)}).outcome == Outcome.FLAKY


def test_step_count_mismatch_raises():
    with pytest.raises(ValueError):
        compare_pair(rec("a", step(), step()), rec("b", step()))


def test_compare_all_pairs():
    recs = [rec(n, step(decision_class=f(1, "proceed"))) for n in "abcde"]
    assert len(compare_all(recs)) == 10


def _five():
    return [
        rec("a", step(decision_class=f(1, "proceed"), x=f(2, 1), info=f(3, "p"))),
        rec("b", step(decision_class=f(1, "proceed"), x=f(2, 1), info=f(3, "q"))),
        rec("c", step(decision_class=f(1, "proceed"), x=f(2, 2), info=f(3, "p"))),
        rec("d", step(decision_class=f(1, "stop"))),
        rec("e", step("silent")),
    ]


def test_partition_by_tier():
    recs = _five()
    assert partition_shape(partition(recs, Tier.T0)) == (4, 1)
    assert partition_shape(partition(recs, Tier.T1)) == (3, 1, 1)
    assert partition_shape(partition(recs, Tier.T2)) == (2, 1, 1, 1)
    assert partition_shape(partition(recs, Tier.T3)) == (1, 1, 1, 1, 1)


def test_partition_independent_of_order():
    recs = _five()
    expected = partition(recs, Tier.T2)
    rng = random.Random(7)
    for _ in range(50):
        shuffled = recs[:]
        rng.shuffle(shuffled)
        assert partition(shuffled, Tier.T2) == expected


def test_partition_drops_harness_faults():
    recs = _five() + [rec("f", step("harness-fault"))]
    assert sum(len(p) for p in partition(recs, Tier.T1)) == 5


def test_pairwise_agreement_matches_partition():
    """Two impls share a partition class at tier t exactly when the pair has no
    counted difference at tier <= t (equivalence by construction)."""
    recs = _five()
    for t in (Tier.T1, Tier.T2, Tier.T3):
        parts = partition(recs, t)
        cls = {impl: i for i, p in enumerate(parts) for impl in p}
        for x, y in itertools.combinations(recs, 2):
            r = compare_pair(x, y)
            same = r.outcome in (Outcome.AGREEMENT, Outcome.CO_FAILURE) or (
                r.outcome == Outcome.DIVERGENCE and r.grade > t
            )
            assert same == (cls[x.impl] == cls[y.impl]), (t, x.impl, y.impl)


def test_record_json_roundtrip():
    r = rec("a", step(decision_class=f(1, "proceed"), via=f(2, ("SIP/2.0/UDP", "h", (("branch", "z"),)))), end={"m": f(2, ["x", "y"])})
    back = ObservationRecord.from_dict(__import__("json").loads(r.to_json()))
    assert back == r


def test_core_imports_no_cell():
    core = Path(__file__).resolve().parents[1] / "polydrift" / "core"
    for path in core.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert not n.startswith("polydrift.cells"), f"{path.name} imports {n}"
                assert n not in ("polydrift.harness", "polydrift.parse", "polydrift.drivers"), f"{path.name} imports {n}"

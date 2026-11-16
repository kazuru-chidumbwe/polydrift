"""Repeat, label, drift, baselines, replay, freeze and canon helpers."""

import json
import re

import pytest

from core_helpers import f, rec, step
from polydrift.core.baselines import coarse_oracle, raw_oracle
from polydrift.core.canon import AlphaRenamer, collapse_lws, digest_after, normalise_line_endings
from polydrift.core.drift import convergence, drift, signature, signatures
from polydrift.core.freeze import SpecNotDeposited, bundle, check_deposited
from polydrift.core.label import (
    FaultClass,
    consequence,
    fault_class,
    label_impl,
    unanimous_violations,
)
from polydrift.core.oracle import STATUS_AXIS, Outcome, compare_pair
from polydrift.core.record import Tier
from polydrift.core.repeat import flaky_triples
from polydrift.core.replay import Confusion, sample
from polydrift.core.spec import FixtureSpec

# --- repeat -----------------------------------------------------------------


def test_flaky_triples_needs_three_repeats():
    recs = [rec("a", step(x=f(2, 1)), repeat=i) for i in (1, 2)]
    with pytest.raises(ValueError):
        flaky_triples(recs)


def test_flaky_triples_finds_self_disagreement():
    recs = [rec("a", step(x=f(2, 1), t=f(3, i)), repeat=i) for i in (1, 2, 3)]
    recs += [rec("b", step(x=f(2, 1), t=f(3, 0)), repeat=i) for i in (1, 2, 3)]
    rep = flaky_triples(recs)
    assert rep.flaky == {("a", "F1", "t")}
    assert rep.flake_rate("a") == 1.0 and rep.flake_rate("b") == 0.0
    assert rep.representative[("a", "F1")].repeat == 1


def test_flaky_status():
    recs = [rec("a", step(), repeat=1), rec("a", step("silent"), repeat=2), rec("a", step(), repeat=3)]
    assert ("a", "F1", STATUS_AXIS) in flaky_triples(recs).flaky


# --- labels -----------------------------------------------------------------


def _fixture(refs, tags=()):
    return FixtureSpec(id="F1", cell="test", spec_refs=tuple(refs), tags=tuple(tags))


def test_violation_when_one_side_breaks_must():
    fx = _fixture([{"clause": "C1", "keyword": "MUST", "axis": "decision_class", "allowed": ["fix-request"]}])
    a = rec("a", step(decision_class=f(1, "fix-request")))
    b = rec("b", step(decision_class=f(1, "proceed")))
    pr = compare_pair(a, b)
    assert fault_class(pr, a, b, fx) == FaultClass.VIOLATION
    assert label_impl(b, fx).verdict == "violation"
    assert label_impl(a, fx).verdict == "conforms"


def test_deviation_for_should():
    fx = _fixture([{"clause": "C2", "keyword": "SHOULD", "axis": "x", "allowed": [1]}])
    a, b = rec("a", step(x=f(2, 1))), rec("b", step(x=f(2, 2)))
    assert fault_class(compare_pair(a, b), a, b, fx) == FaultClass.DEVIATION


def test_permitted_when_both_inside():
    fx = _fixture([{"clause": "C3", "keyword": "MAY", "axis": "x", "allowed": [1, 2]}])
    a, b = rec("a", step(x=f(2, 1))), rec("b", step(x=f(2, 2)))
    assert fault_class(compare_pair(a, b), a, b, fx) == FaultClass.PERMITTED


def test_unspecified_when_no_clause_constrains_axis():
    fx = _fixture([{"clause": "C3", "keyword": "MUST", "axis": "x", "allowed": [1]}])
    a = rec("a", step(x=f(2, 1), y=f(3, "p")))
    b = rec("b", step(x=f(2, 1), y=f(3, "q")))
    assert fault_class(compare_pair(a, b), a, b, fx) == FaultClass.UNSPECIFIED


def test_conflicting_clauses_are_unspecified():
    fx = _fixture(
        [
            {"clause": "A", "keyword": "MUST", "axis": "x", "allowed": [1]},
            {"clause": "B", "keyword": "MUST", "axis": "x", "allowed": [2]},
        ]
    )
    a, b = rec("a", step(x=f(2, 1))), rec("b", step(x=f(2, 2)))
    assert fault_class(compare_pair(a, b), a, b, fx) == FaultClass.UNSPECIFIED


def test_artefact_and_flaky_are_harness_artefacts():
    fx = _fixture([])
    a, b = rec("a", step(x=f(2, 1))), rec("b", step(x=f(2, 2)))
    assert fault_class(compare_pair(a, b), a, b, fx, artefact=True) == FaultClass.HARNESS_ARTEFACT
    fl = compare_pair(a, b, flaky={("a", "F1", STATUS_AXIS)})
    assert fault_class(fl, a, b, fx) == FaultClass.HARNESS_ARTEFACT


def test_unanimous_violation_without_divergence():
    fx = _fixture([{"clause": "C1", "keyword": "MUST", "axis": "decision_class", "allowed": ["fix-request"]}])
    recs = [rec(n, step(decision_class=f(1, "proceed"))) for n in "abc"]
    assert compare_pair(recs[0], recs[1]).outcome == Outcome.AGREEMENT
    assert unanimous_violations(recs, fx) == ["C1"]


def test_status_predicate_and_gating():
    fx = _fixture([{"clause": "C9", "keyword": "MUST", "axis": STATUS_AXIS, "allowed": ["responded"]}])
    assert label_impl(rec("a", step("crashed")), fx).verdict == "violation"
    fx2 = _fixture([{"clause": "C8", "keyword": "MUST", "axis": "y", "step": 2, "allowed": ["ok"]}])
    gated = rec("a", step("silent"), step(y=f(2, "ok")))
    assert label_impl(gated, fx2).verdict == "violation"  # y is undefined after silence


def test_consequence_security_and_interop():
    fx = _fixture(
        [{"clause": "C5", "keyword": "MAY", "axis": "box", "step": "end", "allowed": ["quarantine", "inbox"], "security": "control"}],
        tags=["security:control"],
    )
    fx_strict = _fixture(
        [{"clause": "C5", "keyword": "MAY", "axis": "box", "step": "end", "allowed": ["quarantine"], "security": "control"}],
        tags=["security:control"],
    )
    a = rec("a", step(decision_class=f(1, "proceed")), end={"box": f(2, "quarantine")})
    b = rec("b", step(decision_class=f(1, "proceed")), end={"box": f(2, "inbox")})
    pr = compare_pair(a, b)
    assert fault_class(pr, a, b, fx) == FaultClass.PERMITTED
    assert consequence(pr, a, b, fx).primary == "interoperability-relevant"
    c = consequence(pr, a, b, fx_strict)
    assert c.security and c.primary == "security-relevant"
    # permitted (MAY) yet security-relevant: the two dimensions are independent
    assert fault_class(pr, a, b, fx_strict) == FaultClass.PERMITTED


# --- drift ------------------------------------------------------------------


def test_drift_signature_catches_polarity_flip():
    base = compare_pair(rec("r1", step(aa=f(3, False))), rec("r2", step(aa=f(3, True))))
    pin = compare_pair(rec("r1", step(aa=f(3, True))), rec("r2", step(aa=f(3, False))))
    d = drift("r1", "v1", signatures([base], "r1"), "v2", signatures([pin], "r1"))
    assert d.counts() == {"introduced": 1, "fixed": 1, "persistent": 0}


def test_signature_is_oriented_from_impl_side():
    pr = compare_pair(rec("a", step(x=f(2, 1))), rec("b", step(x=f(2, 2))))
    sa, sb = signature(pr, "a"), signature(pr, "b")
    assert sa[1] == "b" and sb[1] == "a"
    assert sa[3][0][2:] == (1, 2) and sb[3][0][2:] == (2, 1)


def test_convergence():
    peers = [rec(n, step(x=f(2, 1))) for n in "bcd"]
    old = rec("a", step(x=f(2, 2)), version="1")
    new = rec("a", step(x=f(2, 1)), version="2")
    assert convergence(old, new, peers) == "converged"
    assert convergence(new, old, peers) == "diverged"
    assert convergence(old, old, peers) == "unchanged"


# --- baselines --------------------------------------------------------------


def test_raw_oracle_counts_presentation_and_errors():
    a = rec("a", step(order=f(4, "x", raw="x")))
    b = rec("b", step(order=f(4, "x", raw="X")))
    assert compare_pair(a, b).outcome == Outcome.AGREEMENT
    assert raw_oracle(a, b) == "diverge"
    assert raw_oracle(a, rec("b", step("refused-input"))) == "error"


def test_coarse_oracle_uses_native_code_only():
    a = rec("a", step(native_code=f(3, "403", raw="403"), body=f(2, "p")))
    b = rec("b", step(native_code=f(3, "403", raw="403"), body=f(2, "q")))
    assert coarse_oracle(a, b) == "agree"
    assert compare_pair(a, b).grade == Tier.T2


# --- replay -----------------------------------------------------------------


def test_replay_sample_strata_and_determinism():
    pairs = []
    for i in range(80):
        pairs.append(compare_pair(rec("a", step(x=f(3, i)), fixture=f"F{i}"), rec("b", step(x=f(3, -1)), fixture=f"F{i}")))
    for i in range(10):
        pairs.append(compare_pair(rec("a", step(x=f(1, i)), fixture=f"G{i}"), rec("b", step(x=f(1, -1)), fixture=f"G{i}")))
    for i in range(60):
        pairs.append(compare_pair(rec("a", step(x=f(4, i)), fixture=f"H{i}"), rec("b", step(x=f(4, -1)), fixture=f"H{i}")))
    s1, s2 = sample(pairs, seed=1), sample(pairs, seed=1)
    assert len(s1.t0_t2) == 10 and len(s1.t3) == 50 and len(s1.t4_only) == 50
    assert [p.fixture for _, p in s1.all()] == [p.fixture for _, p in s2.all()]


def test_confusion():
    c = Confusion()
    c.add(True, True)
    c.add(True, False)
    c.add(False, True)
    c.add(False, False)
    assert c.precision == 0.5 and c.recall == 0.5


# --- freeze -----------------------------------------------------------------


def test_freeze_gate(tmp_path):
    cell = tmp_path / "sip"
    (cell / "fixtures").mkdir(parents=True)
    (cell / "tiers.yaml").write_text("axes: {}\n")
    (cell / "fixtures" / "F1.yaml").write_text("id: F1\n")
    spec = bundle(cell)
    deposits = tmp_path / "deposits.json"
    with pytest.raises(SpecNotDeposited):
        check_deposited(spec, deposits)
    deposits.write_text(json.dumps({"sip": [{"spec_hash": spec.spec_hash, "doi": "10.5281/zenodo.0"}]}))
    assert check_deposited(spec, deposits)["doi"] == "10.5281/zenodo.0"
    (cell / "tiers.yaml").write_text("axes: {x: 1}\n")
    with pytest.raises(SpecNotDeposited):
        check_deposited(bundle(cell), deposits)


def test_freeze_hash_ignores_line_endings(tmp_path):
    for name, text in (("a", b"axes: {}\n"), ("b", b"axes: {}\r\n")):
        d = tmp_path / name / "sip"
        d.mkdir(parents=True)
        (d / "tiers.yaml").write_bytes(text)
    assert bundle(tmp_path / "a" / "sip").spec_hash == bundle(tmp_path / "b" / "sip").spec_hash


# --- canon ------------------------------------------------------------------


def test_alpha_rename_is_consistent_not_deleting():
    r = AlphaRenamer()
    pat = re.compile(r"tag=([\w-]+)")
    a = r.rename_in("From: <sip:a>;tag=abc", pat)
    b = r.rename_in("To: <sip:b>;tag=xyz", pat)
    c = r.rename_in("echo;tag=abc", pat)
    assert (a, b, c) == ("From: <sip:a>;tag=#1", "To: <sip:b>;tag=#2", "echo;tag=#1")


def test_lws_and_digest():
    assert collapse_lws("SIP/2.0/UDP\r\n  host") == "SIP/2.0/UDP host"
    assert digest_after(normalise_line_endings, "v=0\r\n") == digest_after(normalise_line_endings, "v=0  \n\n")


def test_shared_breach_on_agreeing_axis_does_not_label_the_divergence():
    fx = _fixture([{"clause": "C1", "keyword": "MUST", "axis": "x", "allowed": [1]}])
    a = rec("a", step(x=f(1, 2), y=f(3, "p")))
    b = rec("b", step(x=f(1, 2), y=f(3, "q")))
    assert fault_class(compare_pair(a, b), a, b, fx) == FaultClass.UNSPECIFIED
    assert unanimous_violations([a, b], fx) == ["C1"]


def test_shared_breach_on_differing_axis_is_a_violation():
    fx = _fixture([{"clause": "C1", "keyword": "MUST", "axis": "x", "allowed": [1]}])
    a, b = rec("a", step(x=f(1, 2))), rec("b", step(x=f(1, 3)))
    assert fault_class(compare_pair(a, b), a, b, fx) == FaultClass.VIOLATION


def test_breach_on_flaky_axis_does_not_label():
    fx = _fixture([{"clause": "C1", "keyword": "MUST", "axis": "x", "allowed": [1]}])
    a = rec("a", step(x=f(1, 2), y=f(3, "p")))
    b = rec("b", step(x=f(1, 1), y=f(3, "q")))
    pr = compare_pair(a, b, flaky={("a", "F1", "x")})
    assert fault_class(pr, a, b, fx) == FaultClass.UNSPECIFIED


def test_excludes_predicate_labels_must_not_include():
    spec = FixtureSpec.from_dict({"id": "F1", "cell": "test", "spec_refs": [
        {"req_id": "R1", "keyword": "MUST", "axis": "x", "step": 1, "excludes": ["root"]}]})
    good = rec("a", step(x=f(2, ("a", "b"))))
    bad = rec("b", step(x=f(2, ("a", "root"))))
    none = rec("c", step(x=f(2, None)))
    assert label_impl(good, spec).verdict == "conforms"
    assert label_impl(bad, spec).verdict == "violation"
    assert label_impl(none, spec).verdict == "conforms"
    scalar = rec("d", step(x=f(2, "root")))
    assert label_impl(scalar, spec).verdict == "violation"


def _verdict(ref, value, step2=None):
    spec = FixtureSpec.from_dict({"id": "F1", "cell": "test", "spec_refs": [{"req_id": "R", "keyword": "MUST", **ref}]})
    steps = [step(x=f(2, value))] + ([step(x=f(2, step2))] if step2 is not None else [])
    return label_impl(rec("a", *steps), spec).verdict


def test_excludes_predicates():
    assert _verdict({"axis": "x", "step": 1, "excludes": ["root"]}, ("a", "b")) == "conforms"
    assert _verdict({"axis": "x", "step": 1, "excludes": ["root"]}, ("a", "root")) == "violation"
    assert _verdict({"axis": "x", "step": 1, "excludes": ["root"]}, "root") == "violation"
    assert _verdict({"axis": "x", "step": 1, "excludes": ["root"]}, None) == "conforms"
    assert _verdict({"axis": "x", "step": 1, "excludes_re": " "}, ("ldap://h/a%20b",)) == "conforms"
    assert _verdict({"axis": "x", "step": 1, "excludes_re": " "}, ("ldap://h/a b",)) == "violation"


def test_order_and_same_as_predicates():
    o = {"axis": "x", "step": 1, "order": ["Done", "Bind"]}
    assert _verdict(o, ("Entry", "Done", "Bind")) == "conforms"
    assert _verdict(o, ("Bind",)) == "conforms"
    assert _verdict(o, ("Bind", "Done")) == "violation"
    s = {"axis": "x", "step": 2, "same_as": 1}
    assert _verdict(s, (7,), (7,)) == "conforms"
    assert _verdict(s, (7,), (48,)) == "violation"


def test_includes_predicate():
    i = {"axis": "x", "step": 1, "includes": ["CANCEL"]}
    assert _verdict(i, ("INVITE", "CANCEL")) == "conforms"
    assert _verdict(i, ("INVITE",)) == "violation"
    assert _verdict(i, None) == "violation"

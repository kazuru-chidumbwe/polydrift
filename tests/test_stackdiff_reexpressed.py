"""The StackDiff oracle tests (dns-stack-diff-harness classifier/oracle_test.py),
re-expressed against core/ through the DNS adapter.

Each test keeps the scenario and states what the shared model decides. Where
the model's verdict differs from StackDiff's, the docstring says why.
"""

from polydrift.cells.dns.adapter import load_spec, to_record
from polydrift.core.drift import drift, signatures
from polydrift.core.oracle import STATUS_AXIS, Outcome, compare_pair
from polydrift.core.record import Tier

SPEC = load_spec()


def pair(left, right, names=("unbound", "coredns_fwd")):
    a = to_record(left, names[0], "P", spec=SPEC)
    b = to_record(right, names[1], "P", spec=SPEC)
    return compare_pair(a, b)


def ok(**kw):
    base = {"rcode": "NOERROR", "answers": ["203.0.113.10"], "aa": False, "ra": True, "error": None}
    base.update(kw)
    return base


def dead(error):
    return {"rcode": None, "answers": [], "aa": None, "ra": None, "error": error}


def test_normalize_answers_is_kappa():
    r = pair(ok(answers=["203.0.113.10."]), ok(answers=["203.0.113.10"]))
    assert r.outcome == Outcome.AGREEMENT


def test_normalize_additional_is_kappa():
    r = pair(
        ok(additional=["Ns.Evil.Test.|198.51.100.66"]),
        ok(additional=["ns.evil.test.|198.51.100.66"]),
    )
    assert r.outcome == Outcome.AGREEMENT


def test_glue_cache_accept_divergence():
    """Glue acceptance is end-state cache content (T2); AA stays T3."""
    r = pair(
        ok(answers=["203.0.113.20"], additional=[], glue_cache_accept=False),
        ok(answers=["203.0.113.20"], additional=[], aa=True, glue_cache_accept=True),
    )
    assert r.outcome == Outcome.DIVERGENCE and r.grade == Tier.T2
    axes = {d.axis for d in r.diffs}
    assert {"glue_cache_accept", "aa"} <= axes
    assert [d.step for d in r.diffs if d.axis == "glue_cache_accept"] == ["end"]


def test_agree():
    assert pair(ok(), ok()).outcome == Outcome.AGREEMENT


def test_flag_only_divergence_is_t3():
    """StackDiff's smoke axes ignored AA/RA; under the model they are a T3
    divergence: not interoperability-relevant, still recorded."""
    r = pair(ok(), ok(aa=True, ra=False))
    assert r.outcome == Outcome.DIVERGENCE and r.grade == Tier.T3


def test_rcode_divergence():
    r = pair(ok(), ok(rcode="SERVFAIL", answers=[]))
    assert r.outcome == Outcome.DIVERGENCE and r.grade == Tier.T1
    assert "decision_class" in {d.axis for d in r.diffs}


def test_both_dead_is_not_agreement():
    """StackDiff flagged all_null; the model makes it a co-failure (both silent)."""
    r = pair(dead("timeout"), dead("dig exit 9"))
    assert r.outcome == Outcome.CO_FAILURE
    assert r.outcome != Outcome.AGREEMENT


def test_one_side_responds_is_liveness_divergence():
    r = pair(ok(rcode="SERVFAIL", answers=[]), dead("dig exit 9"), names=("unbound", "dnsmasq"))
    assert r.outcome == Outcome.DIVERGENCE and r.grade == Tier.T0


def test_failure_classes():
    """StackDiff split timeout from 'dig exit 9'. dig exit 9 means no reply
    from the server, which is the same liveness status as a timeout (silent),
    so the model gives co-failure. A failure of the measuring tool itself
    (dig exit 1/10) is a harness fault and the pair is incomparable."""
    assert pair(dead("timeout"), dead("dig exit 9")).outcome == Outcome.CO_FAILURE
    assert pair(dead("dig exit 1"), dead("dig exit 9")).outcome == Outcome.INCOMPARABLE
    r = pair(dead("timeout"), dead("short response"))
    assert r.outcome == Outcome.DIVERGENCE and r.grade == Tier.T0


def test_same_failure_class_different_detail_is_co_failure():
    """StackDiff: zero divergences. Model: co-failure, reported apart from agreement."""
    r = pair(dead("timeout after 5s"), dead("timeout after 2s"))
    assert r.outcome == Outcome.CO_FAILURE and not r.diffs


def test_delta_matches_published_glue_result():
    """August glue pin: one new divergence on ADDITIONAL against the baseline."""
    base = pair(
        {**ok(), "aa": True, "additional": [], "glue_cache_accept": False},
        {**ok(), "additional": [], "glue_cache_accept": False},
        names=("dnsmasq", "unbound"),
    )
    pin = pair(
        {**ok(answers=["203.0.113.20"]), "aa": True, "additional": ["ns.evil.test|198.51.100.66"], "glue_cache_accept": False},
        {**ok(answers=["203.0.113.20"]), "additional": [], "glue_cache_accept": False},
        names=("dnsmasq", "unbound"),
    )
    assert {d.axis for d in base.diffs} == {"aa"}
    assert {d.axis for d in pin.diffs} == {"aa", "additional"}
    d = drift("dnsmasq", "baseline", signatures([base], "dnsmasq"), "pin", signatures([pin], "dnsmasq"))
    # The whole-pair signature changes, so the old one is fixed and a new one introduced.
    assert d.counts() == {"introduced": 1, "fixed": 1, "persistent": 0}
    (new_sig,) = d.introduced
    assert "additional" in {axis for (_s, axis, _l, _r) in new_sig[3]}


def test_delta_catches_polarity_flip():
    base = pair(ok(), ok(aa=True), names=("r1", "r2"))
    pin = pair(ok(aa=True), ok(), names=("r1", "r2"))
    d = drift("r1", "baseline", signatures([base], "r1"), "pin", signatures([pin], "r1"))
    assert d.counts()["introduced"] == 1, "polarity flip must not be masked"


def test_null_aware_gating():
    """SERVFAIL vs no reply: only the liveness status is compared."""
    r = pair(ok(rcode="SERVFAIL", answers=[]), {**dead("dig exit 9"), "aa": False, "ra": False}, names=("unbound", "dnsmasq"))
    assert r.grade == Tier.T0
    assert [d.axis for d in r.diffs] == [STATUS_AXIS]

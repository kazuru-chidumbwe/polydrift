"""Cell report helpers: family/version split and the Tier 2 drift restriction."""
from polydrift.core.oracle import AxisDiff, Outcome, PairResult
from polydrift.core.record import ObservationRecord, Status, StepRecord, Tier
from polydrift.report import at_tier, family_version, family_version_key


def rec(impl):
    return ObservationRecord(cell="x", impl=impl, fixture="f", repeat=1, steps=(StepRecord(status=Status.RESPONDED),))


def test_family_version():
    assert family_version(rec("nanomq-0.25.7")) == ("nanomq", "0.25.7")
    assert family_version(rec("pdns-recursor-5.3.14")) == ("pdns-recursor", "5.3.14")
    assert family_version(rec("hivemq-ce-2026.5")) == ("hivemq-ce", "2026.5")
    assert family_version_key("samba-ad-4.25.1") == "samba-ad"


def test_at_tier_drops_detail_only_divergence():
    d3 = AxisDiff(step=1, axis="connack_props", tier=Tier.T3, left=1, right=2)
    d1 = AxisDiff(step=2, axis="decision_class", tier=Tier.T1, left="proceed", right="stop")
    only3 = PairResult("f", "a", "b", Outcome.DIVERGENCE, grade=Tier.T3, diffs=[d3])
    mixed = PairResult("g", "a", "b", Outcome.DIVERGENCE, grade=Tier.T1, diffs=[d3, d1])
    out = at_tier([only3, mixed], Tier.T2)
    assert len(out) == 1 and out[0].fixture == "g" and [d.axis for d in out[0].diffs] == ["decision_class"]

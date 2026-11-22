"""Replay: deciding steps, and the strict and action levels (manuscript 3.6)."""

from polydrift.core.oracle import AxisDiff, Outcome, PairResult
from polydrift.core.record import Tier
from polydrift.core.replay import deciding_steps, replay_summary
from polydrift.core.replay_action import dns, ldap, mqtt


def _pair(outcome, grade, diffs=(), t4=()):
    return PairResult(fixture="f", left="a", right="b", outcome=outcome, grade=grade,
                      diffs=list(diffs), t4_diffs=list(t4))


def test_deciding_steps_are_the_steps_at_the_grade():
    p = _pair(Outcome.DIVERGENCE, Tier.T1, diffs=[AxisDiff(1, "info", Tier.T3, 1, 2),
                                                 AxisDiff(3, "decision_class", Tier.T1, "x", "y")])
    assert deciding_steps(p) == {3}
    q = _pair(Outcome.AGREEMENT, None, t4=[AxisDiff("end", "order", Tier.T4, 1, 2)])
    assert deciding_steps(q) == set() and deciding_steps(q, end_step=4) == {4}


def test_action_level_ignores_codes_within_failure_and_capabilities():
    a = {"events": [{"ev": "connack", "rc": 0, "sp": 0, "props": [33, 34]}], "server": {"requests": ["CONNECT"]}}
    b = {"events": [{"ev": "connack", "rc": 0, "sp": 0, "props": [34]}], "server": {"requests": ["CONNECT"]}}
    assert mqtt(a) == mqtt(b)
    c = {"events": [{"ev": "connack", "rc": 135, "sp": 0, "props": []}], "server": {"requests": ["CONNECT"]}}
    assert mqtt(a) != mqtt(c)
    l1 = {"ops": [{"op": "search", "result": 32, "error": "NO_SUCH_OBJECT"}], "server": {"requests": ["search"]}}
    l2 = {"ops": [{"op": "search", "result": 50, "error": "INSUFFICIENT_ACCESS"}], "server": {"requests": ["search"]}}
    assert ldap(l1) == ldap(l2)
    d1 = {"res_query": "ok", "answers": ["x 1 a", "x 1 b"], "queries": {"udp": 1}}
    d2 = {"res_query": "ok", "answers": ["x 1 b", "x 1 a"], "queries": {"udp": 1}}
    assert dns(d1) == dns(d2)


def test_summary_reports_both_levels():
    lines = []
    for side, rc in (("left", 32), ("right", 50)):
        lines.append({"stratum": "T3", "fixture": "f", "left": "a", "right": "b", "grade": 3, "step": 1,
                      "consumer": "libldap", "side": side,
                      "result": {"ops": [{"op": "search", "result": rc}], "server": {"requests": ["search"]}}})
    s = replay_summary(lines, [("T3", None)], [], ("libldap",), 1, cell="ldap")
    # strict: a T3 difference is predicted visible, and the code difference is visible (tp)
    assert s["levels"]["strict"]["confusion"]["libldap"]["tp"] == 1
    assert s["levels"]["action"]["confusion"]["libldap"]["tn"] == 1
    assert s["confusion"] == s["levels"]["strict"]["confusion"]


def test_permutation_check_reports_frozen_agreement_and_share():
    from polydrift.core.replay import permutation_check

    lines = []
    # two pairs: a T1 pair the consumer acts on, a T3 pair it does not
    for fx, stratum, axes, rl, rr in (("p1", "T0-T2", [["decision_class", 1, False]], 1, 2),
                                      ("p2", "T3", [["native_codes", 3, False]], 1, 1)):
        for side, v in (("left", rl), ("right", rr)):
            lines.append({"stratum": stratum, "fixture": fx, "left": "a", "right": "b", "grade": None, "axes": axes,
                          "step": 1, "consumer": "c", "side": side, "result": {"v": v}})
    r = permutation_check(lines, {"decision_class": 1, "native_codes": 3}, lambda x: x, seed=1, n=200)
    assert r["frozen_agreement"] == 1.0 and r["cases"] == 2
    assert 0.0 < r["share_at_least_frozen"] <= 1.0  # the identity shuffle also reaches 1.0


def test_lineage_relatives_cast_one_vote_in_scope():
    from core_helpers import f, rec, step
    from polydrift.core.drift import lineage_groups, majority_key

    def r(impl, v):
        return rec(impl, step(decision_class=f(1, v)))

    peers = [r("emqx", "x"), r("vernemq", "x"), r("mosquitto", "y"), r("hivemq-ce", "y"), r("nanomq", "z")]
    fam = lambda x: x.impl  # noqa: E731
    topic = lineage_groups("mqtt", [{"native": "MQTT-4.7.1-1"}])
    other = lineage_groups("mqtt", [{"native": "MQTT-3.1.2-1"}])
    assert topic and not other
    # on a topic-filter fixture EMQX and VerneMQ count once, so "y" (two votes) wins against "x" (one)
    assert majority_key(peers, Tier.T2, groups=topic, family=fam) == majority_key([peers[2]], Tier.T2)
    # elsewhere they count twice and tie with "y"
    assert majority_key(peers, Tier.T2, groups=other, family=fam) is None


def test_strict_level_tests_t4_and_same_project_pairs_are_apart():
    def line(stratum, left, right, consumer, side, v):
        return {"stratum": stratum, "fixture": "f", "left": left, "right": right, "grade": None, "step": 1,
                "consumer": consumer, "side": side, "result": {"ops": [{"op": "search", "result": v}],
                                                               "server": {"requests": ["search"]}}}
    lines = []
    # a T4-only agreement the consumer sees: a false agreement at the strict level (fn)
    lines += [line("T4-only", "389ds-3.1", "lldap-0.6.3", "ldap3", s, v) for s, v in (("left", 0), ("right", 32))]
    # a T0-T2 pair involving OpenLDAP, replayed into libldap (same project): reported apart
    lines += [line("T0-T2", "openldap-2.6.10", "lldap-0.6.3", "libldap", s, v) for s, v in (("left", 0), ("right", 32))]
    s = replay_summary(lines, [("T4-only", None), ("T0-T2", None)], [], ("libldap", "ldap3"), 1, cell="ldap")
    st = s["levels"]["strict"]
    assert st["confusion"]["ldap3"]["fn"] == 1
    assert st["confusion_same_project"]["libldap"]["tp"] == 1
    assert st["confusion_independent"]["libldap"]["tp"] == 0

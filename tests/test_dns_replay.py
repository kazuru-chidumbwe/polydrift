"""DNS consumer replay helpers (polydrift/cells/dns/replay.py)."""

from polydrift.cells.dns import replay
from polydrift.core.oracle import AxisDiff, Outcome, PairResult
from polydrift.core.record import Tier

FX = {"steps": [{"op": "upstream", "match": "x"}, {"qname": "a.pd.pdlab", "qtype": "A"},
                {"qname": "a.pd.pdlab", "qtype": "TYPE65280"}, {"op": "raw", "build": "iquery", "qname": "a"},
                {"qname": "b.pd.pdlab", "qtype": "A", "qclass": "CH"}]}


def pair(*steps, tier=Tier.T2):
    return PairResult(fixture="F", left="a-1", right="b-1", outcome=Outcome.DIVERGENCE, grade=tier,
                      diffs=[AxisDiff(step=k, axis="answers", tier=tier, left=1, right=2) for k in steps],
                      t4_diffs=[AxisDiff(step="end", axis="x", tier=Tier.T4, left=1, right=2)])


def test_qtype_numbers():
    assert replay.qtype_number("aaaa") == 28 and replay.qtype_number("TYPE65280") == 65280
    assert replay.qtype_number("NOPE") is None


def test_only_plain_in_queries_are_replayed():
    assert replay.pair_steps(pair(1, 2, 3, 4, 5), FX) == [2, 3]


def test_side_payload():
    assert replay.side_payload({"wire": "ab", "rcode": "NOERROR"}) == ("answer", "ab")
    assert replay.side_payload({"error": "timeout"}) == ("silent", None)
    assert replay.side_payload({"rcode": "NOERROR"}) == ("missing", None)
    assert replay.side_payload(None) == ("missing", None)


def test_summary_confusion_and_disagreement():
    def ln(stratum, consumer, side, result, fixture="F"):
        return {"stratum": stratum, "fixture": fixture, "left": "a-1", "right": "b-1", "step": 2,
                "consumer": consumer, "side": side, "result": result}

    lines = [ln("T0-T2", "glibc", "left", {"r": 1}), ln("T0-T2", "glibc", "right", {"r": 2}),
             ln("T0-T2", "musl", "left", {"r": 1}), ln("T0-T2", "musl", "right", {"r": 1}),
             ln("T4-only", "glibc", "left", {"r": 1}, "G"), ln("T4-only", "glibc", "right", {"r": 1}, "G")]
    s = replay.summarise(lines, [("T0-T2", None), ("T4-only", None)], ["H a-1 b-1"])
    assert s["confusion"]["glibc"]["tp"] == 1 and s["confusion"]["glibc"]["tn"] == 1
    assert s["confusion"]["musl"]["fp"] == 1
    assert s["consumer_disagreements"] == [["F", "a-1", "b-1", "T0-T2"]]
    assert s["not_replayable"] == 1 and s["sampled"]["T0-T2"] == 1

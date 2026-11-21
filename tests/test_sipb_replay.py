"""sipb consumer replay helpers (polydrift/cells/sipb/replay.py)."""

from polydrift.cells.sipb import replay
from polydrift.core.replay import replay_summary

FX = {"steps": [{"op": "send", "from": "bob", "msg": "REGISTER sip:{domain} SIP/2.0\n\n", "window_ms": 1000},
                {"op": "send", "from": "alice", "msg": "INVITE sip:bob@{domain} SIP/2.0\n\n", "window_ms": 600},
                {"op": "wait", "ms": 500},
                {"op": "reply", "from": "bob", "code": 486, "window_ms": 1200},
                {"op": "ack", "from": "alice", "window_ms": 600},
                {"op": "send", "from": "alice", "msg": "OPTIONS sip:bob@{domain} SIP/2.0\n\n"}]}


def obs(alice_got=None):
    g = alice_got or {}
    return [{"step": 1, "actor": "bob", "got": {"alice": []}},
            {"step": 2, "actor": "alice", "got": {"alice": g.get(2, [{"kind": "resp", "wire": "MTAw"}])}},
            {"step": 4, "actor": "bob", "got": {"alice": g.get(4, [{"kind": "resp", "wire": "NDg2"}])}},
            {"step": 5, "actor": "alice", "got": {"alice": []}},
            {"step": 6, "actor": "alice", "got": {"alice": []}}]


def test_prefix_plan_maps_record_steps_to_fixture_steps():
    p = replay.prefix_plan(FX, obs(), 4)
    assert p["uri"] == f"sip:bob@{replay.SERVER_IP}"
    assert [(s["actor"], s["method"], s["wire"]) for s in p["steps"]] == [
        (False, "", []), (True, "INVITE", ["MTAw"]), (False, "", ["NDg2"]), (True, "ACK", [])]
    assert 3000 <= p["wait_ms"] <= 15000


def test_not_replayable_cases():
    assert replay.prefix_plan(FX, obs(), 1) is None                     # alice has not called yet
    assert replay.prefix_plan(FX, obs(), 5) is None                     # a second request from alice
    assert replay.prefix_plan(FX, obs({4: [{"kind": "req", "wire": "x"}]}), 4) is None   # a request to alice
    assert replay.prefix_plan(FX, obs({2: [{"kind": "resp"}]}), 2) is None              # no bytes recorded


def test_reasons_are_data_not_compared():
    a = replay.split_result(['{"ev":"state","code":486,"reason":"Busy Here"}'])
    b = replay.split_result(['{"ev":"state","code":486,"reason":"Busy"}'])
    assert a["events"] == b["events"] and a["data"] != b["data"]
    lines = [{"stratum": "T4-only", "fixture": "F", "left": "x-1", "right": "y-1", "step": 2, "consumer": "pjsip",
              "side": s, "result": r} for s, r in (("left", a), ("right", b))]
    s = replay_summary(lines, [("T4-only", None)], [], ("pjsip",), 1)
    assert s["confusion"]["pjsip"]["tn"] == 1


def test_non_invite_requests_are_replayable_once():
    fx = {"steps": [{"op": "send", "from": "alice", "msg": "REGISTER sip:lab.test SIP/2.0\nTo: <sip:bob@lab.test>\n\n"},
                    {"op": "send", "from": "alice", "msg": "REGISTER sip:lab.test SIP/2.0\nTo: <sip:bob@lab.test>\n\n"}]}
    ob = [{"step": 1, "actor": "alice", "got": {"alice": [{"kind": "resp", "wire": "QQ=="}]}},
          {"step": 2, "actor": "alice", "got": {"alice": [{"kind": "resp", "wire": "QQ=="}]}}]
    p = replay.prefix_plan(fx, ob, 1)
    assert p["method"] == "REGISTER" and p["uri"] == f"sip:bob@{replay.SERVER_IP}"
    assert replay.prefix_plan(fx, ob, 2) is None  # a second request of alice's is not one library call

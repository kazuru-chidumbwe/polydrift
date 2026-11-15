"""MQTT consumer replay helpers and replay server (polydrift/cells/mqtt)."""

import importlib.util
import json
from pathlib import Path

from polydrift.cells.mqtt import replay

_p = Path(__file__).resolve().parents[1] / "polydrift" / "cells" / "mqtt" / "lab" / "replay_server.py"
_spec = importlib.util.spec_from_file_location("mqtt_replay_server", _p)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

FX = {"steps": [
    {"client": "b", "op": "connect", "args": {"client_id": "{C}b", "properties": {"session_expiry_interval": 30}}},
    {"client": "b", "op": "subscribe", "args": {"packet_id": 1, "filters": [{"topic": "{T}x", "qos": 1}]}},
    {"client": "a", "op": "connect", "args": {"client_id": "{C}a", "will": {"topic": "w"}}},
    {"client": "a", "op": "publish", "args": {"topic": "{T}x", "payload": "hi", "qos": 1}},
    {"client": "b", "op": "hex", "hex": "c000"}]}


def test_client_script_lines_and_limits():
    t, c = replay.prefixes("F", 2)
    s = replay.client_script(FX, "b", 4, t, c)
    assert s == ["connect 5 1 60 pd-F-r2-b sei=30", "subscribe 1 0 0 0 pd/F/r2/x"]
    assert replay.client_script(FX, "b", 5, t, c) is None          # a raw packet step
    assert replay.client_script(FX, "a", 4, t, c) is None          # a will cannot be set the same way


def test_server_steps_mark_actor_and_carry_wire():
    obs = [{"step": i, "clients": {"b": {"wire": [f"0{i}"], "closed_now": i == 4}}} for i in (1, 2, 3, 4)]
    st = replay.server_steps(FX, obs, "b", 4)
    assert [x["actor"] for x in st] == [True, True, False, False]
    assert st[1]["type"] == "SUBSCRIBE" and st[3]["closed"]


def test_canon_and_pair_steps():
    t, c = replay.prefixes("F", 3)
    assert replay.canon([{"topic": t + "x", "cid": c + "b"}], t, c) == [{"cid": "{C}b", "topic": "{T}x"}]

    class D:
        def __init__(self, step):
            self.step = step

    class P:
        diffs = [D(2), D("end")]
        t4_diffs = []

    assert replay.pair_steps(P(), 5) == [2, 5]


def test_server_plays_reply_then_other_clients_deliveries():
    suback = bytes.fromhex("900400070000")
    deliver = bytes.fromhex("30080003742f78006869")
    srv.STEP_GAP_S = 0
    srv.control("LOAD " + json.dumps({"steps": [
        {"actor": True, "type": "CONNECT", "wire": ["2003000000"], "closed": False},
        {"actor": True, "type": "SUBSCRIBE", "wire": [suback.hex()], "closed": False},
        {"actor": False, "type": "", "wire": [deliver.hex()], "closed": False},
        {"actor": True, "type": "DISCONNECT", "wire": [], "closed": False}]}))
    out, close = srv.play(bytes.fromhex("100d00044d5154540502003c000000"))
    assert [p.hex() for _, p in out] == ["2003000000"] and not close
    sub = bytes.fromhex("8209002a0000037432f7800")[:0] or bytes([0x82, 9, 0, 42, 0, 0, 3]) + b"t/x" + b"\x00"
    out, _ = srv.play(sub)
    assert out[0][1] == bytes.fromhex("9004002a0000") and out[1][1] == deliver
    assert srv.play(bytes.fromhex("4002002a"))[0] == []            # a PUBACK from the consumer is not a request
    c = json.loads(srv.control("COUNT"))
    assert c["requests"] == ["CONNECT", "SUBSCRIBE"] and c["unmatched"] == 0

"""JMAP consumer replay helpers (polydrift/cells/jmap/replay.py)."""

import base64

from polydrift.cells.jmap import replay

FX = {"steps": [{"op": "call", "calls": [["Mailbox/set", {"accountId": "{acct}", "create": {"m1": {"name": "{U}a"}}}, "c0"]]},
                {"op": "call", "calls": [["Mailbox/get", {"accountId": "{acct}", "ids": ["{id:m1}"]}, "c0"]]},
                {"op": "raw", "body": "{"}]}


def test_consumer_steps_keep_calls_and_stop_at_raw():
    s = replay.consumer_steps(FX, 2, "F")
    assert s[0][0][1]["create"]["m1"]["name"] == replay.unique("F") + "a"
    assert s[0][0][1]["accountId"] == "{acct}" and s[1][0][1]["ids"] == ["{id:m1}"]
    assert replay.consumer_steps(FX, 3, "F") is None


def test_server_steps_need_bytes():
    obs = [{"step": 1, "http": 200, "wire_ctype": "application/json", "wire": base64.b64encode(b"{}").decode()}]
    assert replay.server_steps(obs, 1) == [{"http": 200, "ctype": "application/json", "body_b64": "e30="}]
    assert replay.server_steps(obs, 2) is None


def test_volatile_values_and_canon():
    doc = {"methodResponses": [["Mailbox/set", {"accountId": "A9", "newState": "s7", "created": {"m1": {"id": "x42"}},
                                                "updated": {"x40": None}}, "c0"]],
           "sessionState": "q1"}
    vol = replay.volatile_values(doc)
    assert {"A9", "s7", "x42", "x40", "q1"} <= vol and "m1" not in vol
    left = [{"responses": [{"data": {"accountId": "A9", "list": [{"id": "x42", "name": "n"}]}}]}]
    right = [{"responses": [{"data": {"accountId": "b", "list": [{"id": "Z", "name": "n"}]}}]}]
    assert replay.canon(left, vol) == replay.canon(right, {"b", "Z"})
    changed = [{"responses": [{"data": {"accountId": "b", "list": [{"id": "Z", "name": "m"}]}}]}]
    assert replay.canon(left, vol) != replay.canon(changed, {"b", "Z"})

"""sipb consumer-replay server: responses tied to the consumer's transaction, everything else as recorded."""

import base64
import importlib.util
import json
from pathlib import Path

_p = Path(__file__).resolve().parents[1] / "polydrift" / "cells" / "sipb" / "lab" / "replay_server.py"
_spec = importlib.util.spec_from_file_location("sipb_replay_server", _p)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

REQ = ("INVITE sip:bob@10.0.0.53 SIP/2.0\r\nVia: SIP/2.0/UDP 10.0.0.11:5070;rport;branch=z9hG4bKcons\r\n"
       "From: <sip:alice@10.0.0.11>;tag=ctag\r\nTo: <sip:bob@10.0.0.53>\r\nCall-ID: cons-1\r\nCSeq: 20 INVITE\r\n"
       "Content-Length: 0\r\n\r\n").encode()


def recorded(code: int, reason: str, extra_via: str = "") -> bytes:
    via = f"v: {extra_via}SIP/2.0/UDP 172.31.60.99:5070;branch=z9hG4bKabc;rport=5070\r\n"
    return (f"SIP/2.0 {code} {reason}\r\n{via}f: <sip:alice@lab.test>;tag=al1\r\nt: <sip:bob@lab.test>;tag=b1\r\n"
            "i: pd-1@lab\r\nCSeq: 1 INVITE\r\nServer: kamailio\r\nContent-Length: 0\r\n\r\n").encode()


def test_rewrite_sets_only_the_transaction_identifiers():
    lines, _ = srv.split_msg(REQ)
    out = srv.rewrite(recorded(486, "Busy Here"), lines).decode()
    assert out.startswith("SIP/2.0 486 Busy Here\r\n")
    assert "v: SIP/2.0/UDP 10.0.0.11:5070;rport;branch=z9hG4bKcons\r\n" in out   # compact name kept
    assert "f: <sip:alice@10.0.0.11>;tag=ctag\r\n" in out
    assert "t: <sip:bob@10.0.0.53>;tag=b1\r\n" in out
    assert "i: cons-1\r\n" in out and "CSeq: 20 INVITE\r\n" in out and "Server: kamailio\r\n" in out


def test_rewrite_keeps_a_proxy_via_left_in_the_response():
    lines, _ = srv.split_msg(REQ)
    out = srv.rewrite(recorded(200, "OK", "SIP/2.0/UDP 172.31.60.10:5060;branch=z9hG4bKpx, "), lines).decode()
    assert "172.31.60.10:5060;branch=z9hG4bKpx" in out and "z9hG4bKcons" in out


def test_play_matches_method_skips_ack_and_retransmissions():
    srv.STEP_GAP_S = 0
    b64 = lambda m: base64.b64encode(m).decode()  # noqa: E731
    srv.control("LOAD " + json.dumps({"steps": [
        {"actor": True, "method": "INVITE", "wire": [b64(recorded(100, "Trying"))]},
        {"actor": False, "method": "", "wire": [b64(recorded(486, "Busy Here"))]},
        {"actor": True, "method": "ACK", "wire": []}]}))
    out = srv.play(REQ)
    assert [m.split(b"\r\n")[0] for _, m in out] == [b"SIP/2.0 100 Trying", b"SIP/2.0 486 Busy Here"]
    assert srv.play(REQ) == []                                         # a retransmission
    assert srv.play(REQ.replace(b"INVITE sip", b"ACK sip").replace(b"20 INVITE", b"20 ACK")) == []
    c = json.loads(srv.control("COUNT"))
    assert c == {"requests": ["INVITE", "ACK"], "retransmissions": 1, "unmatched": 0}

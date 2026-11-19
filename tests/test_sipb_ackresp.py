"""The ackresp extractor and the response-to-ACK axis of uas_ringing_cancel (RFC 3261 17.2.1, SIP-ba004fd6)."""
from pathlib import Path

import yaml

from polydrift.cells.sipb.lab.driver import evaluate


def _resp(code, cseq):
    return {"kind": "resp", "code": code, "hdr": {"cseq": [cseq]}}


def test_ackresp_counts_a_response_to_the_ack():
    ctx = {"alice": [_resp(487, "1 INVITE"), _resp(405, "1 ACK")]}
    assert evaluate("ackresp:alice", ctx) == 1


def test_ackresp_ignores_a_retransmitted_487():
    # the element's own 487 retransmission inside the ACK window is not a response to the ACK
    ctx = {"alice": [_resp(487, "1 INVITE"), _resp(487, "1 INVITE")]}
    assert evaluate("ackresp:alice", ctx) == 0


def test_ackresp_empty_or_missing_inbox_is_zero():
    assert evaluate("ackresp:alice", {"alice": []}) == 0
    assert evaluate("ackresp:alice", {}) == 0


def test_ackresp_ignores_requests_and_case():
    ctx = {"alice": [{"kind": "req", "method": "ACK", "hdr": {"cseq": ["1 ACK"]}}, _resp(405, "1 ack")]}
    assert evaluate("ackresp:alice", ctx) == 1


def test_uas_ringing_cancel_judges_the_ack_on_x5():
    p = Path(__file__).resolve().parents[1] / "polydrift" / "cells" / "sipb" / "fixtures.yaml"
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    fx = (d.get("fixtures") or d)["uas_ringing_cancel"]
    step4 = fx["steps"][3]
    assert step4["op"] == "ack" and step4["extract"]["x5"] == "ackresp:alice"
    refs = [r for r in fx["spec_refs"] if r["req_id"] == "SIP-ba004fd6"]
    assert len(refs) == 1 and refs[0]["axis"] == "x5" and refs[0]["step"] == 4 and refs[0]["allowed"] == [0]
    assert "basis" not in refs[0]

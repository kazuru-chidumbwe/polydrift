"""sipb repeat stability: probes set aside, retransmissions collapsed, random tokens masked."""

import importlib.util
from pathlib import Path

from polydrift.cells.sipb import adapter

_DRV = Path(adapter.__file__).resolve().parent / "lab" / "driver.py"
_spec = importlib.util.spec_from_file_location("sipb_driver", _DRV)
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)


def _req(method, branch="z9hG4bKaaa", cseq="1 INVITE", callid="c1"):
    return {"kind": "req", "method": method,
            "hdr": {"via": [f"SIP/2.0/UDP 10.0.0.1:5060;branch={branch}"], "cseq": [cseq], "call-id": [callid]}}


def _resp(code, branch="z9hG4bKaaa", cseq="1 INVITE"):
    return {"kind": "resp", "code": code, "hdr": {"via": [f"SIP/2.0/UDP 10.0.0.1:5060;branch={branch}"],
                                                  "cseq": [cseq], "call-id": ["c1"]}}


def test_probe_is_options_on_foreign_callid_only():
    own = {"c1"}
    assert driver.is_probe(_req("OPTIONS", callid="keepalive-9"), own)
    assert not driver.is_probe(_req("OPTIONS", callid="c1"), own)       # the fixture's own OPTIONS, forwarded
    assert not driver.is_probe(_req("INVITE", callid="other"), own)     # a UA's own call is behaviour
    assert not driver.is_probe(_resp(200), own)


def test_retransmissions_collapse_but_distinct_transactions_stay():
    msgs = [_req("INVITE"), _req("INVITE"), _req("INVITE", branch="z9hG4bKbbb"), _req("ACK", cseq="1 ACK")]
    assert [m["method"] for m in adapter.distinct(msgs)] == ["INVITE", "INVITE", "ACK"]
    resps = [_resp(180), _resp(200), _resp(200), _resp(200, branch="z9hG4bKccc")]
    assert [m["code"] for m in adapter.distinct(resps)] == [180, 200, 200]


def test_step_record_same_for_one_or_two_copies():
    spec = adapter.load_spec()
    one = {"actor": "alice", "got": {"alice": [_resp(200)], "bob": [_req("INVITE")]}}
    two = {"actor": "alice", "got": {"alice": [_resp(200), _resp(200)], "bob": [_req("INVITE"), _req("INVITE")]}}
    assert adapter.step_record(one, spec) == adapter.step_record(two, spec)


def test_mask_random_tokens():
    a = driver.mask_random("SIP/2.0/UDP {lab}:5070;received={lab};branch=z9hG4bK444ac0133e09;rport=5070")
    b = driver.mask_random("SIP/2.0/UDP {lab}:5070;received={lab};branch=z9hG4bKe187f16c3773;rport=5070")
    assert a == b == "SIP/2.0/UDP {lab}:5070;received={lab};branch=z9hG4bK{r};rport=5070"
    assert driver.mask_random("<sip:0.0.0.0;lr;ftag=59421d8d17>") == "<sip:0.0.0.0;lr;ftag={r}>"
    assert driver.mask_random("<sip:a@x>;tag=abc12") == "<sip:a@x>;tag={r}"
    assert driver.mask_random("<sip:carol@{lab}:5072>;expires=231") == driver.mask_random(
        "<sip:carol@{lab}:5072>;expires=232") == "<sip:carol@{lab}:5072>;expires=240"
    assert driver.mask_random("<sip:bob@{lab}:5071>;expires=300") == "<sip:bob@{lab}:5071>;expires=300"
    assert driver.mask_random("To: <sip:bob@x>") == "To: <sip:bob@x>"  # no tag stays no tag


def test_cyrus_mailbox_names_modified_utf7():
    p = Path(adapter.__file__).resolve().parents[1] / "sieve" / "lab" / "run.py"
    sp = importlib.util.spec_from_file_location("sieve_run", p)
    run = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(run)
    assert run.mutf7_encode("Bücher") == "B&APw-cher"            # RFC 3501 5.1.3 style
    assert run.mutf7_encode("A&B") == "A&-B"
    assert run.mutf7_encode("Archive") == "Archive"
    for s in ("Bücher", "日本語", "A&B", "Ünïcödé.sub", "plain"):
        assert run.mutf7_decode(run.mutf7_encode(s)) == s

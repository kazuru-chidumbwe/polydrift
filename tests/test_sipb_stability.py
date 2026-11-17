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


def test_stray_is_lab_callid_not_sent_in_this_fixture():
    own = {"pdca1b83a9c5-958890a3a7@10.0.0.9", "reg-bob-pdca1b83a9c5@10.0.0.9"}
    assert driver.is_stray(_req("INVITE", callid="pd5636dd9dff-ecbacc1a3b@10.0.0.9"), own)    # another fixture
    assert driver.is_stray(_req("INVITE", callid="pdca1b83a9c5-0000000001@10.0.0.9"), own)    # earlier repeat
    late = _resp(408)
    late["hdr"]["call-id"] = ["pd42848afaa5-dd991815a3@10.0.0.9"]
    assert driver.is_stray(late, own)
    assert not driver.is_stray(_req("INVITE", callid="pdca1b83a9c5-958890a3a7@10.0.0.9"), own)
    assert not driver.is_stray(_req("INVITE", callid="reg-bob-pdca1b83a9c5@10.0.0.9"), own)
    assert not driver.is_stray(_req("INVITE", callid="5c1f0e6a-ua-own@10.0.0.10"), own)        # a UA's own call
    assert not driver.is_stray(_req("INVITE", callid="one"), own)
    assert driver.set_aside(_req("OPTIONS", callid="keepalive-9"), own) == "probe"
    assert driver.set_aside(_req("OPTIONS", callid="pd5636dd9dff-1@x"), own) == "probe"
    assert driver.set_aside(_req("INVITE", callid="pd5636dd9dff-1@x"), own) == "stray"
    assert driver.set_aside(_req("INVITE", callid="reg-bob-pdca1b83a9c5@10.0.0.9"), own) is None


def test_stray_old_dialog_on_ua_callid():
    own = {"pdca1b83a9c5-958890a3a7@10.0.0.9"}
    bye = _req("BYE", callid="23551af62746ea37", cseq="11160 BYE")
    bye["hdr"]["to"] = ["<sip:dave@10.0.0.9:5073>;tag=8f1e"]
    inv = _req("INVITE", callid="5e3ca80c7e6599a1")
    inv["hdr"]["to"] = ["<sip:dave@10.0.0.9:5073>"]
    assert driver.is_stray(bye, own)                                   # a dialog this fixture never opened
    assert not driver.is_stray(inv, own)                               # a new call by the UA is behaviour
    opened = driver.opened_callids([inv])
    bye2 = _req("BYE", callid="5e3ca80c7e6599a1", cseq="2 BYE")
    bye2["hdr"]["to"] = ["<sip:dave@10.0.0.9:5073>;tag=77"]
    assert not driver.is_stray(bye2, own, opened)                      # BYE in the dialog it opened
    other = _req("INVITE", callid="pdf00ac16e8d-ea7432fc27@10.0.0.9")  # another fixture's late INVITE opens nothing
    assert driver.is_stray(other, own, driver.opened_callids([other]))
    assert driver.opened_callids([bye, inv]) == {"5e3ca80c7e6599a1"}
    r = _resp(200)
    r["hdr"]["call-id"] = ["23551af62746ea37"]
    assert driver.is_stray(r, own)
    probe = _req("OPTIONS", callid="keepalive-9")
    opened = driver.opened_callids([probe])
    assert driver.set_aside(probe, own, opened) == "probe"
    assert driver.set_aside(bye, own, opened) == "stray"


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


def test_retransmission_in_a_later_step_is_not_new():
    spec = adapter.load_spec()
    invite_404 = {"actor": "alice", "got": {"alice": [_resp(404)]}}
    ack_quiet = {"actor": "alice", "op": "ack", "got": {"alice": []}}
    ack_late_copy = {"actor": "alice", "op": "ack", "got": {"alice": [_resp(404)]}}
    a = adapter.to_record([invite_404, ack_quiet], "x", "f", spec=spec)
    b = adapter.to_record([invite_404, ack_late_copy], "x", "f", spec=spec)
    assert a.steps == b.steps
    other = {"actor": "alice", "got": {"alice": [_resp(404, branch="z9hG4bKnew")]}}   # a new transaction counts
    c = adapter.to_record([invite_404, other], "x", "f", spec=spec)
    assert c.steps[1] != a.steps[1]


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
    rr = "<sip:1794605499-02065bc64883b512869a7e6f4f711df1b64f757c@{eut};lr>"
    assert driver.mask_random(rr) == "<sip:{r}@{eut};lr>"
    assert driver.mask_random("<sip:{U}@{eut}>") == "<sip:{U}@{eut}>"
    assert driver.mask_random("<sip:pdca1b83a9c5@{eut}>") == "<sip:pdca1b83a9c5@{eut}>"   # 10 hex: a lab name


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


def _party_without_sockets(aside):
    p = object.__new__(driver.Party)
    p.name, p.port, p.inbox = "bob", 5070, []
    p.last_req = p.last_req_src = p.last_resp = None
    p.aside = aside
    return p


def _wire(method, callid):
    return (f"{method} sip:bob@lab.test SIP/2.0\r\nVia: SIP/2.0/UDP 10.0.0.2:5060;branch=z9hG4bKx1\r\n"
            f"From: <sip:alice@lab.test>;tag=a1\r\nTo: <sip:bob@lab.test>\r\nCall-ID: {callid}\r\n"
            f"CSeq: 1 {method}\r\nContent-Length: 0\r\n\r\n").encode()


def test_stray_is_recorded_but_never_becomes_the_request_a_reply_answers():
    # 16 Nov x5: a late PDCENSUS retransmission from px_unknown_method_forwarded reached bob inside
    # px_fork_two_contacts; bob's next `reply` step answered it instead of the fixture's INVITE.
    own = {"pd5636dd9dff-1d2b6a49e0@10.0.0.9"}
    p = _party_without_sockets(lambda m: driver.set_aside(m, own) is not None)
    p._got(_wire("INVITE", "pd5636dd9dff-1d2b6a49e0@10.0.0.9"), "udp", ("udp", ("10.0.0.2", 5060), None))
    p._got(_wire("PDCENSUS", "pda362439e7c-a6401d47af@10.0.0.9"), "udp", ("udp", ("10.0.0.2", 5060), None))
    assert [m["method"] for m in p.inbox] == ["INVITE", "PDCENSUS"]
    assert p.last_req["method"] == "INVITE"


def test_party_without_aside_keeps_old_behaviour():
    p = _party_without_sockets(None)
    p._got(_wire("INVITE", "x@y"), "udp", ("udp", ("10.0.0.2", 5060), None))
    assert p.last_req["method"] == "INVITE"

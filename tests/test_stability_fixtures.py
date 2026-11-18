"""Fixtures and canonical values made stable before the freeze: the stalled TCP client of census_tcp_session, the
10-second floor of a received MQTT Message Expiry Interval, and the SERVFAIL name used only by census_servfail_caching."""

import importlib.util
import socket
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def _driver():
    spec = importlib.util.spec_from_file_location("pd_dns_driver", ROOT / "polydrift/cells/dns/lab/driver.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_stall(monkeypatch, close_peer: bool) -> dict:
    drv = _driver()
    a, b = socket.socketpair()
    if close_peer:
        b.close()
    monkeypatch.setattr(drv.socket, "create_connection", lambda *args, **kw: a)
    try:
        return drv.parallel("127.0.0.1", [{"label": "t1", "stall": True, "hold_ms": 300}], 2)
    finally:
        if not close_peer:
            b.close()


def test_stall_held_when_server_keeps_connection(monkeypatch):
    obs = _run_stall(monkeypatch, close_peer=False)
    assert obs["rcodes"] == {"t1": "held"} and obs["order"] == ["t1"]


def test_stall_closed_when_server_closes(monkeypatch):
    obs = _run_stall(monkeypatch, close_peer=True)
    assert obs["rcodes"] == {"t1": "closed"}


def test_expiry_floor_keeps_undecremented_apart():
    from polydrift.cells.mqtt.adapter import _publish_meta

    def meta(e):
        return _publish_meta({"b": [("x", 0, (), e, False)]})

    assert meta(57) == meta(58) == meta(50)
    assert meta(60) != meta(59)
    assert meta(None) != meta(0)
    assert meta(1) == meta(2)


def test_servfail_caching_has_its_own_name():
    fx = yaml.safe_load((ROOT / "polydrift/cells/dns/fixtures.yaml").read_text(encoding="utf-8"))
    zones = yaml.safe_load((ROOT / "polydrift/cells/dns/lab/zones.yaml").read_text(encoding="utf-8"))
    fixtures = fx.get("fixtures", fx)
    names = {s.get("qname") for s in fixtures["census_servfail_caching"]["steps"] if s.get("qname")}
    assert names == {"servfailc.pd.pdlab"}
    others = {s.get("qname") for k, f in fixtures.items() if k != "census_servfail_caching" and isinstance(f, dict)
              for s in f.get("steps", []) if isinstance(s, dict)}
    assert "servfailc.pd.pdlab" not in others
    assert "servfailc: {kind: rcode" in (ROOT / "polydrift/cells/dns/lab/zones.yaml").read_text(encoding="utf-8")
    assert zones


def _sip_driver():
    spec = importlib.util.spec_from_file_location("pd_sip_driver", ROOT / "polydrift/cells/sipb/lab/driver.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sip_fixtures():
    fx = yaml.safe_load((ROOT / "polydrift/cells/sipb/fixtures.yaml").read_text(encoding="utf-8"))
    return fx.get("fixtures", fx)


def test_sip_hascode_looks_at_every_step_so_far():
    drv = _sip_driver()
    ctx = {"hist": {"alice": [{"kind": "resp", "code": 180}, {"kind": "resp", "code": 200}, {"kind": "resp", "code": 487}]},
           "alice": []}
    assert drv.evaluate("hascode:hist/alice=487", ctx) is True
    assert drv.evaluate("hascode:alice=487", ctx) is False
    assert drv.evaluate("hascode:hist/bob=487", ctx) is False


def test_sip_bye_cseq_compares_method_not_the_ua_chosen_number():
    drv = _sip_driver()
    x8 = _sip_fixtures()["ua_bye_from_callee_route_set"]["steps"][-2]["extract"]["x8"]
    for n in ("1", "31496", "63100"):
        assert drv.evaluate(x8, {"dave": [{"hdr": {"cseq": [f"{n} BYE"]}}]}) is True
    assert drv.evaluate(x8, {"dave": [{"hdr": {"cseq": ["2 INVITE"]}}]}) is False


def test_sip_ringing_cancel_487_counted_in_either_window():
    steps = _sip_fixtures()["uas_ringing_cancel"]["steps"]
    assert steps[-1]["extract"]["x4"] == "hascode:hist/alice=487"


def test_sip_reinvite_overlap_first_reinvite_does_not_listen():
    steps = _sip_fixtures()["uas_reinvite_overlap"]["steps"]
    assert steps[2]["window_ms"] == 0 and "INVITE" in steps[2]["msg"] and "INVITE" in steps[3]["msg"]


def test_jmap_submission_extensions_mask_the_server_clock():
    from polydrift.cells.jmap.driver import evaluate
    x8 = None
    fx = yaml.safe_load((ROOT / "polydrift/cells/jmap/fixtures.yaml").read_text(encoding="utf-8"))
    for st in fx.get("fixtures", fx)["census_session_mail_capability"]["steps"]:
        x8 = (st.get("extract") or {}).get("x8", x8)
    assert x8.startswith("nodate:")
    expr = x8.replace("{acct}", "a1")

    def ctx(ext):
        caps = {"urn:ietf:params:jmap:submission": {"submissionExtensions": ext}}
        return {"body": {"accounts": {"a1": {"accountCapabilities": caps}}}}

    one = evaluate(expr, ctx({"FUTURERELEASE": ["86400", "2026-11-19T06:51:59.936358331Z"], "SIZE": ["1000"]}))
    two = evaluate(expr, ctx({"FUTURERELEASE": ["86400", "2026-11-19T06:53:17.576325789Z"], "SIZE": ["1000"]}))
    assert one == two == {"FUTURERELEASE": ["86400", "<date-time>"], "SIZE": ["1000"]}
    assert evaluate(expr, ctx({"FUTURERELEASE": ["3600", "2026-11-19T06:51:59Z"]})) != one
    assert evaluate(expr, {"body": {"accounts": {}}}) is None


def _reply_lab(drv, bob):
    from types import SimpleNamespace
    sent = []
    lab = SimpleNamespace(parties={"bob": bob}, named={}, host="127.0.0.1", fill=lambda t, _p: t,
                          finish=drv.Lab.finish,
                          send_bytes=lambda party, data, kind, dest=None, via_conn=None: sent.append(data) or None)
    return lab, sent


def _sip_req(method, cseq):
    head = [f"{method} sip:bob@lab.test SIP/2.0", f"Via: SIP/2.0/UDP 172.31.60.10:5060;branch=z9hG4bK{cseq}",
            "From: <sip:alice@lab.test>;tag=a1", "To: <sip:bob@lab.test>", "Call-ID: c1",
            f"CSeq: {cseq} {method}", "Content-Length: 0"]
    return ("\r\n".join(head) + "\r\n\r\n").encode()


def test_sip_reply_answers_the_named_method_when_a_retransmission_lands_last():
    drv = _sip_driver()
    bob = drv.Party("bob", "127.0.0.1", 0)
    try:
        src = ("udp", ("172.31.60.10", 5060), None)
        for m, n in (("MESSAGE", 2), ("INVITE", 1), ("MESSAGE", 2)):  # repeat 3 of D x5: MESSAGE retransmitted last
            bob._got(_sip_req(m, n), "udp", src)
        lab, sent = _reply_lab(drv, bob)
        assert drv.Lab.step_reply(lab, {"from": "bob", "code": 200, "answers": "INVITE"}) is None
        assert drv.Lab.step_reply(lab, {"from": "bob", "code": 200, "answers": "MESSAGE"}) is None
        assert drv.Lab.step_reply(lab, {"from": "bob", "code": 200}) is None
        assert [b"CSeq: 1 INVITE" in x for x in sent] == [True, False, False]
        assert b"CSeq: 2 MESSAGE" in sent[1] and b"CSeq: 2 MESSAGE" in sent[2]
        assert "no request received (BYE)" in drv.Lab.step_reply(lab, {"from": "bob", "code": 200, "answers": "BYE"})
    finally:
        bob.udp.close()
        bob.tcp_listen.close()


def test_sip_record_route_replies_name_their_requests():
    steps = _sip_fixtures()["px_record_route"]["steps"]
    replies = [s for s in steps if s["op"] == "reply"]
    assert [s.get("answers") for s in replies] == ["INVITE", "MESSAGE"]
    assert replies[0]["extract"]["x6"] == "alice/0/hdr/record-route"

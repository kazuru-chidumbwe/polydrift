"""Extract-language changes from the 21 Nov tracing (fixes 1, 2, 3): one response per transaction in codes:,
header values across rows in vals:, conditional rows in when:."""
from polydrift.cells.sipb.lab.driver import evaluate


def resp(code, branch="z9hG4bKa", cseq="1 INVITE", to="<sip:bob@lab.test>;tag=t1"):
    return {"kind": "resp", "code": code, "hdr": {"via": [f"SIP/2.0/UDP 1.2.3.4:5;branch={branch}"],
                                                  "cseq": [cseq], "to": [to]}}


def req(method, branch="z9hG4bKb", cseq="1 INVITE"):
    return {"kind": "req", "method": method, "hdr": {"via": [f"SIP/2.0/UDP 1.2.3.4:5;branch={branch}"],
                                                     "cseq": [cseq]}}


def test_codes_collapse_retransmitted_copies():
    ctx = {"alice": [resp(100), resp(404), resp(404)]}
    assert evaluate("codes:alice", ctx) == [100, 404]
    assert evaluate("ncodes:alice=404", ctx) == 2


def test_codes_keep_forked_2xx_apart():
    ctx = {"alice": [resp(200, to="<sip:bob@lab.test>;tag=a"), resp(200, to="<sip:bob@lab.test>;tag=b")]}
    assert evaluate("codes:alice", ctx) == [200, 200]


def test_methods_collapse_retransmitted_cancel():
    ctx = {"carol": [req("INVITE"), req("CANCEL", cseq="1 CANCEL"), req("CANCEL", cseq="1 CANCEL")]}
    assert evaluate("methods:carol", ctx) == ["INVITE", "CANCEL"]


def test_vals_rows_and_comma_list_equal():
    rows = {"bob": [{"hdr": {"x-pd-multi": ["one", "two"]}}]}
    joined = {"bob": [{"hdr": {"x-pd-multi": ["one, two"]}}]}
    assert evaluate("vals:bob/0/hdr/x-pd-multi", rows) == evaluate("vals:bob/0/hdr/x-pd-multi", joined) == ["one", "two"]


def test_vals_keeps_commas_in_quotes_and_brackets():
    ctx = {"bob": [{"hdr": {"contact": ['"A, B" <sip:a@x;p=1,2>, <sip:b@y>']}}]}
    assert evaluate("vals:bob/0/hdr/contact", ctx) == ['"A, B" <sip:a@x;p=1,2>', "<sip:b@y>"]


def test_when_binds_only_on_condition():
    r423 = resp(423)
    r423["hdr"]["min-expires"] = ["60"]
    assert evaluate("when:hascode:bob=423?re:bob/0/hdr/min-expires/0=\\d+", {"bob": [r423]}) is True
    assert evaluate("when:hascode:bob=423?re:bob/0/hdr/min-expires/0=\\d+", {"bob": [resp(200)]}) is None

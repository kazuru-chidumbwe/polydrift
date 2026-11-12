"""SIP fixtures are wire bytes: every line of the start line and headers ends in CRLF (RFC 3261 Section 7), and a
single Content-Length equals the body length unless the fixture is one of the deliberate length faults."""
import re
from pathlib import Path

FIX = Path(__file__).resolve().parents[1] / "fixtures"
LENGTH_FAULTS = {"torture_bad_clen.sip", "torture_multi_clen.sip", "junk_after_message.sip", "message_min.sip",
                 "r_no_empty_line.sip", "r_leading_crlf.sip"}


def test_fixtures_are_crlf():
    for f in sorted(FIX.glob("*.sip")):
        raw = f.read_bytes()
        head = raw.split(b"\r\n\r\n", 1)[0]
        assert b"\n" not in head.replace(b"\r\n", b""), f"{f.name}: bare LF in start line or headers"


def test_content_length_matches_body():
    for f in sorted(FIX.glob("*.sip")):
        if f.name in LENGTH_FAULTS:
            continue
        raw = f.read_bytes()
        if b"\r\n\r\n" not in raw:
            continue
        head, body = raw.split(b"\r\n\r\n", 1)
        cl = re.findall(rb"(?im)^(?:content-length|l)[ \t]*:[ \t]*(\d+)[ \t]*\r?$", head)
        if len(cl) == 1:
            assert int(cl[0]) == len(body), f"{f.name}: Content-Length {int(cl[0])} != body {len(body)}"

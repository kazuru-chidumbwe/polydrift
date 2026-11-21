"""Lab authority behaviours for the RQ5 DNS cases (MaginotDNS, Phoenix Domain, ResolverFuzz, TuDoor inputs)."""
import sys
import time
from pathlib import Path

import dns.message

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "polydrift" / "cells" / "dns" / "lab"))
import auth  # noqa: E402

ZONE = auth.Zone(
    "pd.pdlab.",
    ["@ SOA ns.pd.pdlab. h.pd.pdlab. 1 3600 600 86400 60", "@ NS ns.pd.pdlab.", "ns A 10.0.0.53", "a A 10.0.0.1",
     "atk NS ns.atk.pd.pdlab.", "ns.atk A 10.0.0.66"],
    {"mg": {"kind": "craft", "answer": ["mg.pd.pdlab. 300 A 10.6.6.6"],
            "authority": ["other.pdlab. 300 NS ns1.rogue.pdlab."], "additional": ["ns1.rogue.pdlab. 300 A 10.6.6.7"]},
     "ref": {"kind": "craft", "aa": False, "authority": ["other.pdlab. 300 NS ns1.rogue.pdlab."]},
     "big": {"kind": "big", "count": 120},
     "mal": {"kind": "malformed", "form": "qr0", "then_ms": 300},
     "mal2": {"kind": "malformed", "form": "empty"},
     "tn": {"kind": "tcp_null"},
     "ic": {"kind": "icmp", "code": 0},
     "atk": {"below": {"kind": "revoke", "key": "atk-test", "seconds": 0.2}}},
)


def ask(name, tr="udp", payload=1232):
    m = dns.message.make_query(name, "A", use_edns=0, payload=payload)
    return auth.respond(ZONE, m.to_wire(), tr, "10.0.0.9")


def test_craft_writes_every_section_and_flags():
    r = dns.message.from_wire(ask("mg.pd.pdlab."))
    assert r.flags & 0x0400 and [str(x) for x in r.authority] == ["other.pdlab. 300 IN NS ns1.rogue.pdlab."]
    assert [str(x) for x in r.additional] == ["ns1.rogue.pdlab. 300 IN A 10.6.6.7"]
    r = dns.message.from_wire(ask("ref.pd.pdlab."))
    assert not r.flags & 0x0400 and not r.answer


def test_big_ignores_the_advertised_size():
    assert len(ask("big.pd.pdlab.", payload=1232)) > 1232


def test_malformed_then_valid():
    out = ask("mal.pd.pdlab.")
    assert [d for d, _ in out] == [0.0, 0.3]
    bad, good = out[0][1], out[1][1]
    assert not bad[2] & 0x80  # QR=0
    assert dns.message.from_wire(good).flags & 0x8000  # the valid response follows
    assert ask("mal2.pd.pdlab.") == [(0.0, b"")]


def test_tcp_null_and_icmp():
    assert dns.message.from_wire(ask("tn.pd.pdlab.")).flags & 0x0200
    assert ask("tn.pd.pdlab.", tr="tcp") is auth.CLOSE
    out = ask("ic.pd.pdlab.")
    assert out[0][1] is auth.ICMP and out[0][2] == 0


def test_revoke_withdraws_the_delegation_after_the_set_time():
    r = dns.message.from_wire(ask("x.atk.pd.pdlab."))
    assert r.rcode() == 0 and r.authority
    time.sleep(0.3)
    assert dns.message.from_wire(ask("y.atk.pd.pdlab.")).rcode() == 3


def test_icmp_checksum_is_valid():
    body = b"\x03\x00\x00\x00\x00\x00\x00\x00" + b"\x45" * 28
    body = body[:2] + auth._csum(body).to_bytes(2, "big") + body[4:]
    assert auth._csum(body) == 0

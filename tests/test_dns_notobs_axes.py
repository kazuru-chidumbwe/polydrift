"""DNS rows made observable on 22 Nov 2026: DNAME target compression read from the response bytes, and the
synthesized CNAME TTL against the DNAME's."""
import dns.message
import dns.rrset

from polydrift.cells.dns.adapter import dname_compressed, synth_ttl_follows


def _dname_response() -> bytes:
    q = dns.message.make_query("x.dn.pd.pdlab", "A")
    r = dns.message.make_response(q)
    r.answer.append(dns.rrset.from_text("dn.pd.pdlab.", 300, "IN", "DNAME", "pd.pdlab."))
    return r.to_wire()


def test_uncompressed_dname_target():
    assert dname_compressed(_dname_response().hex()) is False


def test_compressed_dname_target():
    w = _dname_response()
    target = b"\x02pd\x05pdlab\x00"
    i = w.rindex(target)
    # point the target at "pd.pdlab." inside the question name (offset 12 + len("\x01x\x02dn") = 17)
    rdlen_at = i - 2
    c = w[:rdlen_at] + (2).to_bytes(2, "big") + b"\xc0\x11" + w[i + len(target):]
    assert dname_compressed(c.hex()) is True


def test_no_dname_or_no_wire():
    assert dname_compressed(None) is None
    q = dns.message.make_response(dns.message.make_query("a.pd.pdlab", "A"))
    assert dname_compressed(q.to_wire().hex()) is None


def test_synth_ttl():
    ans = ["dn.pd.pdlab dname dtarget.other.pdlab.", "x.dn.pd.pdlab cname x.dtarget.other.pdlab."]
    assert synth_ttl_follows(ans, [298, 298]) is True
    assert synth_ttl_follows(ans, [298, 300]) is False
    assert synth_ttl_follows(["a.pd.pdlab a 10.0.0.1"], [300]) is None

"""The DNS driver's no-EDNS query must carry no OPT record (dnspython enables EDNS when use_edns is None and a
payload is given)."""

import dns.message


def test_no_edns_query_has_no_opt():
    q = dns.message.make_query("a.pd.pdlab", "A", use_edns=False, payload=1232, want_dnssec=False, options=None)
    assert q.edns == -1 and q.opt is None


def test_driver_uses_false_for_null_edns():
    from pathlib import Path

    src = (Path(__file__).parents[1] / "polydrift/cells/dns/lab/driver.py").read_text(encoding="utf-8")
    assert "use_edns=False if edns is None else edns" in src

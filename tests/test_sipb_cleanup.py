"""sipb runner: bindings a fixture registered are removed after it (cross-fixture isolation)."""

from polydrift.cells.sipb.runner import cleanup_steps, load_fixtures


def test_cleanup_deregisters_each_party_and_aor_once():
    steps = [{"op": "send", "from": "bob", "msg": "REGISTER sip:{domain} SIP/2.0\nTo: <sip:bob@{domain}>\n\n"},
             {"op": "send", "from": "bob", "msg": "REGISTER sip:{domain} SIP/2.0\nTo: <sip:bob@{domain}>\n\n"},
             {"op": "send", "from": "carol", "msg": "REGISTER sip:{domain} SIP/2.0\nTo: <sip:bob@{domain}>;x=1\n\n"},
             {"op": "send", "from": "alice", "msg": "INVITE sip:bob@{domain} SIP/2.0\nTo: <sip:bob@{domain}>\n\n"}]
    c = cleanup_steps(steps)
    assert [s["from"] for s in c] == ["bob", "carol"]
    m = c[0]["msg"]
    assert "Contact: *\n" in m and "Expires: 0\n" in m and m.endswith("\n\n")
    assert "To: <sip:bob@{domain}>\n" in m and "{port:bob}" in m


def test_no_cleanup_for_fixtures_without_register():
    fx = load_fixtures()
    assert any(cleanup_steps(f["steps"]) for f in fx.values())
    assert not cleanup_steps([{"op": "reply", "from": "bob", "code": 200}])

"""Smoke tests for the census additions of 12 Nov 2026: each cell adapter builds a record from a synthetic
observation without error, and the new fields come out as expected."""

from __future__ import annotations

from polydrift.cells.dns import adapter as dns_adapter
from polydrift.cells.jmap.driver import evaluate
from polydrift.cells.ldap import adapter as ldap_adapter
from polydrift.cells.mqtt import adapter as mqtt_adapter
from polydrift.cells.sieve import adapter as sieve_adapter


def test_sieve_canary_effect_only_engine():
    from polydrift.cells.sieve.runner import canary_ok
    kept = {"compile": "ok", "effect_only": True, "effect": {
        "stored": {"INBOX": 1}, "emitted": [], "lmtp": {"code": 250}}}
    assert canary_ok(kept)
    assert not canary_ok({**kept, "effect": {**kept["effect"], "stored": {"INBOX": 0}}})
    assert not canary_ok({**kept, "effect": {**kept["effect"], "lmtp": {"code": 550}}})
    assert not canary_ok({**kept, "harness_error": "cyrus mailbox setup"})
    assert canary_ok({"compile": "ok", "intent": ["keep"]})
    assert not canary_ok({"compile": "ok", "intent": ["discard"]})


def test_sieve_effect_facts_and_flags():
    spec = sieve_adapter.load_spec()
    res = {"compile": "ok", "intent": ["vacation"], "effect": {
        "stored": {"INBOX": 1}, "flags": {"INBOX": ["\\Flagged \\Seen"]},
        "emitted": [{"kind": "vacation", "to": ["probe@example.invalid"], "facts": {
            "received_delta": 0, "in_reply_to_orig": True, "references_orig": True, "subject": "Auto: hi",
            "from": "<user@lab.test>", "to": "probe@example.invalid", "date_present": True, "env_from": None,
            "mdn_disposition": None, "dsn_action": None, "marker": False}}]}}
    recs = sieve_adapter.to_records(res, "x", "f", spec=spec)
    eff = recs[1].steps[0].fields
    assert eff["vacation_in_reply_to"].canon == (True,)
    assert eff["vacation_from"].canon == ("user@lab.test",)
    assert eff["stored_flags"].canon == (("INBOX", ("\\Flagged \\Seen",)),)


def test_mqtt_new_axes():
    spec = mqtt_adapter.load_spec()
    obs = [{"step": 1, "actor": "a", "op": "connect", "end": False, "error": None, "expects_reply": True,
            "clients": {"a": {"packets": [{"type": "CONNACK", "reason_code": 0, "ack_flags": 0, "session_present": 0,
                                           "properties": {"assigned_client_identifier": "x"}}],
                              "closed_now": False, "open": True, "oversize": 0, "alias_over": 0, "pid_reuse": 0,
                              "last_pid_before": None}}},
           {"step": 2, "actor": "a", "op": "pingreq", "end": False, "error": "closed-by-peer", "expects_reply": True,
            "clients": {"a": {"packets": [], "closed_now": False, "open": False}}}]
    rec = mqtt_adapter.to_record(obs, "x", "f", spec=spec, prefix="p/")
    f = rec.steps[0].fields
    assert f["connack_presence"].canon == ("assigned_client_identifier",)
    assert f["connack_reserved"].canon == 0
    assert f["actor_closed"].canon is False
    assert rec.steps[1].status.value == "closed"


def test_ldap_unique_name_maps_back():
    spec = ldap_adapter.load_spec()
    obs = [{"step": 1, "actor": "a", "op": "search", "id": 2, "error": None, "expects_reply": True, "clients": {"a": {
        "messages": [{"op": "SearchResultEntry", "id": 2, "dn": "ou=pdabc123def0t,dc=lab,dc=test",
                      "attributes": {"objectClass": ["top", "organizationalUnit"]}},
                     {"op": "SearchResultDone", "id": 2, "result": 0, "matched": "", "diagnostic": ""}],
        "closed_now": False}}}]
    rec = ldap_adapter.to_record(obs, "x", "f", spec=spec, base="dc=lab,dc=test", unique="pdabc123def0")
    f = rec.steps[0].fields
    assert f["entries"].canon == ("ou={U}t",)
    assert f["objectclasses"].canon == ("organizationalunit", "top")
    assert f["referral_shape"].canon == "none"


def test_dns_upstream_and_query_fields():
    spec = dns_adapter.load_spec()
    up = {"op": "upstream", "rcode": "NOERROR", "error": None, "queries": [
        {"server": "pd", "qname": "rdz.pd.pdlab", "qtype": "A", "rd": False, "edns": 0, "payload": 1232,
         "opts": [65001], "transport": "udp", "t": 1.0}]}
    q = {"op": "query", "rcode": "NOERROR", "answers": ["a.pd.pdlab A 10.0.0.2"], "aa": False, "ra": True, "tc": False,
         "ad": False, "z": False, "rd": True, "qname_case": "A.pd.pdlab", "ttls": [300], "additional": [],
         "additional_soa": False, "authority_soa": False, "edns": True, "error": None}
    rec = dns_adapter.to_record_script([up, q], "x", "f", spec=spec)
    assert rec.steps[0].fields["upstream_count"].canon == (("rdz.pd.pdlab A", 1),)
    assert rec.steps[0].fields["upstream_local_opts"].canon is True
    assert rec.steps[1].fields["z_bit"].canon is False


def test_jmap_extract():
    ctx = {"body": {"methodResponses": [["Mailbox/get", {"list": [], "state": "s1"}, "c0"]]}, "http": 200}
    assert evaluate(r"re:r0/state=s\d", ctx) is True
    assert evaluate("has:r0/notFound", ctx) is False
    assert evaluate("m0", ctx) == "Mailbox/get"


def test_jmap_driver_tolerates_changes_created_list(monkeypatch):
    """A /changes response lists created ids; the driver must not treat it as a creation map."""
    from polydrift.cells.jmap import driver
    body = '{"methodResponses": [["Mailbox/changes", {"created": ["a1"], "updated": [], "destroyed": []}, "c0"]]}'
    monkeypatch.setattr(driver, "_req", lambda *a, **k: (200, "application/json", body, {}))
    out = driver.run_script("http://127.0.0.1:1", "u", "p", {"apiUrl": "/jmap/"},
                            [{"op": "call", "calls": [], "extract": {"x1": "len:r0/created"}}], "pdx")
    assert out[0]["error"] is None and out[0]["extract"]["x1"] == 1

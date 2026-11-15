"""LDAP consumer replay helpers and replay server (polydrift/cells/ldap)."""

import importlib.util
from pathlib import Path

from polydrift.cells.ldap import codec, replay

_p = Path(__file__).resolve().parents[1] / "polydrift" / "cells" / "ldap" / "lab" / "replay_server.py"
_spec = importlib.util.spec_from_file_location("ldap_replay_server", _p)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

FX = {"steps": [{"op": "bind", "args": {"dn": "{A}", "password": "{P}"}}, {"op": "sleep"},
                {"op": "search", "args": {"base": "cn={U},{B}", "scope": "base"}},
                {"op": "hex", "hex": "30 00"}]}
ENV = {"B": "dc=lab,dc=test", "A": "cn=admin,dc=lab,dc=test", "P": "pw", "U": "pdabc"}


def test_prefix_script_substitutes_and_stops_at_raw_steps():
    s = replay.prefix_script(FX, 3, ENV)
    assert [x["op"] for x in s] == ["bind", "search"]
    assert s[1]["args"]["base"] == "cn=pdabc,dc=lab,dc=test"
    assert replay.prefix_script(FX, 4, ENV) is None
    assert replay.prefix_script({"steps": [{"op": "search", "client": "b"}]}, 1, ENV) is None
    assert replay.prefix_script({"steps": [{"op": "search", "args": {"base": "{@x}"}}]}, 1, ENV) is None


def test_server_steps_from_raw():
    obs = [{"step": 1, "clients": {"a": {"wire": ["aa"], "closed_now": False}}},
           {"step": 3, "clients": {"a": {"wire": ["bb", "cc"], "closed_now": True}}}]
    assert replay.server_steps(FX, obs, 3) == [{"op": "bind", "msgs": ["aa"], "closed": False},
                                              {"op": "search", "msgs": ["bb", "cc"], "closed": True}]
    assert replay.server_steps(FX, [{"step": 1, "clients": {"a": {}}}], 1) is None


def test_canon_writes_back_lab_names():
    r = [{"entries": [["CN=PDABC,DC=Lab,DC=Test", {}]], "matched": "cn=admin,dc=lab,dc=test"}]
    assert replay.canon(r, ENV) == [{"entries": [["CN={U},{B}", {}]], "matched": "{A}"}]


def test_server_patches_message_id_in_place():
    resp = codec.message(7, codec.seq(codec.integer(0, 0x0A), codec.octets(""), codec.octets(""), tag=0x61))
    out, re = srv.set_id(resp, 2)
    assert not re and len(out) == len(resp) and codec.decode(out)["id"] == 2
    out, re = srv.set_id(resp, 300)
    assert re and codec.decode(out)["id"] == 300


def test_server_matches_requests_by_operation():
    bind_resp = codec.message(1, codec.seq(codec.integer(49, 0x0A), codec.octets(""), codec.octets(""), tag=0x61))
    srv.control("LOAD " + '{"steps": [{"op": "bind", "msgs": ["%s"], "closed": true}]}' % bind_resp.hex())
    search_req = codec.message(4, codec.search({"base": ""}))
    assert srv.answer(search_req) == ([], False)
    bind_req = codec.message(5, codec.bind({"dn": "", "password": ""}))
    msgs, close = srv.answer(bind_req)
    assert close and codec.decode(msgs[0])["id"] == 5
    import json
    c = json.loads(srv.control("COUNT"))
    assert c["requests"] == ["search", "bind"] and c["unmatched"] == 1

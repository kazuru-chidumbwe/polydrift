"""Tracing fixes 8, 17 and 26: rows that depend on an earlier step (`when`), security rows that test the property
itself, and a Notice of Disconnection followed by a close."""
from __future__ import annotations

from polydrift.cells.ldap.adapter import NOD, classify
from polydrift.core.label import inside, label_impl
from polydrift.core.record import Status
from polydrift.core.spec import FixtureSpec
from polydrift.report import _security_kind

from core_helpers import f, rec, step

ROW = {"req_id": "L-1", "keyword": "MUST", "axis": "native_codes", "step": 2, "allowed": [(49,)],
       "when": {"axis": "native_codes", "step": 1, "allowed": [(0,)]}}


def test_when_row_binds_only_after_the_step_it_depends_on():
    refused_add = rec("a", step(native_codes=f(1, (68,))), step(native_codes=f(1, (0,))))
    assert inside(refused_add, ROW)
    added = rec("a", step(native_codes=f(1, (0,))), step(native_codes=f(1, (0,))))
    assert not inside(added, ROW)


def _bind_fixture():
    d = {"id": "bind_unauthenticated", "cell": "ldap", "kind": "security", "spec_refs": [
        {"req_id": "L-2", "keyword": "SHOULD", "axis": "native_codes", "step": 1, "allowed": [(53,)], "security": False},
        {"req_id": "L-2", "keyword": "SHOULD", "axis": "native_codes", "step": 1, "excludes": [0]}]}
    return FixtureSpec.from_dict(_security_kind(d))


def test_refusal_with_another_code_is_not_a_security_breach():
    lb = label_impl(rec("lldap", step(native_codes=f(1, (49,)))), _bind_fixture())
    assert lb.should_broken and not lb.security_broken


def test_granted_unauthenticated_bind_is_a_security_breach():
    lb = label_impl(rec("x", step(native_codes=f(1, (0,)))), _bind_fixture())
    assert lb.security_broken == ["bind_unauthenticated"]


def test_notice_of_disconnection_then_close_is_closed():
    nod = {"op": "ExtendedResponse", "id": 0, "response_name": NOD, "result": 2}
    obs = {"actor": "a", "op": "hex", "expects_reply": True, "clients": {"a": {"messages": [nod], "closed_now": True}}}
    assert classify(obs) == Status.CLOSED
    obs["clients"]["a"]["closed_now"] = False
    assert classify(obs) == Status.RESPONDED

"""Action view of a consumer replay result (manuscript 3.6).

Replay reports two levels of consequence. The strict level compares everything the consumer API exposes, and it
tests the T4 prediction: a difference erased by canonicalisation should not reach the consumer. The action level
compares what the consumer library itself does and keeps, and it tests the T3 prediction: a difference that is
visible but changes neither the action nor the kept data. What the library does is the requests it sends next (a
retry, a re-bind, a followed referral, a re-fetched session, a retransmission), whether an operation succeeded,
and the event sequence it reports. What it keeps is the returned data: answers, entries, delivered messages,
method responses. Values the library hands to its program as information without acting on them (result and
reason codes within success or failure, reason phrases, diagnostic messages, error type names, announced
capabilities) are left out. Success and failure are the protocol's own split, not the oracle's decision classes, so
the view does not reuse anything the oracle decides. Collections the protocol defines as unordered (DNS answers,
LDAP entries and referrals) are compared as multisets; the strict level keeps their order.
"""

from __future__ import annotations

import json
from typing import Any, Callable


def _ms(items: Any) -> Any:
    """A list as a multiset (sorted canonical JSON)."""
    if not isinstance(items, list):
        return items
    return sorted(json.dumps(x, sort_keys=True) for x in items)


def _server(r: dict[str, Any], keys: tuple[str, ...]) -> Any:
    s = r.get("server") or {}
    return {k: s.get(k) for k in keys if k in s}


def mqtt(r: Any) -> Any:
    if not isinstance(r, dict):
        return r
    ev = []
    for e in r.get("events") or []:
        k = e.get("ev")
        if k == "connack":
            ev.append(("connack", (e.get("rc") or 0) < 0x80, e.get("sp")))
        elif k == "suback":
            ev.append(("suback", tuple((c or 0) < 0x80 for c in e.get("rcs") or [])))
        elif k == "puback":
            ev.append(("puback", (e.get("rc") or 0) < 0x80))
        elif k == "message":
            ev.append(("message", e.get("topic"), e.get("payload"), e.get("qos"), e.get("retain"),
                       json.dumps(e.get("props"), sort_keys=True)))
        elif k == "api":
            ev.append(("api", e.get("call"), "error" in e))
        else:
            ev.append((k,))
    return {"events": ev, "server": _server(r, ("requests", "unmatched"))}


def dns(r: Any) -> Any:
    if not isinstance(r, dict):
        return r
    out = {"res_query": r.get("res_query") == "ok", "answers": _ms(r.get("answers")), "queries": r.get("queries")}
    if "gai" in r:
        out["gai"] = r.get("gai") == 0
        out["addrs"] = r.get("addrs")  # the order getaddrinfo returns is the library's own choice (RFC 6724)
    return out


def ldap(r: Any) -> Any:
    if not isinstance(r, dict) or not isinstance(r.get("ops"), list):
        return r  # a consumer failure is compared as it was recorded
    ops = []
    for o in r.get("ops") or []:
        res = o.get("result")
        # RFC 4511 4.1.9: success, compareFalse and compareTrue are not errors; the compare answer is data
        ok = res in (0, 5, 6)
        ops.append((o.get("op"), ok, res if o.get("op") == "compare" and ok else None,
                    _ms(o.get("entries")), _ms(o.get("referrals"))))
    return {"ops": ops, "server": _server(r, ("requests", "unmatched"))}


def _jmap_resp(x: dict[str, Any]) -> Any:
    if "class" in x:  # jmapc: method errors are classes that are not *Response
        err = not str(x.get("class", "")).endswith("Response")
        return (x.get("id"), "error") if err else (x.get("id"), x.get("class"), x.get("data"))
    err = x.get("name") == "error"  # go-jmap
    return (x.get("id"), "error") if err else (x.get("id"), x.get("name"), x.get("args"))


def jmap(r: Any) -> Any:
    if not isinstance(r, dict):
        return r
    out = []
    for o in r.get("out") or []:
        if isinstance(o, dict) and "responses" in o:
            out.append([_jmap_resp(x) for x in o.get("responses") or []])
        else:
            out.append(o)
    return {"out": out, "server": _server(r, ("posts", "session_gets", "unmatched"))}


def _sip_kind(code: Any) -> str:
    if not isinstance(code, int):
        return "none"
    return "provisional" if code < 200 else "success" if code < 300 else "failure"


def sip(r: Any) -> Any:
    if not isinstance(r, dict):
        return r
    ev = []
    for e in r.get("events") or []:
        k = e.get("ev")
        if k in ("state",):
            ev.append(("state", e.get("state")))
        elif k == "end":
            ev.append(("end", e.get("active"), e.get("state")))
        elif k in ("options", "r_invite", "r_bye", "terminated"):
            ev.append((k, _sip_kind(e.get("code"))))
        else:
            ev.append((k,))
    # a second provisional response repeats the same callback; the library's state is unchanged
    ev = [e for i, e in enumerate(ev) if i == 0 or e != ev[i - 1]]
    return {"events": ev, "server": _server(r, ("requests", "retransmissions", "unmatched"))}


ACTION_VIEWS: dict[str, Callable[[Any], Any]] = {
    "mqtt": mqtt, "dns": dns, "ldap": ldap, "jmap": jmap, "sipb": sip, "sip": sip,
}

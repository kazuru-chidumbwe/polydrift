"""LDAP consumer for replay (manuscript 3.6, lab L9): runs a fixture prefix through a client library and prints, as
one JSON line, what a program using the library sees for each operation: the result code (or the exception the
library raises), the returned entries with their attribute values as the library decodes them, and referrals.

Two libraries: ``libldap`` (OpenLDAP's C library through python-ldap) and ``ldap3`` (pure Python).
Usage: python3 ldapconsumer.py LIB HOST PORT < script.json, the script a list of
{"op": bind|search|add|modify|delete|moddn|compare, "args": {...}} in the cell's fixture argument form.
"""

from __future__ import annotations

import json
import sys

TIMEOUT = 6


def filter_text(f) -> str:  # noqa: ANN001
    """RFC 4515 string of the cell's nested-list filter."""
    def esc(v: str) -> str:
        return "".join(f"\\{ord(c):02x}" if c in "*()\\\0" else c for c in str(v))

    k = f[0]
    if k in ("and", "or"):
        return "(" + ("&" if k == "and" else "|") + "".join(filter_text(x) for x in f[1:]) + ")"
    if k == "not":
        return "(!" + filter_text(f[1]) + ")"
    if k in ("eq", "ge", "le", "approx"):
        return f"({f[1]}{ {'eq': '=', 'ge': '>=', 'le': '<=', 'approx': '~='}[k]}{esc(f[2])})"
    if k == "present":
        return f"({f[1]}=*)"
    if k == "sub":
        mid = "*".join(esc(a) for a in (f[3] or []))
        return f"({f[1]}={esc(f[2]) if f[2] else ''}*{mid + '*' if mid else ''}{esc(f[4]) if f[4] else ''})"
    if k == "ext":
        dn = ":dn" if f[4] else ""
        rule = f":{f[1]}" if f[1] else ""
        return f"({f[2] or ''}{dn}{rule}:={esc(f[3])})"
    raise ValueError(k)


def text(v) -> str:  # noqa: ANN001
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return "hex:" + v.hex()
    return str(v)


def run_libldap(host: str, port: int, script: list[dict]) -> list[dict]:
    import ldap
    import ldap.modlist  # noqa: F401

    conn = ldap.initialize(f"ldap://{host}:{port}")
    conn.set_option(ldap.OPT_PROTOCOL_VERSION, 3)
    conn.set_option(ldap.OPT_REFERRALS, 0)
    conn.set_option(ldap.OPT_NETWORK_TIMEOUT, TIMEOUT)
    conn.set_option(ldap.OPT_TIMEOUT, TIMEOUT)
    scopes = {"base": ldap.SCOPE_BASE, "one": ldap.SCOPE_ONELEVEL, "sub": ldap.SCOPE_SUBTREE}
    mods = {"add": ldap.MOD_ADD, "delete": ldap.MOD_DELETE, "replace": ldap.MOD_REPLACE, "increment": 3}
    out = []
    for st in script:
        a, op, r = st.get("args") or {}, st["op"], {"op": st["op"]}
        try:
            if op == "bind":
                conn.simple_bind_s(a.get("dn", ""), a.get("password", ""))
                r["result"] = 0
            elif op == "search":
                res = conn.search_ext_s(a.get("base", ""), scopes[a.get("scope", "base")],
                                        filter_text(a.get("filter", ["present", "objectClass"])),
                                        a.get("attributes") or None, attrsonly=int(bool(a.get("types_only"))),
                                        sizelimit=int(a.get("size_limit", 0)), timeout=TIMEOUT)
                r["result"] = 0
                r["entries"] = [[dn, {k: [text(x) for x in v] for k, v in (at or {}).items()}]
                                for dn, at in res if dn is not None]
                r["referrals"] = [text(x) for dn, at in res if dn is None for x in (at or [])]
            elif op == "add":
                conn.add_s(a["dn"], [(t, [str(v).encode() for v in vs]) for t, vs in a.get("attributes", [])])
                r["result"] = 0
            elif op == "modify":
                conn.modify_s(a["dn"], [(mods[c["op"]], c["type"], [str(v).encode() for v in c.get("values", [])] or None)
                                        for c in a.get("changes", [])])
                r["result"] = 0
            elif op == "delete":
                conn.delete_s(a["dn"])
                r["result"] = 0
            elif op == "moddn":
                conn.rename_s(a["dn"], a["newrdn"], a.get("newsuperior"), int(bool(a.get("deleteoldrdn", True))))
                r["result"] = 0
            elif op == "compare":
                r["result"] = 6 if conn.compare_s(a["dn"], a["attr"], str(a["value"]).encode()) else 5
            else:
                r["result"] = "unsupported"
        except ldap.LDAPError as e:
            info = e.args[0] if e.args and isinstance(e.args[0], dict) else {}
            r["result"] = info.get("result", type(e).__name__)
            r["error"] = type(e).__name__
            if info.get("matched"):
                r["matched"] = info["matched"]
        out.append(r)
    try:
        conn.unbind_s()
    except Exception:  # noqa: BLE001
        pass
    return out


def run_ldap3(host: str, port: int, script: list[dict]) -> list[dict]:
    import ldap3
    from ldap3.core.exceptions import LDAPException

    server = ldap3.Server(host, port=port, get_info=ldap3.NONE, connect_timeout=TIMEOUT)
    conn = None
    scopes = {"base": ldap3.BASE, "one": ldap3.LEVEL, "sub": ldap3.SUBTREE}
    mods = {"add": ldap3.MODIFY_ADD, "delete": ldap3.MODIFY_DELETE, "replace": ldap3.MODIFY_REPLACE,
            "increment": ldap3.MODIFY_INCREMENT}
    out = []
    for st in script:
        a, op, r = st.get("args") or {}, st["op"], {"op": st["op"]}
        try:
            if conn is None:
                conn = ldap3.Connection(server, receive_timeout=TIMEOUT, auto_referrals=False, raise_exceptions=False)
                conn.open()
            if op == "bind":
                conn.user, conn.password = a.get("dn", "") or None, a.get("password", "") or None
                conn.authentication = ldap3.SIMPLE if a.get("dn") or a.get("password") else ldap3.ANONYMOUS
                conn.bind()
            elif op == "search":
                conn.search(a.get("base", ""), filter_text(a.get("filter", ["present", "objectClass"])),
                            search_scope=scopes[a.get("scope", "base")], attributes=a.get("attributes") or None,
                            types_only=bool(a.get("types_only")), size_limit=int(a.get("size_limit", 0)))
                r["entries"] = [[e["dn"], {k: [text(x) for x in (v or [])] for k, v in (e.get("raw_attributes") or {}).items()}]
                                for e in (conn.response or []) if e.get("type") == "searchResEntry"]
                r["referrals"] = [text(x) for e in (conn.response or []) if e.get("type") == "searchResRef"
                                  for x in e.get("uri") or []]
            elif op == "add":
                conn.add(a["dn"], attributes={t: vs for t, vs in a.get("attributes", [])})
            elif op == "modify":
                conn.modify(a["dn"], {c["type"]: [(mods[c["op"]], c.get("values", []))] for c in a.get("changes", [])})
            elif op == "delete":
                conn.delete(a["dn"])
            elif op == "moddn":
                conn.modify_dn(a["dn"], a["newrdn"], delete_old_dn=bool(a.get("deleteoldrdn", True)),
                               new_superior=a.get("newsuperior"))
            elif op == "compare":
                conn.compare(a["dn"], a["attr"], a["value"])
            else:
                r["result"] = "unsupported"
            if "result" not in r:
                res = conn.result or {}
                r["result"] = res.get("result", "no-result")
                if res.get("dn"):
                    r["matched"] = res["dn"]
        except LDAPException as e:
            r["result"] = type(e).__name__
            r["error"] = type(e).__name__
        out.append(r)
    if conn is not None:
        try:
            conn.unbind()
        except Exception:  # noqa: BLE001
            pass
    return out


def main() -> int:
    lib, host, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
    script = json.load(sys.stdin)
    res = (run_libldap if lib == "libldap" else run_ldap3)(host, port, script)
    print(json.dumps(res, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

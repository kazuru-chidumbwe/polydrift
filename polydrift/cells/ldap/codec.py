"""LDAPv3 BER encoder (fixture side) and decoder (server side) for the LDAP cell (RFC 4511 Section 5.1).

The encoder builds exactly the BER a fixture asks for, including encodings a conforming client never sends (wrong
version, unknown tags, empty SASL mechanism, critical unknown controls); a fixture can also give raw hex. The decoder
never raises: what it cannot parse is returned as {"op": "UNDECODABLE", ...}.
"""

from __future__ import annotations

from typing import Any

# ---------- BER primitives ----------


def length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + length(len(value)) + value


def integer(n: int, tag: int = 0x02) -> bytes:
    if n == 0:
        return tlv(tag, b"\x00")
    b = n.to_bytes((n.bit_length() + 8) // 8, "big", signed=True)
    return tlv(tag, b)


def octets(s: str | bytes, tag: int = 0x04) -> bytes:
    return tlv(tag, s.encode() if isinstance(s, str) else s)


def boolean(v: bool, tag: int = 0x01) -> bytes:
    return tlv(tag, b"\xff" if v else b"\x00")


def seq(*parts: bytes, tag: int = 0x30) -> bytes:
    return tlv(tag, b"".join(parts))


# ---------- filters (RFC 4511 4.5.1.7), from a small prefix syntax ----------


def filt(f: Any) -> bytes:
    """Filter from a nested list: ["eq", attr, value], ["present", attr], ["and", f...], ["or", f...], ["not", f],
    ["sub", attr, initial, [any...], final], ["ge"/"le"/"approx", attr, value],
    ["ext", rule, attr, value, dnattrs]."""
    k = f[0]
    if k == "and":
        return seq(*[filt(x) for x in f[1:]], tag=0xA0)
    if k == "or":
        return seq(*[filt(x) for x in f[1:]], tag=0xA1)
    if k == "not":
        return seq(filt(f[1]), tag=0xA2)
    if k in ("eq", "ge", "le", "approx"):
        tag = {"eq": 0xA3, "ge": 0xA5, "le": 0xA6, "approx": 0xA8}[k]
        return seq(octets(f[1]), octets(f[2]), tag=tag)
    if k == "present":
        return octets(f[1], tag=0x87)
    if k == "sub":
        parts = b""
        if f[2] is not None:
            parts += octets(f[2], tag=0x80)
        for a in f[3] or []:
            parts += octets(a, tag=0x81)
        if f[4] is not None:
            parts += octets(f[4], tag=0x82)
        return seq(octets(f[1]), seq(parts), tag=0xA4)
    if k == "ext":
        body = b""
        if f[1]:
            body += octets(f[1], tag=0x81)
        if f[2]:
            body += octets(f[2], tag=0x82)
        body += octets(f[3], tag=0x83)
        if f[4]:
            body += boolean(True, tag=0x84)
        return seq(body, tag=0xA9)
    raise ValueError(f"unknown filter {k}")


# ---------- requests ----------


def controls(ctrls: list[dict[str, Any]] | None) -> bytes:
    if not ctrls:
        return b""
    out = b""
    for c in ctrls:
        body = octets(c["oid"])
        if c.get("critical"):
            body += boolean(True)
        if "value" in c:
            body += octets(bytes.fromhex(c["value"]) if c.get("hex") else c["value"])
        out += seq(body)
    return seq(out, tag=0xA0)


def message(msgid: int, op: bytes, ctrls: list[dict[str, Any]] | None = None) -> bytes:
    return seq(integer(msgid), op, controls(ctrls))


def bind(a: dict[str, Any]) -> bytes:
    if "sasl" in a:
        cred = octets(a["sasl"]["mechanism"])
        if "credentials" in a["sasl"]:
            cred += octets(a["sasl"]["credentials"])
        auth = seq(cred, tag=0xA3)
    else:
        auth = octets(a.get("password", ""), tag=0x80)
    return seq(integer(int(a.get("version", 3))), octets(a.get("dn", "")), auth, tag=0x60)


def unbind(a: dict[str, Any]) -> bytes:
    return tlv(0x42, b"")


def search(a: dict[str, Any]) -> bytes:
    scope = {"base": 0, "one": 1, "sub": 2}[a.get("scope", "base")]
    deref = {"never": 0, "searching": 1, "finding": 2, "always": 3}[a.get("deref", "never")]
    attrs = seq(*[octets(x) for x in a.get("attributes", [])])
    return seq(octets(a.get("base", "")), integer(scope, 0x0A), integer(deref, 0x0A),
               integer(int(a.get("size_limit", 0))), integer(int(a.get("time_limit", 0))),
               boolean(bool(a.get("types_only", False))), filt(a.get("filter", ["present", "objectClass"])), attrs,
               tag=0x63)


def modify(a: dict[str, Any]) -> bytes:
    ops = {"add": 0, "delete": 1, "replace": 2, "increment": 3}
    changes = b""
    for c in a.get("changes", []):
        changes += seq(integer(ops[c["op"]], 0x0A), seq(octets(c["type"]), seq(*[octets(v) for v in c.get("values", [])], tag=0x31)))
    return seq(octets(a["dn"]), seq(changes), tag=0x66)


def add(a: dict[str, Any]) -> bytes:
    attrs = b"".join(seq(octets(t), seq(*[octets(v) for v in vs], tag=0x31)) for t, vs in a.get("attributes", []))
    return seq(octets(a["dn"]), seq(attrs), tag=0x68)


def delete(a: dict[str, Any]) -> bytes:
    return octets(a["dn"], tag=0x4A)


def moddn(a: dict[str, Any]) -> bytes:
    body = octets(a["dn"]) + octets(a["newrdn"]) + boolean(bool(a.get("deleteoldrdn", True)))
    if a.get("newsuperior") is not None:
        body += octets(a["newsuperior"], tag=0x80)
    return seq(body, tag=0x6C)


def compare(a: dict[str, Any]) -> bytes:
    return seq(octets(a["dn"]), seq(octets(a["attr"]), octets(a["value"])), tag=0x6E)


def abandon(a: dict[str, Any]) -> bytes:
    return integer(int(a["id"]), 0x50)


def extended(a: dict[str, Any]) -> bytes:
    body = octets(a["oid"], tag=0x80)
    if "value" in a:
        body += octets(a["value"], tag=0x81)
    return seq(body, tag=0x77)


BUILDERS = {"bind": bind, "unbind": unbind, "search": search, "modify": modify, "add": add, "delete": delete,
            "moddn": moddn, "compare": compare, "abandon": abandon, "extended": extended}

# ops that get a response (so the driver waits for one)
ANSWERED = {"bind", "search", "modify", "add", "delete", "moddn", "compare", "extended"}

# ---------- decoder ----------

RESPONSE_OPS = {0x61: "BindResponse", 0x64: "SearchResultEntry", 0x65: "SearchResultDone", 0x67: "ModifyResponse",
                0x69: "AddResponse", 0x6B: "DelResponse", 0x6D: "ModifyDNResponse", 0x6F: "CompareResponse",
                0x73: "SearchResultReference", 0x78: "ExtendedResponse", 0x79: "IntermediateResponse"}


def _read(b: bytes, i: int) -> tuple[int, bytes, int]:
    tag = b[i]
    n = b[i + 1]
    j = i + 2
    if n & 0x80:
        k = n & 0x7F
        if k == 0 or k > 4:
            raise ValueError("length form")
        n = int.from_bytes(b[j:j + k], "big")
        j += k
    if j + n > len(b):
        raise ValueError("short")
    return tag, b[j:j + n], j + n


def _children(b: bytes) -> list[tuple[int, bytes]]:
    out, i = [], 0
    while i < len(b):
        t, v, i = _read(b, i)
        out.append((t, v))
    return out


def _int(v: bytes) -> int:
    return int.from_bytes(v, "big", signed=True) if v else 0


def split_stream(buf: bytes) -> tuple[list[bytes], bytes]:
    out = []
    while len(buf) >= 2:
        try:
            _t, _v, end = _read(buf, 0)
        except (ValueError, IndexError):
            break
        out.append(buf[:end])
        buf = buf[end:]
    return out, buf


def decode(msg: bytes) -> dict[str, Any]:
    try:
        tag, body, _ = _read(msg, 0)
        if tag != 0x30:
            raise ValueError(f"message tag 0x{tag:02x}")
        kids = _children(body)
        mid = _int(kids[0][1])
        optag, opv = kids[1]
        name = RESPONSE_OPS.get(optag, f"op0x{optag:02x}")
        p: dict[str, Any] = {"op": name, "id": mid}
        if name == "SearchResultEntry":
            k = _children(opv)
            p["dn"] = k[0][1].decode("utf-8", "replace")
            attrs = {}
            for _t, a in _children(k[1][1]):
                ak = _children(a)
                attrs[ak[0][1].decode("utf-8", "replace")] = [v.decode("utf-8", "replace") for _t2, v in _children(ak[1][1])]
            p["attributes"] = attrs
        elif name == "SearchResultReference":
            p["uris"] = [v.decode("utf-8", "replace") for _t, v in _children(opv)]
        else:
            k = _children(opv)
            p["result"] = _int(k[0][1])
            p["matched"] = k[1][1].decode("utf-8", "replace")
            p["diagnostic"] = k[2][1].decode("utf-8", "replace")
            for t, v in k[3:]:
                if t == 0x8A:
                    p["response_name"] = v.decode("utf-8", "replace")
                elif t == 0x8B:
                    p["response_value"] = v.hex()
                elif t == 0x87:
                    p["sasl_creds"] = v.hex()
                elif t == 0xA3:
                    p["referral"] = [x.decode("utf-8", "replace") for _t, x in _children(v)]
        if len(kids) > 2 and kids[2][0] == 0xA0:
            p["controls"] = [_children(c)[0][1].decode() for _t, c in _children(kids[2][1])]
        return p
    except Exception as e:  # noqa: BLE001
        return {"op": "UNDECODABLE", "error": str(e), "hex": msg[:64].hex()}

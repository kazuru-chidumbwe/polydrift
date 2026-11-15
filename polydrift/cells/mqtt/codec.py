"""MQTT 5.0 and 3.1.1 packet encoder and decoder for the MQTT cell.

The encoder builds the packets fixtures send. It deliberately allows what a conforming client would never send
(reserved bits, wrong lengths, invalid strings), because boundary and malformed fixtures need those bytes; a fixture
can also give the exact bytes as hex. The decoder reads what a broker sends and never raises on bad input: an
undecodable packet becomes ``{"type": "UNDECODABLE", ...}`` so the observation still records it.
"""

from __future__ import annotations

import struct
from typing import Any

TYPES = {
    1: "CONNECT", 2: "CONNACK", 3: "PUBLISH", 4: "PUBACK", 5: "PUBREC", 6: "PUBREL", 7: "PUBCOMP",
    8: "SUBSCRIBE", 9: "SUBACK", 10: "UNSUBSCRIBE", 11: "UNSUBACK", 12: "PINGREQ", 13: "PINGRESP",
    14: "DISCONNECT", 15: "AUTH",
}
TYPE_IDS = {v: k for k, v in TYPES.items()}

# property id -> (name, wire type)
PROPS: dict[int, tuple[str, str]] = {
    0x01: ("payload_format_indicator", "byte"),
    0x02: ("message_expiry_interval", "u32"),
    0x03: ("content_type", "utf8"),
    0x08: ("response_topic", "utf8"),
    0x09: ("correlation_data", "bin"),
    0x0B: ("subscription_identifier", "varint"),
    0x11: ("session_expiry_interval", "u32"),
    0x12: ("assigned_client_identifier", "utf8"),
    0x13: ("server_keep_alive", "u16"),
    0x15: ("authentication_method", "utf8"),
    0x16: ("authentication_data", "bin"),
    0x17: ("request_problem_information", "byte"),
    0x18: ("will_delay_interval", "u32"),
    0x19: ("request_response_information", "byte"),
    0x1A: ("response_information", "utf8"),
    0x1C: ("server_reference", "utf8"),
    0x1F: ("reason_string", "utf8"),
    0x21: ("receive_maximum", "u16"),
    0x22: ("topic_alias_maximum", "u16"),
    0x23: ("topic_alias", "u16"),
    0x24: ("maximum_qos", "byte"),
    0x25: ("retain_available", "byte"),
    0x26: ("user_property", "pair"),
    0x27: ("maximum_packet_size", "u32"),
    0x28: ("wildcard_subscription_available", "byte"),
    0x29: ("subscription_identifier_available", "byte"),
    0x2A: ("shared_subscription_available", "byte"),
}
PROP_IDS = {name: pid for pid, (name, _t) in PROPS.items()}


# ---------- primitives ----------

def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        if n:
            b |= 0x80
        out.append(b)
        if not n:
            return bytes(out)


def utf8(s: str | bytes) -> bytes:
    b = s if isinstance(s, bytes) else s.encode("utf-8", "surrogatepass")
    return struct.pack(">H", len(b)) + b


def binary(b: bytes | str) -> bytes:
    if isinstance(b, str):
        b = b.encode()
    return struct.pack(">H", len(b)) + b


def props(p: dict[str, Any] | None, version: int = 5) -> bytes:
    """Encode a property list. ``user_property`` takes a list of [key, value]; order is kept. ``raw`` appends bytes
    verbatim (for malformed properties)."""
    if version != 5:
        return b""
    body = bytearray()
    for name, value in (p or {}).items():
        if name == "raw":
            body += bytes.fromhex(value)
            continue
        pid = PROP_IDS[name]
        kind = PROPS[pid][1]
        values = value if (kind == "pair" or (name == "subscription_identifier" and isinstance(value, list))) else [value]
        for v in values:
            body.append(pid)
            if kind == "byte":
                body.append(int(v))
            elif kind == "u16":
                body += struct.pack(">H", int(v))
            elif kind == "u32":
                body += struct.pack(">I", int(v))
            elif kind == "varint":
                body += varint(int(v))
            elif kind == "utf8":
                body += utf8(v)
            elif kind == "bin":
                body += binary(bytes.fromhex(v["hex"]) if isinstance(v, dict) else v)
            elif kind == "pair":
                body += utf8(v[0]) + utf8(v[1])
    return varint(len(body)) + bytes(body)


def packet(ptype: int, flags: int, body: bytes, remaining_length: int | None = None) -> bytes:
    rl = len(body) if remaining_length is None else remaining_length
    return bytes([(ptype << 4) | (flags & 0x0F)]) + varint(rl) + body


# ---------- packet builders (fixture side) ----------

def connect(a: dict[str, Any]) -> bytes:
    v = int(a.get("version", 5))
    name = a.get("protocol_name", "MQTT" if v >= 4 else "MQIsdp")
    flags = 0
    if a.get("clean_start", True):
        flags |= 0x02
    will = a.get("will")
    if will:
        flags |= 0x04 | ((int(will.get("qos", 0)) & 3) << 3) | (0x20 if will.get("retain") else 0)
    elif a.get("will_qos_bits") is not None or a.get("will_retain_bit"):
        flags |= (int(a.get("will_qos_bits") or 0) & 3) << 3 | (0x20 if a.get("will_retain_bit") else 0)
    if a.get("username") is not None:
        flags |= 0x80
    if a.get("password") is not None:
        flags |= 0x40
    if a.get("reserved_bit"):
        flags |= 0x01
    body = bytearray(utf8(name) + bytes([int(a.get("protocol_level", v))]) + bytes([flags]))
    body += struct.pack(">H", int(a.get("keep_alive", 60)))
    body += props(a.get("properties"), v)
    body += utf8(a.get("client_id", ""))
    if will:
        body += props(will.get("properties"), v)
        body += utf8(will["topic"]) + binary(will.get("payload", ""))
    if a.get("username") is not None:
        body += utf8(a["username"])
    if a.get("password") is not None:
        body += binary(a["password"])
    return packet(1, int(a.get("fixed_flags", 0)), bytes(body))


def publish(a: dict[str, Any]) -> bytes:
    v = int(a.get("version", 5))
    qos = int(a.get("qos", 0))
    flags = (0x08 if a.get("dup") else 0) | ((qos & 3) << 1) | (0x01 if a.get("retain") else 0)
    if "flags" in a:
        flags = int(a["flags"])
    body = bytearray(utf8(a.get("topic", "")))
    if qos > 0 or a.get("packet_id") is not None:
        body += struct.pack(">H", int(a.get("packet_id", 1)))
    body += props(a.get("properties"), v)
    payload = a.get("payload", "")
    body += payload.encode() if isinstance(payload, str) else bytes(payload)
    return packet(3, flags, bytes(body))


def ack(ptype: str, a: dict[str, Any]) -> bytes:
    """PUBACK, PUBREC, PUBREL, PUBCOMP."""
    v = int(a.get("version", 5))
    t = TYPE_IDS[ptype]
    flags = int(a.get("fixed_flags", 0x02 if ptype == "PUBREL" else 0))
    body = struct.pack(">H", int(a.get("packet_id", 1)))
    if v == 5 and (a.get("reason_code", 0) or a.get("properties")):
        body += bytes([int(a.get("reason_code", 0))]) + props(a.get("properties"), v)
    return packet(t, flags, body)


def subscribe(a: dict[str, Any]) -> bytes:
    v = int(a.get("version", 5))
    body = bytearray(struct.pack(">H", int(a.get("packet_id", 1))))
    body += props(a.get("properties"), v)
    for f in a.get("filters", []):
        if isinstance(f, str):
            f = {"topic": f}
        opts = int(f.get("qos", 0)) & 3
        if v == 5:
            opts |= (0x04 if f.get("no_local") else 0) | (0x08 if f.get("retain_as_published") else 0)
            opts |= (int(f.get("retain_handling", 0)) & 3) << 4
        if "options" in f:
            opts = int(f["options"])
        body += utf8(f["topic"]) + bytes([opts])
    return packet(8, int(a.get("fixed_flags", 0x02)), bytes(body))


def unsubscribe(a: dict[str, Any]) -> bytes:
    v = int(a.get("version", 5))
    body = bytearray(struct.pack(">H", int(a.get("packet_id", 1))))
    body += props(a.get("properties"), v)
    for t in a.get("filters", []):
        body += utf8(t)
    return packet(10, int(a.get("fixed_flags", 0x02)), bytes(body))


def disconnect(a: dict[str, Any]) -> bytes:
    v = int(a.get("version", 5))
    if v == 5 and (a.get("reason_code") or a.get("properties")):
        return packet(14, int(a.get("fixed_flags", 0)), bytes([int(a.get("reason_code", 0))]) + props(a.get("properties"), v))
    return packet(14, int(a.get("fixed_flags", 0)), b"")


def pingreq(a: dict[str, Any]) -> bytes:
    return packet(12, int(a.get("fixed_flags", 0)), b"")


BUILDERS = {
    "connect": connect, "publish": publish, "subscribe": subscribe, "unsubscribe": unsubscribe,
    "disconnect": disconnect, "pingreq": pingreq,
    "puback": lambda a: ack("PUBACK", a), "pubrec": lambda a: ack("PUBREC", a),
    "pubrel": lambda a: ack("PUBREL", a), "pubcomp": lambda a: ack("PUBCOMP", a),
}


# ---------- decoder (broker side) ----------

class _R:
    def __init__(self, b: bytes):
        self.b, self.i = b, 0

    def left(self) -> int:
        return len(self.b) - self.i

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.b):
            raise ValueError("short")
        out = self.b[self.i:self.i + n]
        self.i += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def varint(self) -> int:
        n, mult = 0, 1
        for _ in range(4):
            b = self.u8()
            n += (b & 0x7F) * mult
            if not b & 0x80:
                return n
            mult *= 128
        raise ValueError("varint too long")

    def utf8(self) -> str:
        return self.take(self.u16()).decode("utf-8", "replace")

    def binary(self) -> str:
        return self.take(self.u16()).hex()


def read_props(r: _R) -> dict[str, Any]:
    end = r.varint() + r.i
    out: dict[str, Any] = {}
    while r.i < end:
        pid = r.varint()
        name, kind = PROPS.get(pid, (f"unknown_0x{pid:02x}", "?"))
        if kind == "byte":
            val: Any = r.u8()
        elif kind == "u16":
            val = r.u16()
        elif kind == "u32":
            val = r.u32()
        elif kind == "varint":
            val = r.varint()
        elif kind == "utf8":
            val = r.utf8()
        elif kind == "bin":
            val = r.binary()
        elif kind == "pair":
            val = [r.utf8(), r.utf8()]
        else:
            raise ValueError(f"unknown property 0x{pid:02x}")
        if name in ("user_property", "subscription_identifier"):
            out.setdefault(name, []).append(val)
        else:
            out[name] = val
    return out


def decode(first: int, body: bytes, version: int = 5) -> dict[str, Any]:
    t = first >> 4
    flags = first & 0x0F
    name = TYPES.get(t, f"TYPE{t}")
    p: dict[str, Any] = {"type": name, "flags": flags}
    r = _R(body)
    try:
        if name == "CONNACK":
            p["ack_flags"] = r.u8()
            p["session_present"] = p["ack_flags"] & 1
            p["reason_code"] = r.u8()
            if version == 5 and r.left():
                p["properties"] = read_props(r)
        elif name == "PUBLISH":
            p["qos"] = (flags >> 1) & 3
            p["retain"] = flags & 1
            p["dup"] = (flags >> 3) & 1
            p["topic"] = r.utf8()
            if p["qos"]:
                p["packet_id"] = r.u16()
            if version == 5:
                p["properties"] = read_props(r)
            p["payload"] = r.take(r.left()).decode("utf-8", "replace")
        elif name in ("PUBACK", "PUBREC", "PUBREL", "PUBCOMP"):
            p["packet_id"] = r.u16()
            p["reason_code"] = r.u8() if r.left() else 0
            if version == 5 and r.left():
                p["properties"] = read_props(r)
        elif name in ("SUBACK", "UNSUBACK"):
            p["packet_id"] = r.u16()
            if version == 5:
                p["properties"] = read_props(r)
            p["reason_codes"] = list(r.take(r.left()))
        elif name == "DISCONNECT":
            p["reason_code"] = r.u8() if r.left() else 0
            if version == 5 and r.left():
                p["properties"] = read_props(r)
        elif name == "AUTH":
            p["reason_code"] = r.u8() if r.left() else 0
            if r.left():
                p["properties"] = read_props(r)
        if r.left() and name not in ("PUBLISH",):
            p["trailing"] = r.take(r.left()).hex()
    except ValueError as e:
        return {"type": "UNDECODABLE", "of": name, "flags": flags, "error": str(e), "hex": body.hex()}
    return p


def split_stream_raw(buf: bytes) -> tuple[list[tuple[int, bytes, bytes]], bytes]:
    """Cut complete packets off the front of a receive buffer; returns ([(first byte, body, exact bytes)], rest).
    The exact bytes keep the sender's Remaining Length encoding (a non-minimal one included) for consumer replay."""
    out: list[tuple[int, bytes, bytes]] = []
    while len(buf) >= 2:
        n, mult, i = 0, 1, 1
        ok = False
        while i < len(buf) and i <= 4:
            b = buf[i]
            n += (b & 0x7F) * mult
            mult *= 128
            i += 1
            if not b & 0x80:
                ok = True
                break
        if not ok or len(buf) < i + n:
            break
        out.append((buf[0], bytes(buf[i:i + n]), bytes(buf[:i + n])))
        buf = buf[i + n:]
    return out, buf


def split_stream(buf: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """Cut complete packets off the front of a receive buffer; returns (packets, rest)."""
    pkts, rest = split_stream_raw(buf)
    return [(f, b) for f, b, _raw in pkts], rest

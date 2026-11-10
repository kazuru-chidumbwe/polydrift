"""MQTT reason code -> decision class (manuscript 3.3).

Classes are defined by the client's next action. MQTT 5.0 Section 2.4 (Reason Code table); MQTT 3.1.1 CONNACK return
codes 0 to 5 and the SUBACK failure code 0x80 are mapped by their meaning. Codes below 0x80 report success.
"""

from __future__ import annotations

V5: dict[int, str] = {
    0x80: "stop",            # unspecified error
    0x81: "fix-request",     # malformed packet
    0x82: "fix-request",     # protocol error
    0x83: "stop",            # implementation specific error
    0x84: "fix-request",     # unsupported protocol version
    0x85: "fix-request",     # client identifier not valid
    0x86: "authenticate",    # bad user name or password
    0x87: "stop",            # not authorized
    0x88: "retry-later",     # server unavailable
    0x89: "retry-later",     # server busy
    0x8A: "stop",            # banned
    0x8B: "retry-later",     # server shutting down
    0x8C: "authenticate",    # bad authentication method
    0x8D: "stop",            # keep alive timeout
    0x8E: "stop",            # session taken over
    0x8F: "fix-request",     # topic filter invalid
    0x90: "fix-request",     # topic name invalid
    0x91: "fix-request",     # packet identifier in use
    0x92: "fix-request",     # packet identifier not found
    0x93: "fix-request",     # receive maximum exceeded
    0x94: "fix-request",     # topic alias invalid
    0x95: "fix-request",     # packet too large
    0x96: "retry-later",     # message rate too high
    0x97: "retry-later",     # quota exceeded
    0x98: "stop",            # administrative action
    0x99: "fix-request",     # payload format invalid
    0x9A: "fix-request",     # retain not supported
    0x9B: "fix-request",     # QoS not supported
    0x9C: "redirect",        # use another server
    0x9D: "redirect",        # server moved
    0x9E: "fix-request",     # shared subscriptions not supported
    0x9F: "retry-later",     # connection rate exceeded
    0xA0: "stop",            # maximum connect time
    0xA1: "fix-request",     # subscription identifiers not supported
    0xA2: "fix-request",     # wildcard subscriptions not supported
}

V311_CONNACK: dict[int, str] = {
    0: "proceed", 1: "fix-request", 2: "fix-request", 3: "retry-later", 4: "authenticate", 5: "stop",
}


def decision(packet: dict, version: int = 5) -> str | None:
    """Decision class of one received packet, or None when the packet carries no reason."""
    t = packet.get("type")
    if t == "CONNACK":
        rc = packet.get("reason_code")
        if version < 5:
            return V311_CONNACK.get(rc, "stop")
        return "proceed" if rc is not None and rc < 0x80 else V5.get(rc, "stop")
    if t in ("SUBACK", "UNSUBACK"):
        codes = packet.get("reason_codes") or []
        if not codes:
            return None
        worst = max(codes)
        return "proceed" if worst < 0x80 else V5.get(worst, "stop")
    if t in ("PUBACK", "PUBREC", "PUBREL", "PUBCOMP", "DISCONNECT", "AUTH"):
        rc = packet.get("reason_code", 0)
        return "proceed" if rc < 0x80 else V5.get(rc, "stop")
    if t == "UNDECODABLE":
        return "no-decision"
    return None

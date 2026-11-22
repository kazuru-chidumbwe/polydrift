#!/usr/bin/env python3
"""RQ5 ground truth from MBFuzzer (Song et al., USENIX Security 2025), Tables 10 and 11.

One fixture per non-compliance statement (N1 to N53) and one case per (statement, broker) that Table 10 marks as
affected, for the five brokers of the MQTT cell. Writes the block between the "generated: mbfuzzer" markers in
polydrift/cells/mqtt/fixtures_rq5.yaml and polydrift/rq5/cases.yaml; run again after editing the table below.

Affected versions are the ones the paper tested (its Table 2): EMQX 5.6.0, Mosquitto 2.0.18, NanoMQ 0.21.10,
VerneMQ 1.13.0. For HiveMQ the paper ran HiveMQ Platform 4.24.0 (the artifact's hivemq.jar, Build-Time 2024-01-09);
the community edition of the same core is HiveMQ CE 2024.1, released the same day, so the HiveMQ cases name CE
2024.1 and the recall run reports whether it reproduces.

Table 10 marks: affected (tick), confirmed by the developer (dagger), fixed (double dagger). The paper does not name
fixed versions. Until a fixed release is located in the vendor's tracker (FIXED below), a case is fix-unconfirmed
and its later versions are the campaign's current versions of that broker (run as a bisect, reported as observed).

FlashMQ joined the cell on 22 Nov 2026 (MBFuzzer tested 1.12.1), so its 23 affected statements are included, six of
them FlashMQ-only. N29's NanoMQ instance is case M1 (CVE-2024-42655); its FlashMQ instance uses a broker-neutral fixture.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "polydrift" / "cells" / "mqtt" / "fixtures_rq5.yaml"
CASES = ROOT / "polydrift" / "rq5" / "cases.yaml"
BEGIN, END = "# BEGIN generated: mbfuzzer (tools/gen_rq5_mbfuzzer.py)", "# END generated: mbfuzzer"

AFFECTED = {"emqx": "5.6.0", "mosquitto": "2.0.18", "nanomq": "0.21.10", "vernemq": "1.13.0", "hivemq-ce": "2024.1"}
CURRENT = {"emqx": "6.3.2", "mosquitto": "2.0.22", "nanomq": "0.25.7", "vernemq": "2.2.1", "hivemq-ce": "2026.5"}
# First fixed release per (statement, broker), from the vendor tracker; filled as each is located.
FIXED: dict[tuple[str, str], list[str]] = {}

REJECT = ["__status__", "decision_class", "native_codes", "actor_closed"]
FORWARD = REJECT + ["deliveries", "publish_meta", "forwarded_props"]

V5 = {"client": "a", "op": "connect", "args": {"client_id": "{C}a"}}
V4 = {"client": "a", "op": "connect", "args": {"client_id": "{C}a", "version": 4}}
SUB_B = [{"client": "b", "op": "connect", "args": {"client_id": "{C}b"}},
         {"client": "b", "op": "subscribe", "args": {"packet_id": 1, "filters": [{"topic": "{T}x"}]}}]
SUB_B_LIT = [{"client": "b", "op": "connect", "args": {"client_id": "{C}b"}},
             {"client": "b", "op": "subscribe", "args": {"packet_id": 1, "filters": [{"topic": "x"}]}}]


def pub(props=None, **kw):
    a = {"topic": "{T}x", "payload": "p", "qos": 1, "packet_id": 5}
    a.update(kw)
    if props is not None:
        a["properties"] = props
    return {"client": "a", "op": "publish", "args": a}


def connect5(**args):
    a = {"client_id": "{C}a"}
    a.update(args)
    return {"client": "a", "op": "connect", "args": a}


def hx(h):
    return {"client": "a", "op": "hex", "hex": h, "expect_reply": True}


# id: (statement, keyword, marks {broker: "a" | "a+c" | "a+c+f"}, axes, steps, step of the judged reply, note)
T = {
    "N1": ("MQTT-2.3.1-1", "MUST", {"emqx": "a", "nanomq": "a+c+f", "vernemq": "a+c+f", "hivemq-ce": "a+c+f"}, REJECT,
           [V4, hx("82 06 0000 0001 78 00")], 2, "MQTT 3.1.1 SUBSCRIBE with Packet Identifier 0"),
    "N3": ("MQTT-3.1.2-19", "MUST", {"emqx": "a+c+f", "mosquitto": "a+c", "vernemq": "a+c+f", "hivemq-ce": "a+c"}, REJECT,
           [hx("10 0e 0004 4d515454 04 82 003c 0002 7064")], 1, "MQTT 3.1.1 CONNECT with User Name Flag 1 and no user name"),
    "N4": ("MQTT-3.1.2-22", "MUST", {"emqx": "a+c+f", "nanomq": "a+c"}, REJECT,
           [hx("10 12 0004 4d515454 04 42 003c 0002 7071 0002 7077")], 1,
           "MQTT 3.1.1 CONNECT with Password Flag 1 and User Name Flag 0"),
    "N5": ("MQTT-3.1.2-9", "MUST", {"hivemq-ce": "a+c+f"}, REJECT,
           [hx("10 0e 0004 4d515454 04 06 003c 0002 7072")], 1, "MQTT 3.1.1 CONNECT with Will Flag 1 and no Will fields"),
    "N6": ("MQTT-3.1.3-11", "MUST", {"vernemq": "a+c+f", "hivemq-ce": "a+c+f"}, REJECT,
           [hx("10 12 0004 4d515454 04 82 003c 0002 7073 0002 61ff")], 1, "MQTT 3.1.1 user name not UTF-8"),
    "N7": ("MQTT-3.1.3-4", "MUST", {"vernemq": "a+c+f"}, REJECT,
           [hx("10 0f 0004 4d515454 04 02 003c 0003 70ff74")], 1, "MQTT 3.1.1 ClientId not UTF-8"),
    "N8": ("MQTT-3.1.3-8", "MUST", {"nanomq": "a"}, REJECT,
           [{"client": "a", "op": "connect", "args": {"client_id": "", "version": 4, "clean_start": False}}], 1,
           "MQTT 3.1.1 zero-byte ClientId with CleanSession 0: CONNACK 0x02, then close"),
    "N10": ("MQTT-3.1.2-11", "MUST", {"emqx": "a+c+f", "nanomq": "a+c+f"}, REJECT,
            [connect5(will_qos_bits=1)], 1, "Will Flag 0 with Will QoS 1"),
    "N11": ("MQTT-3.1.2-13", "MUST", {"emqx": "a+c+f", "nanomq": "a+c+f"}, REJECT,
            [connect5(will_retain_bit=True)], 1, "Will Flag 0 with Will Retain 1"),
    "N12": ("MQTT-3.1.2-3", "MUST", {"nanomq": "a+c+f"}, REJECT, [connect5(reserved_bit=True)], 1,
            "CONNECT reserved flag set"),
    "N14": ("MQTT-3.1.3-12", "MUST", {"vernemq": "a+c+f", "hivemq-ce": "a"}, REJECT,
            [hx("10 13 0004 4d515454 05 82 003c 00 0002 7074 0002 61ff")], 1, "MQTT 5 user name not UTF-8"),
    "N15": ("MQTT-3.10.3-1", "MUST", {"emqx": "a+c", "vernemq": "a+c+f"}, REJECT,
            [V5, hx("a2 07 0001 00 0002 61ff")], 2, "UNSUBSCRIBE Topic Filter not UTF-8"),
    "N16": ("MQTT-3.10.3-2", "MUST", {"mosquitto": "a"}, REJECT, [V5, hx("a2 03 0001 00")], 2,
            "UNSUBSCRIBE with no Topic Filter"),
    "N17": ("MQTT-3.2.0-2", "MUST", {"nanomq": "a+c+f"}, REJECT + ["packet_types"],
            [V5, connect5()], 2, "second CONNECT on one connection: at most one CONNACK"),
    "N18": ("MQTT-3.3.1-2", "MUST", {"nanomq": "a+c+f"}, FORWARD,
            SUB_B + [V5, pub(qos=0, dup=True, packet_id=None)], 4, "QoS 0 PUBLISH with DUP 1"),
    "N19": ("MQTT-3.3.2-1", "MUST", {"vernemq": "a+c+f"}, FORWARD,
            SUB_B_LIT + [V5, hx("30 07 0003 61ff62 00 70")], 4, "PUBLISH Topic Name not UTF-8"),
    "N20": ("MQTT-3.3.2-14", "MUST", {"mosquitto": "a+c", "nanomq": "a+c", "vernemq": "a+c+f"}, FORWARD,
            SUB_B + [V5, pub({"response_topic": "r/#"})], 4, "Response Topic with a wildcard"),
    "N21": ("MQTT-3.3.2-19", "MUST", {"nanomq": "a+c+f"}, FORWARD,
            SUB_B + [V5, pub({"raw": "030002ffff"})], 4, "Content Type not UTF-8"),
    "N22": ("MQTT-3.3.2-9", "MUST", {"nanomq": "a+c+f"}, FORWARD,
            SUB_B + [V5, pub({"topic_alias": 65535})], 4,
            "Topic Alias 65535, above the Topic Alias Maximum a broker announces (EMQX announces 65535)"),
    "N23": ("MQTT-3.3.4-6", "MUST", {"emqx": "a+c", "mosquitto": "a+c", "nanomq": "a+c", "vernemq": "a+c+f"}, FORWARD,
            SUB_B + [V5, pub({"subscription_identifier": 5})], 4, "client PUBLISH carrying a Subscription Identifier"),
    "N24": ("MQTT-3.4.2-1", "MUST", {"emqx": "a+c", "nanomq": "a+c"}, REJECT,
            [{"client": "b", "op": "connect", "args": {"client_id": "{C}b"}, "auto_ack": False},
             {"client": "b", "op": "subscribe", "args": {"packet_id": 1, "filters": [{"topic": "{T}x", "qos": 1}]}},
             V5, pub(), {"client": "b", "op": "puback", "args": {"packet_id": "last", "reason_code": 5}}], 5,
            "client PUBACK with Reason Code 0x05, not a PUBACK reason code"),
    "N25": ("MQTT-3.6.1-1", "MUST", {"nanomq": "a+c+f"}, REJECT,
            [V5, pub(qos=2, packet_id=9), {"client": "a", "op": "pubrel", "args": {"packet_id": 9, "fixed_flags": 0}}],
            3, "PUBREL fixed-header flags 0000"),
    "N26": ("MQTT-3.8.3-1", "MUST", {"emqx": "a+c", "vernemq": "a+c+f"}, REJECT,
            [V5, hx("82 08 0001 00 0002 61ff 00")], 2, "SUBSCRIBE Topic Filter not UTF-8"),
    "N27": ("MQTT-3.8.3-2", "MUST", {"mosquitto": "a"}, REJECT, [V5, hx("82 03 0001 00")], 2,
            "SUBSCRIBE with no Topic Filter"),
    "N28": ("MQTT-3.8.3-4", "MUST", {"mosquitto": "a+c+f", "vernemq": "a+c+f"}, REJECT + ["granted"],
            [V5, {"client": "a", "op": "subscribe",
                  "args": {"packet_id": 2, "filters": [{"topic": "$share/g/{T}n", "no_local": True}]}}], 2,
            "No Local 1 on a Shared Subscription"),
    "N30": ("MQTT-4.8.2-2", "MUST", {"mosquitto": "a+c+f", "nanomq": "a+c+f", "vernemq": "a+c+f"}, REJECT + ["granted"],
            [V5, {"client": "a", "op": "subscribe", "args": {"packet_id": 2, "filters": [{"topic": "$share/a+b/{T}s"}]}}],
            2, "ShareName containing '+'"),
    "N31": ("MQTT 5.0 3.1.2.11.10", "MUST", {"emqx": "a+c", "mosquitto": "a+c", "nanomq": "a+c", "vernemq": "a+c+f"}, REJECT,
            [connect5(properties={"raw": "16 0002 6161".replace(" ", "")})], 1,
            "Authentication Data without Authentication Method (Protocol Error, 3.1.2.11.10)"),
    "N32": ("MQTT-1.5.4-1", "MUST", {"nanomq": "a+c"}, REJECT,
            [connect5(properties={"raw": "150002 61ff".replace(" ", "")})], 1, "Authentication Method not UTF-8"),
    "N34": ("MQTT 5.0 3.3.2.3.6", "MUST", {"emqx": "a"}, FORWARD,
            SUB_B + [V5, pub({"raw": "09000161 09000162".replace(" ", "")})], 4,
            "Correlation Data twice (Protocol Error, 3.3.2.3.6)"),
    "N35": ("MQTT-3.3.1-4", "MUST", {"emqx": "a+c"}, REJECT, [V5, hx("36 07 0001 78 0001 00 70")], 2,
            "Table 11 reads 'Maximum QoS field has the value 3'; the broker-received form is a PUBLISH with both QoS "
            "bits set (3.3.1.2, Malformed Packet)"),
    "N36": ("MQTT 5.0 3.3.2.3.3", "MUST", {"nanomq": "a"}, FORWARD,
            SUB_B + [V5, pub({"raw": "020000003c 020000003c".replace(" ", "")})], 4,
            "Message Expiry Interval twice (Protocol Error, 3.3.2.3.3)"),
    "N37": ("MQTT 5.0 3.3.2.3.2", "MUST", {"nanomq": "a+c+f"}, FORWARD,
            SUB_B + [V5, pub({"payload_format_indicator": 2})], 4, "Payload Format Indicator 2"),
    "N38": ("MQTT 5.0 3.1.3.2.3", "MAY", {"emqx": "a", "mosquitto": "a+c", "vernemq": "a+c+f", "hivemq-ce": "a+c"},
            REJECT, [hx("10 19 0004 4d515454 05 06 003c 00 0002 7077 02 0101 0001 77 0002 fffe")], 1,
            "Will with Payload Format Indicator 1 and a Will Payload that is not UTF-8 (the Server MAY validate, "
            "3.1.3.2.3; counted by MBFuzzer and fixed by VerneMQ)"),
    "N39": ("MQTT 5.0 3.3.2.3.2", "MAY", {"mosquitto": "a+c", "nanomq": "a+c+f", "vernemq": "a+c+f", "hivemq-ce": "a+c"},
            FORWARD, SUB_B_LIT + [V5, hx("30 08 0001 78 02 0101 fffe")], 4,
            "PUBLISH with Payload Format Indicator 1 and a payload that is not UTF-8 (receiver MAY validate)"),
    "N40": ("MQTT 5.0 3.3.2.3.2", "MUST", {"emqx": "a+c+f"}, FORWARD,
            SUB_B + [V5, pub({"raw": "0101 0101".replace(" ", "")})], 4, "Payload Format Indicator twice"),
    "N41": ("MQTT 5.0 2.2.2.2", "MUST", {"nanomq": "a"}, FORWARD,
            SUB_B + [V5, pub({"raw": "7f00"})], 4, "PUBLISH with property identifier 0x7F (not valid)"),
    "N42": ("MQTT 5.0 3.1.2.11.3", "MUST", {"nanomq": "a"}, REJECT, [connect5(properties={"receive_maximum": 0})], 1,
            "Receive Maximum 0 (Protocol Error, 3.1.2.11.3)"),
    "N43": ("MQTT 5.0 3.1.2.11.7", "MUST", {"nanomq": "a+c"}, REJECT,
            [connect5(properties={"request_problem_information": 2})], 1, "Request Problem Information 2"),
    "N44": ("MQTT 5.0 3.1.2.11.6", "MUST", {"nanomq": "a+c"}, REJECT,
            [connect5(properties={"request_response_information": 2})], 1, "Request Response Information 2"),
    "N45": ("MQTT 5.0 3.1.2.11.6", "MUST", {"emqx": "a+c+f"}, REJECT,
            [connect5(properties={"raw": "1901 1901".replace(" ", "")})], 1, "Request Response Information twice"),
    "N47": ("MQTT 5.0 3.3.2.3.5", "MUST", {"emqx": "a", "mosquitto": "a+c+f", "nanomq": "a+c"}, FORWARD,
            SUB_B + [V5, pub({"response_topic": ""})], 4, "Response Topic of zero length"),
    "N48": ("MQTT 5.0 3.8.2.1.2", "MUST", {"nanomq": "a+c+f"}, REJECT + ["granted"],
            [V5, {"client": "a", "op": "subscribe",
                  "args": {"packet_id": 2, "filters": [{"topic": "{T}x"}], "properties": {"subscription_identifier": 0}}}],
            2, "Subscription Identifier 0 (range 1 to 268,435,455)"),
    "N49": ("MQTT 5.0 3.3.2.3.4", "MUST", {"emqx": "a+c+f"}, FORWARD,
            SUB_B + [V5, pub({"raw": "230001 230002".replace(" ", "")})], 4, "Topic Alias twice"),
    "N50": ("MQTT-1.5.4-1", "MUST", {"nanomq": "a+c"}, FORWARD,
            SUB_B + [V5, pub({"raw": "260001ff 000161".replace(" ", "")})], 4, "User Property key not UTF-8"),
    "N51": ("MQTT 5.0 3.1.2.6", "MUST", {"vernemq": "a"}, REJECT,
            [connect5(will={"topic": "{T}w", "payload": "w", "qos": 3})], 1, "Will QoS 3"),
    "N53": ("MQTT 5.0 3.1.3.3", "MUST", {"nanomq": "a+c+f"}, REJECT,
            [connect5(will={"topic": "", "payload": "w"})], 1, "zero-length Will Topic"),
}
# FlashMQ (MBFuzzer tested 1.12.1) joined the MQTT cell on 22 Nov 2026. Its Table 10 marks, read from the paper
# (page 6196); N14 is marked "not affected, confirmed" for FlashMQ and is left out.
FLASHMQ = {"N2": "a+c+f", "N9": "a+c+f", "N10": "a+c+f", "N11": "a+c+f", "N13": "a+c+f", "N23": "a+c+f",
           "N24": "a+c", "N25": "a+c+f", "N28": "a+c+f", "N29": "a+c+f", "N30": "a+c+f", "N31": "a+c+f",
           "N33": "a+c+f", "N36": "a+c+f", "N37": "a+c+f", "N38": "a+c+f", "N39": "a+c+f", "N42": "a+c+f",
           "N43": "a+c+f", "N46": "a+c+f", "N47": "a+c+f", "N49": "a+c+f", "N52": "a+c+f"}
AFFECTED["flashmq"] = "1.12.1"
CURRENT["flashmq"] = "1.27.3"
UNSUB_PROPS = "a2 8701 0001 8001 26 0001 6b 007a " + "76" * 122 + " 0001 78"
T.update({
    "N2": ("MQTT-3.1.2-15", "MUST", {}, REJECT,
           [{"client": "a", "op": "connect", "args": {"client_id": "{C}a", "version": 4, "will_retain_bit": True}}], 1,
           "MQTT 3.1.1 CONNECT with Will Flag 0 and Will Retain 1"),
    "N9": ("MQTT-3.1.0-1", "MUST", {}, REJECT, [hx("c0 00")], 1,
           "PINGREQ as the first packet on a new connection"),
    "N13": ("MQTT-3.1.2-9", "MUST", {}, REJECT, [hx("10 0f 0004 4d515454 05 06 003c 00 0002 7033")], 1,
            "MQTT 5 CONNECT with Will Flag 1 and no Will Properties, Will Topic or Will Payload"),
    "N29": ("MQTT-4.7.2-1", "MUST", {}, ["dollar_topics"],
            [{"client": "b", "op": "connect", "args": {"client_id": "{C}b"}, "window_ms": 500},
             {"client": "b", "op": "subscribe", "args": {"packet_id": 1, "filters": [{"topic": "#"}]},
              "window_ms": 12000},
             {"client": "a", "op": "connect", "args": {"client_id": "{C}a"}, "window_ms": 3000},
             {"client": "a", "op": "publish", "args": {"topic": "1/1/1", "payload": "123", "qos": 0}, "window_ms": 3000},
             {"client": "a", "op": "disconnect", "window_ms": 3000}], 2,
            "wildcard subscription receives $-topics; broker-neutral form of case M1 (FlashMQ $SYS every 10 s)"),
    "N33": ("MQTT 5.0 3.3.2.3.9", "MUST", {}, FORWARD,
            SUB_B + [V5, pub({"raw": "0300016103000162"})], 4, "Content Type twice (Protocol Error)"),
    "N46": ("MQTT 5.0 3.3.2.3.5", "MUST", {}, FORWARD,
            SUB_B + [V5, pub({"raw": "0800017208000173"})], 4, "Response Topic twice (Protocol Error)"),
    "N52": ("MQTT 5.0 3.10.2.1.1", "MUST", {}, REJECT, [V5, hx(UNSUB_PROPS)], 2,
            "UNSUBSCRIBE whose Property Length (128) needs a two-byte Variable Byte Integer; a UNSUBACK is expected"),
})
for _nid, _m in FLASHMQ.items():
    T[_nid][2]["flashmq"] = _m
T = dict(sorted(T.items(), key=lambda kv: int(kv[0][1:])))
EXCLUDED = {"N29 (NanoMQ)": "same bug as case M1 (CVE-2024-42655)", "N14 (FlashMQ)": "not affected"}


def flow(x) -> str:
    """YAML flow style that the cell's loader reads (JSON is a YAML subset)."""
    import json
    return json.dumps(x, ensure_ascii=False)


def fixtures_block() -> str:
    out = ["  " + BEGIN]
    for nid, (stmt, kw, marks, axes, steps, judged, note) in T.items():
        out.append(f"  # MBFuzzer {nid}: {note}. Affected per Table 10: "
                   + ", ".join(f"{b} {AFFECTED[b]}" for b in marks) + ".")
        out.append(f"  rq5_mb_{nid.lower()}:")
        out.append("    kind: probe")
        out.append(f"    clause: {flow(f'{stmt} ({kw}); judged at step {judged}')}")
        out.append("    steps:")
        for s in steps:
            s = dict(s)
            if s.get("op") == "publish" and s["args"].get("packet_id", 0) is None:
                s["args"] = {k: v for k, v in s["args"].items() if k != "packet_id"}
            out.append("      - " + flow(s))
        out.append("")
    out.append("  " + END)
    return "\n".join(out)


def cases_block() -> str:
    out = ["  " + BEGIN]
    for nid, (stmt, kw, marks, axes, steps, judged, note) in T.items():
        for b, m in marks.items():
            fixed = FIXED.get((nid, b))
            status = "fixed" if fixed else "fix-unconfirmed"
            later = fixed or [CURRENT[b]]
            out.append(f"  MB-{nid}-{b}:")
            out.append("    cell: mqtt")
            out.append(f"    family: {b}")
            if status != "fixed":
                out.append(f"    status: {status}")
            out.append(f"    fixtures: [rq5_mb_{nid.lower()}]")
            out.append(f"    axes: {flow(axes)}")
            out.append(f'    affected: ["{AFFECTED[b]}"]')
            out.append(f"    fixed: {flow(later)}")
            marks_txt = {"a": "affected", "a+c": "affected, confirmed", "a+c+f": "affected, confirmed, fixed"}[m]
            out.append(f'    source: "MBFuzzer (USENIX Security 2025) Table 10 {nid} ({marks_txt}); '
                       f'{"fixed release from the vendor tracker" if fixed else "no fixed release located yet"}"')
            out.append(f'    clause: "{stmt}"')
    out.append("  " + END)
    return "\n".join(out)


def splice(path: Path, block: str, indent: str = "") -> None:
    text = path.read_text(encoding="utf-8")
    b, e = indent + BEGIN, indent + END
    if b in text:
        pre, rest = text.split(b, 1)
        post = rest.split(e, 1)[1]
        text = pre.rstrip("\n") + "\n\n" + block + post
    else:
        text = text.rstrip("\n") + "\n\n" + block + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    splice(FIX, fixtures_block(), "  ")
    splice(CASES, cases_block(), "  ")
    n = sum(len(v[2]) for v in T.values())
    print(f"{len(T)} fixtures, {n} cases; excluded {sorted(EXCLUDED)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

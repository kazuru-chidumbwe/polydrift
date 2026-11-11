"""DNS cell driver: runs inside the lab client container (pd-dns-py image) and sends one fixture's steps to the
resolver under test. Standalone (dnspython only), so it can be copied into the container as is.

Input (stdin, JSON): {"server": "172.31.53.53", "steps": [...]}. Step ops:
  query  {qname, qtype="A", tcp=false, rd=true, cd=false, do=false, edns=0|null, payload=1232, ednsver=0,
          ednsopt=null (option code with 4 zero bytes), timeout=6}
  raw    {build: two_opt | qdcount0 | opcode15 | update, qname, timeout=6}
  sleep  {ms}
Output (stdout, JSON): one observation per query or raw step:
  {step, op, error, rcode, answers, aa, ra, tc, ad, additional, authority_soa, edns, ms}
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import time

import dns.edns
import dns.exception
import dns.flags
import dns.message
import dns.opcode
import dns.query
import dns.rcode
import dns.rdatatype


def _rr_lines(section) -> list[str]:
    out = []
    for rr in section:
        if rr.rdtype == dns.rdatatype.OPT:
            continue
        for rd in rr:
            name = rr.name.to_text().lower().rstrip(".")
            if rr.rdtype in (dns.rdatatype.A, dns.rdatatype.AAAA):
                out.append(f"{name}|{rd.to_text()}")
            else:
                out.append(f"{name} {dns.rdatatype.to_text(rr.rdtype)} {rd.to_text().lower()}")
    return out


def observe(resp: dns.message.Message) -> dict:
    return {
        "rcode": dns.rcode.to_text(resp.rcode()),
        "answers": sorted(f"{rr.name.to_text().lower().rstrip('.')} {dns.rdatatype.to_text(rr.rdtype)} "
                          f"{rd.to_text().lower()}" for rr in resp.answer for rd in rr),
        "aa": bool(resp.flags & dns.flags.AA),
        "ra": bool(resp.flags & dns.flags.RA),
        "tc": bool(resp.flags & dns.flags.TC),
        "ad": bool(resp.flags & dns.flags.AD),
        "additional": sorted(_rr_lines(resp.additional)),
        "authority_soa": any(rr.rdtype == dns.rdatatype.SOA for rr in resp.authority),
        "edns": resp.edns >= 0,
        "error": None,
    }


def send_raw(server: str, wire: bytes, timeout: float) -> bytes:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(wire, (server, 53))
        data, _ = s.recvfrom(65535)
        return data
    finally:
        s.close()


def header_only(data: bytes) -> dict:
    """A reply dnspython cannot parse: report the header fields."""
    if len(data) < 12:
        return {"error": "short response"}
    _id, f1, f2 = struct.unpack("!HBB", data[:4])
    return {"rcode": dns.rcode.to_text(f2 & 0x0F), "answers": [], "aa": bool(f1 & 0x04), "ra": bool(f2 & 0x80),
            "tc": bool(f1 & 0x02), "ad": bool(f2 & 0x20), "additional": [], "authority_soa": False, "edns": False,
            "error": None, "unparsed": True}


def build_raw(kind: str, qname: str) -> bytes:
    q = dns.message.make_query(qname, "A", use_edns=0, payload=1232)
    if kind == "two_opt":
        w = bytearray(q.to_wire())
        opt = bytes([0]) + struct.pack("!HHIH", 41, 1232, 0, 0)
        w += opt
        arcount = struct.unpack("!H", w[10:12])[0] + 1
        w[10:12] = struct.pack("!H", arcount)
        return bytes(w)
    if kind == "qdcount0":
        w = bytearray(dns.message.make_query(qname, "A").to_wire())
        return bytes(w[:4]) + b"\x00\x00\x00\x00\x00\x00\x00\x00"
    if kind == "opcode15":
        w = bytearray(dns.message.make_query(qname, "A").to_wire())
        w[2] = (w[2] & 0x87) | (15 << 3)
        return bytes(w)
    if kind == "update":
        w = bytearray(dns.message.make_query(qname, "A").to_wire())
        w[2] = (w[2] & 0x87) | (5 << 3)
        return bytes(w)
    raise ValueError(kind)


def run(server: str, steps: list[dict]) -> list[dict]:
    out = []
    for i, st in enumerate(steps, 1):
        op = st.get("op", "query")
        if op == "sleep":
            time.sleep(st.get("ms", 0) / 1000)
            continue
        t0 = time.time()
        timeout = float(st.get("timeout", 6))
        try:
            if op == "raw":
                data = send_raw(server, build_raw(st["build"], st["qname"]), timeout)
                try:
                    obs = observe(dns.message.from_wire(data))
                except Exception:  # noqa: BLE001
                    obs = header_only(data)
            else:
                edns = st.get("edns", 0)
                opts = []
                if st.get("ednsopt") is not None:
                    opts = [dns.edns.GenericOption(int(st["ednsopt"]), b"\x00\x00\x00\x00")]
                # dnspython turns EDNS on when use_edns is None and a payload is given; False means no OPT at all.
                q = dns.message.make_query(st["qname"], st.get("qtype", "A"), use_edns=False if edns is None else edns,
                                           payload=int(st.get("payload", 1232)), want_dnssec=bool(st.get("do")),
                                           options=opts if edns is not None else None)
                if edns is not None and st.get("ednsver"):
                    q.use_edns(int(st["ednsver"]), payload=int(st.get("payload", 1232)), options=opts)
                if not st.get("rd", True):
                    q.flags &= ~dns.flags.RD
                if st.get("cd"):
                    q.flags |= dns.flags.CD
                if st.get("tcp"):
                    resp = dns.query.tcp(q, server, timeout=timeout)
                else:
                    resp = dns.query.udp(q, server, timeout=timeout, ignore_unexpected=True)
                obs = observe(resp)
        except dns.exception.Timeout:
            obs = {"error": "timeout"}
        except (ConnectionError, OSError) as e:
            obs = {"error": f"socket: {e.__class__.__name__}"}
        except dns.message.ShortHeader:
            obs = {"error": "short response"}
        except Exception as e:  # noqa: BLE001
            obs = {"error": f"driver: {e!r}"[:200]}
        obs.update({"step": i, "op": op, "ms": int((time.time() - t0) * 1000)})
        out.append(obs)
    return out


def main() -> int:
    req = json.load(sys.stdin)
    json.dump(run(req["server"], req["steps"]), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

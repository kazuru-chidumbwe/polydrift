"""Lab authoritative DNS server for the DNS cell (manuscript 4.5). Runs inside the pd-dns-py image on the internal
lab network, one process per zone server (root, test, pd, other), UDP and TCP on port 53.

It answers like a plain authoritative server for its one zone: answers with AA, CNAME and DNAME (RFC 6672) chasing
inside the zone, wildcards, empty non-terminals as NODATA, NXDOMAIN and NODATA with the SOA in the authority section
(RFC 2308), referrals with in-zone glue, and truncation when the response exceeds the client's UDP size (512 without
EDNS, else the advertised size). Names listed under `behaviours` in zones.yaml make the server misbehave on purpose,
so a fixture can test how resolvers treat that response.

Usage: auth.py --zones /lab/zones.yaml --server pd [--port 53]
"""

from __future__ import annotations

import argparse
import socket
import socketserver
import struct
import threading
import time

import dns.edns
import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.opcode
import dns.rcode
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.rrset
import yaml

STATE: dict[str, float] = {}
LOCK = threading.Lock()


def mkrr(name: dns.name.Name, ttl: int, rtype: str, text: str, origin: dns.name.Name) -> dns.rrset.RRset:
    rdtype = dns.rdatatype.from_text(rtype)
    rr = dns.rrset.RRset(name, dns.rdataclass.IN, rdtype)
    rr.add(dns.rdata.from_text(dns.rdataclass.IN, rdtype, text, origin=origin, relativize=False), ttl)
    return rr


class Zone:
    def __init__(self, origin: str, records: list[str], behaviours: dict, default_ttl: int = 300):
        self.origin = dns.name.from_text(origin)
        self.rrsets: dict[tuple[dns.name.Name, int], dns.rrset.RRset] = {}
        self.names: set[dns.name.Name] = set()
        for line in records:
            parts = line.split()
            name = dns.name.from_text(parts[0], self.origin)
            if parts[1].isdigit():
                ttl, rest = int(parts[1]), parts[2:]
            else:
                ttl, rest = default_ttl, parts[1:]
            rtype, rdata = rest[0], " ".join(rest[1:])
            rr = mkrr(name, ttl, rtype, rdata, self.origin)
            key = (name, rr.rdtype)
            if key in self.rrsets:
                self.rrsets[key].union_update(rr)
            else:
                self.rrsets[key] = rr
            n = name
            while n != self.origin and n.is_subdomain(self.origin):
                self.names.add(n)
                n = n.parent()
            self.names.add(self.origin)
        self.behaviours = {dns.name.from_text(k, self.origin): v for k, v in (behaviours or {}).items()}
        self.soa = self.rrsets[(self.origin, dns.rdatatype.SOA)]

    def get(self, name: dns.name.Name, rdtype: int) -> dns.rrset.RRset | None:
        return self.rrsets.get((name, rdtype))

    def negative_soa(self) -> dns.rrset.RRset:
        soa = self.soa[0]
        rr = dns.rrset.from_text(self.origin, min(self.soa.ttl, soa.minimum), "IN", "SOA", soa.to_text())
        return rr

    def delegation(self, qname: dns.name.Name) -> dns.rrset.RRset | None:
        """Deepest NS set strictly below the apex that covers qname."""
        n = qname
        best = None
        while n != self.origin and n.is_subdomain(self.origin):
            ns = self.get(n, dns.rdatatype.NS)
            if ns is not None:
                best = ns
            n = n.parent()
        return best

    def glue(self, ns: dns.rrset.RRset) -> list[dns.rrset.RRset]:
        out = []
        for rd in ns:
            for t in (dns.rdatatype.A, dns.rdatatype.AAAA):
                g = self.get(rd.target, t)
                if g is not None:
                    out.append(g)
        return out


def lookup(zone: Zone, qname: dns.name.Name, qtype: int, resp: dns.message.Message) -> None:
    """Fill resp for a standard authoritative answer."""
    resp.flags |= dns.flags.AA
    ns = zone.delegation(qname)
    if ns is not None and not (qtype == dns.rdatatype.DS and ns.name == qname):
        resp.flags &= ~dns.flags.AA
        resp.authority.append(ns)
        resp.additional.extend(zone.glue(ns))
        return
    name = qname
    for _hop in range(20):
        rr = zone.get(name, qtype)
        if rr is not None:
            resp.answer.append(rr)
            return
        cname = zone.get(name, dns.rdatatype.CNAME)
        if cname is not None and qtype != dns.rdatatype.CNAME:
            resp.answer.append(cname)
            target = cname[0].target
            if not target.is_subdomain(zone.origin) or zone.delegation(target) is not None:
                return
            if any(r.name == target for r in resp.answer):
                return  # loop: stop like a server that detects it
            name = target
            continue
        # DNAME at an ancestor (RFC 6672 2.2): DNAME plus synthesised CNAME
        anc = name.parent() if name != zone.origin else None
        while anc is not None and anc.is_subdomain(zone.origin):
            dn = zone.get(anc, dns.rdatatype.DNAME)
            if dn is not None:
                resp.answer.append(dn)
                new = name.relativize(anc).derelativize(dn[0].target)
                resp.answer.append(dns.rrset.from_text(name, dn.ttl, "IN", "CNAME", new.to_text()))
                return
            if anc == zone.origin:
                break
            anc = anc.parent()
        if name in zone.names:  # name exists (other types or an empty non-terminal): NODATA
            resp.authority.append(zone.negative_soa())
            return
        # wildcard at the closest encloser
        enc = name.parent()
        while enc.is_subdomain(zone.origin) and enc not in zone.names:
            enc = enc.parent()
        wild = dns.name.from_text("*", enc)
        if wild in zone.names:
            w = zone.get(wild, qtype)
            if w is not None:
                resp.answer.append(dns.rrset.from_text_list(name, w.ttl, "IN", dns.rdatatype.to_text(qtype),
                                                            [r.to_text() for r in w]))
            else:
                resp.authority.append(zone.negative_soa())
            return
        resp.set_rcode(dns.rcode.NXDOMAIN)  # also at the end of a CNAME chain (RFC 6604 2.1)
        resp.authority.append(zone.negative_soa())
        return


def respond(zone: Zone, wire: bytes, transport: str) -> bytes | None:
    try:
        q = dns.message.from_wire(wire)
    except Exception:  # noqa: BLE001
        if len(wire) >= 12:
            hdr = bytearray(wire[:12])
            hdr[2] |= 0x80
            hdr[3] = (hdr[3] & 0xF0) | dns.rcode.FORMERR
            return bytes(hdr[:4]) + b"\x00" * 8
        return None
    resp = dns.message.make_response(q)
    resp.flags &= ~dns.flags.RA
    if not q.question:
        resp.set_rcode(dns.rcode.FORMERR)
        return resp.to_wire()
    qname, qtype = q.question[0].name, q.question[0].rdtype
    if not qname.is_subdomain(zone.origin):
        resp.set_rcode(dns.rcode.REFUSED)
        return resp.to_wire()
    beh = zone.behaviours.get(qname)
    if beh is not None and "kind" not in beh:
        beh = None  # entry that only defines `below`
    # behaviour that covers every name below it (RFC 8020 probe)
    if beh is None:
        for bname, b in zone.behaviours.items():
            if b.get("below") and qname.is_subdomain(bname) and qname != bname:
                beh = dict(b["below"])
                break
    limit = 65535 if transport == "tcp" else (max(512, q.payload) if q.edns >= 0 else 512)
    if beh:
        kind = beh["kind"]
        if kind == "silent":
            return None
        if kind == "rcode":
            resp.set_rcode(dns.rcode.from_text(beh["rcode"]))
            if beh.get("aa", True):
                resp.flags |= dns.flags.AA
            return resp.to_wire()
        if kind == "edns_formerr" and q.edns >= 0:
            r = dns.message.make_response(q)
            r.use_edns(False)
            r.set_rcode(dns.rcode.FORMERR)
            return r.to_wire()
        if kind == "truncate_udp" and transport == "udp":
            resp.flags |= dns.flags.TC | dns.flags.AA
            return resp.to_wire()
        if kind == "flip":
            # time-based: NXDOMAIN until `negative_seconds` after the first query for the name, then the record.
            # (A count-based flip broke when a resolver sent two upstream queries for one client query.)
            now = time.monotonic()
            with LOCK:
                first = STATE.setdefault(qname.to_text(), now)
            if now - first < float(beh.get("negative_seconds", 0.5)):
                resp.flags |= dns.flags.AA
                resp.set_rcode(dns.rcode.NXDOMAIN)
                resp.authority.append(zone.negative_soa())
                return resp.to_wire()
        if kind == "synth_a":
            resp.flags |= dns.flags.AA
            resp.answer.append(dns.rrset.from_text(qname, 300, "IN", "A", beh["ip"]))
            return _finish(resp, beh, limit)
        if kind == "answer":
            resp.flags |= dns.flags.AA
            for line in beh["answer"]:
                resp.answer.append(_split(line, zone))
            for line in beh.get("additional", []):
                resp.additional.append(_split(line, zone))
            if beh.get("noaa"):
                resp.flags &= ~dns.flags.AA
            return _finish(resp, beh, limit)
    lookup(zone, qname, qtype, resp)
    if beh and beh["kind"] in ("noaa",):
        resp.flags &= ~dns.flags.AA
    if beh and beh["kind"] == "extra_answer":
        for line in beh["answer"]:
            resp.answer.append(_split(line, zone))
    if beh and beh["kind"] == "extra_additional":
        for line in beh["additional"]:
            resp.additional.append(_split(line, zone))
    return _finish(resp, beh or {}, limit)


def _split(line: str, zone: Zone):
    p = line.split()
    return mkrr(dns.name.from_text(p[0], zone.origin), int(p[1]), p[2], " ".join(p[3:]), zone.origin)


def _finish(resp: dns.message.Message, beh: dict, limit: int) -> bytes:
    if beh.get("kind") == "qid_mismatch":
        resp.id = (resp.id + 1) & 0xFFFF
    if beh.get("kind") == "question_mismatch":
        resp.question = [dns.rrset.RRset(dns.name.from_text(beh["qname"]), dns.rdataclass.IN, resp.question[0].rdtype)]
    try:
        return resp.to_wire(max_size=limit)
    except dns.exception.TooBig:
        resp.answer, resp.authority, resp.additional = [], [], []
        resp.flags |= dns.flags.TC
        return resp.to_wire()


class UDP(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        out = respond(self.server.zone, data, "udp")
        if out is not None:
            sock.sendto(out, self.client_address)


class TCP(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            hdr = self.request.recv(2)
            if len(hdr) < 2:
                return
            (n,) = struct.unpack("!H", hdr)
            data = b""
            while len(data) < n:
                chunk = self.request.recv(n - len(data))
                if not chunk:
                    return
                data += chunk
            out = respond(self.server.zone, data, "tcp")
            if out is not None:
                self.request.sendall(struct.pack("!H", len(out)) + out)


class TUDP(socketserver.ThreadingMixIn, socketserver.UDPServer):
    allow_reuse_address = True


class TTCP(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zones", required=True)
    ap.add_argument("--server", required=True)
    ap.add_argument("--port", type=int, default=53)
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.zones, encoding="utf-8"))["servers"][a.server]
    zone = Zone(cfg["zone"], cfg["records"], cfg.get("behaviours"))
    u = TUDP(("0.0.0.0", a.port), UDP)
    t = TTCP(("0.0.0.0", a.port), TCP)
    u.zone = t.zone = zone
    threading.Thread(target=t.serve_forever, daemon=True).start()
    print(f"auth {a.server} {cfg['zone']} on {a.port}", flush=True)
    u.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

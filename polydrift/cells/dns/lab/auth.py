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
# Every query this authority receives, for the driver's `upstream` step (control port 5300, "DUMP" returns the log
# as JSON and clears it): what the resolver under test sent upstream (RD, EDNS, options, transport, timing).
QLOG: list[dict] = []
SERVER_NAME = ""


def log_query(q: dns.message.Message, src: str, transport: str) -> None:
    if not q.question:
        return
    with LOCK:
        QLOG.append({"server": SERVER_NAME, "src": src, "qname": q.question[0].name.to_text(omit_final_dot=True),
                     "qtype": dns.rdatatype.to_text(q.question[0].rdtype), "rd": bool(q.flags & dns.flags.RD),
                     "edns": q.edns, "payload": q.payload if q.edns >= 0 else None,
                     "opts": sorted(int(o.otype) for o in q.options) if q.edns >= 0 else [],
                     "do": bool(q.ednsflags & dns.flags.DO) if q.edns >= 0 else False,
                     "transport": transport, "t": round(time.monotonic(), 3)})


def raw_answer(q: dns.message.Message, rrs: list[tuple[str, int, str, str]], aa: bool = True) -> bytes:
    """A response built by hand, for RRsets dnspython would normalise (two TTLs in one RRset, duplicate RRs)."""
    hdr = struct.pack("!HHHHHH", q.id, 0x8000 | (0x0400 if aa else 0) | (q.flags & 0x0100), 1, len(rrs), 0, 0)
    qn = q.question[0]
    body = qn.name.to_wire() + struct.pack("!HH", qn.rdtype, qn.rdclass)
    for name, ttl, rtype, text in rrs:
        rd = dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.from_text(rtype), text)
        rdw = rd.to_digestable()
        body += dns.name.from_text(name).to_wire() + struct.pack("!HHIH", rd.rdtype, 1, ttl, len(rdw)) + rdw
    return hdr + body


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
            if qtype == dns.rdatatype.NS:
                # additional section processing (RFC 1034 4.3.2 step 6): the addresses of the listed name servers
                # that the zone holds; a root priming response carries them (RFC 8109 3.2), and a resolver may
                # refuse a priming answer without them (Technitium)
                resp.additional.extend(zone.glue(rr))
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


def respond(zone: Zone, wire: bytes, transport: str, src: str | None = None) -> bytes | None:
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
    if src is not None:
        log_query(q, src, transport)
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
        if kind == "craft":
            # a response written in full: flags and every section as given, any owner name (out-of-bailiwick data
            # for the cache-poisoning cases: MaginotDNS Fig. 5, Phoenix Domain Fig. 8, ResolverFuzz Fig. 8)
            r = dns.message.make_response(q)
            r.flags &= ~(dns.flags.RA | dns.flags.AA)
            if beh.get("aa", True):
                r.flags |= dns.flags.AA
            if beh.get("rcode"):
                r.set_rcode(dns.rcode.from_text(beh["rcode"]))
            for sec, lines in (("answer", r.answer), ("authority", r.authority), ("additional", r.additional)):
                for line in beh.get(sec, []):
                    lines.append(_split(line, zone))
            return r.to_wire()
        if kind == "big" and transport == "udp":
            # an answer of `count` A records sent over UDP whatever size the querier advertised (ResolverFuzz CP3:
            # does the resolver accept a UDP response above 1232 bytes)
            resp.flags |= dns.flags.AA
            addrs = [f"10.{60 + i // 250}.{i % 250}.1" for i in range(int(beh.get("count", 120)))]
            resp.answer.append(dns.rrset.from_text_list(qname, 300, "IN", "A", addrs))
            return resp.to_wire(max_size=65535)
        if kind == "malformed" and transport == "udp":
            # TuDoor state machine inputs: a broken packet on the query's four-tuple first, then (if `then_ms` is
            # set) the valid answer; a resolver should drop the first and keep waiting
            form = beh.get("form", "empty")
            if form == "empty":
                bad = b""
            elif form == "short":
                bad = wire[:2] + b"\x81\x80" + b"\x00" * 4
            elif form == "qr0":
                bad = wire
            elif form == "badtxid":
                lookup(zone, qname, qtype, resp)
                resp.id = (resp.id + 1) & 0xFFFF
                bad = resp.to_wire()
                resp = dns.message.make_response(q)
            else:
                raise ValueError(f"malformed form {form}")
            out: list = [(0.0, bad)]
            if beh.get("then_ms") is not None:
                good = dns.message.make_response(q)
                good.flags &= ~dns.flags.RA
                lookup(zone, qname, qtype, good)
                out.append((float(beh["then_ms"]) / 1000, good.to_wire()))
            return out
        if kind == "tcp_null":
            # TuDoor VRC (Knot): UDP says truncated, TCP accepts the query and closes without a payload
            if transport == "udp":
                resp.flags |= dns.flags.TC | dns.flags.AA
                return resp.to_wire()
            return CLOSE
        if kind == "icmp" and transport == "udp":
            # TuDoor VRC/VDS (BIND): ICMP destination unreachable (type 3, `code`) for the query instead of an answer
            return [(0.0, ICMP, int(beh.get("code", 0)))]
        if kind == "revoke":
            # a delegation that the parent withdraws `seconds` after its first query (Phoenix Domain: revocation);
            # before that the zone answers normally (referral), after it NXDOMAIN
            now = time.monotonic()
            with LOCK:
                first = STATE.setdefault("revoke:" + beh.get("key", qname.to_text().lower()), now)
            if now - first >= float(beh.get("seconds", 5)):
                resp.flags |= dns.flags.AA
                resp.set_rcode(dns.rcode.NXDOMAIN)
                resp.authority.append(zone.negative_soa())
                return resp.to_wire()
        if kind == "delay":
            # answers like the zone after `ms` (concurrency clauses: a slow upstream must not block other clients)
            time.sleep(float(beh.get("ms", 3000)) / 1000)
        if kind == "raw_answer":
            # an answer dnspython would normalise: RRs given as [name, ttl, type, rdata] (split TTLs, duplicates)
            return raw_answer(q, [tuple(x) for x in beh["rrs"]], aa=beh.get("aa", True))
        if kind == "referral_up":
            # a referral that is not closer to the answer: NS for the root (RFC 1034 5.3.3)
            resp.authority.append(dns.rrset.from_text(".", 3600, "IN", "NS", "a.root.pdlab."))
            resp.additional.append(dns.rrset.from_text("a.root.pdlab.", 3600, "IN", "A", "172.31.53.10"))
            return resp.to_wire()
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
            # Keyed by the lower-cased name: a resolver that randomises the case of its upstream queries (0x20,
            # Knot Resolver) would otherwise start a new timer on every refetch and see NXDOMAIN for ever.
            now = time.monotonic()
            with LOCK:
                first = STATE.setdefault(qname.to_text().lower(), now)
            if now - first < float(beh.get("negative_seconds", 0.5)):
                resp.flags |= dns.flags.AA
                resp.set_rcode(dns.rcode.NXDOMAIN)
                if not beh.get("no_soa"):
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


CLOSE = object()  # TCP: close the connection without a reply
ICMP = object()   # UDP: answer with ICMP destination unreachable instead of DNS


def _csum(b: bytes) -> int:
    if len(b) % 2:
        b += b"\0"
    s = sum(struct.unpack(f"!{len(b) // 2}H", b))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def send_icmp_unreach(client: tuple[str, int], query: bytes, code: int) -> None:
    """ICMP type 3 for the resolver's query, quoting the IP header and first 8 bytes of the query datagram (RFC 792),
    so the resolver's kernel matches it to the query socket. Needs CAP_NET_RAW (in Docker's default set)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.connect((client[0], 9))
    me = probe.getsockname()[0]
    probe.close()
    udp = struct.pack("!HHHH", client[1], 53, 8 + len(query), 0)
    iph = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + 8 + len(query), 0, 0, 64, socket.IPPROTO_UDP, 0,
                      socket.inet_aton(client[0]), socket.inet_aton(me))
    iph = iph[:10] + struct.pack("!H", _csum(iph)) + iph[12:]
    body = struct.pack("!BBHI", 3, code, 0, 0) + iph + udp
    body = body[:2] + struct.pack("!H", _csum(body)) + body[4:]
    s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    try:
        s.sendto(body, (client[0], 0))
    finally:
        s.close()


class UDP(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        out = respond(self.server.zone, data, "udp", self.client_address[0])
        if out is None:
            return
        if isinstance(out, bytes):
            sock.sendto(out, self.client_address)
            return
        for item in out:  # [(delay_s, bytes)] or [(delay_s, ICMP, code)]
            time.sleep(item[0])
            if item[1] is ICMP:
                send_icmp_unreach(self.client_address, data, item[2])
            else:
                sock.sendto(item[1], self.client_address)


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
            out = respond(self.server.zone, data, "tcp", self.client_address[0])
            if out is CLOSE:
                return
            if isinstance(out, list):
                out = out[-1][1] if out and isinstance(out[-1][1], bytes) else None
            if out is not None:
                self.request.sendall(struct.pack("!H", len(out)) + out)


class Control(socketserver.StreamRequestHandler):
    """Port 5300: "DUMP" returns the query log as one JSON line and clears it."""

    def handle(self):
        if self.rfile.readline().strip() == b"DUMP":
            import json
            with LOCK:
                data, QLOG[:] = list(QLOG), []
            self.wfile.write(json.dumps(data).encode() + b"\n")


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
    global SERVER_NAME
    SERVER_NAME = a.server
    threading.Thread(target=t.serve_forever, daemon=True).start()
    c = TTCP(("0.0.0.0", 5300), Control)
    threading.Thread(target=c.serve_forever, daemon=True).start()
    print(f"auth {a.server} {cfg['zone']} on {a.port}", flush=True)
    u.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

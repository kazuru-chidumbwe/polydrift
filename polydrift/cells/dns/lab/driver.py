"""DNS cell driver: runs inside the lab client container (pd-dns-py image) and sends one fixture's steps to the
resolver under test. Standalone (dnspython only), so it can be copied into the container as is.

Input (stdin, JSON): {"server": "172.31.53.53", "steps": [...]}. Step ops:
  query     {qname, qtype="A", qclass="IN", tcp=false, rd=true, cd=false, do=false, z=false, edns=0|null,
             payload=1232, ednsver=0, ednsopt=null (option code with 4 zero bytes), timeout=6}
  raw       {build: two_opt | qdcount0 | opcode15 | update | iquery | binlabel | badopt, qname, timeout=6}
  parallel  {queries: [{label, qname, qtype}...], timeout=8}: sent at the same time from separate sockets; reports
            the order in which answers arrived and each answer's latency bucket (concurrency clauses)
  tcp_session {queries: [{qname, qtype, idle_ms}...], timeout=6}: one TCP connection, queries in sequence with an
            idle gap before each; reports each rcode and whether the connection was still open
  upstream  {match, volume=false}: the lab authorities' query logs since the previous upstream step (control port
            5300), only queries whose name contains `match`; the first upstream step of a fixture clears the logs.
            volume=true also reports the total in buckets (resource-consumption cases)
  sleep     {ms}
Output (stdout, JSON): one observation per step except sleep:
  query/raw: {step, op, error, rcode, answers, aa, ra, tc, ad, z, rd, qname_case, ttls, additional, additional_soa,
              authority_soa, edns, ms}
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import threading
import time

import dns.edns
import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.opcode
import dns.query
import dns.rcode
import dns.rdataclass
import dns.rdatatype

AUTH_IPS = ["172.31.53.10", "172.31.53.11", "172.31.53.12", "172.31.53.14", "172.31.53.15", "172.31.53.16",
            "172.31.53.17"]


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
        "z": bool(resp.flags & 0x0040),
        "rd": bool(resp.flags & dns.flags.RD),
        "qname_case": resp.question[0].name.to_text(omit_final_dot=True) if resp.question else None,
        "ttls": sorted({rr.ttl for rr in resp.answer}),
        "additional": sorted(_rr_lines(resp.additional)),
        "additional_soa": any(rr.rdtype == dns.rdatatype.SOA for rr in resp.additional),
        "authority_soa": any(rr.rdtype == dns.rdatatype.SOA for rr in resp.authority),
        "edns": resp.edns >= 0,
        "error": None,
    }


class RecSock(socket.socket):
    """A socket that keeps the bytes it receives, handed to dnspython (``sock=``) so the response is parsed and
    matched exactly as before while its wire form is kept for consumer replay (manuscript 3.6). UDP: the last
    datagram is the one dnspython accepted. TCP: everything after the two-byte length prefix."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.got: list[bytes] = []

    def recvfrom(self, n, *a):  # noqa: ANN001, ANN201
        data, addr = super().recvfrom(n, *a)
        self.got.append(data)
        return data, addr

    def recv(self, n, *a):  # noqa: ANN001, ANN201
        data = super().recv(n, *a)
        self.got.append(data)
        return data


def query_wire(q: dns.message.Message, server: str, timeout: float, tcp: bool) -> tuple[dns.message.Message, bytes]:
    if not tcp:
        s = RecSock(socket.AF_INET, socket.SOCK_DGRAM)
        s.setblocking(False)
        with s:
            resp = dns.query.udp(q, server, timeout=timeout, ignore_unexpected=True, sock=s)
            return resp, s.got[-1]
    s = RecSock(socket.AF_INET, socket.SOCK_STREAM)
    with s:
        s.settimeout(timeout)
        try:
            s.connect((server, 53))
        except TimeoutError as e:
            raise dns.exception.Timeout from e
        s.setblocking(False)
        resp = dns.query.tcp(q, server, timeout=timeout, sock=s)
        return resp, b"".join(s.got)[2:]


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
            "tc": bool(f1 & 0x02), "ad": bool(f2 & 0x20), "z": bool(f2 & 0x40), "rd": bool(f1 & 0x01),
            "qname_case": None, "ttls": [], "additional": [], "additional_soa": False, "authority_soa": False,
            "edns": False, "error": None, "unparsed": True}


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
    if kind == "iquery":
        # obsolete inverse query (opcode 1, RFC 3425): answered NOTIMP or FORMERR, never cached (RFC 1035 7.4)
        w = bytearray(dns.message.make_query(qname, "A").to_wire())
        w[2] = (w[2] & 0x87) | (1 << 3)
        return bytes(w)
    if kind == "binlabel":
        # a label whose length octet has the high bits 01 (binary label, RFC 6891 5 deprecates it; RFC 1035 3.1)
        hdr = struct.pack("!HHHHHH", 0x5D51, 0x0100, 1, 0, 0, 0)
        return hdr + bytes([0x41, 0x08, 0xAB]) + dns.name.from_text(qname).to_wire() + struct.pack("!HH", 1, 1)
    if kind == "badopt":
        # an OPT whose option length runs past the end of its RDATA (RFC 6891 7: FORMERR, with an OPT)
        w = bytearray(dns.message.make_query(qname, "A").to_wire())
        rdata = struct.pack("!HH", 65001, 40) + b"\x00\x00"
        w += bytes([0]) + struct.pack("!HHIH", 41, 1232, 0, len(rdata)) + rdata
        w[10:12] = struct.pack("!H", 1)
        return bytes(w)
    if kind == "zflag":
        w = bytearray(dns.message.make_query(qname, "A").to_wire())
        w[3] |= 0x40
        return bytes(w)
    raise ValueError(kind)


def make_query(st: dict) -> dns.message.Message:
    edns = st.get("edns", 0)
    opts = []
    if st.get("ednsopt") is not None:
        opts = [dns.edns.GenericOption(int(st["ednsopt"]), b"\x00\x00\x00\x00")]
    # dnspython turns EDNS on when use_edns is None and a payload is given; False means no OPT at all.
    q = dns.message.make_query(st["qname"], st.get("qtype", "A"), rdclass=st.get("qclass", "IN"),
                               use_edns=False if edns is None else edns, payload=int(st.get("payload", 1232)),
                               want_dnssec=bool(st.get("do")), options=opts if edns is not None else None)
    if edns is not None and st.get("ednsver"):
        q.use_edns(int(st["ednsver"]), payload=int(st.get("payload", 1232)), options=opts)
    if not st.get("rd", True):
        q.flags &= ~dns.flags.RD
    if st.get("cd"):
        q.flags |= dns.flags.CD
    return q


def upstream(match: str) -> dict:
    queries = []
    for ip in AUTH_IPS:
        try:
            with socket.create_connection((ip, 5300), timeout=2) as s:
                s.sendall(b"DUMP\n")
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            queries += json.loads(buf or b"[]")
        except OSError:
            continue  # the dead server (.13) has no authority; others always answer
    keep = [q for q in queries if match and match in q["qname"].lower()]
    return {"rcode": "NOERROR", "queries": sorted(keep, key=lambda q: q["t"]), "error": None}


def parallel(server: str, qs: list[dict], timeout: float) -> dict:
    t0 = time.time()
    done: list[tuple[float, str, str, float]] = []
    lock = threading.Lock()

    def stalled(item: dict) -> str:
        # a TCP client that announces a 64-octet message, sends 3 octets and then waits (RFC 1035 4.2.2: the server
        # should not block other activities waiting for TCP data); "held" if the connection stays open for hold_ms,
        # "closed" if the server closes it first
        s = socket.create_connection((server, 53), timeout=timeout)
        try:
            try:
                s.sendall(struct.pack("!H", 64) + b"\x12\x34\x01")
            except OSError:
                # the server closed or reset before the partial message went out (Linux: BrokenPipeError)
                return "closed"
            s.settimeout(float(item.get("hold_ms", 4000)) / 1000)
            try:
                return "closed" if not s.recv(2) else "answered"
            except socket.timeout:
                return "held"
            except OSError:
                return "closed"
        finally:
            s.close()

    def one(item: dict) -> None:
        time.sleep(float(item.get("delay_ms", 0)) / 1000)
        t1 = time.time()
        try:
            if item.get("stall"):
                rc = stalled(item)
            else:
                q = make_query(item)
                r = (dns.query.tcp(q, server, timeout=timeout) if item.get("tcp")
                     else dns.query.udp(q, server, timeout=timeout, ignore_unexpected=True))
                rc = dns.rcode.to_text(r.rcode())
        except dns.exception.Timeout:
            rc = "timeout"
        except Exception as e:  # noqa: BLE001
            rc = f"error:{e.__class__.__name__}"
        with lock:
            now = time.time()
            done.append((now - t0, item["label"], rc, now - t1))

    th = [threading.Thread(target=one, args=(x,)) for x in qs]
    for t in th:
        t.start()
    for t in th:
        t.join(timeout + 2)
    done.sort()
    bucket = lambda s: "<0.5s" if s < 0.5 else ("<2s" if s < 2 else ">=2s")  # noqa: E731
    # order: by completion time; latency: from each query's own start (after its delay_ms)
    return {"rcode": "NOERROR", "order": [d[1] for d in done], "latency": {d[1]: bucket(d[3]) for d in done},
            "rcodes": {d[1]: d[2] for d in done}, "error": None}


def tcp_session(server: str, qs: list[dict], timeout: float) -> dict:
    rcodes, open_after = [], True
    try:
        s = socket.create_connection((server, 53), timeout=timeout)
    except OSError as e:
        return {"error": f"socket: {e.__class__.__name__}"}
    try:
        for item in qs:
            time.sleep(float(item.get("idle_ms", 0)) / 1000)
            q = make_query(item)
            w = q.to_wire()
            try:
                s.sendall(struct.pack("!H", len(w)) + w)
                n = s.recv(2)
                if len(n) < 2:
                    raise ConnectionError("closed")
                (ln,) = struct.unpack("!H", n)
                data = b""
                while len(data) < ln:
                    chunk = s.recv(ln - len(data))
                    if not chunk:
                        raise ConnectionError("closed")
                    data += chunk
                rcodes.append(dns.rcode.to_text(dns.message.from_wire(data).rcode()))
            except (OSError, ConnectionError):
                rcodes.append("closed")
                open_after = False
                break
    finally:
        s.close()
    return {"rcode": "NOERROR", "rcodes": rcodes, "open_after": open_after, "error": None}


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
            elif op == "upstream":
                obs = upstream(str(st.get("match", "")).lower())
                obs["volume"] = bool(st.get("volume"))
            elif op == "parallel":
                obs = parallel(server, st["queries"], float(st.get("timeout", 8)))
            elif op == "tcp_session":
                obs = tcp_session(server, st["queries"], timeout)
            else:
                q = make_query(st)
                if st.get("z"):
                    q.flags |= 0x0040
                resp, wire = query_wire(q, server, timeout, bool(st.get("tcp")))
                obs = observe(resp)
                obs["wire"] = wire.hex()
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

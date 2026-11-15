"""DNS driver keeps the exact response bytes (consumer replay, manuscript 3.6) without changing what it parses."""

import socket
import struct
import threading

import dns.flags
import dns.message
import dns.rrset

from polydrift.cells.dns.lab import driver


def _answer(query_wire: bytes, ttl: int = 300) -> bytes:
    q = dns.message.from_wire(query_wire)
    r = dns.message.make_response(q)
    r.answer.append(dns.rrset.from_text(q.question[0].name, ttl, "IN", "A", "10.0.0.1"))
    return r.to_wire()


def _udp_server(replies: list[bytes]):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))

    def run():
        data, addr = s.recvfrom(4096)
        for reply in replies:
            s.sendto(reply(data), addr)
        s.close()

    threading.Thread(target=run, daemon=True).start()
    return s.getsockname()[1]


def test_udp_wire_is_the_accepted_datagram(monkeypatch):
    sent = {}

    def right(q):
        sent["w"] = _answer(q, ttl=1234)
        return sent["w"]

    port = _udp_server([right])
    real = driver.dns.query.udp
    monkeypatch.setattr(driver.dns.query, "udp", lambda q, where, **kw: real(q, where, port=port, **kw))
    q = dns.message.make_query("a.pd.pdlab", "A")
    resp, wire = driver.query_wire(q, "127.0.0.1", 3, tcp=False)
    assert wire == sent["w"]
    assert resp.answer[0].ttl == 1234


def test_tcp_wire_strips_length_prefix(monkeypatch):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    sent = {}

    def run():
        c, _ = srv.accept()
        n = struct.unpack("!H", c.recv(2))[0]
        q = c.recv(n)
        sent["w"] = _answer(q)
        c.sendall(struct.pack("!H", len(sent["w"])) + sent["w"])
        c.close()
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    real_connect = driver.RecSock.connect
    monkeypatch.setattr(driver.RecSock, "connect", lambda self, addr: real_connect(self, ("127.0.0.1", port)))
    real = driver.dns.query.tcp
    monkeypatch.setattr(driver.dns.query, "tcp", lambda q, where, **kw: real(q, where, port=port, **kw))
    q = dns.message.make_query("a.pd.pdlab", "A")
    resp, wire = driver.query_wire(q, "127.0.0.1", 3, tcp=True)
    assert wire == sent["w"] and resp.flags & dns.flags.QR

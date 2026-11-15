"""DNS consumer-replay server (manuscript 3.6, lab L9). Runs in the lab image on the replay network and answers every
query, over UDP and TCP on port 53, with one stored response: the exact bytes an implementation under test sent in
a recorded run, with only the message ID set to the consumer's. A stored "silent" response is never answered, as
the implementation was silent. The consumer's queries are counted (retries and the switch to TCP are behaviour).

Control (inside the container): ``python3 /lab/replay_server.py --ctl`` reads one command from stdin and prints the
reply; commands go to the server on 127.0.0.1:5301.
  LOAD {"wire": "<hex>"|null}   store a response (null: stay silent); clears the query counters
  COUNT                         {"udp": n, "tcp": m, "qnames": [...]} since the last LOAD
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import threading

STATE = {"wire": None, "udp": 0, "tcp": 0, "qnames": []}
LOCK = threading.Lock()


def _qname(q: bytes) -> str:
    out, i = [], 12
    try:
        while q[i]:
            n = q[i]
            out.append(q[i + 1:i + 1 + n].decode("latin-1"))
            i += n + 1
    except IndexError:
        return "?"
    return ".".join(out)


def reply_for(query: bytes) -> bytes | None:
    with LOCK:
        wire = STATE["wire"]
    if wire is None or len(query) < 2:
        return None
    return query[:2] + wire[2:]


def serve_udp() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 53))
    while True:
        q, addr = s.recvfrom(65535)
        with LOCK:
            STATE["udp"] += 1
            STATE["qnames"].append(_qname(q))
        r = reply_for(q)
        if r is not None:
            s.sendto(r, addr)


def _tcp_client(c: socket.socket) -> None:
    with c:
        c.settimeout(10)
        try:
            while True:
                hdr = c.recv(2)
                if len(hdr) < 2:
                    return
                (n,) = struct.unpack("!H", hdr)
                q = b""
                while len(q) < n:
                    chunk = c.recv(n - len(q))
                    if not chunk:
                        return
                    q += chunk
                with LOCK:
                    STATE["tcp"] += 1
                    STATE["qnames"].append(_qname(q))
                r = reply_for(q)
                if r is None:
                    return
                c.sendall(struct.pack("!H", len(r)) + r)
        except OSError:
            return


def serve_tcp() -> None:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 53))
    s.listen(16)
    while True:
        c, _ = s.accept()
        threading.Thread(target=_tcp_client, args=(c,), daemon=True).start()


def control(line: str) -> str:
    cmd, _, arg = line.strip().partition(" ")
    with LOCK:
        if cmd == "LOAD":
            w = json.loads(arg).get("wire")
            STATE.update(wire=None if w is None else bytes.fromhex(w), udp=0, tcp=0, qnames=[])
            return "OK"
        if cmd == "COUNT":
            return json.dumps({"udp": STATE["udp"], "tcp": STATE["tcp"], "qnames": STATE["qnames"]})
    return "ERR"


def serve_control() -> None:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 5301))
    s.listen(4)
    while True:
        c, _ = s.accept()
        with c:
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = c.recv(1 << 20)
                if not chunk:
                    break
                buf += chunk
            c.sendall(control(buf.decode()).encode() + b"\n")


def ctl() -> int:
    line = sys.stdin.read().strip() + "\n"
    with socket.create_connection(("127.0.0.1", 5301), timeout=10) as c:
        c.sendall(line.encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = c.recv(65536)
            if not chunk:
                break
            buf += chunk
    sys.stdout.write(buf.decode())
    return 0


def main() -> int:
    if "--ctl" in sys.argv:
        return ctl()
    for f in (serve_udp, serve_tcp):
        threading.Thread(target=f, daemon=True).start()
    serve_control()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

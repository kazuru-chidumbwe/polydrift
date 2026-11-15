"""MQTT consumer-replay server (manuscript 3.6, lab L9). Standalone (stdlib), port 1883 on the replay network.

It holds one client's view of a recorded fixture prefix: per step, whether that client acted (and with which
packet type) and the exact packets the broker sent to it during the step. When the consumer sends a request, the
next unused step of the same packet type is played: its packets are sent, then the packets of the following steps in
which the client did not act (deliveries caused by other clients), until the client's next own step. Packet
Identifiers in the replies to the consumer's own request (SUBACK, UNSUBACK, PUBACK, PUBREC, PUBREL, PUBCOMP) are set
to the consumer's identifier; deliveries keep the broker's. Acknowledgements the consumer sends for deliveries are
not requests. A step after which the broker closed the connection closes it.

Control (inside the container): ``python3 /lab/replay_server.py --ctl`` reads one command from stdin:
  LOAD {"steps": [{"actor": true, "type": "SUBSCRIBE", "wire": ["<hex>"...], "closed": false}, ...]}
  COUNT -> {"requests": [...], "unmatched": n}
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time

TYPES = {1: "CONNECT", 3: "PUBLISH", 4: "PUBACK", 5: "PUBREC", 6: "PUBREL", 7: "PUBCOMP", 8: "SUBSCRIBE",
         10: "UNSUBSCRIBE", 12: "PINGREQ", 14: "DISCONNECT", 15: "AUTH"}
ACKS = {"PUBACK", "PUBREC", "PUBREL", "PUBCOMP"}  # from the consumer: part of a delivery flow, not a request
ID_REPLIES = {4, 5, 6, 7, 9, 11}
STEP_GAP_S = 0.2
STATE: dict = {"steps": [], "requests": [], "unmatched": 0}
LOCK = threading.Lock()


def split(buf: bytes) -> tuple[list[bytes], bytes]:
    out = []
    while len(buf) >= 2:
        n, mult, i, ok = 0, 1, 1, False
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
        out.append(buf[:i + n])
        buf = buf[i + n:]
    return out, buf


def header_len(p: bytes) -> int:
    i = 1
    while p[i] & 0x80:
        i += 1
    return i + 1


def packet_id(p: bytes) -> int | None:
    t, q = p[0] >> 4, (p[0] >> 1) & 3
    h = header_len(p)
    if t in (8, 10) or t in ID_REPLIES:
        return int.from_bytes(p[h:h + 2], "big") if len(p) >= h + 2 else None
    if t == 3 and q:
        tl = int.from_bytes(p[h:h + 2], "big")
        return int.from_bytes(p[h + 2 + tl:h + 4 + tl], "big")
    return None


def with_id(p: bytes, pid: int) -> bytes:
    h = header_len(p)
    if (p[0] >> 4) in ID_REPLIES and len(p) >= h + 2:
        return p[:h] + pid.to_bytes(2, "big") + p[h + 2:]
    return p


def play(req: bytes) -> tuple[list[tuple[float, bytes]], bool]:
    """Packets to send (with the pause before each) and whether to close after them."""
    kind = TYPES.get(req[0] >> 4, f"type{req[0] >> 4}")
    with LOCK:
        if kind in ACKS:
            return [], False
        STATE["requests"].append(kind)
        steps = STATE["steps"]
        for i, st in enumerate(steps):
            if st.get("used") or not st.get("actor") or st.get("type") != kind:
                continue
            st["used"] = True
            pid = packet_id(req)
            out = [(0.0, with_id(bytes.fromhex(h), pid) if pid is not None else bytes.fromhex(h))
                   for h in st["wire"]]
            close = bool(st.get("closed"))
            for nxt in steps[i + 1:]:
                if close or nxt.get("actor"):
                    break
                nxt["used"] = True
                first = True
                for h in nxt["wire"]:
                    out.append((STEP_GAP_S if first else 0.0, bytes.fromhex(h)))
                    first = False
                close = bool(nxt.get("closed"))
            return out, close
        STATE["unmatched"] += 1
    return [], False


def client(c: socket.socket) -> None:
    buf = b""
    with c:
        c.settimeout(60)
        try:
            while True:
                data = c.recv(65536)
                if not data:
                    return
                pkts, buf = split(buf + data)
                for p in pkts:
                    out, close = play(p)
                    for pause, q in out:
                        if pause:
                            time.sleep(pause)
                        c.sendall(q)
                    if close or (p[0] >> 4) == 14:
                        return
        except OSError:
            return


def serve() -> None:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 1883))
    s.listen(16)
    while True:
        c, _ = s.accept()
        threading.Thread(target=client, args=(c,), daemon=True).start()


def control(line: str) -> str:
    cmd, _, arg = line.strip().partition(" ")
    with LOCK:
        if cmd == "LOAD":
            STATE.update(steps=json.loads(arg)["steps"], requests=[], unmatched=0)
            return "OK"
        if cmd == "COUNT":
            return json.dumps({"requests": STATE["requests"], "unmatched": STATE["unmatched"]})
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
    threading.Thread(target=serve, daemon=True).start()
    serve_control()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

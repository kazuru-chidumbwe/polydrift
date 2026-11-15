"""LDAP consumer-replay server (manuscript 3.6, lab L9). Standalone (stdlib only), run in a container on the replay
network, port 389. It holds the recorded session of one implementation for one fixture prefix: per step, the
request operation and the exact messages the implementation sent back. Each request a consumer sends is matched to
the next unused recorded step with the same operation, and that step's messages are sent back with the message ID
set to the consumer's (other bytes unchanged; an unsolicited message, ID 0, is sent as recorded). A step the
implementation did not answer stays unanswered; a step after which it closed the connection closes it. Unbind and
Abandon get no response, as in RFC 4511.

Control (inside the container): ``python3 /lab/replay_server.py --ctl`` reads one command from stdin:
  LOAD {"steps": [{"op": "search", "msgs": ["<hex>", ...], "closed": false}, ...]}   (clears the counters)
  COUNT -> {"requests": [...op names...], "unmatched": n, "reencoded": n}
"""

from __future__ import annotations

import json
import socket
import sys
import threading

REQ_OPS = {0x60: "bind", 0x42: "unbind", 0x63: "search", 0x66: "modify", 0x68: "add", 0x4A: "delete",
           0x6C: "moddn", 0x6E: "compare", 0x50: "abandon", 0x77: "extended"}
NO_RESPONSE = {"unbind", "abandon"}
STATE: dict = {"steps": [], "requests": [], "unmatched": 0, "reencoded": 0}
LOCK = threading.Lock()


def read_tlv(b: bytes, i: int) -> tuple[int, int, int, int]:
    """(tag, value start, value end, header length) of the TLV at i; raises on a short or odd encoding."""
    tag, n, j = b[i], b[i + 1], i + 2
    if n & 0x80:
        k = n & 0x7F
        if k == 0 or k > 4:
            raise ValueError("length form")
        n = int.from_bytes(b[j:j + k], "big")
        j += k
    if j + n > len(b):
        raise ValueError("short")
    return tag, j, j + n, j - i


def enc_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def enc_int(n: int) -> bytes:
    return b"\x00" if n == 0 else n.to_bytes((n.bit_length() + 8) // 8, "big", signed=True)


def set_id(msg: bytes, new_id: int) -> tuple[bytes, bool]:
    """The message with its ID replaced. Same-size IDs are patched in place (every other byte as recorded); a
    different size re-encodes the outer length (returned flag True)."""
    _t, vs, ve, _h = read_tlv(msg, 0)
    it, ivs, ive, _ih = read_tlv(msg, vs)
    old = int.from_bytes(msg[ivs:ive], "big", signed=True)
    if old == 0:
        return msg, False
    new = enc_int(new_id)
    if len(new) == ive - ivs:
        return msg[:ivs] + new + msg[ive:], False
    body = bytes([it]) + enc_len(len(new)) + new + msg[ive:ve]
    return bytes([msg[0]]) + enc_len(len(body)) + body, True


def split(buf: bytes) -> tuple[list[bytes], bytes]:
    out = []
    while len(buf) >= 2:
        try:
            _t, _vs, ve, _h = read_tlv(buf, 0)
        except (ValueError, IndexError):
            break
        out.append(buf[:ve])
        buf = buf[ve:]
    return out, buf


def answer(req: bytes) -> tuple[list[bytes], bool]:
    _t, vs, _ve, _h = read_tlv(req, 0)
    _it, ivs, ive, _ih = read_tlv(req, vs)
    mid = int.from_bytes(req[ivs:ive], "big", signed=True)
    op = REQ_OPS.get(req[ive], f"0x{req[ive]:02x}")
    with LOCK:
        STATE["requests"].append(op)
        if op in NO_RESPONSE:
            return [], op == "unbind"
        for st in STATE["steps"]:
            if not st.get("used") and st["op"] == op:
                st["used"] = True
                out = []
                for h in st["msgs"]:
                    m, re = set_id(bytes.fromhex(h), mid)
                    STATE["reencoded"] += int(re)
                    out.append(m)
                return out, bool(st.get("closed"))
        STATE["unmatched"] += 1
    return [], False


def client(c: socket.socket) -> None:
    buf = b""
    with c:
        c.settimeout(30)
        try:
            while True:
                data = c.recv(65536)
                if not data:
                    return
                msgs, buf = split(buf + data)
                for m in msgs:
                    out, close = answer(m)
                    if out:
                        c.sendall(b"".join(out))
                    if close:
                        return
        except OSError:
            return


def serve() -> None:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 389))
    s.listen(16)
    while True:
        c, _ = s.accept()
        threading.Thread(target=client, args=(c,), daemon=True).start()


def control(line: str) -> str:
    cmd, _, arg = line.strip().partition(" ")
    with LOCK:
        if cmd == "LOAD":
            STATE.update(steps=json.loads(arg)["steps"], requests=[], unmatched=0, reencoded=0)
            return "OK"
        if cmd == "COUNT":
            return json.dumps({k: STATE[k] for k in ("requests", "unmatched", "reencoded")})
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

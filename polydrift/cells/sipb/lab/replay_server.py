"""SIP consumer-replay server (manuscript 3.6, lab L9). Standalone (stdlib), UDP port 5060 on the replay network.

The consumer plays the caller "alice" of a proxy-role fixture. The server holds alice's view of a recorded fixture
prefix: per step, whether alice acted (and with which method) and the exact messages the proxy sent her. When the
consumer sends a request, the next unused step of alice's with the same method is played: its messages are sent,
then the messages of the following steps in which alice did not act, until her next own step. Each recorded
response is sent as recorded except for the identifiers that tie it to the consumer's transaction (RFC 3261 8.1.3,
17.1.3): alice's own Via value (the one with her lab port) becomes the consumer's top Via, and From, Call-ID and the
CSeq number become the consumer's; To keeps the recorded tag on the consumer's To. Header names and every other
byte (other Via values, Record-Route, Contact, the body) are as recorded. ACK is not a request that gets a reply;
a BYE the consumer sends when it hangs up is answered 200 so it can finish (outside the recorded prefix, the same
for both sides).

Control (inside the container): ``python3 /lab/replay_server.py --ctl`` reads one command from stdin:
  LOAD {"steps": [{"actor": true, "method": "INVITE", "wire": ["<b64>", ...]}, ...]}
  COUNT -> {"requests": [...methods...], "retransmissions": n, "unmatched": n}
"""

from __future__ import annotations

import base64
import json
import re
import socket
import sys
import threading
import time

ALICE_PORT = 5070
STEP_GAP_S = 0.2
STATE: dict = {"steps": [], "requests": [], "retransmissions": 0, "unmatched": 0, "seen": set()}
LOCK = threading.Lock()
NAMES = {"v": "via", "f": "from", "t": "to", "i": "call-id"}


def split_msg(raw: bytes) -> tuple[list[str], bytes]:
    head, _, body = raw.partition(b"\r\n\r\n")
    return head.decode("utf-8", "replace").split("\r\n"), body


def hname(line: str) -> str:
    n = line.split(":", 1)[0].strip().lower()
    return NAMES.get(n, n)


def header_values(lines: list[str], name: str) -> list[str]:
    out = []
    for ln in lines[1:]:
        if ":" in ln and hname(ln) == name:
            out.extend(v.strip() for v in re.split(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", ln.split(":", 1)[1])
                       if name == "via" else [ln.split(":", 1)[1].strip()])
    return out


def tag_of(v: str) -> str | None:
    m = re.search(r";\s*tag=([^;>\s]+)", v)
    return m.group(1) if m else None


def rewrite(resp: bytes, req_lines: list[str]) -> bytes:
    lines, body = split_msg(resp)
    via = header_values(req_lines, "via")
    frm = header_values(req_lines, "from")
    to = header_values(req_lines, "to")
    cid = header_values(req_lines, "call-id")
    cseq = header_values(req_lines, "cseq")
    num = cseq[0].split()[0] if cseq else "1"
    out = [lines[0]]
    for ln in lines[1:]:
        if ":" not in ln:
            out.append(ln)
            continue
        name, val = ln.split(":", 1)
        h = hname(ln)
        if h == "via" and via:
            vals = re.split(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", val)
            vals = [(" " + via[0]) if re.search(rf":{ALICE_PORT}\b", v) else v for v in vals]
            ln = name + ":" + ",".join(vals)
        elif h == "from" and frm:
            ln = f"{name}: {frm[0]}"
        elif h == "to" and to:
            t = tag_of(val)
            base = re.sub(r";\s*tag=[^;>\s]+", "", to[0])
            ln = f"{name}: {base}" + (f";tag={t}" if t else "")
        elif h == "call-id" and cid:
            ln = f"{name}: {cid[0]}"
        elif h == "cseq":
            parts = val.split()
            ln = f"{name}: {num} {parts[-1] if parts else ''}"
        out.append(ln)
    return "\r\n".join(out).encode() + b"\r\n\r\n" + body


def play(req: bytes) -> list[tuple[float, bytes]]:
    lines, _ = split_msg(req)
    first = lines[0].split(" ")
    if not first or lines[0].startswith("SIP/"):
        return []  # a response from the consumer (to a request the server did not send)
    method = first[0].upper()
    via = header_values(lines, "via")
    key = (method, via[0] if via else "", tuple(header_values(lines, "cseq")))
    with LOCK:
        if key in STATE["seen"]:
            STATE["retransmissions"] += 1
            return []
        STATE["seen"].add(key)
        STATE["requests"].append(method)
        if method == "ACK":
            return []
        steps = STATE["steps"]
        for i, st in enumerate(steps):
            if st.get("used") or not st.get("actor") or st.get("method") != method:
                continue
            st["used"] = True
            out = [(0.0, rewrite(base64.b64decode(w), lines)) for w in st["wire"]]
            for nxt in steps[i + 1:]:
                if nxt.get("actor"):
                    break
                nxt["used"] = True
                out += [(STEP_GAP_S if j == 0 else 0.0, rewrite(base64.b64decode(w), lines))
                        for j, w in enumerate(nxt["wire"])]
            return out
        if method == "BYE":
            ok = ("SIP/2.0 200 OK\r\nVia: x\r\nFrom: x\r\nTo: x\r\nCall-ID: x\r\nCSeq: 1 BYE\r\nContent-Length: 0"
                  "\r\n\r\n").encode()
            return [(0.0, rewrite(ok, lines))]
        STATE["unmatched"] += 1
    return []


def serve() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 5060))
    while True:
        data, addr = s.recvfrom(65535)
        if not data.strip():
            continue

        def send(out: list[tuple[float, bytes]], dest: tuple) -> None:
            for pause, m in out:
                if pause:
                    time.sleep(pause)
                s.sendto(m, dest)

        threading.Thread(target=send, args=(play(data), addr), daemon=True).start()


def control(line: str) -> str:
    cmd, _, arg = line.strip().partition(" ")
    with LOCK:
        if cmd == "LOAD":
            STATE.update(steps=json.loads(arg)["steps"], requests=[], retransmissions=0, unmatched=0, seen=set())
            return "OK"
        if cmd == "COUNT":
            return json.dumps({k: STATE[k] for k in ("requests", "retransmissions", "unmatched")})
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

"""JMAP consumer-replay server (manuscript 3.6, lab L9). Standalone (stdlib), HTTPS on port 443 of the replay network,
with the lab certificate the consumer image trusts (/certs).

It holds one implementation's recorded fixture prefix: the session object (served at /.well-known/jmap with its URLs
pointing here) and, per API step, the exact response the implementation sent (status, Content-Type, body bytes).
Each POST to the API URL is answered with the next unused recorded step; session fetches do not use a step.

Control (inside the container): ``python3 /lab/replay_server.py --ctl`` reads one command from stdin:
  LOAD {"session": {...}, "steps": [{"http": 200, "ctype": "...", "body_b64": "..."}, ...]}
  COUNT -> {"posts": n, "session_gets": n, "unmatched": n}
"""

from __future__ import annotations

import base64
import http.server
import json
import socket
import ssl
import sys
import threading

HOST = "172.31.63.53"
BASE = f"https://{HOST}"
STATE: dict = {"session": {}, "steps": [], "next": 0, "posts": 0, "session_gets": 0, "unmatched": 0}
LOCK = threading.Lock()


def session_doc() -> bytes:
    s = dict(STATE["session"])
    s["apiUrl"] = BASE + "/jmap/"
    s["uploadUrl"] = BASE + "/upload/{accountId}/"
    s["downloadUrl"] = BASE + "/download/{accountId}/{blobId}/{name}?accept={type}"
    s["eventSourceUrl"] = BASE + "/eventsource/?types={types}&closeafter={closeafter}&ping={ping}"
    return json.dumps(s).encode()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a) -> None:  # noqa: ANN002
        pass

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/.well-known/jmap"):
            with LOCK:
                STATE["session_gets"] += 1
                body = session_doc()
            self._send(200, "application/json", body)
        else:
            self._send(404, "text/plain", b"not recorded")

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        with LOCK:
            STATE["posts"] += 1
            i = STATE["next"]
            if i >= len(STATE["steps"]):
                STATE["unmatched"] += 1
                st = None
            else:
                st = STATE["steps"][i]
                STATE["next"] = i + 1
        if st is None:
            self.close_connection = True
            self._send(503, "text/plain", b"no recorded step")
            return
        self._send(int(st.get("http") or 200), st.get("ctype") or "", base64.b64decode(st.get("body_b64") or ""))


def serve() -> None:
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", 443), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain("/certs/cert.pem", "/certs/key.pem")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    srv.serve_forever()


def control(line: str) -> str:
    cmd, _, arg = line.strip().partition(" ")
    with LOCK:
        if cmd == "LOAD":
            d = json.loads(arg)
            STATE.update(session=d.get("session") or {}, steps=d.get("steps") or [], next=0, posts=0,
                         session_gets=0, unmatched=0)
            return "OK"
        if cmd == "COUNT":
            return json.dumps({k: STATE[k] for k in ("posts", "session_gets", "unmatched")})
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

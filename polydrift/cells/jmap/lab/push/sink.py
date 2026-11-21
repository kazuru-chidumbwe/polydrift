"""JMAP push sink for the lab (RFC 8620 7.2): an HTTPS endpoint that records every request a JMAP server sends to
a push subscription URL. Standalone (stdlib). Runs in the pd-jmap-push image on its own internal network, as
https://pd-push.example.com/ (the server containers resolve that name through their hosts file); its certificate is
signed by a lab CA that only the server containers under test trust.

  :443   HTTPS, any method and path: recorded (path, method, the push-relevant headers, body as text and, when it
         parses, as JSON), answered 201 Created, or with the status set for the path's key (see /mode)
  :8080  control, plain HTTP: GET /log?key=K -> JSON list of the records whose path contains K;
         GET /mode?key=K&code=429&count=N -> the next N requests for K are answered with that status;
         GET /clear -> the log and the modes are emptied (the runner calls it before each server)
"""

from __future__ import annotations

import json
import ssl
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG: list[dict] = []
MODE: dict[str, list] = {}
LOCK = threading.Lock()
HEADERS = ("content-type", "content-encoding", "ttl", "urgency", "topic", "content-length", "authorization",
           "crypto-key", "encryption", "transfer-encoding")


class Push(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _body(self) -> bytes:
        if "chunked" in (self.headers.get("transfer-encoding") or "").lower():
            # HTTP/1.1 chunked body (RFC 9112 7.1): size line, data, CRLF, until a zero-size chunk
            out = b""
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return out
                out += self.rfile.read(size)
                self.rfile.readline()
        n = int(self.headers.get("content-length") or 0)
        return self.rfile.read(n) if n else b""

    def _record(self):
        body = self._body()
        rec = {"t": round(time.time(), 3), "method": self.command, "path": self.path,
               "headers": {h: self.headers.get(h) for h in HEADERS if self.headers.get(h) is not None},
               "body": body.decode("utf-8", "replace")[:4096], "size": len(body)}
        try:
            rec["json"] = json.loads(body)
        except ValueError:
            rec["json"] = None
        code = 201
        with LOCK:
            LOG.append(rec)
            for key, m in MODE.items():
                if key in self.path and m[1] > 0:
                    code, m[1] = m[0], m[1] - 1
                    break
        rec["answered"] = code
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = do_PUT = do_GET = _record


class Control(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        key = q.get("key", "")
        if u.path == "/clear":
            # the runner clears the log and the modes before each server, so no record outlives its server
            with LOCK:
                LOG.clear()
                MODE.clear()
            out = {"ok": True}
        elif u.path == "/mode":
            with LOCK:
                MODE[key] = [int(q.get("code", 429)), int(q.get("count", 1))]
            out = {"ok": True}
        else:
            with LOCK:
                out = [r for r in LOG if key and key in r["path"]]
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    pki = sys.argv[1] if len(sys.argv) > 1 else "/pki"
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(f"{pki}/sink.pem", f"{pki}/sink.key")
    tls = ThreadingHTTPServer(("0.0.0.0", 443), Push)
    tls.socket = ctx.wrap_socket(tls.socket, server_side=True)
    threading.Thread(target=tls.serve_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Control).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

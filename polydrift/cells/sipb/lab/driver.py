"""SIP behaviour cell driver (sipb): runs inside the lab client container and plays every party around the element
under test (EUT): the UAC "alice" and the downstream UAs "bob", "carol", "dave", each on its own UDP and TCP port of
the client address. Standalone (stdlib only).

Input (stdin, JSON): {"eut": "172.31.60.10", "eut_port": 5060, "lab": "172.31.60.99", "domain": "lab.test",
                      "unique": "...", "steps": [...]}
Steps:
  send   {from: alice, transport: udp|tcp, msg: "<template>", to: eut|<party>, window_ms}
         Template lines are joined with CRLF; the blank line before the body is the first empty line; Content-Length
         is filled in for "{clen}". Placeholders: {eut} {lab} {domain} {U} {port:party} {branch} (a new branch per
         send) {branch:k} (named, stable within the fixture) {callid} {tag:k} {last:party:header} (a header of the
         last message the party received, e.g. {last:bob:Via}).
  reply  {from: bob, code, reason="", headers: {...}, body="", window_ms}: a response to the last request the party
         received, copying Via, From, To (adding a To tag unless the request had one), Call-ID and CSeq (RFC 3261 8.2.6).
  ack    {from: alice, window_ms}: ACK for the last INVITE alice sent and the last final response she received
         (non-2xx: same branch, RFC 3261 17.1.1.3; 2xx: new branch, to the Contact or the Record-Route set).
  wait   {ms}
Every step listens for window_ms (default 1500) on all parties and records what each received, parsed:
  {kind: req|resp, method, ruri, code, reason, hdr: {lower-case name: [values]}, body, transport}
`extract` on a step names fixture-specific observations evaluated here (see evaluate()).
Output (stdout, JSON): one observation per step except wait.
"""

from __future__ import annotations

import json
import random
import re
import select
import socket
import sys
import time

PARTIES = {"alice": 5070, "bob": 5071, "carol": 5072, "dave": 5073}
COMPACT = {"v": "via", "f": "from", "t": "to", "i": "call-id", "m": "contact", "l": "content-length",
           "c": "content-type", "k": "supported", "s": "subject", "e": "content-encoding", "o": "event",
           "r": "refer-to", "b": "referred-by", "u": "allow-events", "x": "session-expires"}


def parse(data: bytes, transport: str) -> dict:
    text = data.decode("utf-8", "replace")
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    first = lines[0]
    msg: dict = {"transport": transport, "hdr": {}, "body": body}
    if first.startswith("SIP/"):
        parts = first.split(" ", 2)
        msg.update(kind="resp", code=int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None,
                   reason=parts[2] if len(parts) > 2 else "")
    else:
        parts = first.split(" ")
        msg.update(kind="req", method=parts[0] if parts else "", ruri=parts[1] if len(parts) > 1 else "")
    cur = None
    for ln in lines[1:]:
        if ln[:1] in (" ", "\t") and cur:
            msg["hdr"][cur][-1] += " " + ln.strip()
            continue
        name, _, val = ln.partition(":")
        name = name.strip().lower()
        name = COMPACT.get(name, name)
        cur = name
        # comma-separated multi-values stay one entry per line, except Via/Route/Record-Route which split
        vals = [v.strip() for v in re.split(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", val)] \
            if name in ("via", "route", "record-route", "contact") else [val.strip()]
        msg["hdr"].setdefault(name, []).extend(vals)
    return msg


class Party:
    def __init__(self, name: str, host: str, port: int):
        self.name, self.port = name, port
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind((host, port))
        self.udp.setblocking(False)
        self.tcp_listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp_listen.bind((host, port))
        self.tcp_listen.listen(8)
        self.tcp_listen.setblocking(False)
        self.conns: list[socket.socket] = []
        self.bufs: dict[socket.socket, bytes] = {}
        self.inbox: list[dict] = []
        self.last_req: dict | None = None
        self.last_req_src: tuple | None = None
        self.last_resp: dict | None = None

    def socks(self) -> list[socket.socket]:
        return [self.udp, self.tcp_listen, *self.conns]

    def on_ready(self, s: socket.socket) -> None:
        if s is self.udp:
            try:
                data, src = s.recvfrom(65535)
            except OSError:
                return
            self._got(data, "udp", ("udp", src, None))
        elif s is self.tcp_listen:
            try:
                c, _ = s.accept()
                c.setblocking(False)
                self.conns.append(c)
                self.bufs[c] = b""
            except OSError:
                pass
        else:
            try:
                chunk = s.recv(65535)
            except OSError:
                chunk = b""
            if not chunk:
                self.conns.remove(s)
                return
            self.bufs[s] += chunk
            self._drain_tcp(s)

    def _drain_tcp(self, s: socket.socket) -> None:
        buf = self.bufs[s]
        while b"\r\n\r\n" in buf:
            head, _, rest = buf.partition(b"\r\n\r\n")
            m = re.search(rb"(?im)^(content-length|l)\s*:\s*(\d+)", head)
            n = int(m.group(2)) if m else 0
            if len(rest) < n:
                break
            self._got(head + b"\r\n\r\n" + rest[:n], "tcp", ("tcp", None, s))
            buf = rest[n:]
        self.bufs[s] = buf

    def _got(self, data: bytes, transport: str, src: tuple) -> None:
        if not data.strip():
            return  # keep-alive CRLF
        m = parse(data, transport)
        self.inbox.append(m)
        if m["kind"] == "req":
            self.last_req, self.last_req_src = m, src
        else:
            self.last_resp = m


class Lab:
    def __init__(self, req: dict):
        self.eut, self.eut_port, self.host = req["eut"], int(req.get("eut_port", 5060)), req["lab"]
        self.domain, self.unique = req.get("domain", "lab.test"), req.get("unique", "pd")
        self.parties = {n: Party(n, self.host, p) for n, p in PARTIES.items()}
        self.tcp_out: dict[str, socket.socket] = {}
        self.named: dict[str, str] = {}
        self.callid = f"{self.unique}-{random.getrandbits(40):x}@{self.host}"
        self.sent: dict[str, dict] = {}
        self.last_invite: dict[str, str] = {}
        self.last_raw: dict[str, str] = {}
        self.creds: dict[str, str] = req.get("creds") or {}
        self.ctl: dict = req.get("ctl") or {}
        self.own_callids: set[str] = set()  # every Call-ID the lab sent in this fixture (see is_probe)

    # ---------- templates ----------
    def fill(self, text: str, party: str) -> str:
        def rep(m: re.Match) -> str:
            k = m.group(1)
            if k == "eut":
                return self.eut
            if k == "eut_port":
                return str(self.eut_port)
            if k == "lab":
                return self.host
            if k == "domain":
                return self.domain
            if k == "U":
                return self.unique
            if k == "callid":
                return self.callid
            if k == "branch":
                return f"z9hG4bK{random.getrandbits(48):x}"
            if k.startswith("branch:") or k.startswith("tag:") or k.startswith("callid:"):
                return self.named.setdefault(k, (f"z9hG4bK{random.getrandbits(48):x}" if k.startswith("branch:")
                                                  else f"{random.getrandbits(40):x}"))
            if k.startswith("port:"):
                return str(PARTIES[k[5:]])
            if k.startswith("lasturi:"):
                # the URI inside a header of the last message the party received (Contact for in-dialog requests)
                _, who, hname = k.split(":", 2)
                lr = self.parties[who].last_resp or self.parties[who].last_req or {"hdr": {}}
                val = (lr["hdr"].get(hname.lower()) or [""])[0]
                mm = re.search(r"<([^>]+)>", val)
                return mm.group(1) if mm else val.split(";")[0].strip()
            if k.startswith("last:"):
                _, who, hname = k.split(":", 2)
                lr = self.parties[who].last_req or self.parties[who].last_resp or {"hdr": {}}
                vals = lr["hdr"].get(hname.lower()) or [""]
                return ", ".join(vals) if hname.lower() in ("via", "record-route", "route") else vals[0]
            if k == "clen":
                return "{clen}"
            return m.group(0)
        return re.sub(r"\{([A-Za-z_]+(?::[A-Za-z0-9_\-]+)*)\}", rep, text)

    @staticmethod
    def finish(text: str) -> bytes:
        text = text.replace("\r\n", "\n").rstrip("\n")
        head, sep, body = text.partition("\n\n")
        lines = head.split("\n")
        body = body.replace("\n", "\r\n") + ("\r\n" if body and not body.endswith("\n") else "") if sep else ""
        out = "\r\n".join(lines).replace("{clen}", str(len(body.encode())))
        return (out + "\r\n\r\n" + body).encode()

    # ---------- transport ----------
    def send_bytes(self, party: str, data: bytes, transport: str, dest: tuple | None = None,
                   via_conn: socket.socket | None = None) -> str | None:
        p = self.parties[party]
        dest = dest or (self.eut, self.eut_port)
        cid = re.search(rb"(?im)^(?:call-id|i)\s*:\s*(\S+)", data)
        if cid:
            self.own_callids.add(cid.group(1).decode("utf-8", "replace"))
        try:
            if transport == "udp":
                p.udp.sendto(data, dest)
            else:
                if via_conn is not None:
                    via_conn.sendall(data)
                    return None
                key = f"{party}->{dest}"
                c = self.tcp_out.get(key)
                if c is None:
                    c = socket.create_connection(dest, timeout=3, source_address=(self.host, 0))
                    c.setblocking(False)
                    self.tcp_out[key] = c
                    p.conns.append(c)
                    p.bufs[c] = b""
                c.sendall(data)
            return None
        except OSError as e:
            return f"send: {type(e).__name__}"

    def listen(self, ms: int) -> None:
        end = time.time() + ms / 1000
        while True:
            left = end - time.time()
            if left <= 0:
                return
            owners = {s: p for p in self.parties.values() for s in p.socks()}
            r, _, _ = select.select(list(owners), [], [], min(left, 0.1))
            for s in r:
                owners[s].on_ready(s)

    # ---------- steps ----------
    def step_send(self, st: dict) -> str | None:
        party = st.get("from", "alice")
        text = self.fill(st["msg"], party)
        data = self.finish(text)
        self.sent[party] = parse(data, st.get("transport", "udp"))
        self.last_raw[party] = data.decode("utf-8", "replace")
        if self.sent[party].get("method") == "INVITE":
            self.last_invite[party] = data.decode("utf-8", "replace")
        dest = None
        if st.get("to") and st["to"] != "eut":
            dest = (self.host, PARTIES[st["to"]])
        return self.send_bytes(party, data, st.get("transport", "udp"), dest)

    def step_reply(self, st: dict) -> str | None:
        p = self.parties[st.get("from", "bob")]
        req = p.last_req
        if req is None:
            return "reply: no request received"
        code = int(st["code"])
        lines = [f"SIP/2.0 {code} {st.get('reason', 'OK' if code == 200 else 'Reason')}"]
        for v in req["hdr"].get("via", []):
            lines.append(f"Via: {v}")
        for h in ("record-route",):
            for v in req["hdr"].get(h, []) if code < 300 or st.get("copy_rr") else []:
                lines.append(f"Record-Route: {v}")
        lines.append(f"From: {req['hdr'].get('from', [''])[0]}")
        to = req["hdr"].get("to", [""])[0]
        if ";tag=" not in to and code > 100 and not st.get("no_to_tag"):
            to += f";tag={self.named.setdefault('tag:' + p.name, f'{random.getrandbits(40):x}')}"
        lines.append(f"To: {to}")
        lines.append(f"Call-ID: {req['hdr'].get('call-id', [''])[0]}")
        lines.append(f"CSeq: {req['hdr'].get('cseq', [''])[0]}")
        if 200 <= code < 300 and req.get("method") in ("INVITE", "SUBSCRIBE", "REFER") and "contact" not in \
                {k.lower() for k in (st.get("headers") or {})}:
            lines.append(f"Contact: <sip:{p.name}@{self.host}:{p.port}>")
        for k, v in (st.get("headers") or {}).items():
            for x in (v if isinstance(v, list) else [v]):
                lines.append(f"{k}: {self.fill(str(x), p.name)}")
        body = self.fill(st.get("body", ""), p.name)
        lines.append("Content-Length: {clen}")
        data = self.finish("\n".join(lines) + "\n\n" + body)
        kind, src, conn = p.last_req_src
        return self.send_bytes(p.name, data, kind, src if kind == "udp" else None, via_conn=conn)

    def step_uac(self, st: dict) -> str | None:
        """Make the user agent under test act (UAC role) through its control interface: action call (to a URI),
        hangup, or raw (a command string for the interface). The target's `ctl` gives kind and port."""
        ctl = self.ctl or {}
        kind, port = ctl.get("kind"), int(ctl.get("port", 0))
        action, uri = st.get("action", "call"), self.fill(st.get("uri", ""), "alice")
        if kind == "pjsua":
            line = {"call": f"call new {uri}", "hangup": "call hangup", "hangup_all": "call hangup_all",
                    "message": f"im send {uri} pdcensus", "register": "acc reg"}.get(
                action, st.get("raw", ""))
            data = (line + "\r\n").encode()
        elif kind == "baresip":
            cmd, params = {"call": ("dial", uri), "hangup": ("hangup", ""), "hangup_all": ("hangupall", ""),
                           "message": ("message", f"{uri} pdcensus"),
                           "register": ("uareg", "3600")}.get(
                action, (st.get("raw", ""), ""))
            body = json.dumps({"command": cmd, "params": params, "token": "pd"}).encode()
            data = str(len(body)).encode() + b":" + body + b","
        elif kind == "linphone":
            line = {"call": f"call {uri}", "hangup": "terminate", "hangup_all": "terminate all",
                    "message": f"chat {uri} pdcensus", "register": "register sip:uas@lab.test sip:172.31.60.99:5073"}.get(
                action, st.get("raw", ""))
            data = (line + "\n").encode()
        else:
            return f"uac: target has no control interface ({kind})"
        try:
            with socket.create_connection((self.eut, port), timeout=3) as c:
                c.sendall(data)
                time.sleep(0.2)
        except OSError as e:
            return f"uac: {type(e).__name__}"
        return None

    def step_ack(self, st: dict) -> str | None:
        party = st.get("from", "alice")
        p = self.parties[party]
        inv, resp = self.last_invite.get(party), p.last_resp
        if not inv or not resp:
            return "ack: nothing to acknowledge"
        im = parse(inv.encode(), "udp")
        to = resp["hdr"].get("to", [""])[0]
        code = resp.get("code") or 0
        branch = (im["hdr"]["via"][0].split("branch=")[1].split(";")[0] if code >= 300
                  else f"z9hG4bK{random.getrandbits(48):x}")
        ruri = im["ruri"]
        route = []
        if 200 <= code < 300:
            contact = (resp["hdr"].get("contact") or [""])[0]
            m = re.search(r"<([^>]+)>", contact)
            ruri = m.group(1) if m else contact.split(";")[0] or ruri
            route = list(reversed(resp["hdr"].get("record-route", [])))
        lines = [f"ACK {ruri} SIP/2.0",
                 f"Via: SIP/2.0/{st.get('transport', 'udp').upper()} {self.host}:{p.port};branch={branch};rport",
                 "Max-Forwards: 70", f"From: {im['hdr']['from'][0]}", f"To: {to}",
                 f"Call-ID: {im['hdr']['call-id'][0]}", f"CSeq: {im['hdr']['cseq'][0].split()[0]} ACK"]
        lines += [f"Route: {r}" for r in route]
        lines.append("Content-Length: 0")
        return self.send_bytes(party, self.finish("\n".join(lines)), st.get("transport", "udp"))


# ---------------- extraction (same language as the JMAP cell) ----------------
_MISSING = object()


def _walk(cur, parts):
    for n, p in enumerate(parts):
        if p == "*":
            items = cur if isinstance(cur, list) else (list(cur.values()) if isinstance(cur, dict) else [])
            vals = [_walk(x, parts[n + 1:]) for x in items]
            return [v for v in vals if v is not _MISSING]
        if isinstance(cur, list):
            try:
                cur = cur[int(p)]
            except (ValueError, IndexError):
                return _MISSING
        elif isinstance(cur, dict):
            if p not in cur:
                return _MISSING
            cur = cur[p]
        else:
            return _MISSING
    return cur


FN = r"^(len|keys|type|has|eq|ne|in|re|allre|allin|sorted|lower|ge|le|hasall|count|codes|methods|same|nre|ncodes):"


def _lit(s):
    try:
        return json.loads(s)
    except ValueError:
        return s


def evaluate(expr: str, ctx: dict):
    fn, _, body = expr.partition(":") if re.match(FN, expr) else ("", "", expr)
    if fn == "same":
        # same:p1,p2: the values at two paths are equal (a forwarded body against the sent one)
        pa, pb = body.split(",", 1)
        va, vb = _walk(ctx, [x for x in pa.split("/") if x]), _walk(ctx, [x for x in pb.split("/") if x])
        return va is not _MISSING and va == vb
    path, _, lit = body.partition("=") if fn else (expr, "", "")
    parts = [p for p in path.split("/") if p]
    v = _walk(ctx, parts) if parts else ctx
    missing = v is _MISSING
    v = None if missing else v
    if not fn:
        return v
    if fn == "has":
        return not missing
    if fn == "len":
        return len(v) if isinstance(v, (list, dict, str)) else 0 if missing else None
    if fn == "codes":   # final and provisional status codes in a party's inbox, in order
        return [m.get("code") for m in v or [] if m.get("kind") == "resp"]
    if fn == "ncodes":
        # ncodes:party=code: how many responses with that status code the party received (retransmissions)
        return sum(1 for m in v or [] if m.get("kind") == "resp" and m.get("code") == int(lit))
    if fn == "methods":
        return [m.get("method") for m in v or [] if m.get("kind") == "req"]
    if fn == "keys":
        return sorted(v) if isinstance(v, dict) else None
    if fn == "type":
        return "missing" if missing else type(v).__name__
    if fn == "eq":
        return v == _lit(lit)
    if fn == "ne":
        return v != _lit(lit)
    if fn == "in":
        return isinstance(v, (list, str, dict)) and _lit(lit) in v
    if fn == "re":
        return isinstance(v, str) and re.fullmatch(lit, v, re.S) is not None
    if fn == "nre":
        return not (isinstance(v, str) and re.fullmatch(lit, v, re.S) is not None)
    if fn == "allre":
        return isinstance(v, list) and all(isinstance(x, str) and re.fullmatch(lit, x, re.S) for x in v)
    if fn == "allin":
        return isinstance(v, list) and all(x is None or x in _lit(lit) for x in v)
    if fn == "sorted":
        return sorted(v) if isinstance(v, list) else None
    if fn == "lower":
        return v.lower() if isinstance(v, str) else None
    if fn == "ge":
        return isinstance(v, (int, float)) and v >= float(lit)
    if fn == "le":
        return isinstance(v, (int, float)) and v <= float(lit)
    if fn == "hasall":
        return None if missing else all(x in (v or []) for x in _lit(lit))
    if fn == "count":
        return v.count(_lit(lit)) if isinstance(v, list) else 0
    return None


def digest_header(challenge: str, method: str, uri: str, user: str, password: str) -> str:
    """Authorization/Proxy-Authorization value for an MD5 digest challenge (RFC 3261 22.4, RFC 2617)."""
    import hashlib
    kv = dict(re.findall(r'(\w+)="?([^",]*)"?', challenge))
    realm, nonce, qop = kv.get("realm", ""), kv.get("nonce", ""), kv.get("qop", "")

    def h(x: str) -> str:
        return hashlib.md5(x.encode()).hexdigest()

    ha1, ha2 = h(f"{user}:{realm}:{password}"), h(f"{method}:{uri}")
    parts = [f'username="{user}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"', "algorithm=MD5"]
    if "auth" in qop.split(","):
        cnonce, nc = f"{random.getrandbits(32):08x}", "00000001"
        parts += [f'response="{h(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")}"', "qop=auth", f"nc={nc}",
                  f'cnonce="{cnonce}"']
    else:
        parts.append(f'response="{h(f"{ha1}:{nonce}:{ha2}")}"')
    if kv.get("opaque"):
        parts.append(f'opaque="{kv["opaque"]}"')
    return "Digest " + ", ".join(parts)


def authenticate(lab: "Lab", st: dict, start: int) -> str | None:
    """If the actor got 401/407 for the request it just sent, resend it once with credentials (new branch, CSeq+1).
    Used where the element requires authentication (ejabberd's registrar); the challenge itself stays observed."""
    party = st.get("from", "alice")
    p = lab.parties[party]
    ch = next((m for m in p.inbox[start:] if m.get("kind") == "resp" and m.get("code") in (401, 407)), None)
    if ch is None:
        return "no-challenge"
    hname = "www-authenticate" if ch["code"] == 401 else "proxy-authenticate"
    challenge = (ch["hdr"].get(hname) or [""])[0]
    sent = lab.last_raw.get(party, "")
    head, _sep, body = sent.partition("\r\n\r\n")
    lines = head.split("\r\n")
    method, uri = lines[0].split(" ")[0], lines[0].split(" ")[1]
    out = [lines[0]]
    for ln in lines[1:]:
        low = ln.lower()
        if low.startswith("via:"):
            ln = re.sub(r"branch=[^;\s]+", f"branch=z9hG4bK{random.getrandbits(48):x}", ln)
        elif low.startswith("cseq:"):
            n, m = ln.split(":", 1)[1].split()
            ln = f"CSeq: {int(n) + 1} {m}"
        out.append(ln)
    user, pw = st["auth"]["user"], st["auth"]["password"]
    out.insert(1, f"{'Authorization' if ch['code'] == 401 else 'Proxy-Authorization'}: "
                  f"{digest_header(challenge, method, uri, user, pw)}")
    data = ("\r\n".join(out) + "\r\n\r\n" + body).encode()
    lab.last_raw[party] = data.decode("utf-8", "replace")
    return lab.send_bytes(party, data, st.get("transport", "udp"))


def run(req: dict) -> list[dict]:
    lab = Lab(req)
    out = []
    for i, st in enumerate(req["steps"], 1):
        op = st.get("op", "send")
        if op == "wait":
            lab.listen(int(st.get("ms", 1000)))
            continue
        start = {n: len(p.inbox) for n, p in lab.parties.items()}
        t0 = time.time()
        try:
            err = {"send": lab.step_send, "reply": lab.step_reply, "ack": lab.step_ack, "uac": lab.step_uac}[op](st)
        except Exception as e:  # noqa: BLE001
            err = f"driver: {e!r}"[:200]
        lab.listen(int(st.get("window_ms", 1500)))
        party = st.get("from", "alice")
        if op == "send" and not st.get("auth") and lab.creds.get(party):
            # the element requires authentication (target credentials): answer its challenge once
            st = {**st, "auth": {"user": party, "password": lab.creds[party]}}
        if op == "send" and st.get("auth") and err is None:
            # listen a second window only when a challenge was answered (the authenticated request is a new
            # transaction); without a challenge the step keeps its single window
            err = authenticate(lab, st, start[st.get("from", "alice")])
            if err == "no-challenge":
                err = None
            else:
                lab.listen(int(st.get("window_ms", 1500)))
        raw = {n: p.inbox[start[n]:] for n, p in lab.parties.items()}
        got = {n: [m for m in msgs if not is_probe(m, lab.own_callids)] for n, msgs in raw.items()}
        obs = {"step": i, "op": op, "actor": st.get("from", "alice" if op != "reply" else "bob"), "error": err,
               "got": got, "ms": int((time.time() - t0) * 1000)}
        probes = {n: len(raw[n]) - len(got[n]) for n in raw if len(raw[n]) != len(got[n])}
        if probes:
            obs["probes"] = probes
        if st.get("extract"):
            # step messages per party, the last request each party sent, and every message each party received so
            # far in the fixture ("hist", for clauses that compare a retransmission with the first transmission)
            ctx = {**got, "sent": lab.sent,
                   "hist": {n: [m for m in p.inbox if not is_probe(m, lab.own_callids)] for n, p in lab.parties.items()}}
            obs["extract"] = {k: _unu(evaluate(lab.fill(e, obs["actor"]), ctx), lab) for k, e in st["extract"].items()}
        out.append(obs)
    return out


def is_probe(m: dict, own_callids: set[str]) -> bool:
    """An OPTIONS request on a Call-ID the lab never sent in this fixture: the element's own keep-alive or
    dispatcher probing (or a UA's), which lands in whichever step window is open when its timer fires. It is not
    the fixture's behaviour, so it is kept out of the step (counted in the observation's `probes`)."""
    if m.get("kind") != "req" or m.get("method") != "OPTIONS":
        return False
    cid = (m.get("hdr", {}).get("call-id") or [""])[0]
    return cid not in own_callids


def _ceil10(m: re.Match) -> str:
    return f"{m.group(1)}{-(-int(m.group(2)) // 10) * 10}"


def mask_random(s: str) -> str:
    """Per-run random tokens mapped to placeholders, so a value compares across repeats: Via branches (the RFC 3261
    magic cookie kept), tag and ftag values, and remaining registration time in an expires parameter (rounded up to
    ten seconds: the registrar counts down from the time of the REGISTER, which moves with step timing)."""
    s = re.sub(r"(branch=z9hG4bK)[^;>,\s\"]+", r"\1{r}", s)
    s = re.sub(r"([;?&](?:f?tag|branch))=(?!z9hG4bK)[^;>,\s\"]+", r"\1={r}", s)
    return re.sub(r"(expires=)(\d+)", _ceil10, s)


def _unu(v, lab: Lab):
    """Lab-specific values (unique name, lab and EUT addresses) mapped to placeholders, and per-run random tokens
    masked (mask_random), so values compare."""
    if isinstance(v, str):
        return mask_random(v.replace(lab.unique, "{U}").replace(lab.host, "{lab}").replace(lab.eut, "{eut}"))
    if isinstance(v, list):
        return [_unu(x, lab) for x in v]
    if isinstance(v, dict):
        return {k: _unu(x, lab) for k, x in v.items()}
    return v


def main() -> int:
    req = json.load(sys.stdin)
    json.dump(run(req), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

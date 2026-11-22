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
  reply  {from: bob, code, reason="", headers: {...}, body="", window_ms, answers=METHOD}: a response to the last
         request the party received (with answers: the last request of that method, so a retransmission of another
         request arriving late cannot become the one answered), copying Via, From, To (adding a To tag unless the
         request had one), Call-ID and CSeq (RFC 3261 8.2.6).
  ack    {from: alice, window_ms}: ACK for the last INVITE alice sent and the last final response she received
         (non-2xx: same branch, RFC 3261 17.1.1.3; 2xx: new branch, to the Contact or the Record-Route set).
  wait   {ms}
Every step listens for window_ms (default 1500) on all parties and records what each received, parsed:
  {kind: req|resp, method, ruri, code, reason, hdr: {lower-case name: [values]}, body, transport}
`extract` on a step names fixture-specific observations evaluated here (see evaluate()).
Output (stdout, JSON): one observation per step except wait.
"""

from __future__ import annotations

import base64
import json
import math
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
    # "wire": the exact bytes as received, for consumer replay (manuscript 3.6); never compared
    msg: dict = {"transport": transport, "hdr": {}, "body": body, "wire": base64.b64encode(data).decode()}
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
        self.last_req_by: dict[str, tuple[dict, tuple]] = {}  # method -> (last request of that method, its source)
        self.last_resp: dict | None = None
        self.aside = None  # set by Lab: is a received message a probe or a stray (see set_aside)

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
        m["at"] = round(time.time(), 3)  # arrival time, for the retransmission-timer extracts (retx, firstat)
        self.inbox.append(m)
        if self.aside is not None and self.aside(m):
            # a probe or a stray (another fixture's late retransmission) is recorded but never becomes the message
            # a later reply, ack or {last:...} template answers: answering it would adopt its Call-ID as the lab's
            return
        if m["kind"] == "req":
            self.last_req, self.last_req_src = m, src
            self.last_req_by[str(m.get("method"))] = (m, src)
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
        # answered digest challenges: per party, the branches of the challenged transactions (their responses are
        # left out of extracts, which read the outcome after authentication), and old branch -> (new branch, CSeq
        # number) of the resent request, so a later CANCEL matches the INVITE that was last sent (RFC 3261 9.1)
        self.challenged: dict[str, set[str]] = {}
        self.rebranch: dict[str, tuple[str, int]] = {}
        for p in self.parties.values():
            p.aside = self.is_aside

    def is_aside(self, m: dict) -> bool:
        opened = opened_callids(x for q in self.parties.values() for x in q.inbox)
        return set_aside(m, self.own_callids, opened) is not None

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
        if text.lstrip().upper().startswith("CANCEL "):
            # a CANCEL carries the branch and CSeq number of the INVITE it cancels; after an answered challenge that
            # is the resent INVITE, not the one the fixture text names
            mb = re.search(r"branch=([^;\s]+)", text)
            if mb and mb.group(1) in self.rebranch:
                nb, n = self.rebranch[mb.group(1)]
                text = text.replace(f"branch={mb.group(1)}", f"branch={nb}")
                text = re.sub(r"(?im)^(CSeq:\s*)\d+(\s+CANCEL)", rf"\g<1>{n}\g<2>", text)
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
        req, req_src = p.last_req, p.last_req_src
        if st.get("answers"):
            req, req_src = p.last_req_by.get(str(st["answers"]), (None, None))
        if req is None:
            return "reply: no request received" + (f" ({st['answers']})" if st.get("answers") else "")
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
        kind, src, conn = req_src
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


FN = r"^(len|keys|type|has|eq|ne|in|re|allre|allin|sorted|lower|ge|le|hasall|count|codes|methods|same|nre|ncodes|hascode|ackresp|retx|firstat|vals|firstafter|prov1xx):"


def _branch(m: dict) -> str:
    v = str(((m.get("hdr") or {}).get("via") or [""])[0])
    mb = re.search(r"branch=([^;\s,]+)", v)
    return mb.group(1) if mb else ""


def _txkey(m: dict) -> tuple:
    """One message per transaction and outcome: a retransmitted copy has the same key (code or method, CSeq, top
    Via branch, and for responses the To tag, so the 2xx of two forks stay apart)."""
    h = m.get("hdr") or {}
    cseq = " ".join(str((h.get("cseq") or [""])[0]).split())
    if m.get("kind") == "resp":
        mt = re.search(r";\s*tag=([^;\s]+)", str((h.get("to") or [""])[0]))
        return ("resp", m.get("code"), cseq, _branch(m), mt.group(1) if mt else "")
    return ("req", str(m.get("method", "")).upper(), cseq, _branch(m))


def _once(msgs) -> list:
    seen, out = set(), []
    for m in msgs or []:
        k = _txkey(m)
        if k not in seen:
            seen.add(k)
            out.append(m)
    return out


def _split_list(row: str) -> list[str]:
    """Split one header row into its comma-separated values (RFC 3261 7.3.1), not inside quotes or <...>."""
    out, cur, q, a = [], "", False, 0
    for ch in row:
        if ch == '"':
            q = not q
        elif not q and ch == "<":
            a += 1
        elif not q and ch == ">" and a:
            a -= 1
        if ch == "," and not q and not a:
            out.append(cur.strip())
            cur = ""
            continue
        cur += ch
    out.append(cur.strip())
    return [x for x in out if x]


def _lit(s):
    try:
        return json.loads(s)
    except ValueError:
        return s


def evaluate(expr: str, ctx: dict):
    if expr.startswith("when:"):
        # when:COND?EXPR: EXPR where COND holds, else None (the row does not bind: recorded as null)
        cond, _, rest = expr[5:].partition("?")
        return evaluate(rest, ctx) if evaluate(cond, ctx) else None
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
    if fn == "firstafter":
        # firstafter:party=KEY,SECONDS: the first copy of the request method or response code KEY arrived at least
        # SECONDS after the step started (True), earlier (False), or never (None). For RFC 4320 4.1: no 100 to a
        # non-INVITE request over UDP before Timer E reaches T2 (0.5 + 1 + 2 s with the default timers).
        key, _, sec = lit.partition(",")
        key = key.upper()
        times = sorted(m["at"] for m in v or [] if "at" in m and (
            (m.get("kind") == "req" and str(m.get("method", "")).upper() == key)
            or (m.get("kind") == "resp" and str(m.get("code")) == key)))
        return None if not times else (times[0] - ctx.get("t0", times[0])) >= float(sec)
    if fn == "prov1xx":
        # prov1xx:party: the party received a provisional response other than 100 (RFC 4320 4.1, non-INVITE)
        return any(m.get("kind") == "resp" and 101 <= int(m.get("code") or 0) <= 199 for m in v or [])
    if fn in ("retx", "firstat"):
        # retx:party=KEY: copies of one request method (or response code) the party received in the step, and the
        # gaps between successive copies on the T1 doubling scale (0.5, 1, 2, 4, 8, 16 s: each gap goes to the
        # nearest power of two times 0.5 s), so timers A/E/G and their T2 cap are compared, not network jitter.
        # firstat:party=KEY: seconds from the step's start to the first copy, rounded down to 10 s (None if none);
        # for timers measured in minutes (Timer C).
        key = lit.upper()
        hits = [m for m in v or [] if (m.get("kind") == "req" and str(m.get("method", "")).upper() == key)
                or (m.get("kind") == "resp" and str(m.get("code")) == key)]
        times = sorted(m["at"] for m in hits if "at" in m)
        if fn == "firstat":
            return None if not times else int((times[0] - ctx.get("t0", times[0])) // 10 * 10)
        gaps = [0.5 * 2 ** round(math.log2(max(b - a, 0.125) / 0.5)) for a, b in zip(times, times[1:])]
        return {"copies": len(hits), "gaps": [int(g) if g >= 1 else g for g in gaps]}
    if fn == "codes":
        # final and provisional status codes in a party's inbox, in order, one per transaction and outcome: a
        # retransmitted copy is not a second response (copies are counted by ncodes: and retx:)
        return [m.get("code") for m in _once(v) if m.get("kind") == "resp"]
    if fn == "vals":
        # vals:path: the values of a header, rows split at commas, so two rows and one comma list compare equal
        rows = v if isinstance(v, list) else ([] if missing else [v])
        return [x for r in rows for x in _split_list(str(r))]
    if fn == "ncodes":
        # ncodes:party=code: how many responses with that status code the party received (retransmissions)
        return sum(1 for m in v or [] if m.get("kind") == "resp" and m.get("code") == int(lit))
    if fn == "hascode":
        # hascode:party=code: the party received a response with that status code (any step so far when given hist/)
        return any(m.get("kind") == "resp" and m.get("code") == int(lit) for m in v or [])
    if fn == "ackresp":
        # ackresp:party: how many responses in the party's inbox carry CSeq method ACK; an ACK is never answered, so a
        # response to it shows the ACK was not matched to its transaction (RFC 3261 17.2.1)
        def _cseq_method(m):
            vals = (m.get("hdr") or {}).get("cseq") or [""]
            parts = str(vals[0]).split()
            return parts[-1].upper() if parts else ""
        return sum(1 for m in v or [] if m.get("kind") == "resp" and _cseq_method(m) == "ACK")
    if fn == "methods":
        return [m.get("method") for m in _once(v) if m.get("kind") == "req"]
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
    old_branch = ch_branch = _branch(ch)
    new_branch, new_n = f"z9hG4bK{random.getrandbits(48):x}", None
    out = [lines[0]]
    ack = None
    for ln in lines[1:]:
        low = ln.lower()
        if low.startswith("via:"):
            mb = re.search(r"branch=([^;\s]+)", ln)
            old_branch = mb.group(1) if mb else ch_branch
            ln = re.sub(r"branch=[^;\s]+", f"branch={new_branch}", ln)
        elif low.startswith("cseq:"):
            n, m = ln.split(":", 1)[1].split()
            new_n = int(n) + 1
            ln = f"CSeq: {new_n} {m}"
        out.append(ln)
    if method == "INVITE":
        # the 407/401 to an INVITE is a final response: the UAC acknowledges it (RFC 3261 17.1.1.3) on the challenged
        # transaction, before the authenticated INVITE, so the element stops retransmitting the challenge
        h = ch.get("hdr") or {}
        orig = {ln.split(":", 1)[0].strip().lower(): ln for ln in lines[1:] if ":" in ln}
        n0 = (orig.get("cseq", "CSeq: 1 INVITE").split(":", 1)[1].split() or ["1"])[0]
        ack = "\r\n".join([f"ACK {uri} SIP/2.0", orig.get("via", ""), "Max-Forwards: 70", orig.get("from", ""),
                            f"To: {(h.get('to') or [''])[0]}", orig.get("call-id", ""), f"CSeq: {n0} ACK",
                            "Content-Length: 0"]) + "\r\n\r\n"
    lab.challenged.setdefault(party, set()).add(old_branch)
    if new_n is not None:
        lab.rebranch[old_branch] = (new_branch, new_n)
    user, pw = st["auth"]["user"], st["auth"]["password"]
    out.insert(1, f"{'Authorization' if ch['code'] == 401 else 'Proxy-Authorization'}: "
                  f"{digest_header(challenge, method, uri, user, pw)}")
    data = ("\r\n".join(out) + "\r\n\r\n" + body).encode()
    lab.last_raw[party] = data.decode("utf-8", "replace")
    if method == "INVITE":
        lab.last_invite[party] = lab.last_raw[party]
    if ack is not None:
        err = lab.send_bytes(party, ack.encode(), st.get("transport", "udp"))
        if err:
            return err
    return lab.send_bytes(party, data, st.get("transport", "udp"))


def run(req: dict) -> list[dict]:
    lab = Lab(req)
    out = []
    if lab.ctl.get("kind") and req.get("reset_calls", True):
        # a user agent under test starts every fixture with no call: calls an earlier fixture left answered would
        # count against its call limit (pjsua --max-calls: 486 to the next INVITE, no new UAC call). Messages this
        # causes (BYE to an earlier fixture's Call-ID) arrive before step 1 and belong to no step.
        lab.step_uac({"action": "hangup_all"})
        lab.listen(int(req.get("reset_ms", 600)))
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
        who = st.get("auth_as", party)
        if op == "send" and not st.get("auth") and lab.creds.get(who):
            # the element requires authentication (target credentials): answer its challenge once, as the user
            # the request acts for (`auth_as`: a second contact registered for bob's AOR uses bob's credentials)
            st = {**st, "auth": {"user": who, "password": lab.creds[who]}}
        if op == "send" and st.get("auth") and err is None:
            # listen a second window only when a challenge was answered (the authenticated request is a new
            # transaction); without a challenge the step keeps its single window
            err = authenticate(lab, st, start[st.get("from", "alice")])
            if err == "no-challenge":
                err = None
            else:
                lab.listen(int(st.get("window_ms", 1500)))
        raw = {n: p.inbox[start[n]:] for n, p in lab.parties.items()}
        opened = opened_callids(m for p in lab.parties.values() for m in p.inbox)
        got = {n: [m for m in msgs if not set_aside(m, lab.own_callids, opened)] for n, msgs in raw.items()}
        obs = {"step": i, "op": op, "actor": st.get("from", "alice" if op != "reply" else "bob"), "error": err,
               "got": got, "ms": int((time.time() - t0) * 1000)}
        for why in ("probe", "stray"):
            n_aside = {n: c for n in raw if (c := sum(1 for m in raw[n] if set_aside(m, lab.own_callids, opened) == why))}
            if n_aside:
                obs[why + "s"] = n_aside
        if st.get("extract"):
            # step messages per party, the last request each party sent, and every message each party received so
            # far in the fixture ("hist", for clauses that compare a retransmission with the first transmission)
            def _after_auth(n, msgs):
                # responses on a challenged transaction (the 401/407, its 100 and retransmissions) are left out, so
                # index 0 and code lists read the outcome of the authenticated request; observations keep them
                gone = lab.challenged.get(n) or set()
                return [m for m in msgs if not (m.get("kind") == "resp" and _branch(m) in gone)]
            ctx = {**{n: _after_auth(n, msgs) for n, msgs in got.items()}, "sent": lab.sent, "t0": t0,
                   "hist": {n: _after_auth(n, [m for m in p.inbox if not set_aside(m, lab.own_callids, opened)])
                            for n, p in lab.parties.items()}}
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


LAB_TOKEN = re.compile(r"pd[0-9a-f]{10}")  # the per-fixture unique ("pd" + 10 hex of the fixture id, runner.py)


def _callid(m: dict) -> str:
    return (m.get("hdr", {}).get("call-id") or [""])[0]


def _in_dialog(m: dict) -> bool:
    return ";tag=" in ((m.get("hdr", {}).get("to") or [""])[0]).replace(" ", "").lower()


def opened_callids(msgs) -> set[str]:
    """Call-IDs of out-of-dialog requests (no To tag) a party received: a call or registration the element or the
    user agent under test started itself in this fixture."""
    return {_callid(m) for m in msgs if m.get("kind") == "req" and not _in_dialog(m)}


def is_stray(m: dict, own_callids: set[str], opened: set[str] = frozenset()) -> bool:
    """A message that belongs to another fixture, or to an earlier repeat of this one, and lands in whichever step
    window is open. (1) A Call-ID with a lab token (every lab Call-ID template holds {U} or {callid}) that the lab
    did not send in this fixture: the element still finishing an old transaction (retransmitting toward a registered
    contact, timing out with 408). (2) A Call-ID without a lab token that nothing opened in this fixture (`opened`,
    see opened_callids), on a response or an in-dialog request (To tag): a user agent ending an old dialog (a late
    BYE). A new call the element or user agent starts (no To tag) is behaviour."""
    cid = _callid(m)
    if cid in own_callids:
        return False
    if LAB_TOKEN.search(cid):
        return True
    if cid in opened:
        return False
    return m.get("kind") == "resp" or _in_dialog(m)


def set_aside(m: dict, own_callids: set[str], opened: set[str] = frozenset()) -> str | None:
    """Why a received message is kept out of the step: "probe", "stray", or None (it is the fixture's behaviour)."""
    if is_probe(m, own_callids):
        return "probe"
    if is_stray(m, own_callids, opened):
        return "stray"
    return None


def _ceil10(m: re.Match) -> str:
    return f"{m.group(1)}{-(-int(m.group(2)) // 10) * 10}"


def mask_random(s: str) -> str:
    """Per-run random tokens mapped to placeholders, so a value compares across repeats: Via branches (the RFC 3261
    magic cookie kept), tag and ftag values, and remaining registration time in an expires parameter (rounded up to
    ten seconds: the registrar counts down from the time of the REGISTER, which moves with step timing), and an
    opaque URI user part holding 16 or more hex digits (ejabberd puts a timestamp and hash in its Record-Route)."""
    s = re.sub(r"(branch=z9hG4bK)[^;>,\s\"]+", r"\1{r}", s)
    s = re.sub(r"([;?&](?:f?tag|branch))=(?!z9hG4bK)[^;>,\s\"]+", r"\1={r}", s)
    s = re.sub(r"(sips?:)[0-9A-Za-z-]*[0-9a-f]{16,}[0-9A-Za-z-]*@", r"\1{r}@", s)
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

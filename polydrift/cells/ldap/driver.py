"""LDAP cell driver: one fixture script against one server, raw step observations out.

Steps name a connection (``client``), an ``op`` (open, close, sleep, hex, or a request in codec.BUILDERS), ``args``,
optional ``controls`` and ``id`` (message ID; default: next per connection). A request the protocol answers is
waited for until its final response (SearchResultDone for a search) or REPLY_MAX_MS, then SETTLE_MS more; other steps
listen for ``window_ms``. Every open connection is observed at every step. Placeholders: ``{B}`` is the target's base
DN, ``{A}`` its admin DN, ``{P}`` the admin password, ``{U}`` a name unique to the fixture. ``{@attr}`` is the first
value of attribute ``attr`` in the last search entry this connection received (for example the subschema subentry
named by the root DSE). ``burst`` sends several requests in one write (``args: {requests: [{op, args, id}, ...]}``)
and waits for the final response of the last one (Bind and Abandon interleaving, RFC 4511 4.2.1 and 4.11).
"""

from __future__ import annotations

import select
import socket
import time
from dataclasses import dataclass, field
from typing import Any

from polydrift.cells.ldap import codec

DEFAULT_WINDOW_MS = 800
REPLY_MAX_MS = 5000
SETTLE_MS = 300
FINAL = {"BindResponse", "SearchResultDone", "ModifyResponse", "AddResponse", "DelResponse", "ModifyDNResponse",
         "CompareResponse", "ExtendedResponse"}


def subst(value: Any, env: dict[str, str]) -> Any:
    if isinstance(value, str):
        for k, v in env.items():
            value = value.replace("{" + k + "}", v)
        return value
    if isinstance(value, list):
        return [subst(v, env) for v in value]
    if isinstance(value, dict):
        return {k: subst(v, env) for k, v in value.items()}
    return value


@dataclass
class Conn:
    sock: socket.socket | None = None
    buf: bytes = b""
    closed: bool = False
    next_id: int = 1
    inbox: list[dict[str, Any]] = field(default_factory=list)

    def send(self, data: bytes) -> str | None:
        if self.sock is None or self.closed:
            return "not-open"
        try:
            self.sock.sendall(data)
            return None
        except OSError as e:
            self.closed = True
            return f"send-failed: {type(e).__name__}"

    def pump(self) -> None:
        if self.sock is None or self.closed:
            return
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            self.closed = True
            return
        if not data:
            self.closed = True
            return
        self.buf += data
        msgs, self.buf = codec.split_stream(self.buf)
        self.inbox.extend(codec.decode(m) for m in msgs)


def _lower_dyn(value: Any) -> Any:
    """Lower-case the attribute name inside ``{@attr}`` placeholders (attribute names are case-insensitive)."""
    if isinstance(value, str) and "{@" in value:
        import re
        return re.sub(r"\{@([^}]+)\}", lambda m: "{@" + m.group(1).lower() + "}", value)
    if isinstance(value, list):
        return [_lower_dyn(v) for v in value]
    if isinstance(value, dict):
        return {k: _lower_dyn(v) for k, v in value.items()}
    return value


def run_script(host: str, port: int, steps: list[dict[str, Any]], env: dict[str, str]) -> list[dict[str, Any]]:
    conns: dict[str, Conn] = {}
    out = []
    for i, raw in enumerate(steps, 1):
        name = raw.get("client", "a")
        c = conns.setdefault(name, Conn())
        dyn = dict(env)
        for m in reversed(c.inbox):
            for k, vals in (m.get("attributes") or {}).items():
                if vals and "@" + k.lower() not in dyn:
                    dyn["@" + k.lower()] = vals[0]
        st = subst(_lower_dyn(raw), dyn)
        op = st.get("op", "hex")
        err = None
        was_closed = {n: k.closed for n, k in conns.items()}
        # a connection is opened on first use, or again by an explicit "open"; a request on a connection the server
        # closed is not silently reconnected (it is recorded as a send to a closed connection)
        if (c.sock is None and op not in ("close", "sleep")) or (op == "open" and c.closed):
            try:
                c.sock = socket.create_connection((host, port), timeout=3)
                c.sock.setblocking(False)
                c.closed, c.buf = False, b""
            except OSError as e:
                err = f"tcp-connect-failed: {type(e).__name__}"
        mid = None
        if err is None:
            if op == "hex":
                err = c.send(bytes.fromhex(st["hex"].replace(" ", "")))
            elif op == "close":
                if c.sock is not None:
                    try:
                        c.sock.close()
                    except OSError:
                        pass
                c.closed = True
            elif op in ("open", "sleep"):
                pass
            elif op == "burst":
                data = b""
                for rq in (st.get("args") or {}).get("requests") or []:
                    mid = int(rq.get("id", c.next_id))
                    c.next_id = max(c.next_id, mid) + 1
                    data += codec.message(mid, codec.BUILDERS[rq["op"]](rq.get("args") or {}), rq.get("controls"))
                err = c.send(data)
            elif op in codec.BUILDERS:
                mid = int(st.get("id", c.next_id))
                c.next_id = max(c.next_id, mid) + 1
                err = c.send(codec.message(mid, codec.BUILDERS[op](st.get("args") or {}), st.get("controls")))
            else:
                err = f"unknown-op {op}"
        start = {n: len(k.inbox) for n, k in conns.items()}
        need = err is None and (op in codec.ANSWERED or op == "burst" or st.get("expect_reply")) and "window_ms" not in st
        deadline = time.monotonic() + (REPLY_MAX_MS if need else st.get("window_ms", DEFAULT_WINDOW_MS)) / 1000.0
        settled = False
        while True:
            if need and not settled and (c.closed or any(p.get("op") in FINAL and p.get("id") == mid
                                                         for p in c.inbox[start.get(name, 0):])):
                deadline = min(deadline, time.monotonic() + SETTLE_MS / 1000.0)
                settled = True
            left = deadline - time.monotonic()
            if left <= 0:
                break
            socks = [k.sock for k in conns.values() if k.sock is not None and not k.closed]
            if not socks:
                time.sleep(min(left, 0.05))
                continue
            ready, _, _ = select.select(socks, [], [], min(left, 0.1))
            for k in conns.values():
                if k.sock in ready:
                    k.pump()
        obs = {"step": i, "actor": name, "op": op, "id": mid, "end": bool(st.get("end")), "error": err,
               "expects_reply": bool(need or (op in codec.ANSWERED)), "clients": {}}
        for n, k in conns.items():
            obs["clients"][n] = {"messages": k.inbox[start.get(n, 0):],
                                 "closed_now": k.closed and not was_closed.get(n, False) and not (n == name and op == "close")}
        out.append(obs)
    for k in conns.values():
        if k.sock is not None and not k.closed:
            try:
                k.sock.close()
            except OSError:
                pass
    return out

"""MQTT cell driver: runs one fixture script against one broker and returns raw step observations.

A fixture is a list of steps. Each step is performed by one named client (``a``, ``b``, ...), each client its own TCP
connection. After every step the driver listens on every open connection for the step window and records, per
client, the packets received and whether the connection closed. The consumer in MQTT is not only the sender: a
broker's divergence shows in what other clients receive, so every client is observed at every step.

Placeholders in fixture strings: ``{T}`` is a topic prefix and ``{C}`` a client-id prefix unique to the fixture and
repeat, so fixtures never see each other's retained messages or sessions.

Step keys: ``client``, ``op`` (open, close, sleep, hex, burst, or a builder in codec.BUILDERS), ``args`` (builder
arguments), ``hex`` (exact bytes for op ``hex``), ``window_ms`` (listen time, default 1000; a step that requires an answer waits for it, up to 5 s), ``end`` (true: the step
belongs to the end-state probe, not to the fixture's steps), ``auto_ack`` (client default true: QoS 1 and 2
deliveries are acknowledged so the broker's flow completes).

``burst`` sends several packets in one write (``args: {packets: [{op, args}, ...]}``), so the broker receives them
back to back (ordering clauses, 4.6). ``packet_id: last`` in an acknowledgement's args is the Packet Identifier of
the last QoS 1 or 2 PUBLISH the client received, for clauses on the server's own identifiers (4.3.3).
"""

from __future__ import annotations

import select
import socket
import time
from dataclasses import dataclass, field
from typing import Any

from polydrift.cells.mqtt import codec

DEFAULT_WINDOW_MS = 1000
REPLY_MAX_MS = 5000
SETTLE_MS = 400


def _subst(value: Any, t: str, c: str) -> Any:
    if isinstance(value, str):
        return value.replace("{T}", t).replace("{C}", c)
    if isinstance(value, list):
        return [_subst(v, t, c) for v in value]
    if isinstance(value, dict):
        return {k: _subst(v, t, c) for k, v in value.items()}
    return value


def expects_reply(step: dict[str, Any]) -> bool:
    """Does the protocol require the broker to answer this step? (CONNACK, SUBACK, UNSUBACK, PINGRESP, PUBACK or
    PUBREC, PUBCOMP.) A fixture can override with ``expect_reply``; raw bytes default to expecting one."""
    if "expect_reply" in step:
        return bool(step["expect_reply"])
    op = step.get("op", "hex")
    if op in ("connect", "subscribe", "unsubscribe", "pingreq", "pubrel", "hex"):
        return True
    if op == "publish":
        a = step.get("args") or {}
        return int(a.get("qos", 0)) > 0 or (int(a.get("flags", 0)) >> 1) & 3 > 0
    return False


@dataclass
class Client:
    name: str
    sock: socket.socket | None = None
    version: int = 5
    buf: bytes = b""
    closed: bool = False
    auto_ack: bool = True
    inbox: list[dict[str, Any]] = field(default_factory=list)
    wire: list[str] = field(default_factory=list)  # each inbox packet as received (hex), for consumer replay
    # limits this client announced in its CONNECT (MQTT 5.0 3.1.2.11): the server must respect them when it sends
    max_size: int | None = None
    alias_max: int = 0
    last_pid: int | None = None
    unacked: set = field(default_factory=set)
    reuse: int = 0

    def send(self, data: bytes) -> str | None:
        if self.sock is None:
            return "not-open"
        if self.closed:
            # the broker closed this connection in an earlier step: the send is the broker's outcome, not a fault
            return "closed-by-peer"
        try:
            self.sock.sendall(data)
            return None
        except OSError as e:
            self.closed = True
            return f"send-failed: {type(e).__name__}"

    def pump(self) -> None:
        """Read what is available, split packets, auto-acknowledge deliveries."""
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
        pkts, self.buf = codec.split_stream_raw(self.buf)
        for first, body, raw in pkts:
            p = codec.decode(first, body, self.version)
            p["size"] = 1 + len(codec.varint(len(body))) + len(body)
            if p.get("type") == "PUBLISH" and p.get("qos"):
                self.last_pid = p.get("packet_id")
                if not self.auto_ack and not p.get("dup"):
                    if p.get("packet_id") in self.unacked:
                        self.reuse += 1
                    self.unacked.add(p.get("packet_id"))
            self.inbox.append(p)
            self.wire.append(raw.hex())
            if self.auto_ack and p.get("type") == "PUBLISH" and p.get("qos") in (1, 2):
                kind = "puback" if p["qos"] == 1 else "pubrec"
                self.send(codec.BUILDERS[kind]({"packet_id": p.get("packet_id", 0), "version": self.version}))
            elif self.auto_ack and p.get("type") == "PUBREL":
                self.send(codec.BUILDERS["pubcomp"]({"packet_id": p.get("packet_id", 0), "version": self.version}))


def run_script(host: str, port: int, steps: list[dict[str, Any]], *, topic_prefix: str, client_prefix: str,
               connect_timeout: float = 3.0) -> list[dict[str, Any]]:
    clients: dict[str, Client] = {}
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(steps, 1):
        st = _subst(raw, topic_prefix, client_prefix)
        name = st.get("client", "a")
        c = clients.setdefault(name, Client(name=name))
        if "auto_ack" in st:
            c.auto_ack = bool(st["auto_ack"])
        op = st.get("op", "hex")
        args = dict(st.get("args") or {})
        err: str | None = None
        was_closed = {n: k.closed for n, k in clients.items()}
        if op in ("open", "connect", "hex") and (c.sock is None or c.closed):
            try:
                c.sock = socket.create_connection((host, port), timeout=connect_timeout)
                c.sock.setblocking(False)
                c.closed, c.buf = False, b""
            except OSError as e:
                err = f"tcp-connect-failed: {type(e).__name__}"
        if err is None:
            if op == "connect":
                c.version = int(args.get("version", 5))
                props = args.get("properties") or {}
                c.max_size = int(props["maximum_packet_size"]) if "maximum_packet_size" in props else None
                c.alias_max = int(props.get("topic_alias_maximum", 0))
                err = c.send(codec.connect(args))
            elif op == "hex":
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
                for sub in args.get("packets") or []:
                    sa = dict(sub.get("args") or {})
                    sa.setdefault("version", c.version)
                    data += codec.BUILDERS[sub["op"]](sa)
                err = c.send(data)
            elif op in codec.BUILDERS:
                args.setdefault("version", c.version)
                if args.get("packet_id") == "last":
                    args["packet_id"] = c.last_pid if c.last_pid is not None else 0
                err = c.send(codec.BUILDERS[op](args))
            else:
                err = f"unknown-op {op}"
        start = {n: len(k.inbox) for n, k in clients.items()}
        last_before = {n: k.last_pid for n, k in clients.items()}
        reuse_before = {n: k.reuse for n, k in clients.items()}
        # A step that requires an answer listens until the acting client has one (or is closed), up to REPLY_MAX_MS,
        # then SETTLE_MS more for packets that follow it; other steps listen for their window. Brokers differ in
        # latency (a JVM broker under load answers its first CONNECTs after 600 ms), and a reply that arrives one
        # step late would otherwise be recorded as silence.
        t0 = time.monotonic()
        # With an explicit window the step still waits for its reply, and the window then runs from the reply
        # (a CONNACK after 600 ms, then the queued messages of a resumed session; campaign 79cb3e7: HiveMQ CE).
        need = err is None and expects_reply(st)
        win = st.get("window_ms")
        deadline = t0 + (max(REPLY_MAX_MS, win or 0) if need else (win or DEFAULT_WINDOW_MS)) / 1000.0
        settled = False
        while True:
            if need and not settled and (len(c.inbox) > start.get(name, 0) or c.closed):
                after = (win if win is not None else SETTLE_MS) / 1000.0
                deadline = time.monotonic() + after if win is not None else min(deadline, time.monotonic() + after)
                settled = True
            left = deadline - time.monotonic()
            if left <= 0:
                break
            socks = [k.sock for k in clients.values() if k.sock is not None and not k.closed]
            if not socks:
                time.sleep(min(left, 0.05))
                continue
            ready, _, _ = select.select(socks, [], [], min(left, 0.1))
            for k in clients.values():
                if k.sock in ready:
                    k.pump()
        obs = {"step": i, "actor": name, "op": op, "end": bool(st.get("end")), "error": err,
               "expects_reply": expects_reply(st), "clients": {}}
        for n, k in clients.items():
            got = k.inbox[start.get(n, 0):]
            obs["clients"][n] = {
                "packets": got,
                "wire": k.wire[start.get(n, 0):],
                "last_pid_before": last_before.get(n),
                "oversize": sum(1 for p in got if k.max_size is not None and p.get("size", 0) > k.max_size),
                "pid_reuse": k.reuse - reuse_before.get(n, 0),
                "alias_over": sum(1 for p in got if p.get("type") == "PUBLISH"
                                  and int((p.get("properties") or {}).get("topic_alias", 0)) > k.alias_max),
                "closed_now": k.closed and not was_closed.get(n, False) and not (n == name and op == "close"),
                "open": k.sock is not None and not k.closed,
            }
        out.append(obs)
    for k in clients.values():
        if k.sock is not None and not k.closed:
            try:
                k.sock.close()
            except OSError:
                pass
    return out

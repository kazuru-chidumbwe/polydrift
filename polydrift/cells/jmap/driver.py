"""JMAP cell driver: one fixture against one server over its JMAP API (RFC 8620), raw step observations out.

The session resource gives apiUrl and the primary mail account; a server may advertise its own host and port, so the
API URL is rewritten to the published host port. Steps:

* ``{op: call, using: [...], calls: [[method, args, callId], ...], headers: {...}}`` posts a Request object;
* ``{op: raw, body: "...", content_type: "..."}`` posts exact bytes (malformed requests);
* ``{op: session}`` fetches the session resource again; ``{op: unauth}`` posts a call without credentials;
* ``{op: upload, data: "...", content_type: "..."}`` posts bytes to the session's uploadUrl (RFC 8620 6.1);
* ``{op: download, blob: "{blob:k}", name: "...", type: "..."}`` fetches downloadUrl (6.2);
* ``{op: eventsource, types: "*", closeafter: "state", ping: 0, seconds: 5, during: [calls]}`` opens the
  eventSourceUrl, optionally posts ``during`` calls once it is open, and records the events (7.3).

Placeholders in strings: ``{acct}`` mail account id, ``{U}`` a name unique to the fixture and repeat, ``{id:k}`` the
server id of creation key ``k`` from an earlier /set response in the same fixture, ``{blob:k}`` the blobId of upload
step ``k`` (its ``key``), ``{state:k}`` the state string of the first response of step ``k`` (its ``key``).

``extract: {x1: expr, ...}`` on a step names fixture-specific observations, evaluated here (where the placeholders are
known) and returned as ``extract``; the adapter maps them to the generic axes x1 to x8. An expression is a path or
``fn:path`` / ``fn:path=literal``. Paths are ``/``-separated from a root: ``rN`` (arguments of method response N),
``mN`` (its name), ``body``, ``session``, ``http``, ``ctype``, ``hdr`` (response headers, lower-case names),
``events``. Functions: ``len``, ``keys``, ``type``, ``has``, ``eq``, ``ne``, ``in`` (literal in value), ``re``
(regex match), ``allre`` (every element matches), ``sorted``, ``lower``, ``ge`` (number >= literal), ``nodate`` (the
value with every RFC 3339 date-time string replaced by ``<date-time>``, for values that carry the server's clock).
"""

from __future__ import annotations

import base64
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

CORE = "urn:ietf:params:jmap:core"
MAIL = "urn:ietf:params:jmap:mail"


def _subst(v: Any, env: dict[str, str]) -> Any:
    if isinstance(v, str):
        def rep(m: re.Match) -> str:
            return env.get(m.group(1), m.group(0))
        return re.sub(r"\{([a-zA-Z]+(?::[\w-]+)?)\}", rep, v)
    if isinstance(v, list):
        return [_subst(x, env) for x in v]
    if isinstance(v, dict):
        return {_subst(k, env): _subst(x, env) for k, x in v.items()}
    return v


# The exact bytes of the last HTTP response body, kept with each step for consumer replay (manuscript 3.6).
LAST_RAW: dict[str, bytes | None] = {"body": None}


def _req(url: str, body: bytes | None, ctype: str | None, auth: str | None, method: str = "POST",
         headers: dict[str, str] | None = None) -> tuple[int, str, str, dict[str, str]]:
    r = urllib.request.Request(url, data=body, method=method)
    if ctype:
        r.add_header("Content-Type", ctype)
    r.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    if auth:
        r.add_header("Authorization", auth)
    LAST_RAW["body"] = None
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            data = resp.read()
            LAST_RAW["body"] = data
            return (resp.status, resp.headers.get("Content-Type", ""), data.decode("utf-8", "replace"),
                    {k.lower(): v for k, v in resp.headers.items()})
    except urllib.error.HTTPError as e:
        data = e.read()
        LAST_RAW["body"] = data
        return (e.code, e.headers.get("Content-Type", "") if e.headers else "", data.decode("utf-8", "replace"),
                {k.lower(): v for k, v in (e.headers.items() if e.headers else [])})


def _post(url: str, body: bytes, ctype: str, auth: str | None) -> tuple[int, str, str]:
    code, ct, text, _h = _req(url, body, ctype, auth)
    return code, ct, text


def _template(t: str, values: dict[str, str]) -> str:
    """RFC 6570 level 1 expansion, plus the {?a,b} query form some servers use."""
    def rep(m: re.Match) -> str:
        inner = m.group(1)
        if inner.startswith("?"):
            names = inner[1:].split(",")
            return "?" + "&".join(f"{n}={urllib.parse.quote(values.get(n, ''), safe='')}" for n in names)
        return urllib.parse.quote(values.get(inner, ""), safe="")
    return re.sub(r"\{([^}]+)\}", rep, t)


def _local(url: str, host: urllib.parse.SplitResult) -> str:
    u = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((host.scheme, host.netloc, u.path, u.query, ""))


# ---------------- extraction ----------------

def _walk(root: Any, parts: list[str]) -> Any:
    cur = root
    for n, p in enumerate(parts):
        if p == "*":
            # wildcard: the rest of the path over every element (list) or value (object), missing ones dropped
            items = cur if isinstance(cur, list) else (list(cur.values()) if isinstance(cur, dict) else [])
            vals = [_walk(x, parts[n + 1:]) for x in items]
            return [v for v in vals if v is not _MISSING]
        # an index past the end, a key that is absent, or a step through a scalar or null is a missing path (so
        # has: is false there, e.g. has:r0/created/p when a server answers "created": null)
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
        if cur is _MISSING:
            return cur
    return cur


_MISSING = object()


_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", re.I)


def _nodate(v: Any) -> Any:
    """The value with every RFC 3339 date-time string replaced by "<date-time>" (keys and other values kept)."""
    if isinstance(v, str):
        return "<date-time>" if _DATETIME.fullmatch(v) else v
    if isinstance(v, list):
        return [_nodate(x) for x in v]
    if isinstance(v, dict):
        return {k: _nodate(x) for k, x in v.items()}
    return v


def _path(ctx: dict[str, Any], path: str) -> Any:
    parts = [p for p in path.split("/") if p != ""]
    if not parts:
        return None
    head, rest = parts[0], parts[1:]
    m = re.fullmatch(r"([rmc])(\d+)", head)
    if m:
        resps = (ctx.get("body") or {}).get("methodResponses") if isinstance(ctx.get("body"), dict) else None
        if not isinstance(resps, list) or int(m.group(2)) >= len(resps):
            return _MISSING
        r = resps[int(m.group(2))]
        base = {"r": r[1], "m": r[0], "c": r[2]}[m.group(1)] if isinstance(r, list) and len(r) == 3 else None
        return _walk(base, rest) if rest else base
    if head in ctx:
        return _walk(ctx[head], rest) if rest else ctx[head]
    return _MISSING


FN = r"^(len|keys|type|has|eq|ne|in|re|allre|allin|sorted|lower|ge|hasall|nullorge|lensum|sortedby|allvals|count|nodate|hoursahead):"
# The lab push sink's control port (runner.PUSH_IP, lab/push/sink.py)
PUSH_CONTROL = "http://93.184.215.14:8080"


def evaluate(expr: str, ctx: dict[str, Any]) -> Any:
    fn, _, body = expr.partition(":") if re.match(FN, expr) else ("", "", expr)
    if not fn:
        v = _path(ctx, expr)
        return None if v is _MISSING else v
    path, _, lit = body.partition("=")
    if fn == "lensum":
        # lensum:p1,p2,p3: total length of the lists at the given paths (missing counts 0)
        tot = 0
        for pth in path.split(","):
            x = _path(ctx, pth)
            tot += len(x) if isinstance(x, (list, dict)) else 0
        return tot
    v = _path(ctx, path)
    missing = v is _MISSING
    v = None if missing else v
    if fn == "has":
        return not missing
    if fn == "len":
        return len(v) if isinstance(v, (list, dict, str)) else None
    if fn == "keys":
        return sorted(v) if isinstance(v, dict) else None
    if fn == "type":
        return "missing" if missing else {dict: "object", list: "array", str: "string", bool: "boolean",
                                          int: "number", float: "number", type(None): "null"}.get(type(v), "?")
    if fn == "eq":
        return v == _lit(lit)
    if fn == "ne":
        return v != _lit(lit)
    if fn == "in":
        return isinstance(v, (list, str, dict)) and _lit(lit) in v
    if fn == "re":
        return isinstance(v, str) and re.fullmatch(lit, v) is not None
    if fn == "allre":
        return isinstance(v, list) and all(isinstance(x, str) and re.fullmatch(lit, x) for x in v)
    if fn == "sorted":
        return sorted(v) if isinstance(v, list) else None
    if fn == "lower":
        return v.lower() if isinstance(v, str) else None
    if fn == "hasall":
        # every element of the JSON list literal is a key (object) or an element (list); null when missing
        if missing:
            return None
        pool = set(v) if isinstance(v, (dict, list)) else set()
        return all(x in pool for x in _lit(lit))
    if fn == "allin":
        # every non-null element is in the JSON list literal
        return isinstance(v, list) and all(x is None or x in _lit(lit) for x in v)
    if fn == "nullorge":
        return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and v >= float(lit))
    if fn == "sortedby":
        if not isinstance(v, list):
            return None
        keys = [x.get(lit) for x in v if isinstance(x, dict)]
        return keys == sorted(keys)
    if fn == "allvals":
        return isinstance(v, dict) and all(x == _lit(lit) for x in v.values())
    if fn == "count":
        return v.count(_lit(lit)) if isinstance(v, list) else None
    if fn == "hoursahead":
        # an RFC 3339 date-time against now, in the buckets RFC 8620 7.2 names for a maximum expiry: "<48", "48-168",
        # ">=168" hours ahead; "missing" when absent or not a date-time
        if not isinstance(v, str) or not _DATETIME.fullmatch(v):
            return "missing"
        import calendar
        h = (calendar.timegm(time.strptime(v[:19], "%Y-%m-%dT%H:%M:%S")) - time.time()) / 3600
        return "<48" if h < 48 else "48-168" if h < 168 else ">=168"
    if fn == "nodate":
        return _nodate(v)
    if fn == "ge":
        return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= float(lit)
    return None


def _lit(s: str) -> Any:
    try:
        return json.loads(s)
    except ValueError:
        return s


# ---------------- EventSource ----------------

def _eventsource(url: str, auth: str, seconds: float, during, out: dict) -> None:
    u = urllib.parse.urlsplit(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    events: list[dict[str, Any]] = []
    status = None
    try:
        with socket.create_connection((u.hostname, port), timeout=seconds + 5) as s:
            path = u.path + ("?" + u.query if u.query else "")
            s.sendall((f"GET {path} HTTP/1.1\r\nHost: {u.netloc}\r\nAuthorization: {auth}\r\n"
                       "Accept: text/event-stream\r\nConnection: close\r\n\r\n").encode())
            s.settimeout(0.5)
            buf, t0, fired, head_done = b"", time.time(), False, False
            while time.time() - t0 < seconds:
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    chunk = b""
                if chunk:
                    buf += chunk
                elif chunk == b"" and head_done and status is not None and not fired:
                    pass
                if not head_done and b"\r\n\r\n" in buf:
                    head, buf = buf.split(b"\r\n\r\n", 1)
                    status = int(head.split(b" ", 2)[1])
                    head_done = True
                if head_done and not fired and during is not None:
                    fired = True
                    during()
                while head_done and b"\n\n" in buf.replace(b"\r\n", b"\n"):
                    norm = buf.replace(b"\r\n", b"\n")
                    block, rest = norm.split(b"\n\n", 1)
                    buf = rest
                    ev = {"event": "message", "data": "", "id": None}
                    for line in block.decode("utf-8", "replace").split("\n"):
                        line = re.sub(r"^[0-9a-fA-F]+$", "", line)  # chunk-size lines of chunked encoding
                        if line.startswith("event:"):
                            ev["event"] = line[6:].strip()
                        elif line.startswith("data:"):
                            ev["data"] += line[5:].strip()
                        elif line.startswith("id:"):
                            ev["id"] = line[3:].strip()
                    if ev["data"] or ev["event"] != "message":
                        try:
                            ev["data"] = json.loads(ev["data"]) if ev["data"] else None
                        except ValueError:
                            pass
                        events.append(ev)
                if not chunk and head_done and time.time() - t0 > seconds:
                    break
    except OSError as e:
        out["error_es"] = f"{type(e).__name__}"
    out["es_status"] = status
    out["events"] = events


def _unu(v: Any, unique: str) -> Any:
    """Map the fixture's unique name back to "{U}" so extracted values compare across repeats and servers."""
    if isinstance(v, str):
        return v.replace(unique, "{U}")
    if isinstance(v, list):
        return [_unu(x, unique) for x in v]
    if isinstance(v, dict):
        return {_unu(k, unique): _unu(x, unique) for k, x in v.items()}
    return v


def run_script(base: str, user: str, password: str, session: dict[str, Any], steps: list[dict[str, Any]],
               unique: str) -> list[dict[str, Any]]:
    auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    api = urllib.parse.urlsplit(session.get("apiUrl", "/jmap/"))
    host = urllib.parse.urlsplit(base)
    api_url = urllib.parse.urlunsplit((host.scheme, host.netloc, api.path or "/jmap/", api.query, ""))
    acct = (session.get("primaryAccounts") or {}).get(MAIL) or next(iter(session.get("accounts", {})), "")
    env: dict[str, str] = {"acct": acct, "U": unique}
    out = []
    for i, raw in enumerate(steps, 1):
        # {in:N}: the UTC date-time N seconds from now (a push subscription expiry set by the fixture)
        for n in re.findall(r"\{in:(\d+)\}", json.dumps(raw)):
            env[f"in:{n}"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + int(n)))
        st = _subst(raw, env)
        op = st.get("op", "call")
        obs: dict[str, Any] = {"step": i, "op": op, "error": None}
        headers: dict[str, str] = {}
        try:
            if op in ("call", "unauth"):
                body = json.dumps({"using": st.get("using", [CORE, MAIL]), "methodCalls": st.get("calls", []),
                                   **({"createdIds": st["createdIds"]} if "createdIds" in st else {})}).encode()
                code, ctype, text, headers = _req(api_url, body, "application/json",
                                                  None if op == "unauth" else auth, headers=st.get("headers"))
            elif op == "raw":
                code, ctype, text, headers = _req(api_url, st["body"].encode(), st.get("content_type", "application/json"),
                                                  auth)
            elif op == "session":
                code, ctype, text, headers = _req(urllib.parse.urlunsplit((host.scheme, host.netloc, "/.well-known/jmap",
                                                                           "", "")), None, None,
                                                  None if st.get("noauth") else auth, method="GET")
            elif op == "upload":
                url = _local(_template(session.get("uploadUrl", ""), {"accountId": acct}), host)
                data = st.get("data", "").encode("utf-8")
                code, ctype, text, headers = _req(url, data, st.get("content_type", "application/octet-stream"),
                                                  None if st.get("noauth") else auth)
            elif op == "download":
                url = _local(_template(session.get("downloadUrl", ""), {"accountId": acct, "blobId": st.get("blob", ""),
                                                                         "name": st.get("name", "f.bin"),
                                                                         "type": st.get("type", "application/octet-stream")}),
                             host)
                code, ctype, text, headers = _req(url, None, None, None if st.get("noauth") else auth, method="GET")
            elif op == "eventsource":
                url = _local(_template(session.get("eventSourceUrl", ""),
                                       {"types": st.get("types", "*"), "closeafter": st.get("closeafter", "no"),
                                        "ping": str(st.get("ping", 0))}), host)
                es: dict[str, Any] = {}
                calls = st.get("during")
                fire = (lambda: _req(api_url, json.dumps({"using": [CORE, MAIL], "methodCalls": calls}).encode(),
                                     "application/json", auth)) if calls else None
                _eventsource(url, auth, float(st.get("seconds", 5)), fire, es)
                code, ctype, text = es.get("es_status") or 0, "text/event-stream", ""
                obs["events"] = es.get("events", [])
                obs["es_error"] = es.get("error_es")
            elif op in ("pushlog", "pushmode"):
                # the push sink's records for this fixture (path holds {U} and the step's `sub`), or a status the sink
                # answers the next `count` pushes with (RFC 8620 7.2: 429 must make the server slow down)
                key = f"{unique}-{st.get('sub', 'p')}"
                q = {"key": key} if op == "pushlog" else {"key": key, "code": st.get("code", 429), "count": st.get("count", 1)}
                path = "/log?" if op == "pushlog" else "/mode?"
                code, ctype, text, headers = _req(PUSH_CONTROL + path + urllib.parse.urlencode(q), None, None, None,
                                                  method="GET")
                if op == "pushlog":
                    try:
                        recs = json.loads(text)
                    except ValueError:
                        recs = []
                    for rec in recs:
                        jv = rec.get("json") if isinstance(rec, dict) else None
                        if isinstance(jv, dict) and jv.get("@type") == "PushVerification":
                            env[f"push:{st.get('sub', 'p')}"] = str(jv.get("verificationCode", ""))
                            break
            else:
                raise ValueError(f"unknown op {op}")
            obs.update({"http": code, "content_type": ctype.split(";")[0].strip().lower()})
            if op != "eventsource" and LAST_RAW["body"] is not None:
                obs["wire"] = base64.b64encode(LAST_RAW["body"]).decode()
                obs["wire_ctype"] = ctype
            try:
                obs["json"] = json.loads(text) if text.strip() else None
            except ValueError:
                obs["json"] = None
                obs["text"] = text[:300]
            j = obs.get("json")
            for resp in j.get("methodResponses", []) if isinstance(j, dict) else []:
                if isinstance(resp, list) and len(resp) == 3 and isinstance(resp[1], dict):
                    created = resp[1].get("created")
                    # /set and /import map creation ids to objects; /changes lists created ids
                    for k, v in (created.items() if isinstance(created, dict) else []):
                        if isinstance(v, dict) and "id" in v:
                            env[f"id:{k}"] = v["id"]
                        if isinstance(v, dict) and "blobId" in v:
                            env[f"blob:{k}"] = v["blobId"]
            if st.get("key"):
                if op == "upload" and isinstance(j, dict) and "blobId" in j:
                    env[f"blob:{st['key']}"] = j["blobId"]
                first = (j.get("methodResponses") or [[None, {}]])[0] if isinstance(j, dict) else [None, {}]
                if isinstance(first, list) and len(first) >= 2 and isinstance(first[1], dict):
                    for k in ("state", "newState", "queryState", "newQueryState"):
                        if k in first[1]:
                            env[f"state:{st['key']}"] = first[1][k]
                            break
            if st.get("extract"):
                ctx = {"body": j, "session": session, "http": code, "ctype": obs["content_type"], "hdr": headers,
                       "events": obs.get("events")}
                obs["extract"] = {k: _unu(evaluate(e, ctx), unique) for k, e in st["extract"].items()}
        except (OSError, ValueError) as e:
            obs["error"] = f"{type(e).__name__}: {e}"
        out.append(obs)
        if st.get("settle_ms"):
            # a server may index or deliver asynchronously (RFC 8620 does not require a write to be visible to the
            # next query at once); the fixture waits so it measures the result, not the race
            time.sleep(float(st["settle_ms"]) / 1000)
    return out

"""JMAP cell driver: one fixture against one server over its JMAP API (RFC 8620), raw step observations out.

The session resource gives apiUrl and the primary mail account; a server may advertise its own host and port, so the
API URL is rewritten to the published host port. Steps:

* ``{op: call, using: [...], calls: [[method, args, callId], ...]}`` posts a Request object;
* ``{op: raw, body: "...", content_type: "..."}`` posts exact bytes (malformed requests);
* ``{op: session}`` fetches the session resource again; ``{op: unauth}`` posts a call without credentials.

Placeholders in strings: ``{acct}`` mail account id, ``{U}`` a name unique to the fixture and repeat, ``{id:k}`` the
server id of creation key ``k`` from an earlier /set response in the same fixture.
"""

from __future__ import annotations

import base64
import json
import re
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


def _post(url: str, body: bytes, ctype: str, auth: str | None) -> tuple[int, str, str]:
    r = urllib.request.Request(url, data=body, method="POST")
    r.add_header("Content-Type", ctype)
    r.add_header("Accept", "application/json")
    if auth:
        r.add_header("Authorization", auth)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", e.read().decode("utf-8", "replace")


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
        st = _subst(raw, env)
        op = st.get("op", "call")
        obs: dict[str, Any] = {"step": i, "op": op, "error": None}
        try:
            if op in ("call", "unauth"):
                body = json.dumps({"using": st.get("using", [CORE, MAIL]),
                                   "methodCalls": st.get("calls", [])}).encode()
                code, ctype, text = _post(api_url, body, "application/json", None if op == "unauth" else auth)
            elif op == "raw":
                code, ctype, text = _post(api_url, st["body"].encode(), st.get("content_type", "application/json"), auth)
            elif op == "session":
                r = urllib.request.Request(urllib.parse.urlunsplit((host.scheme, host.netloc, "/.well-known/jmap", "", "")))
                r.add_header("Authorization", auth)
                try:
                    with urllib.request.urlopen(r, timeout=15) as resp:
                        code, ctype, text = resp.status, resp.headers.get("Content-Type", ""), resp.read().decode()
                except urllib.error.HTTPError as e:
                    code, ctype, text = e.code, "", e.read().decode("utf-8", "replace")
            else:
                raise ValueError(f"unknown op {op}")
            obs.update({"http": code, "content_type": ctype.split(";")[0].strip().lower()})
            try:
                obs["json"] = json.loads(text) if text.strip() else None
            except ValueError:
                obs["json"] = None
                obs["text"] = text[:300]
            for resp in (obs.get("json") or {}).get("methodResponses", []) if isinstance(obs.get("json"), dict) else []:
                if isinstance(resp, list) and len(resp) == 3 and isinstance(resp[1], dict):
                    for k, v in (resp[1].get("created") or {}).items():
                        if isinstance(v, dict) and "id" in v:
                            env[f"id:{k}"] = v["id"]
        except (OSError, ValueError) as e:
            obs["error"] = f"{type(e).__name__}: {e}"
        out.append(obs)
    return out

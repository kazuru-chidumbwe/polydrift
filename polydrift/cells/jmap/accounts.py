"""JMAP cell: create the test account on a freshly started server, then confirm an authenticated JMAP session.

Each server is provisioned with nothing but its image defaults (provisioning recipes in the lab folder); the account
the fixtures use is created here, after every clean restart, through the server's own administration path:

* Stalwart: the first-run temporary administrator printed in the container log, then the management API
  (POST /api/principal: domain lab.test, individual pd with an e-mail address).
* James (memory): the web administration API on port 8000 (PUT /domains/lab.test, PUT /users/pd@lab.test), with the
  WebAdmin password the image generates per run and writes to its log.
* Cyrus: saslpasswd2 in the container (sasl_pwcheck_method auxprop); the mailbox is created on first login, since
  the Debian imapd.conf sets autocreate_quota: 0.

``ensure(target, port)`` returns (username, password, session_json) or raises with the reason.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import time
import urllib.error
import urllib.request

USER, DOMAIN, PASSWORD = "pd", "lab.test", "Pd-Lab-Pass-2026"


def _req(method: str, url: str, user: str | None = None, pw: str | None = None, body: object | None = None,
         timeout: float = 10) -> tuple[int, str]:
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    if user is not None:
        r.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode())
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def session(base: str, user: str, pw: str) -> tuple[int, str]:
    return _req("GET", base + "/.well-known/jmap", user, pw)


def stalwart(container: str, base: str) -> tuple[str, str]:
    logs = subprocess.run(["docker", "logs", container], capture_output=True, text=True).stdout + \
        subprocess.run(["docker", "logs", container], capture_output=True, text=True).stderr
    m = re.search(r"password:\s*(\S+)", logs) or re.search(r"with password '([^']+)'", logs)
    if not m:
        raise RuntimeError("stalwart: no administrator password in the log")
    apw = m.group(1)
    out = []
    for body in ({"type": "domain", "name": DOMAIN},
                 {"type": "individual", "name": USER, "secrets": [PASSWORD], "emails": [f"{USER}@{DOMAIN}"],
                  "roles": ["user"]}):
        out.append(_req("POST", base + "/api/principal", "admin", apw, body))
    return f"{USER}", ";".join(f"{c}:{t[:80]}" for c, t in out)


def _req_h(method: str, url: str, headers: dict, body: object | None = None) -> tuple[int, str]:
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def james(container: str, base: str, admin_base: str) -> tuple[str, str]:
    # the memory image generates a WebAdmin password per run and logs it; requests carry it in a Password header
    logs = subprocess.run(["docker", "logs", container], capture_output=True, text=True)
    m = re.search(r"Generated WebAdmin password: (\S+)", logs.stdout + logs.stderr)
    h = {"Password": m.group(1)} if m else {}
    a = _req_h("PUT", f"{admin_base}/domains/{DOMAIN}", h)
    b = _req_h("PUT", f"{admin_base}/users/{USER}@{DOMAIN}", h, body={"password": PASSWORD})
    return f"{USER}@{DOMAIN}", f"{a[0]}:{a[1][:60]};{b[0]}:{b[1][:60]}"


def cyrus(container: str, base: str) -> tuple[str, str]:
    r = subprocess.run(["docker", "exec", "-i", container, "sh", "-c",
                        f"echo '{PASSWORD}' | saslpasswd2 -p -c {USER} && chown cyrus:mail /etc/sasldb2 && echo ok"],
                       capture_output=True, text=True)
    return USER, (r.stdout + r.stderr).strip()[-120:]


def ensure(impl: str, container: str, base: str, admin_base: str = "") -> tuple[str, str, dict, str]:
    if impl == "stalwart":
        user, note = stalwart(container, base)
    elif impl == "james":
        user, note = james(container, base, admin_base)
    elif impl == "cyrus":
        user, note = cyrus(container, base)
    else:
        raise ValueError(impl)
    for _ in range(10):
        code, text = session(base, user, PASSWORD)
        if code == 200:
            return user, PASSWORD, json.loads(text), note
        time.sleep(2)
    raise RuntimeError(f"{impl}: session {code} {text[:120]} after account step: {note}")

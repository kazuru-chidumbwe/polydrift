"""JMAP cell runner: fixtures x repeats x servers -> observation records (manuscript 4.5).

Per repeat: a fresh container from the pinned image (clean restart), wait until HTTP answers on the JMAP port, create
the test account (accounts.py), canary Core/echo, every fixture, canary again, container log kept, container removed.
A repeat whose canary fails is redone once; the failed attempt is kept. Records with a failed canary are harness
faults.

Usage (Testbed B, detached):
  python -m polydrift.cells.jmap.runner --targets jmap_targets.json --out DIR [--repeats 3] [--only key,...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import yaml

from polydrift.cells.jmap import accounts, adapter, driver
from polydrift.cells.sip.runner import harness_commit
from polydrift.core.fixset import fixtures_file
from polydrift.core.record import ObservationRecord, Status, StepRecord

CELL_DIR = Path(__file__).resolve().parent
CANARY = [{"op": "call", "using": [driver.CORE], "calls": [["Core/echo", {"pd": 1}, "c0"]]}]
# Account warm-up, once after the account exists and before the canary: James creates its default mailboxes on the
# first Mailbox/get, so a Mailbox/query sent first finds none (smoke 10 Nov 17:42). Every server gets the same call.
WARMUP = [{"op": "call", "calls": [["Mailbox/get", {"accountId": "{acct}", "ids": None, "properties": ["role"]}, "c0"]]}]

# Push sink (RFC 8620 7.2, lab/push): the servers deliver PushVerification and StateChange objects to
# https://pd-push.example.com/, which every server container resolves through its hosts file to the sink on an
# internal network with no route out. example.com is the IANA documentation domain, under a public suffix, and
# 93.184.215.14 is outside every reserved range, so the servers' own outbound guards accept the URL as they would a
# public endpoint; the guard itself is measured separately (census_push_ssrf). The sink's certificate comes from a lab
# CA that only these containers trust (bundle.pem as the system bundle, trust.p12 for the JVM).
PUSH_NET, PUSH_SUBNET, PUSH_IP, PUSH_HOST = "pd-jmap-push", "93.184.215.8/29", "93.184.215.14", "pd-push.example.com"
PUSH_IMAGE, PUSH_SINK = "pd-jmap-push:lab", "pd-jmap-push-sink"
PUSH_CONTROL = f"http://{PUSH_IP}:8080"


def push_up(pki: Path) -> str:
    """Start the sink once per run; returns "" or the reason it is not available (push fixtures then record it)."""
    pki.mkdir(parents=True, exist_ok=True)
    if sh(["docker", "network", "inspect", PUSH_NET]).returncode != 0:
        r = sh(["docker", "network", "create", "--internal", "--subnet", PUSH_SUBNET, PUSH_NET])
        if r.returncode != 0:
            return f"push network: {r.stderr.strip()[-160:]}"
    sh(["docker", "rm", "-f", PUSH_SINK])
    r = sh(["docker", "run", "-d", "--name", PUSH_SINK, "--network", PUSH_NET, "--ip", PUSH_IP, "-v", f"{pki}:/pki",
            PUSH_IMAGE])
    if r.returncode != 0:
        return f"push sink: {r.stderr.strip()[-160:]}"
    for _ in range(60):
        if (pki / "bundle.pem").exists() and (pki / "trust.p12").exists():
            try:
                urllib.request.urlopen(PUSH_CONTROL + "/log?key=none", timeout=3)
                return ""
            except OSError:
                pass
        time.sleep(1)
    return "push sink: not ready after 60 s"


def push_args(pki: Path) -> list[str]:
    """docker run arguments that let a server container reach and trust the sink."""
    trust = ("-Djavax.net.ssl.trustStore=/pd/push/trust.p12 -Djavax.net.ssl.trustStorePassword=pdlab "
             "-Djavax.net.ssl.trustStoreType=PKCS12")
    return ["--add-host", f"{PUSH_HOST}:{PUSH_IP}", "-v", f"{pki}/bundle.pem:/etc/ssl/certs/ca-certificates.crt:ro",
            "-v", f"{pki}:/pd/push:ro", "-e", "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
            "-e", f"JAVA_TOOL_OPTIONS={trust}"]


def push_down() -> None:
    sh(["docker", "rm", "-f", PUSH_SINK])
    sh(["docker", "network", "rm", PUSH_NET])


def load_fixtures() -> dict:
    return yaml.safe_load(fixtures_file(CELL_DIR).read_text(encoding="utf-8"))["fixtures"]


def sh(a: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(a, capture_output=True, text=True, timeout=timeout)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def canary_ok(base: str, user: str, pw: str, sess: dict) -> bool:
    o = driver.run_script(base, user, pw, sess, CANARY, "canary")[0]
    j = o.get("json") or {}
    return o.get("http") == 200 and isinstance(j, dict) and bool(j.get("methodResponses")) \
        and j["methodResponses"][0][0] == "Core/echo"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--fixtures", default="")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def log(m: str) -> None:
        with (out / "progress").open("a") as f:
            f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {m}\n")

    targets = json.loads(Path(a.targets).read_text())
    pki = out / "push-pki"
    push_note = push_up(pki)
    log(f"push sink: {push_note or 'up at ' + PUSH_IP}")
    if a.only:
        targets = [t for t in targets if t["key"] in set(a.only.split(","))]
    fx = load_fixtures()
    if a.fixtures:
        fx = {k: v for k, v in fx.items() if k in set(a.fixtures.split(","))}
    spec = adapter.load_spec()
    commit = harness_commit() or os.environ.get("POLYDRIFT_COMMIT", "")
    log(f"start: {len(targets)} servers x {len(fx)} fixtures x {a.repeats} repeats, harness {commit}")
    for t in targets:
        for r in range(1, a.repeats + 1):
            for attempt in (1, 2):
                name = f"pdcell-jmap-{t['key']}"
                sh(["docker", "rm", "-f", name])
                port = free_port()
                args = ["docker", "run", "-d", "--name", name, "--cpus=4", "--memory=4g",
                        "-p", f"127.0.0.1:{port}:{t['port']}"]
                admin_base = ""
                if t.get("admin_port"):
                    ap2 = free_port()
                    args += ["-p", f"127.0.0.1:{ap2}:{t['admin_port']}"]
                    admin_base = f"http://127.0.0.1:{ap2}"
                if not push_note:
                    args += push_args(pki)
                    try:  # every server starts with an empty sink log, so no record outlives its server
                        urllib.request.urlopen(PUSH_CONTROL + "/clear", timeout=5)
                    except OSError as e:
                        log(f"{t['key']} repeat {r}: push sink clear failed: {e}")
                rr = sh(args + [*[str(x).replace("{celldir}", str(CELL_DIR)) for x in t.get("extra", [])], t["image"], *(t.get("cmd") or [])])
                if rr.returncode != 0:
                    log(f"{t['key']} repeat {r}: start failed {rr.stderr[-200:]}")
                    break
                if not push_note:
                    sh(["docker", "network", "connect", PUSH_NET, name])
                base = f"http://127.0.0.1:{port}"
                t0, up = time.time(), False
                while time.time() - t0 < t.get("ready", 180):
                    try:
                        urllib.request.urlopen(base + "/.well-known/jmap", timeout=3)
                        up = True
                    except urllib.error.HTTPError:
                        up = True
                    except OSError:
                        pass
                    if up:
                        break
                    time.sleep(3)
                time.sleep(int(t.get("settle", 5)))
                ok_start, note, user, pw, sess = False, "", "", "", {}
                try:
                    user, pw, sess, note = accounts.ensure(t["impl"], name, base, admin_base)
                    driver.run_script(base, user, pw, sess, WARMUP, "warmup")
                    ok_start = canary_ok(base, user, pw, sess)
                except Exception as e:  # noqa: BLE001
                    note = f"account: {e}"[:300]
                image_id = sh(["docker", "image", "inspect", "-f", "{{.Id}}", t["image"]]).stdout.strip()
                batch = []
                for fid, f in fx.items():
                    try:
                        obs = driver.run_script(base, user, pw, sess, f["steps"], "pd" + hashlib.sha1(fid.encode()).hexdigest()[:10]) if ok_start else []
                    except Exception as e:  # noqa: BLE001
                        obs = [{"step": 1, "op": "?", "error": f"driver: {e!r}"}]
                    batch.append((fid, obs))
                ok_end = ok_start and canary_ok(base, user, pw, sess)
                final = (ok_start and ok_end) or attempt == 2
                with (out / ("raw.jsonl" if final else "raw-failed.jsonl")).open("a") as rawf, \
                        (out / ("records.jsonl" if final else "records-failed.jsonl")).open("a") as recf:
                    for fid, obs in batch:
                        rawf.write(json.dumps({"impl": t["key"], "repeat": r, "fixture": fid, "image_id": image_id, "session": sess,
                                               "canary": [ok_start, ok_end], "account": note, "obs": obs}) + "\n")
                        if ok_start and ok_end and obs:
                            rec = adapter.to_record(obs, t["key"], fid, spec=spec, repeat=r,
                                                    impl_version=t.get("version", ""), harness_commit=commit)
                        else:
                            rec = ObservationRecord(cell="jmap", impl=t["key"], impl_version=t.get("version", ""),
                                                    fixture=fid, repeat=r, harness_commit=commit,
                                                    steps=(StepRecord(status=Status.HARNESS_FAULT,
                                                                      detail=f"canary failed; {note}"[:200]),))
                        recf.write(rec.to_json() + "\n")
                log(f"{t['key']} repeat {r} attempt {attempt}: canary start={ok_start} end={ok_end}, "
                    f"{len(batch)} fixtures, image {image_id[:19]}, account: {note[:80]}")
                lg = sh(["docker", "logs", "--tail", "400", name])
                (out / f"container-{t['key']}-r{r}-a{attempt}.log").write_text(lg.stdout + lg.stderr)
                sh(["docker", "rm", "-f", name])
                if final:
                    break
    push_down()
    log("DONE")
    (out / "DONE").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

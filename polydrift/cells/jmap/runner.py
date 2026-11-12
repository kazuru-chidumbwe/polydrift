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
from polydrift.core.record import ObservationRecord, Status, StepRecord

CELL_DIR = Path(__file__).resolve().parent
CANARY = [{"op": "call", "using": [driver.CORE], "calls": [["Core/echo", {"pd": 1}, "c0"]]}]
# Account warm-up, once after the account exists and before the canary: James creates its default mailboxes on the
# first Mailbox/get, so a Mailbox/query sent first finds none (smoke 10 Nov 17:42). Every server gets the same call.
WARMUP = [{"op": "call", "calls": [["Mailbox/get", {"accountId": "{acct}", "ids": None, "properties": ["role"]}, "c0"]]}]


def load_fixtures() -> dict:
    return yaml.safe_load((CELL_DIR / "fixtures.yaml").read_text(encoding="utf-8"))["fixtures"]


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
                rr = sh(args + [*t.get("extra", []), t["image"], *(t.get("cmd") or [])])
                if rr.returncode != 0:
                    log(f"{t['key']} repeat {r}: start failed {rr.stderr[-200:]}")
                    break
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
                        rawf.write(json.dumps({"impl": t["key"], "repeat": r, "fixture": fid, "image_id": image_id,
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
    log("DONE")
    (out / "DONE").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

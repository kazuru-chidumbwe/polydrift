"""LDAP cell runner: fixtures x repeats x servers -> observation records (manuscript 4.5). Same protocol as the
MQTT cell runner; per target the base DN and admin credentials fill {B}, {A}, {P}.

Every repeat starts a fresh container from the broker's pinned image (clean restart), waits until the canary
(simple bind as the target's admin, BindResponse success; not every server accepts anonymous binds) answers, runs every fixture, runs the canary again and removes the container. If either
canary fails, every record of that batch is marked harness-fault: the instrument, not the broker, is in doubt.

Usage (Testbed B, detached):
  python -m polydrift.cells.ldap.runner --targets targets.json --out DIR [--repeats 3] [--only impl,...]
Writes DIR/records.jsonl, DIR/raw.jsonl (driver observations), DIR/progress, DIR/DONE.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from polydrift.cells.ldap import adapter, driver
from polydrift.cells.sip.runner import harness_commit
from polydrift.core.fixset import fixtures_file
from polydrift.core.record import ObservationRecord, Status, StepRecord

CELL_DIR = Path(__file__).resolve().parent
FIXTURES_PATH = CELL_DIR / "fixtures.yaml"
CANARY = [{"client": "a", "op": "bind", "args": {"dn": "{A}", "password": "{P}"}},
          {"client": "a", "op": "unbind", "window_ms": 200}]


def load_fixtures() -> dict[str, dict[str, Any]]:
    return yaml.safe_load(fixtures_file(CELL_DIR).read_text(encoding="utf-8"))["fixtures"]


CURRENT: dict = {}


def canary(host: str, port: int, tag: str) -> bool:
    obs = driver.run_script(host, port, CANARY, {"A": CURRENT.get("admin", ""), "P": CURRENT.get("password", "")})
    pk = obs[0]["clients"]["a"]["messages"]
    return bool(pk) and pk[0].get("op") == "BindResponse" and pk[0].get("result") == 0


def sh(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(t: dict[str, Any], name: str) -> int:
    port = free_port()
    sh(["docker", "rm", "-f", name])
    args = ["docker", "run", "-d", "--name", name, "--cpus=4", "--memory=4g", "-p", f"127.0.0.1:{port}:{t['port']}",
            *[str(x).replace("{celldir}", str(CELL_DIR)) for x in t.get("extra", [])], t["image"], *(t.get("cmd") or [])]
    r = sh(args)
    if r.returncode != 0:
        raise RuntimeError(f"start failed: {r.stderr.strip()[-200:]}")
    return port


def wait_ready(port: int, seconds: int, tag: str) -> bool:
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            if canary("127.0.0.1", port, tag):
                return True
        except OSError:
            pass
        time.sleep(2)
    return False


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
    prog = out / "progress"

    def log(msg: str) -> None:
        with prog.open("a") as f:
            f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}\n")

    targets = json.loads(Path(a.targets).read_text())
    if a.only:
        keep = set(a.only.split(","))
        targets = [t for t in targets if t["key"] in keep]
    fixtures = load_fixtures()
    if a.fixtures:
        fixtures = {k: v for k, v in fixtures.items() if k in set(a.fixtures.split(","))}
    spec = adapter.load_spec()
    # a deployed copy has no .git: the deploy step passes the commit it was taken from
    commit = harness_commit() or os.environ.get("POLYDRIFT_COMMIT", "")
    log(f"start: {len(targets)} brokers x {len(fixtures)} fixtures x {a.repeats} repeats, harness {commit}")
    for t in targets:
        CURRENT.clear(); CURRENT.update(t)
        for r in range(1, a.repeats + 1):
            for attempt in (1, 2):   # a repeat whose canary fails is redone once; the failed attempt is kept
                name = f"pdcell-ldap-{t['key']}"
                try:
                    port = start(t, name)
                except Exception as e:  # noqa: BLE001
                    log(f"{t['key']} repeat {r}: {e}")
                    break
                ok_start = wait_ready(port, t.get("ready", 180), f"{t['key']}-r{r}")
                image_id = sh(["docker", "image", "inspect", "-f", "{{.Id}}", t["image"]]).stdout.strip()
                batch = []
                lacks = set(t.get("lacks") or ())
                for fid, fx in fixtures.items():
                    need = set(fx.get("requires") or ()) & lacks
                    if need:
                        # outside the subset the server documents (lldap: two fixed OUs, no aliases): not run, so
                        # never compared; written down once per target with the reason
                        if r == 1 and attempt == 1:
                            with (out / "skipped.jsonl").open("a") as sk:
                                sk.write(json.dumps({"impl": t["key"], "fixture": fid, "lacks": sorted(need)}) + "\n")
                        continue
                    tp = t.get("base", "")
                    # {U}: unique per fixture (each repeat starts a fresh container), the same in every repeat and target, so
                    # records stay comparable; the adapter maps it back to the literal "{U}"
                    env = {"B": tp, "A": t.get("admin", ""), "P": t.get("password", ""),
                           "U": "pd" + hashlib.sha1(fid.encode()).hexdigest()[:10]}
                    try:
                        obs = driver.run_script("127.0.0.1", port, fx["steps"], env)
                    except Exception as e:  # noqa: BLE001
                        obs = [{"step": 1, "actor": "a", "op": "?", "end": False, "error": f"driver: {e!r}", "clients": {}}]
                    batch.append((fid, tp, obs, set(fx.get("value_attrs", [])), env["U"]))
                ok_end = ok_start and canary("127.0.0.1", port, f"{t['key']}-r{r}-end")
                final = (ok_start and ok_end) or attempt == 2
                with (out / ("raw.jsonl" if final else "raw-failed.jsonl")).open("a") as rawf,                         (out / ("records.jsonl" if final else "records-failed.jsonl")).open("a") as recf:
                    for fid, tp, obs, ver, uniq in batch:
                        rawf.write(json.dumps({"impl": t["key"], "repeat": r, "fixture": fid, "image_id": image_id,
                                               "canary": [ok_start, ok_end], "obs": obs}) + "\n")
                        if ok_start and ok_end:
                            rec = adapter.to_record(obs, t["key"], fid, spec=spec, base=tp, value_attrs=ver, unique=uniq, repeat=r,
                                                    impl_version=t.get("version", ""), harness_commit=commit)
                        else:
                            rec = ObservationRecord(cell="ldap", impl=t["key"], impl_version=t.get("version", ""),
                                                    fixture=fid, repeat=r, harness_commit=commit,
                                                    steps=(StepRecord(status=Status.HARNESS_FAULT, detail="canary failed"),))
                        recf.write(rec.to_json() + "\n")
                log(f"{t['key']} repeat {r} attempt {attempt}: canary start={ok_start} end={ok_end}, {len(batch)} fixtures, image {image_id[:19]}")
                lg = sh(["docker", "logs", "--tail", "400", name])
                state = sh(["docker", "inspect", "-f", "{{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}", name]).stdout.strip()
                (out / f"container-{t['key']}-r{r}-a{attempt}.log").write_text(f"# {state}\n" + lg.stdout + lg.stderr)
                sh(["docker", "rm", "-f", name])
                if final:
                    break
    log("DONE")
    (out / "DONE").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

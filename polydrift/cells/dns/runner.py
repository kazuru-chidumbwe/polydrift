"""DNS cell runner, recursive role: fixtures x repeats x resolvers -> observation records (manuscript 4.5).

Every repeat starts a fresh lab: the five lab authorities (lab/zones.yaml), the client and the resolver under test,
all on the internal network pd-dns (172.31.53.0/24, no route out). The canary (ok.pd.pdlab A -> 10.0.0.1) must answer
before and after the fixtures; if either fails, every record of that batch is a harness fault and the repeat is
redone once (the failed attempt is kept). Containers are removed after each repeat, so no cache or authority state
carries over.

Usage (lab host, detached):
  python -m polydrift.cells.dns.runner --targets dns_targets.json --lab DIR --out DIR [--repeats 3] [--only key,...]
DIR for --lab is the host copy of cells/dns/lab (mounted read-only into the containers).
Writes OUT/records.jsonl, OUT/raw.jsonl, OUT/progress, container logs, OUT/DONE.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from polydrift.cells.dns import adapter
from polydrift.cells.sip.runner import harness_commit
from polydrift.core.fixset import fixtures_file
from polydrift.core.record import ObservationRecord, Status, StepRecord

CELL_DIR = Path(__file__).resolve().parent
FIXTURES_PATH = CELL_DIR / "fixtures.yaml"
NET, SUBNET = "pd-dns", "172.31.53.0/24"
RESOLVER_IP, CLIENT_IP, UPSTREAM_IP = "172.31.53.53", "172.31.53.99", "172.31.53.54"
LAB_IMAGE = "pd-dns-py:lab"
CANARY = [{"qname": "ok.pd.pdlab", "qtype": "A", "timeout": 3}]
CANARY_ANSWER = ["ok.pd.pdlab A 10.0.0.1"]


def load_fixtures() -> dict[str, dict[str, Any]]:
    return yaml.safe_load(fixtures_file(CELL_DIR).read_text(encoding="utf-8"))["fixtures"]


def sh(args: list[str], timeout: int = 300, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=stdin)


def drive(steps: list[dict]) -> list[dict]:
    budget = 30 + sum(float(s.get("timeout", 6)) + s.get("ms", 0) / 1000 for s in steps)
    r = sh(["docker", "exec", "-i", "pdns-client", "python3", "/lab/driver.py"], timeout=int(budget),
           stdin=json.dumps({"server": RESOLVER_IP, "steps": steps}))
    if r.returncode != 0:
        raise RuntimeError(f"driver exit {r.returncode}: {r.stderr.strip()[-200:]}")
    return json.loads(r.stdout)


def canary() -> bool:
    try:
        o = drive(CANARY)
    except Exception:  # noqa: BLE001
        return False
    return bool(o) and o[0].get("answers") == CANARY_ANSWER


def wait_canary(ready: float) -> bool:
    t0 = time.time()
    while time.time() - t0 < ready:
        if canary():
            return True
        time.sleep(2)
    return False


def running(name: str) -> bool:
    return sh(["docker", "inspect", "-f", "{{.State.Running}}", name]).stdout.strip() == "true"


def mark_crashed(obs: list[dict]) -> list[dict]:
    """Steps the resolver left unanswered because it exited (the adapter maps them to Status.CRASHED)."""
    return [{**o, "crashed": True} if o.get("error") or o.get("rcode") in (None, "") else o for o in obs]


def lab_up(lab: str, zones: dict) -> None:
    if sh(["docker", "network", "inspect", NET]).returncode != 0:
        sh(["docker", "network", "create", "--internal", "--subnet", SUBNET, NET])
    for name, cfg in zones["servers"].items():
        c = f"pdns-auth-{name}"
        sh(["docker", "rm", "-f", c])
        r = sh(["docker", "run", "-d", "--name", c, "--network", NET, "--ip", cfg["ip"], "-v", f"{lab}:/lab:ro",
                LAB_IMAGE, "python3", "/lab/auth.py", "--zones", "/lab/zones.yaml", "--server", name])
        if r.returncode != 0:
            raise RuntimeError(f"auth {name}: {r.stderr.strip()[-200:]}")
    sh(["docker", "rm", "-f", "pdns-client"])
    sh(["docker", "run", "-d", "--name", "pdns-client", "--network", NET, "--ip", CLIENT_IP, "-v", f"{lab}:/lab:ro",
        LAB_IMAGE, "sleep", "infinity"])


def lab_down(zones: dict) -> None:
    for name in zones["servers"]:
        sh(["docker", "rm", "-f", f"pdns-auth-{name}"])
    sh(["docker", "rm", "-f", "pdns-client", "pdns-res", "pdns-up"])


def start_resolver(t: dict[str, Any], lab: str, name: str = "pdns-res", ip: str = RESOLVER_IP) -> None:
    """Start the element under test at .53. Forwarding-role targets name an `upstream` (a recursive resolver
    target, same fields) that is started first at .54; the forwarder's config points at that address."""
    if name == "pdns-res" and t.get("upstream"):
        start_resolver(t["upstream"], lab, "pdns-up", UPSTREAM_IP)
    sh(["docker", "rm", "-f", name])
    args = ["docker", "run", "-d", "--name", name, "--network", NET, "--ip", ip, "--cpus=2",
            "--memory=2g", "-v", f"{lab}/resolvers:/conf:ro"]
    for f, dst in (t.get("mounts") or {}).items():
        args += ["-v", f"{lab}/resolvers/{f}:{dst}:ro"]
    if t.get("entrypoint"):
        args += ["--entrypoint", t["entrypoint"]]
    args += [t["image"], *(t.get("cmd") or [])]
    r = sh(args)
    if r.returncode != 0:
        raise RuntimeError(f"resolver start: {r.stderr.strip()[-200:]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", required=True)
    ap.add_argument("--lab", required=True)
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

    lab = str(Path(a.lab).resolve())
    zones = yaml.safe_load((Path(lab) / "zones.yaml").read_text(encoding="utf-8"))
    targets = json.loads(Path(a.targets).read_text())
    if a.only:
        targets = [t for t in targets if t["key"] in set(a.only.split(","))]
    fixtures = load_fixtures()
    if a.fixtures:
        fixtures = {k: v for k, v in fixtures.items() if k in set(a.fixtures.split(","))}
    spec = adapter.load_spec()
    commit = harness_commit() or os.environ.get("POLYDRIFT_COMMIT", "")
    lab_image = sh(["docker", "image", "inspect", "-f", "{{.Id}}", LAB_IMAGE]).stdout.strip()
    log(f"start: {len(targets)} resolvers x {len(fixtures)} fixtures x {a.repeats} repeats, harness {commit}, "
        f"lab image {lab_image[:19]}")
    for t in targets:
        for r in range(1, a.repeats + 1):
            for attempt in (1, 2):
                try:
                    lab_up(lab, zones)
                    start_resolver(t, lab)
                except Exception as e:  # noqa: BLE001
                    log(f"{t['key']} repeat {r}: {e}")
                    lab_down(zones)
                    break
                ok_start = wait_canary(t.get("ready", 90))
                image_id = sh(["docker", "image", "inspect", "-f", "{{.Id}}", t["image"]]).stdout.strip()
                batch = []
                alive = ok_start
                for fid, fx in fixtures.items():
                    try:
                        obs = drive(fx["steps"]) if alive else []
                    except Exception as e:  # noqa: BLE001
                        obs = [{"step": 1, "op": "?", "error": f"driver: {e!r}"[:200]}]
                    if alive and not running("pdns-res"):
                        # The element under test exited during this fixture: a behaviour (crashed), not an instrument
                        # failure. Its unanswered steps are marked, the container log is kept, and a fresh resolver
                        # is started for the remaining fixtures (the end canary still has to pass).
                        obs = mark_crashed(obs)
                        lg = sh(["docker", "logs", "--tail", "200", "pdns-res"])
                        (out / f"crash-{t['key']}-r{r}-a{attempt}-{fid}.log").write_text(lg.stdout + lg.stderr)
                        log(f"{t['key']} repeat {r}: resolver exited during {fid}; restarting")
                        try:
                            start_resolver(t, lab)
                            alive = wait_canary(t.get("ready", 90))
                        except Exception as e:  # noqa: BLE001
                            log(f"{t['key']} repeat {r}: restart after {fid}: {e}")
                            alive = False
                    batch.append((fid, obs))
                ok_end = ok_start and canary()
                final = (ok_start and ok_end) or attempt == 2
                with (out / ("raw.jsonl" if final else "raw-failed.jsonl")).open("a") as rawf, \
                        (out / ("records.jsonl" if final else "records-failed.jsonl")).open("a") as recf:
                    for fid, obs in batch:
                        rawf.write(json.dumps({"impl": t["key"], "repeat": r, "fixture": fid, "image_id": image_id,
                                               "canary": [ok_start, ok_end], "obs": obs}) + "\n")
                        if ok_start and ok_end:
                            rec = adapter.to_record_script(obs, t["key"], fid, spec=spec, repeat=r,
                                                           impl_version=t.get("version", ""), harness_commit=commit)
                        else:
                            rec = ObservationRecord(cell="dns", impl=t["key"], impl_version=t.get("version", ""),
                                                    fixture=fid, repeat=r, harness_commit=commit,
                                                    steps=(StepRecord(status=Status.HARNESS_FAULT, detail="canary failed"),))
                        recf.write(rec.to_json() + "\n")
                log(f"{t['key']} repeat {r} attempt {attempt}: canary start={ok_start} end={ok_end}, {len(batch)} "
                    f"fixtures, image {image_id[:19]}")
                lg = sh(["docker", "logs", "--tail", "400", "pdns-res"])
                state = sh(["docker", "inspect", "-f", "{{.State.Status}} exit={{.State.ExitCode}} "
                            "oom={{.State.OOMKilled}}", "pdns-res"]).stdout.strip()
                (out / f"container-{t['key']}-r{r}-a{attempt}.log").write_text(f"# {state}\n" + lg.stdout + lg.stderr)
                lab_down(zones)
                if final:
                    break
    log("DONE")
    (out / "DONE").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

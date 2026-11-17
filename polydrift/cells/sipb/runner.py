"""SIP behaviour cell runner (sipb): fixtures x repeats x elements -> observation records (manuscript 4.5).

Every repeat starts a fresh lab on the internal network pd-sipb (172.31.60.0/24, no route out): the element under
test (EUT) at .10 with its role configuration from lab/elements, and the lab client at .99 that plays alice, bob,
carol and dave (lab/driver.py). The canary (OPTIONS to the EUT's domain; any response) must answer before and after
the fixtures; if either fails, every record of that batch is a harness fault and the repeat is redone once (the
failed attempt is kept). Containers are removed after each repeat, so no registration or transaction state carries
over between repeats.

Target fields: key, impl, version, role (proxy | registrar | uas | uac), image, config (file under lab/elements),
config_dst (mount point), cmd (optional), ready (seconds).

Usage (lab host, detached):
  python -m polydrift.cells.sipb.runner --targets sipb_targets.json --lab DIR --out DIR [--repeats 3] [--only k,..]
Writes OUT/records.jsonl, OUT/raw.jsonl, OUT/progress, container logs, OUT/DONE.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from polydrift.cells.sip.runner import harness_commit
from polydrift.cells.sipb import adapter
from polydrift.core.fixset import fixtures_file
from polydrift.core.record import ObservationRecord, Status, StepRecord

CELL_DIR = Path(__file__).resolve().parent
FIXTURES_PATH = CELL_DIR / "fixtures.yaml"
NET, SUBNET = "pd-sipb", "172.31.60.0/24"
EUT_IP, CLIENT_IP = "172.31.60.10", "172.31.60.99"
LAB_IMAGE = "pd-sipb-py:lab"
CANARY = [{"op": "send", "window_ms": 1500, "msg": "OPTIONS sip:{domain} SIP/2.0\nVia: SIP/2.0/UDP {lab}:{port:alice};"
           "branch={branch};rport\nMax-Forwards: 70\nFrom: <sip:alice@{domain}>;tag={tag:c}\nTo: <sip:{domain}>\n"
           "Call-ID: {callid}\nCSeq: 1 OPTIONS\nContent-Length: {clen}\n\n", "extract": {"x1": "codes:alice"}}]


def load_fixtures() -> dict[str, dict[str, Any]]:
    return yaml.safe_load(fixtures_file(CELL_DIR).read_text(encoding="utf-8"))["fixtures"]


def sh(args: list[str], timeout: int = 300, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=stdin)


def drive(steps: list[dict], unique: str, domain: str, creds: dict | None = None,
          ctl: dict | None = None) -> list[dict]:
    # with credentials a challenged send listens a second window after the authenticated resend
    k = 2 if creds else 1
    budget = 30 + sum(k * st.get("window_ms", 1500) / 1000 + st.get("ms", 0) / 1000 for st in steps)
    try:
        r = sh(["docker", "exec", "-i", "psipb-client", "python3", "/lab/driver.py"], timeout=int(budget),
               stdin=json.dumps({"eut": EUT_IP, "eut_port": 5060, "lab": CLIENT_IP, "domain": domain,
                                 "unique": unique, "creds": creds or {}, "ctl": ctl or {}, "steps": steps}))
    except subprocess.TimeoutExpired:
        # the exec'd driver keeps running inside the client and holds the party ports; stop it before going on
        sh(["docker", "exec", "psipb-client", "pkill", "-f", "/lab/driver.py"], timeout=30)
        raise
    if r.returncode != 0:
        raise RuntimeError(f"driver exit {r.returncode}: {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout)


_TO_AOR = re.compile(r"^To:\s*<sip:([^>;]+)>", re.MULTILINE | re.IGNORECASE)


def cleanup_steps(steps: list[dict]) -> list[dict]:
    """Deregistration of every address of record the fixture registered (REGISTER with Contact: * and Expires: 0,
    from the same party, RFC 3261 10.2.2), run after the fixture and not recorded. Without it a binding made by one
    fixture is still listed in a later fixture's 200 OK, with a remaining time that moves with step timing."""
    seen, out = set(), []
    for st in steps:
        msg = st.get("msg") or ""
        if st.get("op") != "send" or not msg.startswith("REGISTER "):
            continue
        m = _TO_AOR.search(msg)
        if not m or (st.get("from"), m.group(1)) in seen:
            continue
        seen.add((st.get("from"), m.group(1)))
        n = len(out) + 1
        party, aor = st.get("from"), m.group(1)
        out.append({"op": "send", "from": party, "window_ms": 400, "msg": "\n".join([
            "REGISTER sip:{domain} SIP/2.0",
            "Via: SIP/2.0/UDP {lab}:{port:" + party + "};branch={branch};rport",
            "Max-Forwards: 70",
            "From: <sip:" + aor + ">;tag={tag:dereg" + str(n) + "}",
            "To: <sip:" + aor + ">",
            "Call-ID: dereg" + str(n) + "-{U}@{lab}",
            "CSeq: 1 REGISTER",
            "Contact: *",
            "Expires: 0",
            "Content-Length: {clen}",
            "", ""])})
    return out


def canary(domain: str) -> bool:
    try:
        o = drive(CANARY, "pdcanary", domain)
    except Exception:  # noqa: BLE001
        return False
    return bool(o and (o[0].get("extract") or {}).get("x1"))


def lab_up(lab: str, t: dict[str, Any]) -> None:
    if sh(["docker", "network", "inspect", NET]).returncode != 0:
        nc = sh(["docker", "network", "create", "--internal", "--subnet", SUBNET, NET])
        if nc.returncode != 0:  # e.g. another network already holds the subnet: fail here, not in every lookup
            raise RuntimeError(f"network {NET} ({SUBNET}): {nc.stderr.strip()[-200:]}")
    lab_down()
    args = ["docker", "run", "-d", "--name", "psipb-eut", "--network", NET, "--ip", EUT_IP, "--cpus=2", "--memory=2g"]
    if t.get("config"):
        args += ["-v", f"{lab}/elements/{t['config']}:{t['config_dst']}:ro"]
    for src, dst in (t.get("mounts") or {}).items():
        args += ["-v", f"{lab}/elements/{src}:{dst}:ro"]
    for k, v in (t.get("env") or {}).items():
        args += ["-e", f"{k}={v}"]
    if t.get("entrypoint"):
        args += ["--entrypoint", t["entrypoint"]]
    args += [t["image"], *(t.get("cmd") or [])]
    r = sh(args)
    if r.returncode != 0:
        raise RuntimeError(f"eut start: {r.stderr.strip()[-200:]}")
    r = sh(["docker", "run", "-d", "--name", "psipb-client", "--network", NET, "--ip", CLIENT_IP, "-v",
            f"{lab}:/lab:ro", LAB_IMAGE, "sleep", "infinity"])
    if r.returncode != 0:
        raise RuntimeError(f"client start: {r.stderr.strip()[-200:]}")


def lab_down() -> None:
    sh(["docker", "rm", "-f", "psipb-eut", "psipb-client"])


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
    targets = json.loads(Path(a.targets).read_text())
    if a.only:
        targets = [t for t in targets if t["key"] in set(a.only.split(","))]
    fixtures = load_fixtures()
    if a.fixtures:
        fixtures = {k: v for k, v in fixtures.items() if k in set(a.fixtures.split(","))}
    spec = adapter.load_spec()
    commit = harness_commit() or os.environ.get("POLYDRIFT_COMMIT", "")
    log(f"start: {len(targets)} elements x {len(fixtures)} fixtures x {a.repeats} repeats, harness {commit}")
    for t in targets:
        domain = t.get("domain", "lab.test")
        for r in range(1, a.repeats + 1):
            for attempt in (1, 2):
                try:
                    lab_up(lab, t)
                except Exception as e:  # noqa: BLE001
                    log(f"{t['key']} repeat {r}: {e}")
                    lab_down()
                    break
                t0, ok_start = time.time(), False
                while time.time() - t0 < t.get("ready", 60):
                    if canary(domain):
                        ok_start = True
                        break
                    time.sleep(2)
                image_id = sh(["docker", "image", "inspect", "-f", "{{.Id}}", t["image"]]).stdout.strip()
                batch = []
                for fid, fx in fixtures.items():
                    roles = set(t.get("roles") or [t.get("role")])
                    if fx.get("roles") and not roles & set(fx["roles"]):
                        continue
                    unique = "pd" + hashlib.sha1(fid.encode()).hexdigest()[:10]
                    try:
                        obs = drive(fx["steps"], unique, domain, t.get("creds"), t.get("ctl")) if ok_start else []
                    except Exception as e:  # noqa: BLE001
                        obs = [{"step": 1, "op": "?", "error": f"driver: {e!r}"[:300]}]
                    batch.append((fid, obs))
                    clean = cleanup_steps(fx["steps"]) if ok_start and "proxy" in roles else []
                    if clean:
                        try:
                            drive(clean, unique, domain, t.get("creds"), t.get("ctl"))
                        except Exception as e:  # noqa: BLE001
                            log(f"{t['key']} repeat {r}: cleanup after {fid}: {e!r}"[:300])
                # the end canary retries like the start one (linphone after a long run of calls answered OPTIONS
                # again only after a pause, stability x5 16 Nov): it checks the element is alive, not its latency
                ok_end, t1 = False, time.time()
                while ok_start and time.time() - t1 < t.get("end_ready", 30):
                    if canary(domain):
                        ok_end = True
                        break
                    time.sleep(3)
                final = (ok_start and ok_end) or attempt == 2
                with (out / ("raw.jsonl" if final else "raw-failed.jsonl")).open("a") as rawf, \
                        (out / ("records.jsonl" if final else "records-failed.jsonl")).open("a") as recf:
                    for fid, obs in batch:
                        rawf.write(json.dumps({"impl": t["key"], "repeat": r, "fixture": fid, "image_id": image_id,
                                               "canary": [ok_start, ok_end], "obs": obs}) + "\n")
                        if ok_start and ok_end:
                            rec = adapter.to_record(obs, t["key"], fid, spec=spec, repeat=r,
                                                    impl_version=t.get("version", ""), harness_commit=commit)
                        else:
                            rec = ObservationRecord(cell="sipb", impl=t["key"], impl_version=t.get("version", ""),
                                                    fixture=fid, repeat=r, harness_commit=commit,
                                                    steps=(StepRecord(status=Status.HARNESS_FAULT,
                                                                      detail="canary failed"),))
                        recf.write(rec.to_json() + "\n")
                log(f"{t['key']} repeat {r} attempt {attempt}: canary start={ok_start} end={ok_end}, {len(batch)} "
                    f"fixtures, image {image_id[:19]}")
                lg = sh(["docker", "logs", "--tail", "400", "psipb-eut"])
                (out / f"container-{t['key']}-r{r}-a{attempt}.log").write_text(lg.stdout + lg.stderr)
                lab_down()
                if final:
                    break
    log("DONE")
    (out / "DONE").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""SIP consumer replay, proxy role (manuscript 3.6, lab L9): does a proxy's divergence change what a calling user
agent's library reports to its program?

Input is one sipb run of proxy-role targets (records.jsonl, and raw.jsonl whose messages carry their exact bytes,
harness >= the wire capture in lab/driver.py). Pairs and the sample are as in RQ3 and core.replay.

The consumer plays the caller alice. A sampled pair is replayed up to record step k (each step with a difference)
when, in the fixture's steps up to k, alice sends one INVITE over UDP without authentication and otherwise only
ACKs, and receives only responses (no requests forwarded to her). The replay server (lab/replay_server.py) answers
the consumer's INVITE with the responses the proxy sent alice in the recorded run, those of later steps included,
first implementation i's and then j's. Consumers (lab/consumers): PJSIP pjsua-lib and Sofia-SIP nua, each calling
the same user at the replay server. A consumer's events (call states and status codes) and the requests it sent
(ACK, BYE, retransmissions) are compared; reason phrases are kept as data and not compared (the program is handed
them, it does not act on them).

Usage (lab host, detached): python -m polydrift.cells.sipb.replay --run RUN --lab LAB --out OUT
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from polydrift.cells.sipb import adapter
from polydrift.cells.sipb.runner import load_fixtures
from polydrift.core.oracle import compare_all
from polydrift.core.record import ObservationRecord, Status, read_jsonl
from polydrift.core.repeat import MIN_REPEATS, flaky_triples
from polydrift.core.replay import replay_summary, sample

SEED = 20261116
NET, SUBNET, SERVER_IP, CONSUMER_IP = "pd-replay-sip", "172.31.64.0/24", "172.31.64.53", "172.31.64.11"
IMAGE = "pd-consumer-sip"
CONSUMERS = {"pjsip": "pjconsumer", "sofia": "sofconsumer"}


def prefix_plan(fixture: dict[str, Any], obs: list[dict[str, Any]], k: int) -> dict[str, Any] | None:
    """Consumer call and server steps for record steps 1..k (obs[0..k-1]), or None if not replayable."""
    if k > len(obs):
        return None
    steps = fixture.get("steps") or []
    user, server, wait_ms, invited = None, [], 0, False
    for o in obs[:k]:
        st = steps[o["step"] - 1] if 0 < o.get("step", 0) <= len(steps) else {}
        got = [m for m in (o.get("got") or {}).get("alice") or []]
        if any(m.get("kind") != "resp" or "wire" not in m for m in got):
            return None
        actor = o.get("actor") == "alice"
        method = ""
        if actor:
            if st.get("op") == "send":
                msg = st.get("msg") or ""
                if invited or not msg.startswith("INVITE ") or st.get("auth") or st.get("transport", "udp") != "udp":
                    return None
                m = re.match(r"INVITE sips?:([^@\s;>]+)@", msg)
                if not m:
                    return None
                user, invited, method = m.group(1), True, "INVITE"
            elif st.get("op") == "ack" and invited:
                method = "ACK"
            else:
                return None
        elif not invited and got:
            return None  # alice receives something before she calls: not a session a library caller can have
        if invited:
            wait_ms += int(st.get("window_ms", 1500))
        server.append({"actor": actor, "method": method, "wire": [m["wire"] for m in got]})
    if not invited:
        return None
    return {"uri": f"sip:{user}@{SERVER_IP}", "wait_ms": max(3000, min(wait_ms + 1000, 15000)), "steps": server}


def pair_steps(pair: Any) -> list[int]:
    return sorted({d.step for d in [*pair.diffs, *pair.t4_diffs] if isinstance(d.step, int)})


def split_result(lines: list[str]) -> dict[str, Any]:
    events, reasons = [], []
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if "reason" in e:
            reasons.append(e.pop("reason"))
        events.append(e)
    return {"events": events, "data": {"reasons": reasons}}


def sh(a: list[str], timeout: int = 120, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(a, capture_output=True, text=True, timeout=timeout, input=stdin)


def lab_up(lab: str) -> str:
    if sh(["docker", "network", "inspect", NET]).returncode != 0:
        nc = sh(["docker", "network", "create", "--internal", "--subnet", SUBNET, NET])
        if nc.returncode != 0:  # e.g. another network already holds the subnet: fail here, not in every lookup
            raise RuntimeError(f"network {NET} ({SUBNET}): {nc.stderr.strip()[-200:]}")
    sh(["docker", "rm", "-f", "pdr-sip-server", "pdr-sip-consumer"])
    for name, ip, cmd in (("pdr-sip-server", SERVER_IP, ["python3", "/lab/replay_server.py"]),
                          ("pdr-sip-consumer", CONSUMER_IP, ["sleep", "infinity"])):
        r = sh(["docker", "run", "-d", "--name", name, "--network", NET, "--ip", ip, "-v", f"{lab}:/lab:ro", IMAGE, *cmd])
        if r.returncode != 0:
            raise RuntimeError(f"{name}: {r.stderr.strip()[-200:]}")
    time.sleep(1)
    return (sh(["docker", "exec", "pdr-sip-consumer", "cat", "/CONSUMER-VERSION"]).stdout.strip() + " image " +
            sh(["docker", "image", "inspect", "-f", "{{.Id}}", IMAGE]).stdout.strip()[:19])


def ctl(cmd: str) -> str:
    return sh(["docker", "exec", "-i", "pdr-sip-server", "python3", "/lab/replay_server.py", "--ctl"], stdin=cmd).stdout


def main() -> int:
    from polydrift.cells.dns.replay import peers_of

    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--lab", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--current", default="")
    ap.add_argument("--min-repeats", type=int, default=MIN_REPEATS)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        with (out / "progress").open("a") as f:
            f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}\n")

    run = Path(a.run)
    records = [r for r in read_jsonl(str(run / "records.jsonl"))
               if not any(s.status == Status.HARNESS_FAULT for s in r.steps)]
    raw = {}
    for line in (run / "raw.jsonl").read_text().splitlines():
        d = json.loads(line)
        raw[(d["impl"], d["fixture"], d["repeat"])] = d["obs"]
    rep = flaky_triples(records, min_repeats=a.min_repeats)
    current = peers_of(records, set(a.current.split(",")) if a.current else None)
    rc = adapter.load_spec().refusal_class
    fixtures = load_fixtures()
    reps: dict[str, list[ObservationRecord]] = {}
    for (impl, fid), r in rep.representative.items():
        if impl in current:
            reps.setdefault(fid, []).append(r)
    pairs = [p for fid in sorted(reps) for p in compare_all(reps[fid], flaky=rep.flaky, refusal_class=rc)]
    sampled = sample(pairs, SEED).all()
    log(f"start: {len(current)} peers, {len(pairs)} pairs, sampled {len(sampled)}")
    version = lab_up(str(Path(a.lab).resolve()))
    log(f"consumers: {version}")
    lines: list[dict[str, Any]] = []
    not_replayable: list[str] = []
    with (out / "replay.jsonl").open("w") as fo:
        for stratum, p in sampled:
            fx = fixtures.get(p.fixture) or {}
            todo = []
            for k in pair_steps(p):
                plans = {}
                for side, impl in (("left", p.left), ("right", p.right)):
                    rpt = rep.representative[(impl, p.fixture)].repeat
                    plan = prefix_plan(fx, raw.get((impl, p.fixture, rpt)) or [], k)
                    if plan is None:
                        break
                    plans[side] = plan
                if len(plans) == 2:
                    todo.append((k, plans))
            if not todo:
                not_replayable.append(f"{p.fixture} {p.left} {p.right}")
                continue
            for k, plans in todo:
                for name, prog in CONSUMERS.items():
                    for side, plan in plans.items():
                        ctl("LOAD " + json.dumps({"steps": plan["steps"]}))
                        r = sh(["docker", "exec", "pdr-sip-consumer", "timeout", "60", prog, plan["uri"],
                                str(plan["wait_ms"])], timeout=90)
                        res = split_result(r.stdout.splitlines())
                        res["server"] = json.loads(ctl("COUNT") or "{}")
                        ln = {"stratum": stratum, "fixture": p.fixture, "left": p.left, "right": p.right,
                              "grade": None if p.grade is None else int(p.grade), "step": k, "consumer": name,
                              "side": side, "result": res}
                        lines.append(ln)
                        fo.write(json.dumps(ln) + "\n")
            log(f"{stratum} {p.fixture} {p.left} {p.right}: {len(todo)} steps")
    summ = replay_summary(lines, sampled, not_replayable, tuple(CONSUMERS), SEED)
    summ.update({"run": str(run), "peers": sorted(current), "consumers": version, "not_replayable_pairs": not_replayable})
    (out / "summary.json").write_text(json.dumps(summ, indent=2, sort_keys=True, default=str) + "\n")
    sh(["docker", "rm", "-f", "pdr-sip-server", "pdr-sip-consumer"])
    log("DONE")
    (out / "DONE").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

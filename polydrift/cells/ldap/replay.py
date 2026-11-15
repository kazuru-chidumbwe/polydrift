"""LDAP consumer replay (manuscript 3.6, lab L9): does a divergence change what an LDAP client library returns?

Input is one LDAP cell run directory (records.jsonl, and raw.jsonl whose steps carry the response bytes, harness >=
the wire capture in driver.py) and the targets file of that run (each side's base DN, admin DN and password, which
the fixture placeholders stand for). Pairs and the sample are as in RQ3 and core.replay (seed below).

For each sampled pair, every step k on which the pair differs is replayed if the fixture's steps 1..k form a
session a client library can issue: one connection, operations bind (simple), search, add, modify, delete, moddn
and compare, no request controls and no values taken from earlier responses. The replay server
(lab/replay_server.py) serves implementation i's recorded responses to steps 1..k, then j's; each consumer
(lab/consumers: OpenLDAP libldap through python-ldap, and ldap3) runs the same operations and reports what it got.
Before the two results are compared, each side's base DN, admin DN and unique name are written back as {B}, {A}
and {U} (as the cell adapter does), so a difference in the lab's naming is not taken for a consequence.

Usage (Testbed B, detached): python -m polydrift.cells.ldap.replay --run RUN --targets T.json --lab LAB --out OUT
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from polydrift.cells.ldap import adapter
from polydrift.cells.ldap.driver import subst
from polydrift.cells.ldap.runner import load_fixtures
from polydrift.core.oracle import compare_all
from polydrift.core.record import ObservationRecord, Status, read_jsonl
from polydrift.core.repeat import MIN_REPEATS, flaky_triples
from polydrift.core.replay import replay_summary, sample

SEED = 20261116
NET, SUBNET, SERVER_IP, CONSUMER_IP = "pd-replay-ldap", "172.31.61.0/24", "172.31.61.53", "172.31.61.11"
IMAGE = "pd-consumer-ldap"
CONSUMERS = ("libldap", "ldap3")
API_OPS = {"bind", "search", "add", "modify", "delete", "moddn", "compare"}


def unique(fid: str) -> str:
    return "pd" + hashlib.sha1(fid.encode()).hexdigest()[:10]


def env_of(target: dict[str, Any], fid: str) -> dict[str, str]:
    return {"B": target.get("base", ""), "A": target.get("admin", ""), "P": target.get("password", ""),
            "U": unique(fid)}


def prefix_script(fixture: dict[str, Any], k: int, env: dict[str, str]) -> list[dict[str, Any]] | None:
    """The consumer operations for steps 1..k, or None if that prefix cannot be issued through a client API."""
    out = []
    for st in (fixture.get("steps") or [])[:k]:
        op = st.get("op", "hex")
        if op == "sleep":
            continue
        if st.get("client", "a") != "a" or op not in API_OPS or st.get("controls") or "id" in st:
            return None
        args = st.get("args") or {}
        if "{@" in json.dumps(args) or (op == "bind" and ("sasl" in args or int(args.get("version", 3)) != 3)):
            return None
        out.append({"op": op, "args": subst(args, env)})
    return out or None


def server_steps(fixture: dict[str, Any], obs: list[dict[str, Any]], k: int) -> list[dict[str, Any]] | None:
    steps = []
    for i, st in enumerate((fixture.get("steps") or [])[:k], 1):
        if st.get("op") == "sleep":
            continue
        o = next((x for x in obs if x.get("step") == i), None)
        if o is None:
            return None
        me = (o.get("clients") or {}).get("a") or {}
        if "wire" not in me:
            return None
        steps.append({"op": st["op"], "msgs": me["wire"], "closed": bool(me.get("closed_now"))})
    return steps


def canon(result: Any, env: dict[str, str]) -> Any:
    text = json.dumps(result, sort_keys=True)
    for key, ph in (("U", "{U}"), ("A", "{A}"), ("B", "{B}")):  # the admin DN usually ends in the base
        v = env.get(key)
        if v:
            text = re.sub(re.escape(v), ph, text, flags=re.IGNORECASE)
    return json.loads(text)


def pair_steps(pair: Any) -> list[int]:
    return sorted({d.step for d in [*pair.diffs, *pair.t4_diffs] if isinstance(d.step, int)})


def sh(a: list[str], timeout: int = 120, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(a, capture_output=True, text=True, timeout=timeout, input=stdin)


def lab_up(lab: str) -> str:
    if sh(["docker", "network", "inspect", NET]).returncode != 0:
        sh(["docker", "network", "create", "--internal", "--subnet", SUBNET, NET])
    sh(["docker", "rm", "-f", "pdr-ldap-server", "pdr-ldap-consumer"])
    for name, ip, cmd in (("pdr-ldap-server", SERVER_IP, ["python3", "/lab/replay_server.py"]),
                          ("pdr-ldap-consumer", CONSUMER_IP, ["sleep", "infinity"])):
        r = sh(["docker", "run", "-d", "--name", name, "--network", NET, "--ip", ip, "-v", f"{lab}:/lab:ro", IMAGE, *cmd])
        if r.returncode != 0:
            raise RuntimeError(f"{name}: {r.stderr.strip()[-200:]}")
    time.sleep(1)
    return (sh(["docker", "exec", "pdr-ldap-consumer", "cat", "/CONSUMER-VERSION"]).stdout.strip() + " image " +
            sh(["docker", "image", "inspect", "-f", "{{.Id}}", IMAGE]).stdout.strip()[:19])


def ctl(cmd: str) -> str:
    return sh(["docker", "exec", "-i", "pdr-ldap-server", "python3", "/lab/replay_server.py", "--ctl"], stdin=cmd).stdout


def consume(lib: str, script: list[dict[str, Any]]) -> Any:
    r = sh(["docker", "exec", "-i", "pdr-ldap-consumer", "python3", "/lab/consumers/ldapconsumer.py", lib, SERVER_IP,
            "389"], stdin=json.dumps(script), timeout=180)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"consumer_failed": (r.stderr or r.stdout).strip()[-300:]}


def main() -> int:
    from polydrift.cells.dns.replay import peers_of

    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--targets", required=True)
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
    targets = {t["key"]: t for t in json.loads(Path(a.targets).read_text())}
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
                sides = {}
                for side, impl in (("left", p.left), ("right", p.right)):
                    env = env_of(targets.get(impl, {}), p.fixture)
                    obs = raw.get((impl, p.fixture, rep.representative[(impl, p.fixture)].repeat)) or []
                    script, srv = prefix_script(fx, k, env), server_steps(fx, obs, k)
                    if script is None or srv is None or impl not in targets:
                        break
                    sides[side] = (env, script, srv)
                if len(sides) == 2:
                    todo.append((k, sides))
            if not todo:
                not_replayable.append(f"{p.fixture} {p.left} {p.right}")
                continue
            for k, sides in todo:
                for lib in CONSUMERS:
                    for side, (env, script, srv) in sides.items():
                        ctl("LOAD " + json.dumps({"steps": srv}))
                        res = {"ops": canon(consume(lib, script), env), "server": json.loads(ctl("COUNT") or "{}")}
                        ln = {"stratum": stratum, "fixture": p.fixture, "left": p.left, "right": p.right,
                              "grade": None if p.grade is None else int(p.grade), "step": k, "consumer": lib,
                              "side": side, "result": res}
                        lines.append(ln)
                        fo.write(json.dumps(ln) + "\n")
            log(f"{stratum} {p.fixture} {p.left} {p.right}: {len(todo)} steps")
    summ = replay_summary(lines, sampled, not_replayable, CONSUMERS, SEED)
    summ.update({"run": str(run), "peers": sorted(current), "consumers": version, "not_replayable_pairs": not_replayable})
    (out / "summary.json").write_text(json.dumps(summ, indent=2, sort_keys=True, default=str) + "\n")
    sh(["docker", "rm", "-f", "pdr-ldap-server", "pdr-ldap-consumer"])
    log("DONE")
    (out / "DONE").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

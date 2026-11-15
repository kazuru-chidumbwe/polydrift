"""JMAP consumer replay (manuscript 3.6, lab L9): does a divergence change what a JMAP client library gives its
program?

Input is one JMAP cell run directory (records.jsonl, and raw.jsonl whose steps carry each response's exact body
bytes and the session object, harness >= the wire capture in driver.py). Pairs and the sample are as in RQ3 and
core.replay.

A sampled pair is replayed up to step k (each step with a difference) when steps 1..k are all API calls a library can
send as given (no raw bodies, unauthenticated calls, uploads, downloads, event sources, extra headers or
createdIds). The replay server (lab/replay_server.py) serves implementation i's session and recorded responses, then
j's; each consumer (lab/consumers: jmapc in Python, go-jmap in Go) sends the same calls and reports what it parsed.
Server-assigned values (ids, states, account ids, blob ids) differ between servers without consequence, as in the
cell oracle, which compares response shapes; each side's consumer output therefore has the opaque values its
recorded responses carry renamed in order of first appearance (core.canon.AlphaRenamer) before the comparison.

Usage (Testbed B, detached): python -m polydrift.cells.jmap.replay --run RUN --lab LAB --out OUT
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from polydrift.cells.jmap import adapter
from polydrift.cells.jmap.runner import load_fixtures
from polydrift.core.canon import AlphaRenamer
from polydrift.core.oracle import compare_all
from polydrift.core.record import ObservationRecord, Status, read_jsonl
from polydrift.core.repeat import MIN_REPEATS, flaky_triples
from polydrift.core.replay import replay_summary, sample

SEED = 20261116
NET, SUBNET, SERVER_IP, CONSUMER_IP = "pd-replay-jmap", "172.31.63.0/24", "172.31.63.53", "172.31.63.11"
IMAGE = "pd-consumer-jmap"
CONSUMERS = {"jmapc": ["python3", "/lab/consumers/jmapcconsumer.py"], "go-jmap": ["pd-gojmap"]}
VOLATILE_KEYS = {"id", "accountId", "state", "newState", "oldState", "queryState", "newQueryState",
                 "oldQueryState", "blobId", "threadId", "emailId", "identityId", "sessionState", "parentId"}
VOLATILE_LISTS = {"ids", "notFound", "destroyed", "created", "updated", "removed", "added"}
VOLATILE_MAP_KEYS = {"updated", "notUpdated", "notDestroyed", "mailboxIds", "accounts", "primaryAccounts"}


def unique(fid: str) -> str:
    return "pd" + hashlib.sha1(fid.encode()).hexdigest()[:10]


def volatile_values(doc: Any, out: set[str] | None = None) -> set[str]:
    """Opaque server-assigned strings in a recorded response or session object."""
    out = set() if out is None else out
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k in VOLATILE_KEYS and isinstance(v, str):
                out.add(v)
            elif k in VOLATILE_LISTS and isinstance(v, list):
                out.update(x for x in v if isinstance(x, str))
            if k in VOLATILE_MAP_KEYS and isinstance(v, dict):
                out.update(x for x in v if isinstance(x, str) and not x.startswith("urn:"))
            if k == "primaryAccounts" and isinstance(v, dict):
                out.update(x for x in v.values() if isinstance(x, str))
            volatile_values(v, out)
    elif isinstance(doc, list):
        for x in doc:
            volatile_values(x, out)
    return out


def canon(result: Any, volatile: set[str]) -> Any:
    """Opaque values renamed #1, #2, ... in order of first appearance (keys and string values)."""
    ren = AlphaRenamer()

    def walk(x: Any) -> Any:
        if isinstance(x, dict):
            return {(ren(k) if k in volatile else k): walk(v) for k, v in sorted(x.items())}
        if isinstance(x, list):
            return [walk(v) for v in x]
        if isinstance(x, str) and x in volatile:
            return ren(x)
        return x

    return walk(result)


def consumer_steps(fixture: dict[str, Any], k: int, fid: str) -> list[list] | None:
    out = []
    for st in (fixture.get("steps") or [])[:k]:
        if st.get("op", "call") != "call" or st.get("headers") or "createdIds" in st:
            return None
        calls = json.loads(json.dumps(st.get("calls") or []).replace("{U}", unique(fid)))
        if not calls:
            return None
        out.append(calls)
    return out or None


def server_steps(obs: list[dict[str, Any]], k: int) -> list[dict[str, Any]] | None:
    out = []
    for i in range(1, k + 1):
        o = next((x for x in obs if x.get("step") == i), None)
        if o is None or "wire" not in o:
            return None
        out.append({"http": o.get("http"), "ctype": o.get("wire_ctype", ""), "body_b64": o["wire"]})
    return out


def pair_steps(pair: Any) -> list[int]:
    return sorted({d.step for d in [*pair.diffs, *pair.t4_diffs] if isinstance(d.step, int)})


def sh(a: list[str], timeout: int = 120, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(a, capture_output=True, text=True, timeout=timeout, input=stdin)


def lab_up(lab: str) -> str:
    if sh(["docker", "network", "inspect", NET]).returncode != 0:
        sh(["docker", "network", "create", "--internal", "--subnet", SUBNET, NET])
    sh(["docker", "rm", "-f", "pdr-jmap-server", "pdr-jmap-consumer"])
    for name, ip, cmd in (("pdr-jmap-server", SERVER_IP, ["python3", "/lab/replay_server.py"]),
                          ("pdr-jmap-consumer", CONSUMER_IP, ["sleep", "infinity"])):
        r = sh(["docker", "run", "-d", "--name", name, "--network", NET, "--ip", ip, "-v", f"{lab}:/lab:ro", IMAGE, *cmd])
        if r.returncode != 0:
            raise RuntimeError(f"{name}: {r.stderr.strip()[-200:]}")
    time.sleep(1)
    return (sh(["docker", "exec", "pdr-jmap-consumer", "cat", "/CONSUMER-VERSION"]).stdout.strip() + " image " +
            sh(["docker", "image", "inspect", "-f", "{{.Id}}", IMAGE]).stdout.strip()[:19])


def ctl(cmd: str) -> str:
    return sh(["docker", "exec", "-i", "pdr-jmap-server", "python3", "/lab/replay_server.py", "--ctl"], stdin=cmd).stdout


def consume(name: str, user: str, steps: list[list]) -> list[Any]:
    script = {"host": SERVER_IP, "endpoint": f"https://{SERVER_IP}/.well-known/jmap", "user": user,
              "password": "replay", "steps": steps}
    r = sh(["docker", "exec", "-i", "pdr-jmap-consumer", *CONSUMERS[name]], stdin=json.dumps(script), timeout=180)
    out = []
    for ln in r.stdout.splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            out.append({"unparsed": ln[-200:]})
    return out or [{"consumer_failed": (r.stderr or "").strip()[-300:]}]


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
    raw, sessions = {}, {}
    for line in (run / "raw.jsonl").read_text().splitlines():
        d = json.loads(line)
        raw[(d["impl"], d["fixture"], d["repeat"])] = d["obs"]
        sessions[(d["impl"], d["repeat"])] = d.get("session") or {}
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
                calls = consumer_steps(fx, k, p.fixture)
                sides = {}
                for side, impl in (("left", p.left), ("right", p.right)):
                    rpt = rep.representative[(impl, p.fixture)].repeat
                    srv = server_steps(raw.get((impl, p.fixture, rpt)) or [], k)
                    sess = sessions.get((impl, rpt)) or {}
                    if calls is None or srv is None or not sess:
                        break
                    vol = volatile_values(sess)
                    for s in srv:
                        try:
                            volatile_values(json.loads(base64.b64decode(s["body_b64"]) or b"null"), vol)
                        except ValueError:
                            pass
                    sides[side] = (sess, srv, vol)
                if len(sides) == 2:
                    todo.append((k, calls, sides))
            if not todo:
                not_replayable.append(f"{p.fixture} {p.left} {p.right}")
                continue
            for k, calls, sides in todo:
                for name in CONSUMERS:
                    for side, (sess, srv, vol) in sides.items():
                        ctl("LOAD " + json.dumps({"session": sess, "steps": srv}))
                        user = str(sess.get("username") or "pd")
                        got = consume(name, user, calls)
                        res = {"out": canon(got, vol | {user}), "server": json.loads(ctl("COUNT") or "{}")}
                        ln = {"stratum": stratum, "fixture": p.fixture, "left": p.left, "right": p.right,
                              "grade": None if p.grade is None else int(p.grade), "step": k, "consumer": name,
                              "side": side, "result": res}
                        lines.append(ln)
                        fo.write(json.dumps(ln) + "\n")
            log(f"{stratum} {p.fixture} {p.left} {p.right}: {len(todo)} steps")
    summ = replay_summary(lines, sampled, not_replayable, tuple(CONSUMERS), SEED)
    summ.update({"run": str(run), "peers": sorted(current), "consumers": version, "not_replayable_pairs": not_replayable})
    (out / "summary.json").write_text(json.dumps(summ, indent=2, sort_keys=True, default=str) + "\n")
    sh(["docker", "rm", "-f", "pdr-jmap-server", "pdr-jmap-consumer"])
    log("DONE")
    (out / "DONE").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

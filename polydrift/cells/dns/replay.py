"""DNS consumer replay (manuscript 3.6, lab L9): does a divergence change what a stub-resolver consumer does?

Input is one DNS cell run directory (records.jsonl, and raw.jsonl whose query steps carry the response bytes,
harness >= the wire capture in lab/driver.py). The pairs are those of RQ3: the current peers of the run, compared
per fixture with the run's flaky triples. ``core.replay.sample`` draws the strata with the seed fixed in the
harness (every T0 to T2 divergence up to 50, 50 T3 divergences, 50 agreements that differ only at T4).

For each sampled pair, every `query` step on which the pair differs (counted or T4 axes) is replayed: the replay
server (lab/replay_server.py) answers the consumer with the exact bytes implementation i sent at that step, then
with j's bytes (silence for a silent step). Each consumer (lab/consumers: glibc and musl, the same program) makes
the same lookup as the fixture step and reports what the library returned. The pair is consequential for a
consumer if any replayed step gives a different consumer result. Steps that are not plain queries (raw, upstream,
parallel, tcp_session) cannot be asked through a stub API and are not replayed; a pair with no replayable step is
counted as not replayable.

Usage (lab host, detached): python -m polydrift.cells.dns.replay --run RUN_DIR --lab LAB_DIR --out OUT
Writes OUT/replay.jsonl (one line per pair, step, consumer and side), OUT/summary.json, OUT/progress, OUT/DONE.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from polydrift.cells.dns import adapter
from polydrift.cells.dns.runner import load_fixtures
from polydrift.core.oracle import compare_all
from polydrift.core.record import ObservationRecord, Status, read_jsonl
from polydrift.core.replay import replay_summary, sample
from polydrift.core.repeat import MIN_REPEATS, flaky_triples

SEED = 20261116  # part of the frozen oracle specification (3.6): the replay sample is drawn with it
NET, SUBNET, SERVER_IP = "pd-replay-dns", "172.31.65.0/24", "172.31.65.53"
LAB_IMAGE = "pd-dns-py:lab"
CONSUMERS = {"glibc": ("pd-consumer-glibc", "172.31.65.11"), "musl": ("pd-consumer-musl", "172.31.65.12")}
QTYPES = {"A": 1, "NS": 2, "CNAME": 5, "SOA": 6, "PTR": 12, "MX": 15, "TXT": 16, "AAAA": 28, "SRV": 33,
          "NAPTR": 35, "DNAME": 39, "DS": 43, "RRSIG": 46, "DNSKEY": 48, "CAA": 257, "ANY": 255}


def qtype_number(name: str) -> int | None:
    name = str(name).upper()
    if name in QTYPES:
        return QTYPES[name]
    if name.startswith("TYPE") and name[4:].isdigit():
        return int(name[4:])
    return None


def replayable(step: dict[str, Any]) -> bool:
    return (step.get("op", "query") == "query" and str(step.get("qclass", "IN")).upper() == "IN"
            and qtype_number(step.get("qtype", "A")) is not None)


def pair_steps(pair: Any, fixture: dict[str, Any]) -> list[int]:
    """Replayable steps (1-based) where the pair differs on any axis, counted or T4."""
    steps = fixture.get("steps") or []
    out = set()
    for d in [*pair.diffs, *pair.t4_diffs]:
        if isinstance(d.step, int) and 1 <= d.step <= len(steps) and replayable(steps[d.step - 1]):
            out.add(d.step)
    return sorted(out)


def side_payload(obs: dict[str, Any] | None) -> tuple[str, str | None]:
    """('answer', hex) for a response with bytes, ('silent', None) for a timeout, ('missing', None) otherwise."""
    if obs is None:
        return "missing", None
    if obs.get("wire"):
        return "answer", obs["wire"]
    if adapter.classify_status(obs) == Status.SILENT:
        return "silent", None
    return "missing", None


def peers_of(records: list[ObservationRecord], current: set[str] | None) -> set[str]:
    from polydrift.report import _vkey, family_version

    fam: dict[str, dict[str, str]] = {}
    for r in records:
        f, v = family_version(r)
        fam.setdefault(f, {})[v] = r.impl
    return current or {vs[max(vs, key=_vkey)] for vs in fam.values()}


def consumer_result(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"unparsed_output": raw.strip()[-200:]}


def summarise(lines: list[dict[str, Any]], sampled: list[tuple[str, Any]], not_replayable: list[str]) -> dict:
    return replay_summary(lines, sampled, not_replayable, tuple(CONSUMERS), SEED)


def sh(a: list[str], timeout: int = 120, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(a, capture_output=True, text=True, timeout=timeout, input=stdin)


def lab_up(lab: str) -> dict[str, str]:
    if sh(["docker", "network", "inspect", NET]).returncode != 0:
        sh(["docker", "network", "create", "--internal", "--subnet", SUBNET, NET])
    sh(["docker", "rm", "-f", "pdr-server", *[c for c, _ in CONSUMERS.values()]])
    r = sh(["docker", "run", "-d", "--name", "pdr-server", "--network", NET, "--ip", SERVER_IP, "-v",
            f"{lab}:/lab:ro", LAB_IMAGE, "python3", "/lab/replay_server.py"])
    if r.returncode != 0:
        raise RuntimeError(f"replay server: {r.stderr.strip()[-200:]}")
    versions = {}
    for name, (image, ip) in CONSUMERS.items():
        r = sh(["docker", "run", "-d", "--name", image, "--network", NET, "--ip", ip, "-v",
                f"{lab}/consumers/resolv.conf:/etc/resolv.conf:ro", image])
        if r.returncode != 0:
            raise RuntimeError(f"consumer {name}: {r.stderr.strip()[-200:]}")
        versions[name] = (sh(["docker", "exec", image, "cat", "/CONSUMER-VERSION"]).stdout.strip() + " image " +
                          sh(["docker", "image", "inspect", "-f", "{{.Id}}", image]).stdout.strip()[:19])
    time.sleep(1)
    return versions


def ctl(cmd: str) -> str:
    return sh(["docker", "exec", "-i", "pdr-server", "python3", "/lab/replay_server.py", "--ctl"], stdin=cmd).stdout


def main() -> int:
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
    versions = lab_up(str(Path(a.lab).resolve()))
    log(f"consumers: {versions}")
    lines: list[dict[str, Any]] = []
    not_replayable: list[str] = []
    with (out / "replay.jsonl").open("w") as fo:
        for stratum, p in sampled:
            fx = fixtures.get(p.fixture) or {}
            steps = pair_steps(p, fx)  # a T0 (liveness) divergence is a diff at its first step, so it is included
            sides = {}
            for side, impl in (("left", p.left), ("right", p.right)):
                repeat = rep.representative[(impl, p.fixture)].repeat
                sides[side] = raw.get((impl, p.fixture, repeat)) or []
            todo = []
            for k in steps:
                pl = {s: side_payload(next((o for o in sides[s] if o.get("step") == k), None)) for s in sides}
                if all(kind != "missing" for kind, _ in pl.values()):
                    todo.append((k, pl))
            if not todo:
                not_replayable.append(f"{p.fixture} {p.left} {p.right}")
                continue
            for k, pl in todo:
                st = fx["steps"][k - 1]
                qn, qt = str(st["qname"]), qtype_number(st.get("qtype", "A"))
                for c, (container, _) in CONSUMERS.items():
                    for side in ("left", "right"):
                        kind, wire = pl[side]
                        ctl("LOAD " + json.dumps({"wire": wire}))
                        res = consumer_result(sh(["docker", "exec", container, "pdstub", qn, str(qt)], timeout=60).stdout)
                        res["queries"] = json.loads(ctl("COUNT") or "{}")
                        res["queries"].pop("qnames", None)
                        ln = {"stratum": stratum, "fixture": p.fixture, "left": p.left, "right": p.right,
                              "grade": None if p.grade is None else int(p.grade), "step": k, "consumer": c,
                              "side": side, "payload": kind, "result": res}
                        lines.append(ln)
                        fo.write(json.dumps(ln) + "\n")
            log(f"{stratum} {p.fixture} {p.left} {p.right}: {len(todo)} steps")
    summ = summarise(lines, sampled, not_replayable)
    summ.update({"run": str(run), "peers": sorted(current), "consumers": versions,
                 "not_replayable_pairs": not_replayable})
    (out / "summary.json").write_text(json.dumps(summ, indent=2, sort_keys=True, default=str) + "\n")
    sh(["docker", "rm", "-f", "pdr-server", *[c for c, _ in CONSUMERS.values()]])
    log("DONE")
    (out / "DONE").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""MQTT consumer replay (manuscript 3.6, lab L9): does a divergence change what an MQTT client library delivers to
its program?

Input is one MQTT cell run directory (records.jsonl, and raw.jsonl whose steps carry each client's received packet
bytes, harness >= the wire capture in driver.py). Pairs and the sample are as in RQ3 and core.replay.

A sampled pair is replayed up to step k (each step with a difference; the whole fixture for an end-state
difference) for every fixture client whose own steps a client library can issue: connect (version, clean start,
keep alive, client id and the numeric limits session expiry, receive maximum, maximum packet size, topic alias
maximum), single-filter subscribe and unsubscribe, publish at QoS 0 or 1, disconnect. The consumer library plays
that client; the replay server (lab/replay_server.py) answers it with the packets the broker sent to that client in
the recorded run, deliveries caused by other clients included, first implementation i's and then j's. Consumers
(lab/consumers): libmosquitto (C) and Eclipse Paho (Python). Before comparing, each side's topic and client-id
prefixes ({T}, {C}) are written back. The pair is consequential for a consumer if any replayed client sees a
different sequence of events.

Usage (lab host, detached): python -m polydrift.cells.mqtt.replay --run RUN --lab LAB --out OUT
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from polydrift.cells.mqtt import adapter
from polydrift.cells.mqtt.runner import load_fixtures
from polydrift.core.oracle import compare_all
from polydrift.core.record import ObservationRecord, Status, read_jsonl
from polydrift.core.repeat import MIN_REPEATS, flaky_triples
from polydrift.core.replay import replay_summary, sample

SEED = 20261116
NET, SUBNET, SERVER_IP, CONSUMER_IP = "pd-replay-mqtt", "172.31.62.0/24", "172.31.62.53", "172.31.62.11"
IMAGE = "pd-consumer-mqtt"
CONSUMERS = {"libmosquitto": ["mosqconsumer", SERVER_IP, "1883"],
             "paho": ["python3", "/lab/consumers/pahoconsumer.py", SERVER_IP, "1883"]}
PTYPE = {"connect": "CONNECT", "subscribe": "SUBSCRIBE", "unsubscribe": "UNSUBSCRIBE", "publish": "PUBLISH",
         "disconnect": "DISCONNECT"}
CONNECT_KEYS = {"version", "clean_start", "keep_alive", "client_id", "properties", "will", "username", "password"}
CONNECT_PROPS = {"session_expiry_interval": "sei", "receive_maximum": "rmax", "maximum_packet_size": "mps",
                 "topic_alias_maximum": "tam", "request_problem_information": "rpi",
                 "request_response_information": "rri", "authentication_method": "am"}


def prefixes(fid: str, repeat: int) -> tuple[str, str]:
    return f"pd/{fid}/r{repeat}/", f"pd-{fid[:20]}-r{repeat}-"


def subst(value: Any, t: str, c: str) -> Any:
    if isinstance(value, str):
        return value.replace("{T}", t).replace("{C}", c)
    if isinstance(value, list):
        return [subst(v, t, c) for v in value]
    if isinstance(value, dict):
        return {k: subst(v, t, c) for k, v in value.items()}
    return value


def _token(s: Any) -> bool:
    return isinstance(s, str) and s != "" and not re.search(r"\s", s)


def line_for(st: dict[str, Any]) -> str | None:
    """One consumer script line for a fixture step of the replayed client, or None if a library cannot issue it."""
    op, a = st.get("op"), dict(st.get("args") or {})
    if st.get("auto_ack") is False:
        return None
    if op == "connect":
        props = a.get("properties") or {}
        cid = a.get("client_id", "")
        if set(a) - CONNECT_KEYS or set(props) - set(CONNECT_PROPS) or not (cid == "" or _token(cid)):
            return None
        v = int(a.get("version", 5))
        if v not in (4, 5):
            return None
        extra = []
        for k, x in sorted(props.items()):
            if k == "authentication_method":
                if not _token(x):
                    return None
                extra.append(f"am={x}")
            else:
                extra.append(f"{CONNECT_PROPS[k]}={int(x)}")
        will = a.get("will")
        if will is not None:
            # a will a library can set: topic, payload, QoS 0 to 2, retain; no will properties
            if set(will) - {"topic", "payload", "qos", "retain"} or not _token(will.get("topic")) \
                    or int(will.get("qos", 0)) not in (0, 1, 2):
                return None
            pl = will.get("payload", "")
            hx = (pl.encode() if isinstance(pl, str) else bytes(pl)).hex() or "-"
            extra.append(f"will={int(will.get('qos', 0))},{int(bool(will.get('retain')))},{will['topic']},{hx}")
        for k, tag in (("username", "user"), ("password", "pass")):
            if a.get(k) is not None:
                if not (_token(a[k]) or a[k] == ""):
                    return None
                extra.append(f"{tag}={a[k] or '-'}")
        return (f"connect {v} {int(bool(a.get('clean_start', True)))} {int(a.get('keep_alive', 60))} {cid or '-'}"
                + (" " + " ".join(extra) if extra else ""))
    if op == "subscribe":
        fs = [{"topic": f} if isinstance(f, str) else f for f in a.get("filters") or []]
        if set(a) - {"filters", "packet_id", "version"} or len(fs) != 1 or a.get("properties") or a.get("fixed_flags") is not None or not _token(fs[0].get("topic")):
            return None
        f = fs[0]
        if set(f) - {"topic", "qos", "no_local", "retain_as_published", "retain_handling"}:
            return None
        return (f"subscribe {int(f.get('qos', 0))} {int(bool(f.get('no_local')))} "
                f"{int(bool(f.get('retain_as_published')))} {int(f.get('retain_handling', 0))} {f['topic']}")
    if op == "unsubscribe":
        ts = [x if isinstance(x, str) else x.get("topic") for x in a.get("filters") or []]
        if set(a) - {"filters", "packet_id", "version"} or len(ts) != 1 or not _token(ts[0]):
            return None
        return f"unsubscribe {ts[0]}"
    if op == "publish":
        if set(a) - {"topic", "payload", "qos", "retain", "version", "packet_id"} or int(a.get("qos", 0)) not in (0, 1):
            return None
        if not _token(a.get("topic")):
            return None
        payload = a.get("payload", "")
        hx = (payload.encode() if isinstance(payload, str) else bytes(payload)).hex() or "-"
        return f"publish {int(a.get('qos', 0))} {int(bool(a.get('retain')))} {a['topic']} {hx}"
    if op == "disconnect":
        return "disconnect" if not (set(a) - {"version"}) else None
    return None


def client_script(fixture: dict[str, Any], client: str, k: int, t: str, c: str) -> list[str] | None:
    lines = []
    for st in (fixture.get("steps") or [])[:k]:
        if st.get("client", "a") != client or st.get("op") == "sleep":
            continue
        ln = line_for(subst(st, t, c))
        if ln is None:
            return None
        lines.append(ln)
    return lines if lines and lines[0].startswith("connect ") else None


def server_steps(fixture: dict[str, Any], obs: list[dict[str, Any]], client: str, k: int) -> list[dict] | None:
    out = []
    for i, st in enumerate((fixture.get("steps") or [])[:k], 1):
        o = next((x for x in obs if x.get("step") == i), None)
        if o is None:
            return None
        me = (o.get("clients") or {}).get(client)
        if me is not None and "wire" not in me:
            return None
        actor = st.get("client", "a") == client and st.get("op") != "sleep"
        out.append({"actor": actor, "type": PTYPE.get(st.get("op"), "") if actor else "",
                    "wire": (me or {}).get("wire") or [], "closed": bool((me or {}).get("closed_now"))})
    return out


def pair_steps(pair: Any, n_steps: int) -> list[int]:
    out = set()
    for d in [*pair.diffs, *pair.t4_diffs]:
        out.add(d.step if isinstance(d.step, int) else n_steps)
    return sorted(out)


def canon(events: list[Any], t: str, c: str) -> list[Any]:
    text = json.dumps(events, sort_keys=True).replace(t, "{T}").replace(c, "{C}")
    return json.loads(text)


def sh(a: list[str], timeout: int = 120, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(a, capture_output=True, text=True, timeout=timeout, input=stdin)


def lab_up(lab: str) -> str:
    if sh(["docker", "network", "inspect", NET]).returncode != 0:
        nc = sh(["docker", "network", "create", "--internal", "--subnet", SUBNET, NET])
        if nc.returncode != 0:  # e.g. another network already holds the subnet: fail here, not in every lookup
            raise RuntimeError(f"network {NET} ({SUBNET}): {nc.stderr.strip()[-200:]}")
    sh(["docker", "rm", "-f", "pdr-mqtt-server", "pdr-mqtt-consumer"])
    for name, ip, cmd in (("pdr-mqtt-server", SERVER_IP, ["python3", "/lab/replay_server.py"]),
                          ("pdr-mqtt-consumer", CONSUMER_IP, ["sleep", "infinity"])):
        r = sh(["docker", "run", "-d", "--name", name, "--network", NET, "--ip", ip, "-v", f"{lab}:/lab:ro", IMAGE, *cmd])
        if r.returncode != 0:
            raise RuntimeError(f"{name}: {r.stderr.strip()[-200:]}")
    time.sleep(1)
    return (sh(["docker", "exec", "pdr-mqtt-consumer", "cat", "/CONSUMER-VERSION"]).stdout.strip() + " image " +
            sh(["docker", "image", "inspect", "-f", "{{.Id}}", IMAGE]).stdout.strip()[:19])


def ctl(cmd: str) -> str:
    return sh(["docker", "exec", "-i", "pdr-mqtt-server", "python3", "/lab/replay_server.py", "--ctl"], stdin=cmd).stdout


def consume(name: str, script: list[str]) -> list[Any]:
    r = sh(["docker", "exec", "-i", "pdr-mqtt-consumer", *CONSUMERS[name]], stdin="\n".join(script) + "\n", timeout=180)
    out = []
    for ln in r.stdout.splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            out.append({"unparsed": ln[-200:]})
    return out


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
            n = len(fx.get("steps") or [])
            clients = sorted({st.get("client", "a") for st in fx.get("steps") or []})
            todo = []
            for k in pair_steps(p, n):
                for cl in clients:
                    sides = {}
                    for side, impl in (("left", p.left), ("right", p.right)):
                        rpt = rep.representative[(impl, p.fixture)].repeat
                        t, c = prefixes(p.fixture, rpt)
                        script = client_script(fx, cl, k, t, c)
                        srv = server_steps(fx, raw.get((impl, p.fixture, rpt)) or [], cl, k)
                        if script is None or srv is None:
                            break
                        sides[side] = (t, c, script, srv)
                    if len(sides) == 2:
                        todo.append((k, cl, sides))
            if not todo:
                not_replayable.append(f"{p.fixture} {p.left} {p.right}")
                continue
            for k, cl, sides in todo:
                for name in CONSUMERS:
                    for side, (t, c, script, srv) in sides.items():
                        ctl("LOAD " + json.dumps({"steps": srv}))
                        res = {"events": canon(consume(name, script), t, c), "server": json.loads(ctl("COUNT") or "{}")}
                        ln = {"stratum": stratum, "fixture": p.fixture, "left": p.left, "right": p.right,
                              "grade": None if p.grade is None else int(p.grade), "step": f"{k}:{cl}",
                              "consumer": name, "side": side, "result": res}
                        lines.append(ln)
                        fo.write(json.dumps(ln) + "\n")
            log(f"{stratum} {p.fixture} {p.left} {p.right}: {len(todo)} client-steps")
    summ = replay_summary(lines, sampled, not_replayable, tuple(CONSUMERS), SEED)
    summ.update({"run": str(run), "peers": sorted(current), "consumers": version, "not_replayable_pairs": not_replayable})
    (out / "summary.json").write_text(json.dumps(summ, indent=2, sort_keys=True, default=str) + "\n")
    sh(["docker", "rm", "-f", "pdr-mqtt-server", "pdr-mqtt-consumer"])
    log("DONE")
    (out / "DONE").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

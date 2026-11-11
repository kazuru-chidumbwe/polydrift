"""Sieve cell runner: fixtures x repeats x engines -> observation records (manuscript 4.5).

Each fixture run is a fresh container with no network: the fixture directory (script.sieve, msg.eml, meta.json) is
mounted at /fx and the engine wrapper (lab/run.py, with lab/capture.sh) at /lab. The canary, an explicit keep on a
plain message, must compile and keep before and after every repeat; if it fails, that repeat's records are harness
faults and the repeat is redone once (the failed attempt is kept).

Usage (lab host, detached):
  python -m polydrift.cells.sieve.runner --targets sieve_targets.json --lab DIR --out DIR [--repeats 3] [--only k,...]
Writes OUT/records.jsonl, OUT/raw.jsonl, OUT/progress, OUT/DONE.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from polydrift.cells.sieve import adapter
from polydrift.cells.sip.runner import harness_commit
from polydrift.core.record import ObservationRecord, Status, StepRecord

CELL_DIR = Path(__file__).resolve().parent
FIXTURES_PATH = CELL_DIR / "fixtures.yaml"
CANARY = {"script": "keep;\n", "message": "clean.eml"}


def load_all() -> tuple[dict, dict[str, dict[str, Any]]]:
    d = yaml.safe_load(FIXTURES_PATH.read_text(encoding="utf-8"))
    return d.get("defaults", {}), d["fixtures"]


def load_fixtures() -> dict[str, dict[str, Any]]:
    """Fixtures keyed by record id: every fixture, plus `<id>@effect` (same spec, effect-layer refs only)."""
    _, fx = load_all()
    out = {}
    for k, v in fx.items():
        refs = v.get("spec_refs") or []
        out[k] = {**v, "spec_refs": [r for r in refs if r.get("layer") != "effect"]}
        out[k + adapter.EFFECT] = {**v, "spec_refs": [r for r in refs if r.get("layer") == "effect"]}
    return out


def sh(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def run_one(t: dict[str, Any], lab: str, fx: dict[str, Any], defaults: dict) -> dict[str, Any]:
    meta = {k: fx.get(k, defaults.get(k)) for k in ("from", "to", "folders")}
    with tempfile.TemporaryDirectory(prefix="pdsv-") as d:
        dp = Path(d)
        (dp / "script.sieve").write_text(fx["script"], encoding="utf-8")
        shutil.copy(Path(lab) / "messages" / fx["message"], dp / "msg.eml")
        (dp / "meta.json").write_text(json.dumps(meta))
        os.chmod(d, 0o755)
        for p in dp.iterdir():
            os.chmod(p, 0o644)
        args = ["docker", "run", "--rm", "--network", "none", "--cpus=2", "--memory=1g",
                "-e", f"PD_FOLDERS={','.join(meta['folders'])}", "-v", f"{lab}:/lab:ro", "-v", f"{d}:/fx:ro"]
        if t.get("user"):
            args += ["--user", t["user"]]
        args += [t["image"], "python3", "/lab/run.py", "--engine", t["impl"], "--fx", "/fx"]
        r = sh(args)
    line = next((ln for ln in reversed(r.stdout.splitlines()) if ln.startswith("{")), None)
    if not line:
        return {"harness_error": f"exit {r.returncode}: {(r.stderr or r.stdout).strip()[-200:]}"}
    return json.loads(line)


def canary(t: dict[str, Any], lab: str, defaults: dict) -> bool:
    res = run_one(t, lab, CANARY, defaults)
    return res.get("compile") == "ok" and res.get("intent") == ["keep"]


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

    def log(msg: str) -> None:
        with (out / "progress").open("a") as f:
            f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}\n")

    lab = str(Path(a.lab).resolve())
    defaults, fixtures = load_all()
    if a.fixtures:
        fixtures = {k: v for k, v in fixtures.items() if k in set(a.fixtures.split(","))}
    targets = json.loads(Path(a.targets).read_text())
    if a.only:
        targets = [t for t in targets if t["key"] in set(a.only.split(","))]
    spec = adapter.load_spec()
    commit = harness_commit() or os.environ.get("POLYDRIFT_COMMIT", "")
    log(f"start: {len(targets)} engines x {len(fixtures)} fixtures x {a.repeats} repeats, harness {commit}")
    for t in targets:
        image_id = sh(["docker", "image", "inspect", "-f", "{{.Id}}", t["image"]]).stdout.strip()
        for r in range(1, a.repeats + 1):
            for attempt in (1, 2):
                ok_start = canary(t, lab, defaults)
                batch = [(fid, run_one(t, lab, fx, defaults) if ok_start else {}) for fid, fx in fixtures.items()]
                ok_end = ok_start and canary(t, lab, defaults)
                final = (ok_start and ok_end) or attempt == 2
                with (out / ("raw.jsonl" if final else "raw-failed.jsonl")).open("a") as rawf, \
                        (out / ("records.jsonl" if final else "records-failed.jsonl")).open("a") as recf:
                    for fid, res in batch:
                        rawf.write(json.dumps({"impl": t["key"], "repeat": r, "fixture": fid, "image_id": image_id,
                                               "canary": [ok_start, ok_end], "res": res}) + "\n")
                        if ok_start and ok_end:
                            recs = adapter.to_records(res, t["key"], fid, spec=spec, repeat=r,
                                                      impl_version=t.get("version", ""), harness_commit=commit)
                        else:
                            recs = [ObservationRecord(cell="sieve", impl=t["key"], impl_version=t.get("version", ""),
                                                      fixture=fid, repeat=r, harness_commit=commit,
                                                      steps=(StepRecord(status=Status.HARNESS_FAULT,
                                                                        detail="canary failed"),))]
                        for rec in recs:
                            recf.write(rec.to_json() + "\n")
                log(f"{t['key']} repeat {r} attempt {attempt}: canary start={ok_start} end={ok_end}, "
                    f"{len(batch)} fixtures, image {image_id[:19]}")
                if final:
                    break
    log("DONE")
    (out / "DONE").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

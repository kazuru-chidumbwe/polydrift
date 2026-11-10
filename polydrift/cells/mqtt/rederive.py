"""Re-derive observation records from a run's raw.jsonl with the current adapter (no broker contact).

Usage: python -m polydrift.cells.mqtt.rederive RUN_DIR [--out records-rederived.jsonl]
The runner keeps every driver observation, so an adapter correction is applied to recorded behaviour, not to a new run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from polydrift.cells.mqtt import adapter
from polydrift.cells.mqtt.runner import load_fixtures
from polydrift.core.record import ObservationRecord, Status, StepRecord


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--out", default="records-rederived.jsonl")
    ap.add_argument("--commit", default="")
    a = ap.parse_args()
    run = Path(a.run)
    fx = load_fixtures()
    spec = adapter.load_spec()
    n = 0
    with (run / a.out).open("w", newline="\n") as out:
        for line in (run / "raw.jsonl").open():
            r = json.loads(line)
            f = fx[r["fixture"]]
            tp = f"pd/{r['fixture']}/r{r['repeat']}/"
            if all(r["canary"]):
                rec = adapter.to_record(r["obs"], r["impl"], r["fixture"], spec=spec, prefix=tp,
                                        version=int(f.get("version", 5)), repeat=r["repeat"], harness_commit=a.commit,
                                        aggregate_deliveries=bool(f.get("aggregate_deliveries")))
            else:
                rec = ObservationRecord(cell="mqtt", impl=r["impl"], fixture=r["fixture"], repeat=r["repeat"],
                                        harness_commit=a.commit,
                                        steps=(StepRecord(status=Status.HARNESS_FAULT, detail="canary failed"),))
            out.write(rec.to_json() + "\n")
            n += 1
    print(f"{n} records -> {run / a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

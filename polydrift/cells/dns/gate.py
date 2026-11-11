"""DNS smoke gate and partition summary (manuscript 4.4: a divergence on a positive control stops the cell).

Usage: python -m polydrift.cells.dns.gate RECORDS.jsonl [--tier 2]
Prints, per fixture, the partition of resolvers into behaviour classes at the tier, the step and axis where classes
first differ, and the verdict of the gate: PASS when every control fixture has one class.
"""

from __future__ import annotations

import argparse
import collections

import yaml
from pathlib import Path

from polydrift.cells.dns.runner import load_fixtures
from polydrift.core.oracle import behaviour_key, partition
from polydrift.core.record import Status, Tier, read_jsonl


def first_difference(keys: list[tuple]) -> str:
    for i in range(max(len(k) for k in keys)):
        vals = {k[i] if i < len(k) else None for k in keys}
        if len(vals) > 1:
            items = [v for v in vals if v is not None]
            return f"step {items[0][0]} {items[0][1]}" if items else "length"
    return "-"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("records")
    ap.add_argument("--tier", type=int, default=2)
    ap.add_argument("--drift", action="store_true",
                    help="drift run: check each control within an implementation family, not across peers")
    a = ap.parse_args()
    tier = Tier(a.tier)
    recs = read_jsonl(a.records)
    fx = load_fixtures()
    by = collections.defaultdict(list)
    for r in recs:
        if r.repeat == 1:
            by[r.fixture].append(r)
    faults = sorted({(r.impl, r.fixture) for r in recs if any(s.status == Status.HARNESS_FAULT for s in r.steps)})
    traced = yaml.safe_load((Path(__file__).with_name("gate_traced.yaml")).read_text(encoding="utf-8"))["traced"]
    ok_minority = collections.defaultdict(set)
    for t in traced:
        for f in t["fixtures"]:
            ok_minority[f] |= set(t["impls"])
    bad_controls = []
    for fid in fx:
        rs = by.get(fid, [])
        parts = partition(rs, tier)
        keys = {r.impl: behaviour_key(r, tier) for r in rs if not any(s.status == Status.HARNESS_FAULT for s in r.steps)}
        where = first_difference(list(keys.values())) if len(parts) > 1 else "-"
        kind = fx[fid].get("kind", "")
        print(f"{fid:42s} {kind:9s} {len(parts)} classes  {where:28s} " + " | ".join(",".join(sorted(p)) for p in parts))
        if kind == "control" and a.drift:
            fam = collections.defaultdict(list)
            for r in rs:
                fam[r.impl.rsplit("-", 1)[0]].append(r)
            for name, frs in fam.items():
                fparts = partition(frs, tier)
                if len(fparts) > 1 and not set().union(*fparts[1:]) <= ok_minority[fid]:
                    bad_controls.append(f"{fid}[{name}]")
        elif kind == "control" and len(parts) > 1:
            minority = set().union(*parts[1:])
            if not minority <= ok_minority[fid]:
                bad_controls.append(fid)
    print(f"\nharness faults: {len(faults)} {faults[:10]}")
    print(f"GATE {'PASS' if not bad_controls and not faults else 'FAIL'}: controls with more than one class: {bad_controls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

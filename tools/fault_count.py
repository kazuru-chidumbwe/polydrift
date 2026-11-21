"""Fault candidates for upstream reporting: per current implementation, the MUST and SHOULD requirements its records
break (core.label.inside against each fixture's allowed values), one row per (implementation, requirement).

A row is a candidate, not a fault: it still needs tracing (harness cause ruled out) and a reproduction on the latest
release. `breakers` says how many current peers break the same requirement: a lone or minority break is the usual
report; a requirement every peer breaks is checked against the predicate first (the clause may be read wrongly).

Usage (lab host): python3 tools/fault_count.py --cell CELL --out OUT.csv RUN_DIR [RUN_DIR ...]
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

from polydrift.core.label import conflicting_refs, inside, labels, strength
from polydrift.core.record import Status, read_jsonl
from polydrift.core.repeat import MIN_REPEATS, flaky_triples
from polydrift.report import cell_parts, family_version, _vkey


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("runs", nargs="+")
    a = ap.parse_args()
    spec, _, fixtures = cell_parts(a.cell)
    records = []
    for r in a.runs:
        records += [x for x in read_jsonl(str(Path(r) / "records.jsonl"))
                    if not any(s.status == Status.HARNESS_FAULT for s in x.steps)]
    rep = flaky_triples(records, min_repeats=MIN_REPEATS)
    # current peers: the highest version of each family (role variants keep their own key)
    best: dict[str, tuple] = {}
    for (impl, _fid), rec in rep.representative.items():
        fam, ver = family_version(rec)
        if fam not in best or _vkey(ver) > _vkey(best[fam][1]):
            best[fam] = (impl, ver)
    current = {impl for impl, _ in best.values()}
    broken: dict[tuple, dict[str, str]] = defaultdict(dict)
    for (impl, fid), rec in rep.representative.items():
        fx = fixtures.get(fid)
        if impl not in current or fx is None:
            continue
        bad = conflicting_refs(fx.spec_refs)
        for i, ref in enumerate(fx.spec_refs):
            s = strength(ref.get("keyword", ""))
            if i in bad or s not in ("MUST", "SHOULD") or not labels(ref) or (impl, fid, ref.get("axis")) in rep.flaky:
                continue  # comparison-only rows and axes unstable across repeats are never counted
            if not inside(rec, ref, spec.refusal_class):
                broken[(ref.get("req_id") or ref.get("native") or "?", s, fid)][impl] = str(ref.get("note", ""))[:120]
    rows = []
    for (rid, s, fid), impls in sorted(broken.items()):
        for impl in sorted(impls):
            rows.append({"cell": a.cell, "impl": impl, "req_id": rid, "keyword": s, "fixture": fid,
                         "breakers": len(impls), "peers": len(current),
                         "shape": "unanimous" if len(impls) == len(current) else "lone" if len(impls) == 1 else "minority"
                         if len(impls) * 2 < len(current) else "majority", "note": impls[impl]})
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["cell"])
        w.writeheader()
        w.writerows(rows)
    by = defaultdict(lambda: defaultdict(int))
    for r in rows:
        by[r["impl"]][(r["keyword"], r["shape"])] += 1
    print(f"{a.cell}: {len(current)} current peers, {len(rows)} (implementation, requirement) breaks")
    for impl in sorted(by):
        print(f"  {impl}: " + ", ".join(f"{k[0]} {k[1]} {n}" for k, n in sorted(by[impl].items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

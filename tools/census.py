"""Fixture census: which testable ledger rows each cell's fixtures exercise (manuscript 4.4).

Reads the ledgers (default: the Atlas ledger folder, or --ledgers) and every cell's fixtures.yaml, and reports per cell
the testable rows covered by at least one spec_ref, and the uncovered rows by section, keyword and role.
A row counts as covered when a fixture's spec_ref names its req_id (or, for MQTT, its native_id).
`merged` rows are covered through the row they merge into.

Each covered row also gets an evidence class from its refs: `labelled` (at least one ref states `allowed`, so the
labeller gives a verdict), else `option` (every ref has `basis: option`: a MAY or free choice, any outcome conforms
and the ref records which one the implementation took), else `conditional` (`basis: conditional`: the row binds only
after an implementation choice that is not visible on the wire), else `untriaged` (comparison only with no stated
basis). `basis: not-observable` (the outcome the clause fixes cannot be told apart on the fixture's axis within
the fixture) and `basis: not-applicable` (the clause concerns a role or feature the implementations do not have
here) are the other two stated bases. The freeze requires zero untriaged rows.

    python tools/census.py --ledgers <dir> [--cell mqtt] [--uncovered out.csv] [--json out.json]
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CELLS = ["sip", "dns", "mqtt", "ldap", "sieve", "jmap"]
LEDGER_FILES = {"sip": ["sip.ledger.csv", "sipx.ledger.csv"]}


def load_ledger(ledgers: pathlib.Path, cell: str) -> list[dict]:
    rows = []
    for name in LEDGER_FILES.get(cell, [f"{cell}.ledger.csv"]):
        with open(ledgers / name, newline="", encoding="utf-8") as fh:
            rows.extend(csv.DictReader(fh))
    return rows


# cells whose ledger rows are also exercised by another cell's fixtures (the SIP behaviour cell sipb shares the
# SIP ledger with the parser cell)
EXTRA_FIXTURE_CELLS = {"sip": ["sipb"]}


BASES = ("option", "conditional", "not-observable", "not-applicable")


def fixture_refs(cell: str, kinds: dict[str, set[str]] | None = None) -> dict[str, set[str]]:
    """Map of ledger key (req_id or native id) to the fixtures that name it; `kinds` collects each key's ref kinds
    (labelled, option, conditional, untriaged)."""
    out: dict[str, set[str]] = collections.defaultdict(set)
    for c in [cell, *EXTRA_FIXTURE_CELLS.get(cell, [])]:
        path = ROOT / "polydrift" / "cells" / c / "fixtures.yaml"
        if path.exists():
            _collect(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, out, c, kinds)
    return out


def ref_kind(ref: dict) -> str:
    if "allowed" in ref:
        return "labelled"
    return ref.get("basis") if ref.get("basis") in BASES else "untriaged"


def _collect(data: dict, out: dict[str, set[str]], cell: str, kinds: dict[str, set[str]] | None = None) -> None:
    for fid, fx in (data.get("fixtures") or {}).items():
        for ref in (fx or {}).get("spec_refs") or []:
            for key in ("req_id", "native"):
                if ref.get(key):
                    out[ref[key]].add(f"{cell}:{fid}")
                    if kinds is not None:
                        kinds.setdefault(ref[key], set()).add(ref_kind(ref))


def evidence_class(ks: set[str]) -> str:
    """A row's evidence class: the strongest kind among its refs."""
    for k in ("labelled", "untriaged", "conditional", "option", "not-observable", "not-applicable"):
        if k in ks:
            return k
    return "untriaged"


def census(ledgers: pathlib.Path, cell: str) -> dict:
    rows = load_ledger(ledgers, cell)
    kinds: dict[str, set[str]] = {}
    refs = fixture_refs(cell, kinds)
    by_id = {r["req_id"]: r for r in rows}
    known = set(by_id) | {r["native_id"] for r in rows if r.get("native_id")}
    testable = [r for r in rows if r["testable"] == "yes"]
    covered, uncovered = [], []
    for r in testable:
        hit = refs.get(r["req_id"], set()) | (refs.get(r["native_id"], set()) if r.get("native_id") else set())
        (covered if hit else uncovered).append(r)
    dangling = sorted(k for k in refs if k not in known)
    evidence = collections.Counter(
        evidence_class(kinds.get(r["req_id"], set()) | (kinds.get(r["native_id"], set()) if r.get("native_id")
                                                         else set())) for r in covered)
    def tally(rs, key):
        return dict(collections.Counter(key(r) for r in rs).most_common())
    role = lambda r: next((n[5:] for n in (r.get("notes") or "").split(";") if n.strip().startswith("role:")), "-")
    return {
        "cell": cell,
        "testable": len(testable),
        "covered": len(covered),
        "uncovered": len(uncovered),
        "dangling_refs": dangling,
        "evidence": {k: evidence.get(k, 0) for k in ("labelled", *BASES, "untriaged")},
        "uncovered_by_section": tally(uncovered, lambda r: f'{r["doc"]} {r["section"].split(".")[0]}'),
        "uncovered_by_keyword": tally(uncovered, lambda r: r["keyword"]),
        "uncovered_by_role": tally(uncovered, role),
        "_uncovered_rows": uncovered,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledgers", type=pathlib.Path, required=True)
    ap.add_argument("--cell", choices=CELLS, action="append")
    ap.add_argument("--uncovered", type=pathlib.Path, help="write uncovered testable rows (all cells) to this CSV")
    ap.add_argument("--json", type=pathlib.Path)
    a = ap.parse_args(argv)
    results = [census(a.ledgers, c) for c in (a.cell or CELLS)]
    tot_t = sum(r["testable"] for r in results)
    tot_c = sum(r["covered"] for r in results)
    for r in results:
        print(f'{r["cell"]:6} covered {r["covered"]:4} of {r["testable"]:4} testable'
              f'  uncovered {r["uncovered"]:4}  dangling refs {len(r["dangling_refs"])}'
              f'  evidence {" ".join(f"{k}={v}" for k, v in r["evidence"].items() if v)}')
    print(f'total  covered {tot_c:4} of {tot_t:4}')
    if a.uncovered:
        with open(a.uncovered, "w", newline="", encoding="utf-8") as fh:
            w = None
            for r in results:
                for row in r["_uncovered_rows"]:
                    if w is None:
                        w = csv.DictWriter(fh, fieldnames=list(row))
                        w.writeheader()
                    w.writerow(row)
    if a.json:
        a.json.write_text(json.dumps([{k: v for k, v in r.items() if not k.startswith("_")} for r in results],
                                     indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

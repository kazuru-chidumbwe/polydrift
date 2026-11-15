"""RQ5 recall (manuscript 4.6): does the oracle re-detect known divergences at the affected versions, and do they
disappear at the fixed versions?

A case comes from the frozen list in ``polydrift/rq5/cases.yaml`` (selection rule 4.6). It names the cell, the
implementation family, its affected and fixed versions, the known-bug fixtures (each cell's ``fixtures_rq5.yaml``,
a set kept out of the RQ3 counts), the axes on which the reported behaviour shows, and, where the report states it,
the value the affected version is reported to give (``known``).

The run of a case holds the affected and fixed versions together with the current peers of the cell (the highest
version of every other family). Per version and fixture, the version is compared with each peer by the same oracle
as RQ3 (``compare_pair``, with the flaky triples of the run). Only differences on the case's axes count, so an
unrelated difference that the fixture also happens to show cannot make a case look recalled.

* Reproduced: every ``known`` value is observed at every affected version. A case that is not reproduced says the
  lab input or environment did not trigger the reported bug; it is reported apart from a miss of the oracle.
* Detected (recall): every affected version diverges from at least one peer on a case axis.
* Fix confirmed: detected, and no case-axis signature of the affected versions (fixture, peer, step, axis, own
  value, peer value) appears at any fixed version. A fixed version may still diverge on a case axis with a different
  value (``still_divergent``): the reported behaviour is gone, the version has not joined its peers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from polydrift.core.label import observed
from polydrift.core.oracle import Outcome, compare_pair
from polydrift.core.record import ObservationRecord, Status, freeze_value
from polydrift.core.repeat import MIN_REPEATS, flaky_triples

CASES_PATH = Path(__file__).resolve().parent / "rq5" / "cases.yaml"
STATUSES = ("fixed", "unfixed", "fix-unconfirmed")


@dataclass(frozen=True)
class Case:
    id: str
    cell: str
    family: str
    fixtures: tuple[str, ...]
    axes: tuple[str, ...]
    affected: tuple[str, ...]
    fixed: tuple[str, ...] = ()
    status: str = "fixed"
    known: tuple[dict[str, Any], ...] = ()
    source: str = ""
    clause: str = ""
    note: str = ""

    @classmethod
    def from_dict(cls, cid: str, d: dict[str, Any]) -> "Case":
        status = d.get("status", "fixed")
        if status not in STATUSES:
            raise ValueError(f"{cid}: status {status!r}")
        fixed = tuple(str(v) for v in d.get("fixed") or ())
        if status == "fixed" and not fixed:
            raise ValueError(f"{cid}: a fixed case names its fixed versions")
        fixtures = tuple(d["fixtures"])
        known = tuple(d.get("known") or ())
        for k in known:
            if k["fixture"] not in fixtures or k["axis"] not in d["axes"]:
                raise ValueError(f"{cid}: known value {k} is outside the case's fixtures or axes")
        return cls(id=cid, cell=d["cell"], family=d["family"], fixtures=fixtures, axes=tuple(d["axes"]),
                   affected=tuple(str(v) for v in d["affected"]), fixed=fixed, status=status, known=known,
                   source=d.get("source", ""), clause=d.get("clause", ""), note=d.get("note", ""))


def load_cases(path: Path = CASES_PATH) -> dict[str, Case]:
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return {cid: Case.from_dict(cid, c) for cid, c in (d.get("cases") or {}).items()}


@dataclass
class VersionResult:
    version: str
    impl: str | None
    run: bool = False
    divergent_peers: list[str] = field(default_factory=list)
    signatures: set[tuple] = field(default_factory=set)
    known_reproduced: bool | None = None
    flaky_case_axes: list[str] = field(default_factory=list)
    values: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "impl": self.impl, "run": self.run,
                "divergent_peers": sorted(self.divergent_peers), "signatures": len(self.signatures),
                "known_reproduced": self.known_reproduced, "flaky_case_axes": sorted(self.flaky_case_axes),
                "values": self.values}


def _family_versions(records: Iterable[ObservationRecord]) -> dict[str, dict[str, str]]:
    from polydrift.report import family_version

    fam: dict[str, dict[str, str]] = {}
    for r in records:
        f, v = family_version(r)
        fam.setdefault(f, {})[v] = r.impl
    return fam


def _norm(v: str) -> str:
    return v.lstrip("v")


def evaluate(
    records: Iterable[ObservationRecord],
    case: Case,
    *,
    current: set[str] | None = None,
    refusal_class: str | None = None,
    min_repeats: int = MIN_REPEATS,
) -> dict[str, Any]:
    from polydrift.report import _vkey

    records = [r for r in records if r.fixture in case.fixtures]
    faults = [r for r in records if any(s.status == Status.HARNESS_FAULT for s in r.steps)]
    records = [r for r in records if not any(s.status == Status.HARNESS_FAULT for s in r.steps)]
    fam = _family_versions(records)
    if current is None:
        current = {vs[max(vs, key=_vkey)] for f, vs in fam.items() if f != case.family}
    current = {k for k in current if k not in fam.get(case.family, {}).values()}
    rep = flaky_triples(records, min_repeats=min_repeats) if records else None
    axes = set(case.axes)

    def version_result(v: str) -> VersionResult:
        impl = fam.get(case.family, {}).get(_norm(v))
        res = VersionResult(version=v, impl=impl)
        if impl is None or rep is None:
            return res
        known_ok: list[bool] = []
        for fid in case.fixtures:
            mine = rep.representative.get((impl, fid))
            if mine is None:
                continue
            res.run = True
            res.flaky_case_axes += [f"{fid}:{ax}" for (i, fx, ax) in rep.flaky if i == impl and fx == fid and ax in axes]
            for k in case.known:
                if k["fixture"] == fid:
                    got = observed(mine, k["axis"], k.get("step", 1), refusal_class)
                    res.values[f"{fid}:{k['axis']}@{k.get('step', 1)}"] = got
                    known_ok.append(got == freeze_value(k["value"]))
            for peer in sorted(current):
                other = rep.representative.get((peer, fid))
                if other is None:
                    continue
                p = compare_pair(mine, other, flaky=rep.flaky, refusal_class=refusal_class)
                if p.outcome != Outcome.DIVERGENCE:
                    continue
                hits = tuple(sorted((str(d.step), d.axis, repr(d.left), repr(d.right))
                                    for d in p.diffs if d.axis in axes))
                if hits:
                    res.divergent_peers.append(peer)
                    res.signatures.add((fid, peer, hits))
        res.divergent_peers = sorted(set(res.divergent_peers))
        res.known_reproduced = None if not case.known else (bool(known_ok) and all(known_ok))
        return res

    aff = [version_result(v) for v in case.affected]
    fix = [version_result(v) for v in case.fixed]
    run_aff = [r for r in aff if r.run]
    detected = bool(run_aff) and len(run_aff) == len(aff) and all(r.divergent_peers for r in aff)
    reproduced = None if not case.known else (bool(run_aff) and all(r.known_reproduced for r in aff))
    aff_sigs = set().union(*(r.signatures for r in aff)) if aff else set()
    fix_run = bool(fix) and all(r.run for r in fix)
    gone = fix_run and all(not (r.signatures & aff_sigs) for r in fix)
    if not run_aff:
        outcome = "not-run"
    elif detected:
        if case.status == "fixed":
            outcome = "recalled, fix confirmed" if gone else ("recalled, fix not confirmed" if fix_run
                                                               else "recalled, fixed version not run")
        else:
            outcome = "recalled"
    elif reproduced is False:
        outcome = "not reproduced"
    else:
        outcome = "missed"
    return {
        "case": case.id, "cell": case.cell, "family": case.family, "status": case.status,
        "fixtures": list(case.fixtures), "axes": list(case.axes), "peers": sorted(current),
        "harness_faults": len(faults), "reproduced": reproduced, "detected": detected,
        "fix_confirmed": bool(detected and case.status == "fixed" and gone),
        "still_divergent": {r.version: r.divergent_peers for r in fix if r.divergent_peers},
        "affected": [r.to_dict() for r in aff], "fixed": [r.to_dict() for r in fix], "outcome": outcome,
        "source": case.source, "clause": case.clause,
    }


def summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Recall over cases run (not reproduced counted apart); fix confirmation over detected fixed cases."""
    ran = [r for r in results if r["outcome"] != "not-run"]
    judged = [r for r in ran if r["outcome"] != "not reproduced"]
    det = [r for r in judged if r["detected"]]
    fixed = [r for r in det if r["status"] == "fixed"]
    return {
        "cases": len(results), "run": len(ran), "not_reproduced": len(ran) - len(judged),
        "detected": len(det), "recall": None if not judged else len(det) / len(judged),
        "fixed_cases_detected": len(fixed), "fix_confirmed": sum(r["fix_confirmed"] for r in fixed),
        "fix_confirmation": None if not fixed else sum(r["fix_confirmed"] for r in fixed) / len(fixed),
    }

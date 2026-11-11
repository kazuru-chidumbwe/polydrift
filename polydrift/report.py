"""Cell report (manuscript 3.3 to 3.7, RQ3 and RQ4): one protocol-independent analysis of a cell's records.

* Peers. RQ3 compares the current version of each implementation family (the highest version present, or the
  versions named in `current`). Older versions are drift points, not peers.
* Outcomes and labels. Every pair of peers per fixture gets the oracle outcome (3.4); every divergence gets its
  fault class and consequence from the fixture's predicates (3.5, label.py). Unanimous MUST violations are listed.
* Baselines. Raw-byte and coarse-outcome oracle counts over the same peer pairs (RQ2, baselines.py).
* Drift (3.7). For each family with more than one version: the divergence signatures of every version against the
  current peers, introduced / fixed / persistent between consecutive versions, and per-fixture convergence.

The spec hash of the cell (freeze.bundle) is recorded; there is no deposit check (dropped 6 Nov 2026: the run of
record is the campaign at the frozen harness commit).
"""

from __future__ import annotations

import dataclasses
import importlib
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from polydrift.core.baselines import coarse_oracle, raw_oracle
from polydrift.core.drift import convergence, drift, signatures
from polydrift.core.freeze import bundle
from polydrift.core.label import consequence, fault_class, unanimous_violations
from polydrift.core.oracle import Outcome, compare_all, compare_pair, partition, partition_shape
from polydrift.core.record import ObservationRecord, Status, Tier
from polydrift.core.repeat import MIN_REPEATS, flaky_triples
from polydrift.core.spec import CellSpec, FixtureSpec

CELLS = ("sip", "dns", "mqtt", "ldap", "jmap", "sieve")


def _sip_fixtures() -> dict[str, dict[str, Any]]:
    from polydrift.fixtures import FIXTURE_INDEX
    d = yaml.safe_load((Path(__file__).parent / "cells" / "sip" / "fixtures.yaml").read_text(encoding="utf-8"))
    by_stem = d.get("fixtures") or {}
    return {fid: by_stem.get(Path(name).stem, {}) for fid, name in FIXTURE_INDEX.items()}


def cell_parts(cell: str) -> tuple[CellSpec, Path, dict[str, FixtureSpec]]:
    adapter = importlib.import_module(f"polydrift.cells.{cell}.adapter")
    spec = adapter.load_spec()
    cell_dir = Path(adapter.__file__).resolve().parent
    if cell == "sip":
        raw = _sip_fixtures()
    else:
        raw = importlib.import_module(f"polydrift.cells.{cell}.runner").load_fixtures()
    fx = {k: FixtureSpec.from_dict(_security_kind({**v, "id": k, "cell": cell})) for k, v in raw.items()}
    return spec, cell_dir, fx


def _security_kind(d: dict[str, Any]) -> dict[str, Any]:
    """Fixtures written with `kind: security` (DNS, MQTT, LDAP, JMAP, Sieve) as label.py expects them: a
    `security:<id>` tag on the fixture and the same property on each of its predicates."""
    if d.get("kind") != "security":
        return d
    prop = d["id"].split("@")[0]
    refs = [{**r, "security": r.get("security", prop)} for r in d.get("spec_refs") or []]
    return {**d, "tags": [*(d.get("tags") or []), f"security:{prop}"], "spec_refs": refs}


_VER = re.compile(r"^(.*?)-(v?\d[\w.]*)$")


def family_version(rec: ObservationRecord) -> tuple[str, str]:
    m = _VER.match(rec.impl)
    if m:
        return m.group(1), m.group(2).lstrip("v")
    return rec.impl, rec.impl_version or ""


def _vkey(v: str) -> tuple:
    return tuple((0, int(x)) if x.isdigit() else (1, x) for x in re.split(r"[.\-]", v))


def _empty_fixture(fid: str, cell: str) -> FixtureSpec:
    return FixtureSpec.from_dict({"id": fid, "cell": cell})


def report(
    records: Iterable[ObservationRecord],
    cell: str,
    *,
    current: set[str] | None = None,
    min_repeats: int = MIN_REPEATS,
    parts: Callable[[str], tuple[CellSpec, Path, dict[str, FixtureSpec]]] = cell_parts,
) -> dict[str, Any]:
    records = list(records)
    # A harness fault is an instrument failure, not behaviour (3.5 rule 1): counted, never compared.
    faults = [r for r in records if any(s.status == Status.HARNESS_FAULT for s in r.steps)]
    records = [r for r in records if not any(s.status == Status.HARNESS_FAULT for s in r.steps)]
    spec, cell_dir, fixtures = parts(cell)
    rc = spec.refusal_class
    rep = flaky_triples(records, min_repeats=min_repeats)

    fam: dict[str, dict[str, str]] = defaultdict(dict)  # family -> version -> impl key
    for r in records:
        f, v = family_version(r)
        fam[f][v] = r.impl
    if current is None:
        current = {vs[max(vs, key=_vkey)] for vs in fam.values()}

    by_fixture: dict[str, dict[str, ObservationRecord]] = defaultdict(dict)
    for (impl, fixture), r in rep.representative.items():
        by_fixture[fixture][impl] = r

    summary: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    conseq: Counter[str] = Counter()
    baselines: Counter[str] = Counter()
    listed: list[dict[str, Any]] = []
    partitions: dict[str, dict[str, list[list[str]]]] = {}
    unanimous: dict[str, list[str]] = {}
    for fid, impls in sorted(by_fixture.items()):
        fx = fixtures.get(fid) or _empty_fixture(fid, cell)
        peers = [r for k, r in sorted(impls.items()) if k in current]
        partitions[fid] = {f"T{int(t)}": [sorted(c) for c in partition(peers, t, flaky=rep.flaky, refusal_class=rc)]
                           for t in (Tier.T1, Tier.T2, Tier.T3)}
        uv = unanimous_violations(peers, fx, refusal_class=rc)
        if uv:
            unanimous[fid] = uv
        by_impl = {r.impl: r for r in peers}
        for p in compare_all(peers, flaky=rep.flaky, refusal_class=rc):
            left, right = by_impl[p.left], by_impl[p.right]
            baselines[f"raw:{raw_oracle(left, right)}"] += 1
            baselines[f"coarse:{coarse_oracle(left, right)}"] += 1
            key = p.outcome.value if p.outcome != Outcome.DIVERGENCE else f"divergence-T{int(p.grade)}"
            summary[key] += 1
            fc = fault_class(p, left, right, fx, refusal_class=rc)
            if fc is not None:
                labels[fc.value] += 1
            cq = consequence(p, left, right, fx, refusal_class=rc)
            if cq is not None:
                conseq[cq.primary] += 1
            if fc is not None and (fc.value == "violation" or (cq and cq.security)):
                listed.append({"fixture": fid, "left": p.left, "right": p.right, "grade": int(p.grade),
                               "fault_class": fc.value, "consequence": cq.primary if cq else None,
                               "axes": sorted({d.axis for d in p.diffs})})

    drift_out: dict[str, Any] = {}
    for f, vs in sorted(fam.items()):
        if len(vs) < 2:
            continue
        cur_peers = {k for k in current if family_version_key(k) != f}
        order = sorted(vs, key=_vkey)
        sigs: dict[str, set] = {}
        sigs_all: dict[str, set] = {}
        for v in order:
            impl = vs[v]
            pairs = []
            for fid, impls in by_fixture.items():
                if impl not in impls:
                    continue
                for peer in sorted(cur_peers & set(impls)):
                    pairs.append(compare_pair(impls[impl], impls[peer], flaky=rep.flaky, refusal_class=rc))
            sigs_all[v] = signatures(pairs, impl)
            sigs[v] = signatures(at_tier(pairs, Tier.T2), impl)
        steps = [drift(f, a, sigs[a], b, sigs[b]).counts() | {"from": a, "to": b}
                 for a, b in zip(order, order[1:])]
        conv: Counter[str] = Counter()
        for a, b in zip(order, order[1:]):
            for fid, impls in by_fixture.items():
                if vs[a] in impls and vs[b] in impls:
                    peers = [impls[k] for k in cur_peers if k in impls]
                    conv[convergence(impls[vs[a]], impls[vs[b]], peers, Tier.T2, rc)] += 1
        steps_all = [drift(f, a, sigs_all[a], b, sigs_all[b]).counts() | {"from": a, "to": b}
                     for a, b in zip(order, order[1:])]
        drift_out[f] = {"versions": order, "signatures_T2": {v: len(s) for v, s in sigs.items()},
                        "steps_T2": steps, "signatures_all": {v: len(s) for v, s in sigs_all.items()},
                        "steps_all": steps_all, "convergence_T2": dict(sorted(conv.items()))}

    return {
        "cell": cell,
        "spec_hash": bundle(cell_dir, spec.cell).spec_hash,
        "harness_commits": sorted({r.harness_commit for r in records}),
        "impls": sorted({r.impl for r in records}),
        "current": sorted(current),
        "repeat": {**rep.counts(), "flaky": sorted(list(t) for t in rep.flaky)},
        "harness_faults": {"records": len(faults), "by_impl": dict(Counter(r.impl for r in faults))},
        "summary": dict(sorted(summary.items())),
        "fault_classes": dict(sorted(labels.items())),
        "consequences": dict(sorted(conseq.items())),
        "violations_and_security": listed,
        "unanimous_violations": unanimous,
        "baselines": dict(sorted(baselines.items())),
        "partition_shapes": {fx: {t: list(partition_shape([frozenset(c) for c in cl])) for t, cl in p.items()}
                             for fx, p in partitions.items()},
        "partitions": partitions,
        "drift": drift_out,
    }


def at_tier(pairs: Iterable[Any], max_tier: Tier) -> list[Any]:
    """Divergences restricted to diffs at or below `max_tier`. A Tier 3 detail that every fixture carries (an
    MQTT CONNACK capability list, for example) would otherwise enter every fixture's signature and count one
    release change once per fixture."""
    out = []
    for p in pairs:
        if p.outcome != Outcome.DIVERGENCE:
            continue
        keep = [d for d in p.diffs if d.tier <= max_tier]
        if keep:
            out.append(dataclasses.replace(p, diffs=keep, grade=min(d.tier for d in keep)))
    return out


def family_version_key(impl: str) -> str:
    m = _VER.match(impl)
    return m.group(1) if m else impl

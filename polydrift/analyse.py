"""Cell analysis: records from every implementation -> outcomes, partitions,
repeatability and baselines. Protocol-independent apart from the CellSpec
the caller passes in."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from polydrift.core.baselines import coarse_oracle, raw_oracle
from polydrift.core.freeze import bundle, check_deposited
from polydrift.core.oracle import Outcome, compare_all, partition, partition_shape
from polydrift.core.record import ObservationRecord, Tier
from polydrift.core.repeat import MIN_REPEATS, flaky_triples
from polydrift.core.spec import CellSpec


def analyse_cell(
    records: list[ObservationRecord],
    spec: CellSpec,
    cell_dir: str | Path,
    *,
    deposits: str | Path,
    provisional: bool = False,
    min_repeats: int = MIN_REPEATS,
) -> dict[str, Any]:
    bundle_ = bundle(cell_dir, spec.cell)
    deposit: dict[str, str] | None = None
    if not provisional:
        deposit = check_deposited(bundle_, deposits)
    rep = flaky_triples(records, min_repeats=min_repeats)
    by_fixture: dict[str, list[ObservationRecord]] = defaultdict(list)
    for (impl, fixture), r in rep.representative.items():
        by_fixture[fixture].append(r)

    pairs = []
    partitions: dict[str, dict[str, list[list[str]]]] = {}
    baselines: Counter[str] = Counter()
    for fixture, recs in sorted(by_fixture.items()):
        for p in compare_all(recs, flaky=rep.flaky, refusal_class=spec.refusal_class):
            pairs.append(p)
        partitions[fixture] = {
            f"T{int(t)}": [sorted(c) for c in partition(recs, t, flaky=rep.flaky, refusal_class=spec.refusal_class)]
            for t in (Tier.T0, Tier.T1, Tier.T2, Tier.T3)
        }
        srt = sorted(recs, key=lambda r: r.impl)
        for i, a in enumerate(srt):
            for b in srt[i + 1 :]:
                baselines[f"raw:{raw_oracle(a, b)}"] += 1
                baselines[f"coarse:{coarse_oracle(a, b)}"] += 1

    summary: Counter[str] = Counter()
    per_pair: dict[str, Counter[str]] = defaultdict(Counter)
    for p in pairs:
        label = p.outcome.value if p.outcome != Outcome.DIVERGENCE else f"divergence-T{int(p.grade)}"
        summary[label] += 1
        per_pair[f"{p.left} vs {p.right}"][label] += 1
        if p.t4_only_agreement:
            summary["t4-only-agreement"] += 1
            per_pair[f"{p.left} vs {p.right}"]["t4-only-agreement"] += 1

    return {
        "cell": spec.cell,
        "provisional": provisional,
        "spec_hash": bundle_.spec_hash,
        "deposit": deposit,
        "harness_commits": sorted({r.harness_commit for r in records}),
        "impls": sorted({r.impl for r in records}),
        "repeat": {**rep.counts(), "flaky": sorted(list(t) for t in rep.flaky)},
        "summary": dict(sorted(summary.items())),
        "per_pair": {k: dict(sorted(v.items())) for k, v in sorted(per_pair.items())},
        "baselines": dict(sorted(baselines.items())),
        "partition_shapes": {
            fx: {t: list(partition_shape([frozenset(c) for c in cl])) for t, cl in parts.items()}
            for fx, parts in partitions.items()
        },
        "partitions": partitions,
        "pairs": [p.to_dict() for p in pairs],
    }

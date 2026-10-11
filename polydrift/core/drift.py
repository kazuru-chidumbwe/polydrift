"""Drift across versions (manuscript 3.7).

A divergence signature is (fixture, other implementation, grade, differing
axes with their canonical value pairs). Value pairs are part of the
signature: an axis whose two sides swap values between versions is a new
disagreement, not the old one persisting.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from polydrift.core.oracle import Outcome, PairResult, behaviour_key
from polydrift.core.record import ObservationRecord, Tier

Signature = tuple[Any, ...]


def _hashable(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_hashable(v) for v in value)
    return value


def signature(pair: PairResult, impl: str) -> Signature:
    """Signature of a divergence from ``impl``'s side (its value first)."""
    if pair.outcome != Outcome.DIVERGENCE:
        raise ValueError("only divergences have signatures")
    if impl == pair.left:
        other, flip = pair.right, False
    elif impl == pair.right:
        other, flip = pair.left, True
    else:
        raise ValueError(f"{impl} is not in this pair")
    axes = tuple(
        sorted(
            (
                str(d.step),
                d.axis,
                _hashable(d.right if flip else d.left),
                _hashable(d.left if flip else d.right),
            )
            for d in pair.diffs
        )
    )
    return (pair.fixture, other, int(pair.grade) if pair.grade is not None else None, axes)


def signatures(pairs: Iterable[PairResult], impl: str) -> set[Signature]:
    return {
        signature(p, impl)
        for p in pairs
        if p.outcome == Outcome.DIVERGENCE and impl in (p.left, p.right)
    }


@dataclass
class DriftStep:
    impl: str
    old_version: str
    new_version: str
    introduced: set[Signature] = field(default_factory=set)
    fixed: set[Signature] = field(default_factory=set)
    persistent: set[Signature] = field(default_factory=set)

    def counts(self) -> dict[str, int]:
        return {
            "introduced": len(self.introduced),
            "fixed": len(self.fixed),
            "persistent": len(self.persistent),
        }


def drift(impl: str, old_version: str, old: set[Signature], new_version: str, new: set[Signature]) -> DriftStep:
    return DriftStep(
        impl=impl,
        old_version=old_version,
        new_version=new_version,
        introduced=new - old,
        fixed=old - new,
        persistent=old & new,
    )


def majority_key(peers: Iterable[ObservationRecord], tier: Tier, refusal_class: str | None = None) -> Any | None:
    """Behaviour key of the largest class among peers; None if there is a tie."""
    counts = Counter(behaviour_key(r, tier, refusal_class=refusal_class) for r in peers)
    if not counts:
        return None
    ranked = counts.most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0]


def convergence(
    old: ObservationRecord,
    new: ObservationRecord,
    peers: Iterable[ObservationRecord],
    tier: Tier = Tier.T3,
    refusal_class: str | None = None,
) -> str:
    """Did a release move the implementation toward its peers' majority class?

    Returns one of: unchanged, converged, diverged, moved (changed but neither
    version is in the majority), no-majority.
    """
    k_old = behaviour_key(old, tier, refusal_class=refusal_class)
    k_new = behaviour_key(new, tier, refusal_class=refusal_class)
    if k_old == k_new:
        return "unchanged"
    maj = majority_key(peers, tier, refusal_class)
    if maj is None:
        return "no-majority"
    if k_new == maj:
        return "converged"
    if k_old == maj:
        return "diverged"
    return "moved"

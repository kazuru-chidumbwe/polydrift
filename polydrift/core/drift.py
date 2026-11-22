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


# Declared lineage relatives (manuscript 4.3): members of a group that behave alike cast one vote in a majority.
# The scope limits a group to fixtures citing requirements with one of the given prefixes (None: every fixture).
# VerneMQ's topic module credits emqtt, the predecessor of EMQX, so the two are one vote on topic names and filters
# (MQTT 5.0 4.7); OpenSIPS shares SER ancestry with Kamailio; Stalwart embeds sieve-rs.
LINEAGE: dict[str, list[tuple[frozenset[str], tuple[str, ...] | None]]] = {
    "mqtt": [(frozenset({"emqx", "vernemq"}), ("MQTT-4.7",))],
    "sipb": [(frozenset({"kamailio", "opensips"}), None)],
    "sip": [(frozenset({"kamailio", "opensips"}), None)],
    "sieve": [(frozenset({"stalwart", "sieve-rs"}), None)],
}


def lineage_groups(cell: str, spec_refs: Iterable[dict[str, Any]]) -> list[frozenset[str]]:
    natives = [str(r.get("native", "")) for r in spec_refs]
    return [g for g, scope in LINEAGE.get(cell, [])
            if scope is None or any(n.startswith(scope) for n in natives)]


def majority_key(peers: Iterable[ObservationRecord], tier: Tier, refusal_class: str | None = None,
                 groups: Iterable[frozenset[str]] = (), family: Any = None) -> Any | None:
    """Behaviour key of the largest class among peers; None if there is a tie. Peers in one lineage group
    (``family(record)`` names the implementation family) that share a key count once."""
    groups = list(groups)
    seen: set[tuple[int, Any]] = set()
    keys = []
    for r in peers:
        k = behaviour_key(r, tier, refusal_class=refusal_class)
        g = next((i for i, grp in enumerate(groups) if family is not None and family(r) in grp), None)
        if g is not None:
            if (g, k) in seen:
                continue
            seen.add((g, k))
        keys.append(k)
    counts = Counter(keys)
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
    groups: Iterable[frozenset[str]] = (),
    family: Any = None,
) -> str:
    """Did a release move the implementation toward its peers' majority class?

    Returns one of: unchanged, converged, diverged, moved (changed but neither
    version is in the majority), no-majority.
    """
    k_old = behaviour_key(old, tier, refusal_class=refusal_class)
    k_new = behaviour_key(new, tier, refusal_class=refusal_class)
    if k_old == k_new:
        return "unchanged"
    maj = majority_key(peers, tier, refusal_class, groups=groups, family=family)
    if maj is None:
        return "no-majority"
    if k_new == maj:
        return "converged"
    if k_old == maj:
        return "diverged"
    return "moved"

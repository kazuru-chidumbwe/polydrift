"""Consumer replay: sampling rule and confusion matrix (manuscript 3.6).

The replay servers and consumers live in each cell's ``consumers/`` folder.
This module holds the protocol-independent parts: the sampling rule fixed in
advance and the confusion matrix of predicted tier against observed
consequence.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable

from polydrift.core.oracle import Outcome, PairResult
from polydrift.core.record import Tier

STRATUM_CAP_T0_T2 = 50
STRATUM_T3 = 50
STRATUM_T4_ONLY = 50


@dataclass
class ReplaySample:
    t0_t2: list[PairResult] = field(default_factory=list)
    t3: list[PairResult] = field(default_factory=list)
    t4_only: list[PairResult] = field(default_factory=list)

    def all(self) -> list[tuple[str, PairResult]]:
        return (
            [("T0-T2", p) for p in self.t0_t2]
            + [("T3", p) for p in self.t3]
            + [("T4-only", p) for p in self.t4_only]
        )


def _key(p: PairResult) -> tuple[str, str, str]:
    return (p.fixture, p.left, p.right)


def sample(pairs: Iterable[PairResult], seed: int) -> ReplaySample:
    """Every T0-T2 divergence up to 50 (uniform sample if more), a uniform
    sample of 50 T3 divergences and of 50 T4-only agreements.

    The seed is part of the deposited oracle specification.
    """
    rng = random.Random(seed)
    pairs = sorted(pairs, key=_key)
    hi = [p for p in pairs if p.outcome == Outcome.DIVERGENCE and p.grade is not None and p.grade <= Tier.T2]
    t3 = [p for p in pairs if p.outcome == Outcome.DIVERGENCE and p.grade == Tier.T3]
    t4 = [p for p in pairs if p.t4_only_agreement]

    def take(pop: list[PairResult], n: int) -> list[PairResult]:
        if len(pop) <= n:
            return list(pop)
        return sorted(rng.sample(pop, n), key=_key)

    return ReplaySample(t0_t2=take(hi, STRATUM_CAP_T0_T2), t3=take(t3, STRATUM_T3), t4_only=take(t4, STRATUM_T4_ONLY))


@dataclass
class Confusion:
    """Predicted consequential (T0-T2) vs not (T3, T4-only) against observed."""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, predicted: bool, observed: bool) -> None:
        if predicted and observed:
            self.tp += 1
        elif predicted:
            self.fp += 1
        elif observed:
            self.fn += 1
        else:
            self.tn += 1

    @property
    def precision(self) -> float | None:
        d = self.tp + self.fp
        return None if d == 0 else self.tp / d

    @property
    def recall(self) -> float | None:
        d = self.tp + self.fn
        return None if d == 0 else self.tp / d


def predicted_consequential(stratum: str) -> bool:
    return stratum == "T0-T2"

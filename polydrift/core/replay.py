"""Consumer replay: sampling rule and confusion matrix (manuscript 3.6).

The replay servers and consumers live in each cell's ``consumers/`` folder.
This module holds the protocol-independent parts: the sampling rule fixed in
advance and the confusion matrix of predicted tier against observed
consequence.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from polydrift.core.oracle import Outcome, PairResult
from polydrift.core.replay_action import ACTION_VIEWS
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


def deciding_steps(pair: PairResult, end_step: int | None = None) -> set[int]:
    """Steps that carry the difference the pair's stratum rests on: for a divergence the differences at its grade,
    for a T4-only agreement the erased ones. End-state differences map to ``end_step`` (None: not replayable).
    A pair is replayed at these steps only; if none of them can be replayed, the pair is not replayable, because
    replaying another step would test a different difference."""
    ds = [d for d in pair.diffs if d.tier == pair.grade] if pair.outcome == Outcome.DIVERGENCE else pair.t4_diffs
    out = set()
    for d in ds:
        if isinstance(d.step, int):
            out.add(d.step)
        elif end_step is not None:
            out.add(end_step)
    return out


def diff_axes(pair: PairResult) -> list[list[Any]]:
    """[axis, frozen tier, erased] for every difference of a pair, kept with each replay line so the
    non-circularity check (3.6) can regrade the pair under shuffled tier placements."""
    return [[d.axis, int(d.tier), bool(getattr(d, "erased", False))] for d in [*pair.diffs, *pair.t4_diffs]]


# The prediction each level tests (3.6). Strict: any difference the consumer API exposes, so a T0 to T3 divergence
# is predicted visible and only a T4-only agreement is predicted invisible. Action: what the library does and keeps,
# so T0 to T2 is predicted consequential and T3 and T4-only are not.
LEVEL_MAX_TIER = {"strict": 3, "action": 2}
STRATUM_TIER = {"T0-T2": 2, "T3": 3, "T4-only": 4}

# Consumers that come from the same project as an implementation under test and may share its parsing code; their
# results on pairs that involve that implementation are reported apart (3.6).
SAME_PROJECT = {"libmosquitto": "mosquitto", "libldap": "openldap"}


def predicted_consequential(stratum: str, level: str = "action") -> bool:
    return STRATUM_TIER[stratum] <= LEVEL_MAX_TIER[level]


def family(impl: str) -> str:
    """Implementation family: the name without its version (emqx-6.3.2 -> emqx, hivemq-ce-2026.5 -> hivemq-ce)."""
    import re

    return re.sub(r"-v?\d[\w.]*(-.*)?$", "", impl)


def same_project(consumer: str, left: str, right: str) -> bool:
    fam = SAME_PROJECT.get(consumer)
    return fam is not None and fam in (family(left), family(right))


def _strict(r: Any) -> Any:
    # a result's "data" (values the library passes to its program without acting on them, e.g. a SIP reason
    # phrase) is kept in replay.jsonl but not compared
    return {k: v for k, v in r.items() if k != "data"} if isinstance(r, dict) else r


def _cm(m: Confusion) -> dict[str, Any]:
    return {"tp": m.tp, "fp": m.fp, "fn": m.fn, "tn": m.tn, "precision": m.precision, "recall": m.recall}


def _level(by_pair: dict[tuple, dict[str, dict]], consumers: tuple[str, ...], view: Callable[[Any], Any],
           strata: dict[str, int], level: str) -> dict[str, Any]:
    conf = {c: Confusion() for c in consumers}
    indep = {c: Confusion() for c in consumers}
    same = {c: Confusion() for c in consumers}
    consequential: dict[tuple, dict[str, bool]] = {}
    for k, per_c in by_pair.items():
        for c, steps in per_c.items():
            obs = any(view(s.get("left")) != view(s.get("right")) for s in steps.values())
            consequential.setdefault(k, {})[c] = obs
            pred = predicted_consequential(k[3], level)
            conf[c].add(pred, obs)
            (same if same_project(c, k[1], k[2]) else indep)[c].add(pred, obs)
    return {
        "predicts": f"T0 to T{LEVEL_MAX_TIER[level]} consequential",
        "confusion": {c: _cm(m) for c, m in conf.items()},
        "confusion_independent": {c: _cm(m) for c, m in indep.items()},
        "confusion_same_project": {c: _cm(m) for c, m in same.items() if c in SAME_PROJECT},
        "consumer_disagreements": [list(k) for k, v in consequential.items() if len(set(v.values())) > 1],
        "per_stratum_consequential": {
            s: {c: sum(1 for k, v in consequential.items() if k[3] == s and v.get(c)) for c in consumers}
            for s in strata},
    }


PERMUTATIONS = 1000


def cell_tiers(cell: str) -> dict[str, int]:
    """Frozen tier per compared axis, from the cell's tiers.yaml."""
    import yaml
    from pathlib import Path

    from polydrift.core.oracle import NOT_COMPARED

    d = yaml.safe_load((Path(__file__).resolve().parents[1] / "cells" / cell / "tiers.yaml").read_text(encoding="utf-8"))
    return {a: int(v["tier"]) for a, v in (d.get("axes") or {}).items() if a not in NOT_COMPARED}


def permutation_check(lines: list[dict[str, Any]], tiers: dict[str, int], view: Callable[[Any], Any],
                      seed: int, n: int = PERMUTATIONS, level: str = "action") -> dict[str, Any]:
    """Non-circularity (3.6): agreement of the frozen placement with replay, against placements that give the cell's
    axes the same tiers in random order. Agreement is the share of replayed (pair, consumer) cases whose predicted
    class (at or below the level's highest predicted tier, or not) matches the observed consequence. Liveness (T0) and erased raw differences are not
    placements and keep their tier. Returns the frozen agreement and the share of shuffles that match or beat it."""
    obs: dict[tuple, bool] = {}
    axes: dict[tuple, list] = {}
    steps: dict[tuple, dict[Any, dict[str, Any]]] = {}
    for ln in lines:
        pk = (ln["fixture"], ln["left"], ln["right"], ln["stratum"])
        axes.setdefault(pk, ln.get("axes") or [])
        steps.setdefault((pk, ln["consumer"]), {}).setdefault(ln["step"], {})[ln["side"]] = ln["result"]
    for (pk, c), st in steps.items():
        obs[(pk, c)] = any(view(s.get("left")) != view(s.get("right")) for s in st.values())
    if not obs or not all(axes.values()):
        return {"cases": len(obs), "frozen_agreement": None, "share_at_least_frozen": None, "permutations": 0}

    def predicted(pk: tuple, place: dict[str, int] | None) -> bool:
        if place is None:
            return predicted_consequential(pk[3], level)
        g = None
        for axis, tier, erased in axes[pk]:
            t = 4 if erased else place.get(axis, tier)
            if t <= 3:
                g = t if g is None else min(g, t)
        return g is not None and g <= LEVEL_MAX_TIER[level]

    def agreement(place: dict[str, int] | None) -> float:
        return sum(predicted(pk, place) == o for (pk, _), o in obs.items()) / len(obs)

    frozen = agreement(None)
    rng = random.Random(seed)
    names = sorted(tiers)
    values = [tiers[a] for a in names]
    hits = 0
    for _ in range(n):
        rng.shuffle(values)
        if agreement(dict(zip(names, values))) >= frozen:
            hits += 1
    return {"cases": len(obs), "frozen_agreement": frozen, "share_at_least_frozen": hits / n, "permutations": n}


def replay_summary(lines: list[dict[str, Any]], sampled: list[tuple[str, Any]], not_replayable: list[str],
                   consumers: tuple[str, ...], seed: int, cell: str | None = None) -> dict[str, Any]:
    """Per consumer and per level: confusion of the level's prediction against observed consequence,
    consequential pairs per stratum, and pairs on which the consumers disagree (reported apart).

    The strict level counts any difference the consumer API exposes; it tests T4 erasure. The action level counts
    only what the consumer library does and keeps (core/replay_action.py); it tests the T3 placements. The
    top-level confusion and consumer_disagreements are the strict level, as before."""
    by_pair: dict[tuple, dict[str, dict]] = {}
    for ln in lines:
        k = (ln["fixture"], ln["left"], ln["right"], ln["stratum"])
        by_pair.setdefault(k, {}).setdefault(ln["consumer"], {}).setdefault(ln["step"], {})[ln["side"]] = ln["result"]
    strata = {s: sum(1 for st, _ in sampled if st == s) for s in ("T0-T2", "T3", "T4-only")}
    strict = _level(by_pair, consumers, _strict, strata, "strict")
    out = {"seed": seed, "sampled": strata, "replayed_pairs": len(by_pair), "not_replayable": len(not_replayable),
           **strict, "levels": {"strict": strict}}
    if cell is not None:
        view = ACTION_VIEWS[cell]
        action = lambda r: view(_strict(r))  # noqa: E731
        out["levels"]["action"] = _level(by_pair, consumers, action, strata, "action")
        tiers = cell_tiers(cell)
        out["non_circularity"] = {"strict": permutation_check(lines, tiers, _strict, seed, level="strict"),
                                  "action": permutation_check(lines, tiers, action, seed, level="action")}
    return out

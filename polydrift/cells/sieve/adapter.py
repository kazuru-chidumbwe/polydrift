"""Sieve adapter: the engine wrapper's JSON (lab/run.py) as observation records (manuscript 3.1).

One engine run gives two records. `<fixture>` holds the compile result and the intent (every engine has them);
`<fixture>@effect` holds where the message ended up and what was sent, and exists only for engines that executed
the script against a lab mailbox. An engine without an effect layer therefore never diverges on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from polydrift.core.canon import identity
from polydrift.core.record import Field, ObservationRecord, Status, StepRecord, Tier
from polydrift.core.spec import AxisSpec, CellSpec

CELL_DIR = Path(__file__).resolve().parent
EFFECT = "@effect"


def load_spec() -> CellSpec:
    t = yaml.safe_load((CELL_DIR / "tiers.yaml").read_text(encoding="utf-8"))
    axes = {n: AxisSpec(name=n, tier=Tier(r["tier"]), kappa=identity, rule_clause=r.get("clause", ""))
            for n, r in t["axes"].items()}
    return CellSpec(cell="sieve", axes=axes, refusal_class=t.get("refusal_class"))


def outcome(res: dict[str, Any]) -> str:
    if res.get("compile") == "refused":
        return "refused"
    if res.get("runtime_error") or res.get("intent") is None:
        return "runtime-error"
    return "ok"


def _f(spec: CellSpec, values: dict[str, Any]) -> dict[str, Field]:
    return {k: spec.axis(k).field(v) for k, v in values.items()}


def intent_step(res: dict[str, Any], spec: CellSpec) -> StepRecord:
    if res.get("harness_error") or res.get("runner_error"):
        return StepRecord(status=Status.HARNESS_FAULT, detail=str(res.get("harness_error") or res.get("compile_text"))[:200])
    oc = outcome(res)
    vals = {"decision_class": "fix-request" if oc == "refused" else "proceed", "outcome": oc,
            "compile_class": res.get("compile_class") if oc == "refused" else None,
            "intent": tuple(sorted(res.get("intent") or [])) if oc == "ok" else None}
    return StepRecord(status=Status.RESPONDED, fields=_f(spec, vals))


def effect_step(res: dict[str, Any], spec: CellSpec) -> StepRecord | None:
    eff = res.get("effect")
    if not eff:
        return None
    stored = tuple(sorted((k, int(v)) for k, v in (eff.get("stored") or {}).items() if int(v) > 0))
    emitted = tuple(sorted((e.get("kind"), tuple(sorted(e.get("to") or []))) for e in (eff.get("emitted") or [])))
    return StepRecord(status=Status.RESPONDED, fields=_f(spec, {"stored": stored, "emitted": emitted}))


def to_records(res: dict[str, Any], impl: str, fixture: str, *, spec: CellSpec, repeat: int = 1,
               impl_version: str = "", harness_commit: str = "") -> list[ObservationRecord]:
    base = dict(cell="sieve", impl=impl, impl_version=impl_version, repeat=repeat, harness_commit=harness_commit)
    out = [ObservationRecord(fixture=fixture, steps=(intent_step(res, spec),), **base)]
    eff = effect_step(res, spec)
    if eff is not None:
        out.append(ObservationRecord(fixture=fixture + EFFECT, steps=(eff,), **base))
    return out

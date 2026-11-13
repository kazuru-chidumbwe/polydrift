"""SIP behaviour cell adapter (sipb): driver step observations -> ObservationRecord.

Each step's status is responded unless the driver could not send (harness fault): what the element did, including
silence, is in the fields. The decision class is that of the first final response the acting party received
(2xx proceed, 3xx redirect, 401/407 authenticate, other 4xx fix-request, 5xx retry-later, 6xx stop, none
no-response). Fixture-named observations (driver `extract`) sit on the generic axes x1 to x8. The generic codes and
requests axes compare transactions, not copies (retransmissions collapsed, see distinct); the driver has already
set aside OPTIONS probes on Call-IDs the lab never sent and masked per-run random tokens in extracts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from polydrift.core.canon import identity
from polydrift.core.record import Field, ObservationRecord, Status, StepRecord, Tier
from polydrift.core.spec import AxisSpec, CellSpec

CELL_DIR = Path(__file__).resolve().parent


def load_spec() -> CellSpec:
    t = yaml.safe_load((CELL_DIR / "tiers.yaml").read_text(encoding="utf-8"))
    axes = {n: AxisSpec(name=n, tier=Tier(r["tier"]), kappa=identity, rule_clause=r.get("clause", ""))
            for n, r in t["axes"].items()}
    return CellSpec(cell="sipb", axes=axes, refusal_class=None)


def decision(code: int | None) -> str:
    if code is None:
        return "no-response"
    if 200 <= code < 300:
        return "proceed"
    if 300 <= code < 400:
        return "redirect"
    if code in (401, 407):
        return "authenticate"
    if 400 <= code < 500:
        return "fix-request"
    if 500 <= code < 600:
        return "retry-later"
    return "stop"


def _txn_key(m: dict[str, Any]) -> tuple:
    """A message's transaction identity: method or status code, top Via branch and CSeq. Retransmissions share it."""
    hdr = m.get("hdr") or {}
    via = (hdr.get("via") or [""])[0]
    br = via.split("branch=", 1)[1].split(";", 1)[0].strip() if "branch=" in via else ""
    return (m.get("kind"), m.get("method") or m.get("code"), br, (hdr.get("cseq") or [""])[0].strip())


def distinct(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages with retransmissions collapsed (first transmission kept, order kept). How many copies arrive in a
    window depends on the element's timers against the window edge, so the generic axes compare transactions;
    fixtures that test retransmission itself count copies in their own extracts (ncodes, hist)."""
    seen: set[tuple] = set()
    out = []
    for m in msgs:
        k = _txn_key(m)
        if k not in seen:
            seen.add(k)
            out.append(m)
    return out


def step_record(obs: dict[str, Any], spec: CellSpec) -> StepRecord:
    err = obs.get("error")
    if err and (str(err).startswith("driver") or str(err).startswith("send")):
        return StepRecord(status=Status.HARNESS_FAULT, detail=str(err)[:200])
    got = obs.get("got") or {}
    actor = obs.get("actor", "alice")
    finals = [m.get("code") for m in got.get(actor, []) if m.get("kind") == "resp" and (m.get("code") or 0) >= 200]
    v: dict[str, Any] = {
        "decision_class": decision(finals[0] if finals else None),
        "codes": {p: [m.get("code") for m in distinct(msgs) if m.get("kind") == "resp"]
                  for p, msgs in got.items() if msgs},
        "requests": {p: [m.get("method") for m in distinct(msgs) if m.get("kind") == "req"]
                     for p, msgs in got.items() if msgs},
    }
    fields = {k: spec.axis(k).field(x) for k, x in v.items()}
    for k, val in (obs.get("extract") or {}).items():
        if k in spec.axes:
            fields[k] = spec.axis(k).field(val if val is not None else "null")
    return StepRecord(status=Status.RESPONDED, fields=fields, detail=str(err or ""))


def to_record(observations: list[dict[str, Any]], impl: str, fixture: str, *, spec: CellSpec, repeat: int = 1,
              impl_version: str = "", harness_commit: str = "") -> ObservationRecord:
    return ObservationRecord(cell="sipb", impl=impl, impl_version=impl_version, fixture=fixture, repeat=repeat,
                             harness_commit=harness_commit, steps=tuple(step_record(o, spec) for o in observations))
